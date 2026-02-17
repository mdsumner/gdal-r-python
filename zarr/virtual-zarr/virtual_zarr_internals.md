---
title: "Virtual Zarr Stores: Kerchunk and Icechunk Internals"
author: "Combined from code tracing sessions"
date: "2026-02-17"
format: gfm
---

# Virtual Zarr Stores: Kerchunk and Icechunk Internals

This document combines two investigations into how virtual Zarr stores work:

1. **Kerchunk/fsspec**: How parquet-based reference stores are read
2. **VirtualiZarr/Icechunk**: How to create and read Icechunk stores with virtual chunks

Both approaches solve the same problem: make existing NetCDF/HDF5/COG archives
accessible as cloud-native Zarr without copying data.

## Table of Contents

- [Conceptual Overview](#conceptual-overview)
- [Part 1: Kerchunk Parquet Format](#part-1-kerchunk-parquet-format)
- [Part 2: Icechunk Virtual Chunks](#part-2-icechunk-virtual-chunks)
- [Part 3: The Bridge - Converting Between Formats](#part-3-the-bridge)
- [Part 4: Practical Workflows](#part-4-practical-workflows)
- [Appendix: API Reference](#appendix-api-reference)

---

## Conceptual Overview

A "virtual Zarr store" contains:

1. **Metadata**: Array shapes, chunks, dtypes, attributes, dimension names
2. **Chunk references**: For each chunk, a pointer to `(path, byte_offset, byte_length)` in a source file

When you read data, the client:
1. Looks up which source file and byte range contains the chunk
2. Fetches just those bytes over HTTP/S3
3. Decompresses and returns the data

No data is copied. The virtual store is just an index.

### Format Comparison

| Aspect | Kerchunk Parquet | Icechunk |
|--------|------------------|----------|
| Storage | Directory of `.parq` files | Single store (local/S3) |
| Versioning | None | Git-like snapshots, branches, tags |
| Chunk indexing | Implicit (row order) | Explicit in manifest |
| Readers | fsspec, xarray, GDAL (via kerchunk) | xarray/zarr-python |
| Write API | VirtualiZarr, kerchunk | VirtualiZarr, direct API |

---

## Part 1: Kerchunk Parquet Format

### Store Layout

```
store.parquet/
├── .zmetadata              # JSON: consolidated Zarr metadata + record_size
├── analysed_sst/
│   ├── refs.0.parq         # Chunk refs 0 to record_size-1
│   ├── refs.1.parq         # Chunk refs record_size to 2*record_size-1
│   └── ...
├── analysis_error/
│   └── refs.0.parq
└── time/
    └── refs.0.parq
```

### Parquet Schema

Each `refs.{N}.parq` file has this schema:

| Column | Type | Description |
|--------|------|-------------|
| `path` | string | Source file URL (null if inline data) |
| `offset` | int64 | Byte offset in source file |
| `size` | int64 | Byte length |
| `raw` | binary | Inline data for small chunks (null if external) |

**Chunk index is implicit**: Row 0 = chunk 0, row 1 = chunk 1, etc.
Multi-dimensional indices are flattened in row-major (C) order.

### .zmetadata Structure

```json
{
  "record_size": 100000,
  "metadata": {
    ".zattrs": "{...}",
    ".zgroup": "{\"zarr_format\": 2}",
    "analysed_sst/.zarray": "{\"chunks\": [1, 512, 512], \"shape\": [8660, 17999, 36000], ...}",
    "analysed_sst/.zattrs": "{\"units\": \"kelvin\", ...}",
    "time/.zarray": "{...}",
    "time/.zattrs": "{...}"
  }
}
```

- `record_size`: Chunks per parquet file (typically 100,000)
- `metadata`: All Zarr metadata as JSON strings

### How fsspec Reads It

The code path when you call `xr.open_dataset("store.parquet", engine="kerchunk")`:

```
xarray.open_dataset()
  → kerchunk.xarray_backend.open_dataset()
    → kerchunk.utils.refs_as_fs()
      → fsspec.filesystem("reference", fo="store.parquet")
        → ReferenceFileSystem.__init__()
          → LazyReferenceMapper(root, fs)  # Parquet path detected
```

#### LazyReferenceMapper Internals

```python
class LazyReferenceMapper:
    def __init__(self, root, fs, cache_size=128):
        self.root = root
        self.fs = fs
        self.url = f"{root}/{{field}}/refs.{{record}}.parq"
        self.setup()
    
    def setup(self):
        # Read consolidated metadata
        zmetadata = json.loads(self.fs.cat_file(f"{self.root}/.zmetadata"))
        self.record_size = zmetadata["record_size"]
        self.zmetadata = zmetadata["metadata"]
        
        # LRU-cached parquet reader
        @lru_cache(maxsize=self.cache_size)
        def open_refs(field, record):
            path = self.url.format(field=field, record=record)
            df = pd.read_parquet(io.BytesIO(self.fs.cat_file(path)))
            return {c: df[c].to_numpy() for c in df.columns}
        
        self.open_refs = open_refs
```

#### Chunk Key to Partition Mapping

```python
def _key_to_record(self, key):
    """Map "analysed_sst/0.5.3" → (field, partition_number, row_in_partition)"""
    field, chunk_key = key.rsplit("/", 1)
    chunk_indices = [int(c) for c in chunk_key.split(".")]
    
    # Get chunk grid dimensions from metadata
    zarray = json.loads(self.zmetadata[f"{field}/.zarray"])
    nchunks = [math.ceil(s / c) for s, c in zip(zarray["shape"], zarray["chunks"])]
    
    # Flatten to single index (row-major order)
    flat_index = np.ravel_multi_index(chunk_indices, nchunks)
    
    record = flat_index // self.record_size
    row = flat_index % self.record_size
    
    return field, record, row
```

#### Reading a Chunk Reference

```python
def _load_one_key(self, key):
    field, record, row = self._key_to_record(key)
    refs = self.open_refs(field, record)  # Cached parquet read
    
    if refs.get("raw") is not None and refs["raw"][row] is not None:
        return refs["raw"][row]  # Inline data
    else:
        return [refs["path"][row], refs["offset"][row], refs["size"][row]]
```

### Key Design Decisions

1. **No filesystem listing**: Partition paths are computed, not discovered
2. **Lazy loading**: Parquet files read on-demand with LRU cache
3. **Flat indexing**: `np.ravel_multi_index` for chunk coordinate → row mapping
4. **record_size**: Balances file count vs. file size (100k chunks/file typical)

---

## Part 2: Icechunk Virtual Chunks

### Core Concepts

Icechunk stores:
- **Repository**: The store itself (on S3, local filesystem, etc.)
- **Session**: A read or write transaction
- **VirtualChunkContainer**: Configuration for fetching virtual chunks from external sources

### Working API (tested 2026-02-04)

Package versions: VirtualiZarr 2.4.0, Icechunk 1.1.18, obstore 0.8.2

#### Creating a Virtual Dataset

```python
from obstore.store import HTTPStore
from obspec_utils.registry import ObjectStoreRegistry
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser

url = "https://thredds.example.com/data/file.nc"
host = "https://thredds.example.com"

# Registry uses plain URL prefix, NOT regex
http_store = HTTPStore.from_url(host)
registry = ObjectStoreRegistry({host: http_store})
parser = HDFParser()  # No arguments

vds = open_virtual_dataset(url, registry=registry, parser=parser)
```

#### Writing to Icechunk

```python
import icechunk

# Storage location for the Icechunk store
storage = icechunk.local_filesystem_storage("/path/to/store.icechunk")
# Or: storage = icechunk.s3_storage(bucket, prefix, endpoint_url=..., from_env=True)

# Configure virtual chunk container
config = icechunk.RepositoryConfig.default()
container = icechunk.VirtualChunkContainer(
    url_prefix="https://thredds.example.com/",  # Trailing slash!
    store=icechunk.http_store(),
)
config.set_virtual_chunk_container(container)

# Create and write
repo = icechunk.Repository.create(storage=storage, config=config)
session = repo.writable_session("main")

vds.virtualize.to_icechunk(session.store)  # Note: session.store
snapshot_id = session.commit("Initial virtualization")
```

#### Reading from Icechunk

**Critical**: Must pass BOTH `config` AND `authorize_virtual_chunk_access`:

```python
import xarray as xr

storage = icechunk.local_filesystem_storage("/path/to/store.icechunk")

# Same config as write
config = icechunk.RepositoryConfig.default()
config.set_virtual_chunk_container(icechunk.VirtualChunkContainer(
    url_prefix="https://thredds.example.com/",
    store=icechunk.http_store(),
))

# Open with authorization for virtual chunks
repo = icechunk.Repository.open(
    storage=storage,
    config=config,
    authorize_virtual_chunk_access={"https://thredds.example.com/": None}  # None = anonymous
)

session = repo.readonly_session("main")
ds = xr.open_zarr(session.store, consolidated=False)

# Access data - fetches bytes from source via virtual refs
print(ds.temp.isel(time=0).values)
```

### Direct Ref Writing (Bypass VirtualiZarr)

For maximum control, write refs directly:

```python
import icechunk
import zarr

# Setup repo (same as above)
repo = icechunk.Repository.create(storage=storage, config=config)
session = repo.writable_session("main")
store = session.store

# Create zarr structure
root = zarr.open_group(store, mode="w", zarr_format=3)

# Create array with dimension_names (required for xarray)
root.create_array(
    "sst",
    shape=(365, 720, 1440),
    chunks=(1, 720, 1440),
    dtype="float32",
    dimension_names=["time", "lat", "lon"],
)

# Set virtual refs directly
specs = [
    icechunk.VirtualChunkSpec(
        index=(0, 0, 0),
        location="https://example.com/data.nc",
        offset=1000,
        length=5000,
    ),
    icechunk.VirtualChunkSpec(
        index=(1, 0, 0),
        location="https://example.com/data.nc",
        offset=6000,
        length=5000,
    ),
    # ...
]

store.set_virtual_refs("sst", specs, validate_containers=False)
session.commit("Direct refs")
```

### VirtualChunkSpec Fields

```python
icechunk.VirtualChunkSpec(
    index=(0, 0, 0),           # Chunk coordinates (tuple of ints)
    location="https://...",     # Full URL to source file
    offset=1000,                # Byte offset
    length=5000,                # Byte length
    etag_checksum=None,         # Optional ETag for validation
    last_updated_at_checksum=None,  # Optional timestamp
)
```

---

## Part 3: The Bridge

### Converting Kerchunk Parquet to Icechunk

The key insight: both formats store the same information, just structured differently.

```python
import pandas as pd
import numpy as np
import icechunk
import zarr

def flat_to_chunk_index(flat_idx: int, n_chunks_per_dim: tuple) -> tuple:
    """Convert flat row index to chunk tuple (row-major order)."""
    coords = []
    remaining = flat_idx
    for n in reversed(n_chunks_per_dim):
        coords.append(remaining % n)
        remaining //= n
    return tuple(reversed(coords))


def kerchunk_parquet_to_icechunk(
    parquet_dir: str,
    icechunk_path: str,
    url_prefix: str,
):
    """Convert a Kerchunk parquet store to Icechunk."""
    import json
    from pathlib import Path
    
    # Read .zmetadata
    with open(Path(parquet_dir) / ".zmetadata") as f:
        zmetadata = json.load(f)
    
    record_size = zmetadata["record_size"]
    metadata = zmetadata["metadata"]
    
    # Setup Icechunk
    storage = icechunk.local_filesystem_storage(icechunk_path)
    config = icechunk.RepositoryConfig.default()
    config.set_virtual_chunk_container(icechunk.VirtualChunkContainer(
        url_prefix=url_prefix,
        store=icechunk.http_store(),
    ))
    
    repo = icechunk.Repository.create(storage=storage, config=config)
    session = repo.writable_session("main")
    store = session.store
    root = zarr.open_group(store, mode="w", zarr_format=3)
    
    # Find variables (directories with .zarray)
    variables = set()
    for key in metadata:
        if "/.zarray" in key:
            variables.add(key.split("/")[0])
    
    for var in variables:
        zarray = json.loads(metadata[f"{var}/.zarray"])
        shape = zarray["shape"]
        chunks = zarray["chunks"]
        dtype = zarray["dtype"]
        
        # Infer dimension names from .zattrs if available
        zattrs = json.loads(metadata.get(f"{var}/.zattrs", "{}"))
        dims = zattrs.get("_ARRAY_DIMENSIONS", [f"dim_{i}" for i in range(len(shape))])
        
        # Create array
        root.create_array(
            var,
            shape=shape,
            chunks=chunks,
            dtype=dtype,
            dimension_names=dims,
        )
        
        # Calculate chunk grid
        n_chunks_per_dim = tuple((s + c - 1) // c for s, c in zip(shape, chunks))
        total_chunks = np.prod(n_chunks_per_dim)
        
        # Read all parquet files for this variable
        specs = []
        parquet_files = sorted(Path(parquet_dir).glob(f"{var}/refs.*.parq"))
        
        for pq_file in parquet_files:
            record_num = int(pq_file.stem.split(".")[1])
            df = pd.read_parquet(pq_file)
            
            for row_idx, row in df.iterrows():
                if pd.isna(row.get("path")):
                    continue  # Skip missing chunks
                
                flat_idx = record_num * record_size + row_idx
                if flat_idx >= total_chunks:
                    break
                
                chunk_idx = flat_to_chunk_index(flat_idx, n_chunks_per_dim)
                
                specs.append(icechunk.VirtualChunkSpec(
                    index=chunk_idx,
                    location=row["path"],
                    offset=int(row["offset"]),
                    length=int(row["size"]),
                ))
        
        store.set_virtual_refs(var, specs, validate_containers=False)
        print(f"{var}: {len(specs)} chunks")
    
    snapshot_id = session.commit("Converted from Kerchunk parquet")
    return snapshot_id
```

### Proposed Flat Parquet Schema

For tools like rustycogs that generate refs directly, an explicit schema is cleaner:

```
| variable | i0 | i1 | i2 | path | offset | length |
|----------|----|----|----|-----------------------------|--------|--------|
| sst      | 0  | 0  | 0  | s3://bucket/data.nc         | 1000   | 5000   |
| sst      | 0  | 0  | 1  | s3://bucket/data.nc         | 6000   | 5000   |
| sst      | 0  | 1  | 0  | s3://bucket/data.nc         | 11000  | 5000   |
| anom     | 0  | 0  | 0  | s3://bucket/data.nc         | 50000  | 5000   |
```

Advantages:
- Single file for entire store
- Explicit chunk indices (no shape/chunks needed to decode)
- Trivial to convert to either Kerchunk or Icechunk
- Can be generated from any language (R, Rust, Python)

Companion metadata (JSON or parquet header):

```json
{
  "variables": {
    "sst": {
      "shape": [365, 720, 1440],
      "chunks": [1, 720, 1440],
      "dtype": "float32",
      "dims": ["time", "lat", "lon"]
    },
    "anom": {
      "shape": [365, 720, 1440],
      "chunks": [1, 720, 1440],
      "dtype": "float32", 
      "dims": ["time", "lat", "lon"]
    }
  },
  "coords": {
    "time": {"shape": [365], "dtype": "float64"},
    "lat": {"shape": [720], "dtype": "float32"},
    "lon": {"shape": [1440], "dtype": "float32"}
  }
}
```

### Explicit Parquet to Icechunk

```python
def explicit_parquet_to_icechunk(
    parquet_path: str,
    metadata: dict,
    icechunk_path: str,
    url_prefix: str,
    coords: dict = None,
):
    """
    Convert explicit-index parquet to Icechunk.
    
    Args:
        parquet_path: Path to parquet with columns: variable, i0, i1, ..., path, offset, length
        metadata: Dict with variable metadata (shape, chunks, dtype, dims)
        icechunk_path: Output path for Icechunk store
        url_prefix: URL prefix for virtual chunk container
        coords: Optional dict of coordinate arrays
    """
    df = pd.read_parquet(parquet_path)
    
    # Find index columns
    idx_cols = sorted([c for c in df.columns if c.startswith("i") and c[1:].isdigit()])
    
    # Setup Icechunk
    storage = icechunk.local_filesystem_storage(icechunk_path)
    config = icechunk.RepositoryConfig.default()
    config.set_virtual_chunk_container(icechunk.VirtualChunkContainer(
        url_prefix=url_prefix,
        store=icechunk.http_store() if url_prefix.startswith("http") else icechunk.s3_store(),
    ))
    
    repo = icechunk.Repository.create(storage=storage, config=config)
    session = repo.writable_session("main")
    store = session.store
    root = zarr.open_group(store, mode="w", zarr_format=3)
    
    # Create coordinates
    if coords:
        for name, values in coords.items():
            root.create_array(name, data=np.asarray(values), dimension_names=[name])
    
    # Process each variable
    for var in df["variable"].unique():
        meta = metadata[var]
        root.create_array(
            var,
            shape=meta["shape"],
            chunks=meta["chunks"],
            dtype=meta["dtype"],
            dimension_names=meta["dims"],
        )
        
        var_df = df[df["variable"] == var]
        specs = [
            icechunk.VirtualChunkSpec(
                index=tuple(int(row[c]) for c in idx_cols),
                location=row["path"],
                offset=int(row["offset"]),
                length=int(row["length"]),
            )
            for _, row in var_df.iterrows()
            if not pd.isna(row["path"])
        ]
        
        store.set_virtual_refs(var, specs, validate_containers=False)
    
    return session.commit("From explicit parquet")
```

---

## Part 4: Practical Workflows

### Workflow 1: Virtualize Thredds Archive to Icechunk

```python
from virtualizarr import open_virtual_mfdataset
from virtualizarr.parsers import HDFParser
from obstore.store import HTTPStore
from obspec_utils.registry import ObjectStoreRegistry
import icechunk

# 1. Generate URLs
base = "https://thredds.nci.org.au/thredds/fileServer/gb6/BRAN/BRAN2023/daily"
urls = [f"{base}/ocean_temp_{year}_{month:02d}.nc" 
        for year in range(2020, 2025) for month in range(1, 13)]

# 2. Virtualize
host = "https://thredds.nci.org.au"
registry = ObjectStoreRegistry({host: HTTPStore.from_url(host)})
vds = open_virtual_mfdataset(urls, registry=registry, parser=HDFParser(),
                              concat_dim="Time", combine="nested")

# 3. Write to Icechunk on S3
storage = icechunk.s3_storage(
    bucket="my-bucket",
    prefix="bran2023.icechunk",
    endpoint_url="https://s3.amazonaws.com",
    from_env=True,
)
config = icechunk.RepositoryConfig.default()
config.set_virtual_chunk_container(icechunk.VirtualChunkContainer(
    url_prefix="https://thredds.nci.org.au/",
    store=icechunk.http_store(),
))

repo = icechunk.Repository.create(storage=storage, config=config)
session = repo.writable_session("main")
vds.virtualize.to_icechunk(session.store)
session.commit("BRAN2023 full archive")
```

### Workflow 2: rustycogs → Icechunk

```r
# In R: generate refs with rustycogs
library(rustycogs)

refs <- scan_cogs(
  urls = cog_urls,
  output_format = "explicit_parquet"  # Proposed format
)

arrow::write_parquet(refs$chunks, "refs.parquet")
jsonlite::write_json(refs$metadata, "metadata.json")
```

```python
# In Python: convert to Icechunk
import json

with open("metadata.json") as f:
    metadata = json.load(f)

explicit_parquet_to_icechunk(
    "refs.parquet",
    metadata["variables"],
    "output.icechunk",
    url_prefix="s3://source-bucket/",
    coords=metadata.get("coords"),
)
```

### Workflow 3: Read Icechunk from Anywhere

```python
import xarray as xr
import icechunk

# S3-hosted Icechunk store
storage = icechunk.s3_storage(
    bucket="public-data",
    prefix="bran2023.icechunk",
    endpoint_url="https://s3.amazonaws.com",
    allow_anonymous=True,
)

config = icechunk.RepositoryConfig.default()
config.set_virtual_chunk_container(icechunk.VirtualChunkContainer(
    url_prefix="https://thredds.nci.org.au/",
    store=icechunk.http_store(),
))

repo = icechunk.Repository.open(
    storage=storage,
    config=config,
    authorize_virtual_chunk_access={"https://thredds.nci.org.au/": None},
)

ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False)

# Lazy - no data loaded yet
print(ds)

# Fetches only the chunks needed
sst_slice = ds.temp.sel(Time="2024-01", st_ocean=0).mean(dim="Time")
print(sst_slice.values)
```

---

## Appendix: API Reference

### VirtualiZarr 2.4.0

```python
# Virtualize single file
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser
from obspec_utils.registry import ObjectStoreRegistry

vds = open_virtual_dataset(
    url,                    # Full URL to file
    registry=registry,      # ObjectStoreRegistry (required)
    parser=parser,          # HDFParser() (required, no args)
)

# Virtualize multiple files
from virtualizarr import open_virtual_mfdataset

vds = open_virtual_mfdataset(
    urls,                   # List of URLs
    registry=registry,
    parser=parser,
    concat_dim="time",      # Dimension to concat along
    combine="nested",       # "nested" or "by_coords"
)

# Write to formats
vds.virtualize.to_icechunk(session.store)
vds.virtualize.to_kerchunk(path, format="parquet")  # or "json"
```

### Icechunk 1.1.18

```python
import icechunk

# Storage backends
storage = icechunk.local_filesystem_storage(path)
storage = icechunk.s3_storage(bucket, prefix, endpoint_url=..., from_env=True, force_path_style=True)
storage = icechunk.gcs_storage(bucket, prefix, ...)

# Repository operations
repo = icechunk.Repository.create(storage=storage, config=config)
repo = icechunk.Repository.open(storage=storage, config=config, 
                                 authorize_virtual_chunk_access={prefix: None})

# Sessions
session = repo.writable_session("main")
session = repo.readonly_session("main")
session = repo.readonly_session(snapshot_id)  # Historical version

# Writing
store = session.store
vds.virtualize.to_icechunk(store)
# or direct:
store.set_virtual_refs(array_path, [VirtualChunkSpec(...), ...])
snapshot_id = session.commit("message")

# Reading
ds = xr.open_zarr(session.store, consolidated=False)

# Virtual chunk container
config = icechunk.RepositoryConfig.default()
config.set_virtual_chunk_container(icechunk.VirtualChunkContainer(
    url_prefix="https://...",           # Include trailing slash
    store=icechunk.http_store(),        # or s3_store(), gcs_store()
))

# VirtualChunkSpec
spec = icechunk.VirtualChunkSpec(
    index=(0, 0, 0),        # Chunk coordinates
    location="https://...", # Full URL
    offset=1000,            # Byte offset
    length=5000,            # Byte length
)
```

### Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| "need to authorize virtual chunk container" | Missing authorization on read | Add `authorize_virtual_chunk_access={prefix: None}` to `Repository.open()` |
| "ObjectStoreRegistry" import error | Moved to obspec-utils | `from obspec_utils.registry import ObjectStoreRegistry` |
| "missing dimension_names" | Zarr v3 + xarray | Use `dimension_names=` in `create_array()` |
| Empty/wrong results | URL prefix mismatch | Ensure trailing slash consistency |

---

## Summary

Both Kerchunk and Icechunk solve the same problem: indexing existing archives as Zarr.

**Kerchunk Parquet**:
- Mature, widely supported (fsspec, GDAL)
- Implicit chunk indexing (row order)
- No versioning

**Icechunk**:
- Git-like versioning (snapshots, branches, tags)
- Explicit chunk indexing
- Direct API for writing refs
- Python-only (for now)

**The bridge**: Both can be populated from the same source data. A flat parquet with explicit indices is the cleanest interchange format.

For new projects, consider:
- Icechunk if versioning matters and Python is your stack
- Kerchunk parquet if you need GDAL compatibility
- Both if you want maximum flexibility (same source, dual outputs)
