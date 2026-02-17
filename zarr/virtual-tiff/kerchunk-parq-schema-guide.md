---
title: "How Kerchunk Opens Parquet-Based Virtual Zarr Stores"
author: "Generated from code tracing session"
date: "2026-02-17"
format: html
---

## Overview

When you call `xarray.open_dataset("path/to/store.parquet", engine="kerchunk")`,
the actual work of discovering and reading individual `.parq` partition files
happens inside **fsspec**, not kerchunk. Kerchunk provides a thin xarray backend
that passes the path to fsspec's `ReferenceFileSystem`.

This document traces the full code path.

## Environment Setup

Python 3.10 was used. To recreate the environment:

```bash
python3.10 -m venv workenv
source workenv/bin/activate
pip install xarray kerchunk fsspec zarr pandas pyarrow
```

Key package versions used in this trace:

```bash
pip show xarray kerchunk fsspec zarr pandas pyarrow | grep -E "^(Name|Version)"
```

At the time of writing:

- xarray
- kerchunk (released/pip version, not Git checkout)
- fsspec
- zarr
- pandas
- pyarrow

## Example Parquet Store Layout

A parquet-based virtual Zarr store has this structure:

```
/tmp/vzarr/ghrsst_mur_june2002.parquet/
├── .zmetadata
├── analysed_sst/
│   ├── refs.0.parq
│   ├── refs.1.parq
│   ├── refs.89.parq
│   ├── refs.112.parq
│   ├── refs.131.parq
│   ├── refs.177.parq
│   └── ...
├── analysis_error/
│   └── ...
├── mask/
│   └── ...
├── sea_ice_fraction/
│   └── ...
└── time/
    └── ...
```

- `.zmetadata` is a JSON file containing consolidated Zarr metadata **and** the
  `record_size` used to compute partition indices.
- Each subdirectory corresponds to a Zarr variable.
- Each `refs.{N}.parq` file is a Parquet DataFrame where each row is a chunk
  reference: `[path, offset, size]` or `[raw]` bytes.

## Call Chain

### 1. Kerchunk xarray backend — entry point

File: `kerchunk/xarray_backend.py`

```python
# KerchunkBackend.open_dataset() calls:
open_reference_dataset(filename_or_obj, ...)
    → refs_as_store(filename_or_obj)    # from kerchunk/utils.py
        → refs_as_fs(refs)              # calls fsspec.filesystem("reference", fo=refs)
        → fs_as_store(fss)              # wraps in zarr.storage.FsspecStore
    → xr.open_zarr(store, zarr_format=2, consolidated=False)
```

The function `refs_as_fs()` in `kerchunk/utils.py` does:

```python
def refs_as_fs(refs, **kwargs):
    refs = refs_as_dict(refs)
    return fsspec.filesystem("reference", fo=refs, **kwargs)
```

When `refs` is a path string ending in `.parquet`, it is passed directly as `fo`
to fsspec's `ReferenceFileSystem`.

### 2. fsspec `ReferenceFileSystem.__init__` — parquet detection

File: `fsspec/implementations/reference.py` (line ~706 in the version tested)

```python
class ReferenceFileSystem(AsyncFileSystem):
    def __init__(self, fo, ...):
        ...
        if ".json" not in fo2 and (
            fo.endswith(("parq", "parquet", "/")) or ref_fs.isdir(fo2)
        ):
            # Path looks like a parquet store, use lazy loading
            self.references = LazyReferenceMapper(
                fo2, fs=ref_fs, cache_size=cache_size
            )
        ...
```

This is the branching point: if the path looks like a parquet directory (not a
`.json` file), fsspec creates a `LazyReferenceMapper` instead of loading a
monolithic JSON reference set.

### 3. `LazyReferenceMapper` — the core class

File: `fsspec/implementations/reference.py`

#### Initialization and `setup()`

```python
class LazyReferenceMapper(collections.abc.MutableMapping):
    def __init__(self, root, fs=None, cache_size=128, ...):
        self.root = root
        self.fs = fs or fsspec.filesystem("file")
        self.cache_size = cache_size
        self.url = self.root + "/{field}/refs.{record}.parq"
        ...
        self.setup()
```

`setup()` reads `.zmetadata` and defines the cached parquet reader:

```python
def setup(self):
    self._items[".zmetadata"] = self.fs.cat_file(
        self.root + "/.zmetadata"
    )
    met = json.loads(self._items[".zmetadata"])
    self.record_size = met["record_size"]
    self.zmetadata = met["metadata"]

    @lru_cache(maxsize=self.cache_size)
    def open_refs(field, record):
        """Read a single .parq partition file, cached by LRU"""
        path = self.url.format(field=field, record=record)
        data = io.BytesIO(self.fs.cat_file(path))
        df = pd.read_parquet(data, engine=self.engine)
        refs = {c: df[c].to_numpy() for c in df.columns}
        return refs

    self.open_refs = open_refs
```

#### `.zmetadata` contents

The `.zmetadata` file is JSON with this structure:

