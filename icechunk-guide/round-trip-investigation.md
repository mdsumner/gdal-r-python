# Round-trip Investigation Plan
## blocklist (GDAL+R) vs VirtualiZarr — one BRAN NetCDF

The goal: understand exactly what each producer writes and where they differ,
so we can either fix blocklist output, fix VirtualiZarr ingestion, or both.

## Test subject

One BRAN2023 daily file — e.g. `ocean_temp_2010_01_01.nc` — small enough to
work with interactively.

## Producer A: blocklist / GDAL+R

```r
# existing pipeline — produces kerchunk parquet
library(blocklist)
bl <- build_blocklist("ocean_temp_2010_01_01.nc", vars = "temp")
write_parquet_store(bl, "test_blocklist.zarr")
```

## Producer B: VirtualiZarr (Python)

```python
import virtualizarr as vz

vds = vz.open_virtual_dataset(
    "ocean_temp_2010_01_01.nc",
    indexes={},
)
vds.to_kerchunk("test_vz.zarr", format="parquet")
# or directly to icechunk:
# vds.virtualize.to_icechunk(session)
```

## Comparison points

For each producer, inspect:

### 1. .zmetadata structure
```python
import json
with open("test_blocklist.zarr/.zmetadata") as f:
    bl = json.load(f)
with open("test_vz.zarr/.zmetadata") as f:
    vz = json.load(f)

# Compare temp/.zarray
print(json.dumps(bl["metadata"]["temp/.zarray"], indent=2))
print(json.dumps(vz["metadata"]["temp/.zarray"], indent=2))
```

Key fields to compare:
- `filters` — shuffle handling
- `compressor` — zlib/blosc config
- `fill_value` — null vs number vs string
- `dimension_separator` — `.` vs `/`
- `chunks` — should be identical

### 2. Parquet schema
```python
import pyarrow.parquet as pq
bl_t = pq.read_table("test_blocklist.zarr/temp/refs.0.parq")
vz_t = pq.read_table("test_vz.zarr/temp/refs.0.parq")
print(bl_t.schema)
print(vz_t.schema)
# key/no-key, length vs size, etc.
```

### 3. Chunk references — do they agree?
```python
# Compare offset/size for chunk 0.0.0.0
bl_row = bl_t.slice(0, 1).to_pydict()
vz_row = vz_t.slice(0, 1).to_pydict()
print(bl_row)
print(vz_row)
# offsets and sizes should be identical for the same source file
```

### 4. VirtualiZarr ingestion of blocklist output
```python
from virtualizarr.parsers import KerchunkParquetParser
from obstore.store import LocalStore
from obspec_utils.registry import ObjectStoreRegistry

store = LocalStore(prefix="test_blocklist.zarr")
registry = ObjectStoreRegistry({"file://test_blocklist.zarr": store})

# This currently fails with shuffle filter error
vds = vz.open_virtual_dataset(
    "test_blocklist.zarr",
    parser=KerchunkParquetParser(),
    registry=registry,
)
```

Expected failure: `TypeError: Expected codec config to be a dict`
Fix candidate: in `virtualizarr/codecs.py::zarr_codec_config_to_v3`,
handle shuffle by dropping it (absorbed into bytes codec in V3).

### 5. Icechunk round-trip
```python
# Producer B path (known working via VirtualiZarr)
vds_b = vz.open_virtual_dataset("ocean_temp_2010_01_01.nc", indexes={})
repo = ic.Repository.create(ic.in_memory_storage())
session = repo.writable_session("main")
vds_b.virtualize.to_icechunk(session)
session.commit("vz direct")

# Check xarray opens correctly
ds = xr.open_zarr(session.store, consolidated=False)
print(ds)  # does fill_value work here?
```

### 6. Inspect the icechunk zarr.json VirtualiZarr writes
```python
from zarr.core.sync import sync as zsync
from zarr.core.buffer.cpu import Buffer, NDBuffer
from zarr.core.buffer.core import BufferPrototype
proto = BufferPrototype(buffer=Buffer, nd_buffer=NDBuffer)

raw = zsync(session.store.get("temp/zarr.json", proto))
print(json.loads(raw.to_bytes()))
# Compare fill_value, codecs, dimension_names with what parq2ice.py writes
```

## Expected differences and fixes

| Difference | blocklist output | VirtualiZarr output | Fix |
|------------|-----------------|---------------------|-----|
| shuffle filter | `{"id":"shuffle","elementsize":2}` | absent or handled | VZ PR: drop shuffle in codec translator |
| fill_value encoding | raw float/null | string for floats | Already fixed in parq2ice.py |
| dimension_separator | `"."` | `"/"` | Cosmetic, both valid |
| key column | absent | possibly present | parq2ice.py handles both |

## Decision tree

```
blocklist parquet → VirtualiZarr KerchunkParquetParser → to_icechunk
         ↓ fails on shuffle
         
Option A: Fix VirtualiZarr to drop shuffle filter in V2→V3 translation
          → PR to virtualizarr/codecs.py
          → blocklist output unchanged, VZ handles it

Option B: Fix blocklist to not write shuffle filter
          → changes R output, may affect other consumers
          → not recommended

Option C: Keep parq2ice.py as the conversion path
          → bypasses VirtualiZarr entirely
          → fill_value fix already in place
          → xarray fill_value bug still present (separate issue)

Recommendation: Option A (VZ PR) + keep parq2ice.py as fallback
```

## The xarray fill_value bug (separate)

Even with VirtualiZarr's `to_icechunk`, xarray may still fail to open the
result due to zarr-python 3.2.1 decoding fill_value before xarray sees it.

Test:
```python
# Does VirtualiZarr's to_icechunk produce xarray-readable stores?
ds = xr.open_zarr(session.store, consolidated=False)
# If this fails with fill_value error, it's a zarr-python/xarray issue
# independent of the producer path
```

If it fails: file against zarr-python or xarray depending on which side
is wrong about the V3 fill_value contract.
