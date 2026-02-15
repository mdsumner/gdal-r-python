# VirtualiZarr → Icechunk Workflow Guide

**Tested 2026-02-04** with VirtualiZarr 2.4.0, Icechunk 1.1.18, obstore 0.8.2

This guide documents a working workflow for virtualizing NetCDF files from HTTP sources (Thredds, S3) into Icechunk stores. The API surface across these packages is evolving rapidly—this captures what actually works.

## The Goal

Turn any HTTP-accessible NetCDF archive into cloud-native Zarr:

```
NCI Thredds (TB of NetCDF) → VirtualiZarr (scan structure) → Icechunk (store refs) → xarray (read as Zarr)
```

No data copying. Just byte offsets pointing back to the original files.

## Installation

```bash
pip install "virtualizarr[hdf,icechunk]" xarray obstore
```

The `virtualizarr[hdf,icechunk]` extras pull in `h5py`, `icechunk`, and `obspec-utils`.

## Key API Discoveries

### 1. ObjectStoreRegistry wants plain URL prefixes, not regex

```python
# WRONG - will fail silently or error
registry = ObjectStoreRegistry({r"https://thredds\.nci\.org\.au/.*": http_store})

# CORRECT - plain prefix string
registry = ObjectStoreRegistry({"https://thredds.nci.org.au": http_store})
```

### 2. HDFParser takes no arguments

```python
# WRONG
parser = HDFParser(store=http_store)

# CORRECT
parser = HDFParser()
```

### 3. Repository.open() needs both config AND authorize_virtual_chunk_access

This is the critical one. Writing works with just `config`, but reading fails with a cryptic error about "need to authorize virtual chunk container".

```python
# WRITE - config sets up the container definition
repo = icechunk.Repository.create(storage=storage, config=config)
session = repo.writable_session("main")
vds.virtualize.to_icechunk(session.store)
session.commit("message")

# READ - must pass BOTH config and authorization
repo = icechunk.Repository.open(
    storage=storage,
    config=config,  # Still needed!
    authorize_virtual_chunk_access={"https://thredds.nci.org.au/": None}  # None = anonymous HTTP
)
session = repo.readonly_session("main")
ds = xr.open_zarr(session.store, consolidated=False)
```

### 4. Use session.store, not repo directly

```python
# For writing
session = repo.writable_session("main")
vds.virtualize.to_icechunk(session.store)  # ← session.store

# For reading
session = repo.readonly_session("main")
ds = xr.open_zarr(session.store, consolidated=False)  # ← session.store
```

## Complete Working Example

```python
import icechunk
import xarray as xr
from obstore.store import HTTPStore
from obspec_utils.registry import ObjectStoreRegistry
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser

# 1. Virtualize from HTTP
url = "https://projects.pawsey.org.au/bucket/path/to/file.nc"
host = "https://projects.pawsey.org.au"
url_prefix = "https://projects.pawsey.org.au/"

http_store = HTTPStore.from_url(host)
registry = ObjectStoreRegistry({host: http_store})
parser = HDFParser()

vds = open_virtual_dataset(url, registry=registry, parser=parser)

# 2. Create Icechunk repo with virtual chunk container
storage = icechunk.local_filesystem_storage("/tmp/my_store.icechunk")

config = icechunk.RepositoryConfig.default()
container = icechunk.VirtualChunkContainer(
    url_prefix=url_prefix,
    store=icechunk.http_store(),
)
config.set_virtual_chunk_container(container)

repo = icechunk.Repository.create(storage=storage, config=config)

# 3. Write virtual refs
session = repo.writable_session("main")
vds.virtualize.to_icechunk(session.store)
snapshot_id = session.commit("Initial virtualization")
print(f"Committed: {snapshot_id}")

# 4. Read back (must reopen with authorization)
storage = icechunk.local_filesystem_storage("/tmp/my_store.icechunk")
repo = icechunk.Repository.open(
    storage=storage,
    config=config,
    authorize_virtual_chunk_access={url_prefix: None}
)
session = repo.readonly_session("main")
ds = xr.open_zarr(session.store, consolidated=False)

# 5. Access data - fetches bytes from original HTTP source
print(ds.var.isel(time=0).values)
```

## Icechunk on S3-Compatible Storage (Pawsey Acacia)

```python
storage = icechunk.s3_storage(
    bucket="your-bucket",
    prefix="path/to/store.icechunk",
    endpoint_url="https://projects.pawsey.org.au",
    region=None,  # None for non-AWS S3
    from_env=True,  # Reads AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    force_path_style=True,  # Required for most S3-compatible stores
)
```

## Multiple Files with open_virtual_mfdataset

```python
from virtualizarr import open_virtual_mfdataset

urls = [
    "https://thredds.nci.org.au/.../ocean_temp_2024_01.nc",
    "https://thredds.nci.org.au/.../ocean_temp_2024_02.nc",
    # ...
]

vds = open_virtual_mfdataset(
    urls,
    registry=registry,
    parser=parser,
    concat_dim="Time",
    combine="nested",
)
```

## Performance Notes

- **Virtualization is I/O bound**: Scanning HDF5 structure over HTTP is slow for large files. Running on NCI (local filesystem) is much faster than over Thredds.
- **Thredds throttling**: NCI Thredds handles ~10 concurrent requests well; more may get throttled.
- **Parallelization**: External orchestration (R + mirai, Python multiprocessing) works better than internal dask for virtualization. Serializing xarray objects across processes is problematic.

## Common Errors and Fixes

### "need to authorize virtual chunk container"

You're reading without `authorize_virtual_chunk_access`:

```python
# Fix: add authorization when opening for reads
repo = icechunk.Repository.open(
    storage=storage,
    config=config,
    authorize_virtual_chunk_access={"https://your-host.com/": None}
)
```

### "ObjectStoreRegistry" import error

It moved from virtualizarr to obspec-utils:

```python
# Old (broken)
from virtualizarr.readers.common import ObjectStoreRegistry

# New (correct)
from obspec_utils.registry import ObjectStoreRegistry
```

### Metadata reads work but data fetch fails

The virtual dataset opens fine but `.values` or `.compute()` fails. This usually means:
1. Missing `authorize_virtual_chunk_access` (most common)
2. URL prefix mismatch between write and read config
3. Source files moved or access changed

## What This Enables

- **Zero-copy cloud-native access**: Any Thredds/HTTP archive becomes Zarr-accessible
- **Versioning**: Icechunk provides Git-like snapshots, branches, tags
- **Incremental updates**: Add new files without reprocessing everything
- **Multi-source fusion**: Combine variables from different archives into one virtual store
- **Democratized access**: Store the Icechunk refs on public S3; anyone can `xr.open_zarr()`

## Links

- [VirtualiZarr docs](https://virtualizarr.readthedocs.io/)
- [Icechunk docs](https://icechunk.io/)
- [Icechunk virtual chunks guide](https://icechunk.io/en/stable/virtual/)