```json
{
  "record_size": 100000,
  "metadata": {
    ".zattrs": "{...}",
    ".zgroup": "{...}",
    "analysed_sst/.zarray": "{\"chunks\": [1, 512, 512], \"shape\": [8660, 17999, 36000], ...}",
    "analysed_sst/.zattrs": "{...}",
    ...
  }
}
```

`record_size` is the number of chunk references packed into each `.parq` file
(typically 100,000).

`metadata` contains all the Zarr metadata keys (`.zarray`, `.zattrs`, `.zgroup`)
as JSON strings.

#### `_key_to_record()` — mapping chunk keys to partition files

When zarr requests a chunk like `"analysed_sst/0.5.3"`, this method computes
which `.parq` file contains the reference and which row:

```python
def _key_to_record(self, key):
    """
    key: e.g. "analysed_sst/0.5.3"
    returns: (field, record_number, row_within_record)
    """
    field, chunk_key = key.rsplit("/", 1)

    # Parse the chunk coordinates
    chunk_indices = [int(c) for c in chunk_key.split(".")]

    # Get the number of chunks per dimension from .zarray metadata
    zarray = json.loads(self.zmetadata[field + "/.zarray"])
    shape = zarray["shape"]
    chunks = zarray["chunks"]
    nchunks = [
        math.ceil(s / c) for s, c in zip(shape, chunks)
    ]

    # Flatten multi-dimensional chunk index to a single integer
    flat_index = np.ravel_multi_index(chunk_indices, nchunks)

    # Which .parq file and which row within it
    record = flat_index // self.record_size
    row = flat_index % self.record_size

    return field, record, row
```

#### `_load_one_key()` — reading a single chunk reference

```python
def _load_one_key(self, key):
    field, record, row = self._key_to_record(key)
    refs = self.open_refs(field, record)  # cached .parq read

    # Extract reference: either raw bytes or [path, offset, size]
    if "raw" in refs and refs["raw"][row] is not None:
        return refs["raw"][row]
    else:
        path = refs["path"][row]
        offset = refs["offset"][row]
        size = refs["size"][row]
        return [path, offset, size]
```

#### `listdir()` — discovering variables

Variables are **not** discovered by listing the filesystem. They come from
`.zmetadata`:

```python
def listdir(self):
    dirs = (
        p.rsplit("/", 1)[0]
        for p in self.zmetadata
        if not p.startswith(".z")
    )
    return set(dirs)
```

## Worked Example

Using the dataset at `/tmp/vzarr/ghrsst_mur_june2002.parquet`:

```python
import xarray as xr
import fsspec
import json

path = "/tmp/vzarr/ghrsst_mur_june2002.parquet"

# Step 1: Create the ReferenceFileSystem
fs = fsspec.filesystem("reference", fo=path)

# The .references attribute is a LazyReferenceMapper
mapper = fs.references

# Step 2: Inspect .zmetadata
zmetadata = json.loads(mapper._items[".zmetadata"])
print(f"record_size: {zmetadata['record_size']}")
print(f"variables: {mapper.listdir()}")

# Step 3: Look up a specific chunk
key = "analysed_sst/0.5.3"
field, record, row = mapper._key_to_record(key)
print(f"key={key} → field={field}, record={record}, row={row}")
print(f"parquet file: {field}/refs.{record}.parq")

# Step 4: Read the reference
ref = mapper._load_one_key(key)
print(f"reference: path={ref[0]}, offset={ref[1]}, size={ref[2]}")

# Step 5: Open with xarray (the normal user path)
ds = xr.open_dataset(path, engine="kerchunk")
print(ds)
```

## Key Design Decisions

1. **No filesystem listing**: Partition files are never globbed or listed. The
   path `{root}/{field}/refs.{record}.parq` is constructed from the chunk index
   and `record_size`.

2. **Lazy loading**: `.parq` files are only read when a chunk is actually
   accessed. An LRU cache (default size 128) keeps recently-read partitions in
   memory.

3. **Flat indexing**: Multi-dimensional chunk coordinates are flattened with
   `np.ravel_multi_index` to compute the partition number. This means chunk
   `(0, 5, 3)` in a variable with `nchunks = (8660, 36, 71)` maps to flat index
   `5 * 71 + 3 = 358`, record `358 // 100000 = 0`, row `358 % 100000 = 358`.

4. **record_size**: Typically 100,000. Each `.parq` file contains up to this
   many chunk references. For `analysed_sst` with ~22 million chunks, this means
   ~222 `.parq` files.

## Where the Code Lives

| Component | Location |
|-----------|----------|
| xarray backend entry point | `kerchunk/xarray_backend.py` |
| `refs_as_fs()` / `refs_as_store()` | `kerchunk/utils.py` |
| Parquet detection | `fsspec/implementations/reference.py` `ReferenceFileSystem.__init__` |
| Partition mapping & reading | `fsspec/implementations/reference.py` `LazyReferenceMapper` |
| `.parq` file reading | `LazyReferenceMapper.open_refs()` → `pd.read_parquet()` |
| Chunk key → record mapping | `LazyReferenceMapper._key_to_record()` |

The critical insight: **kerchunk provides the xarray engine shim; fsspec does
all the parquet partition work**.
