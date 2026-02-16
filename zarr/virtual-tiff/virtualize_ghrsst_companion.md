# Virtualizing ZSTD-compressed GeoTIFF COGs with VirtualiZarr 2 and virtual-tiff

## Companion notes for `virtualize_ghrsst.py`

### Date: February 2026

### Tested versions

| Package | Version | Notes |
|---------|---------|-------|
| virtualizarr | 2.2.1 | PyPI latest at time of writing |
| virtual-tiff | 0.2.1 | Provides VirtualTIFF parser |
| obstore | 0.5.x | Rust-backed object store I/O |
| zarr-python | 3.x | Zarr v3 |
| kerchunk | 0.2.9 | Required only for parquet serialization and read-back |
| xarray | latest | Dataset manipulation |
| numcodecs | latest | Codec registry for v2 metadata |
| imagecodecs | latest | Pulled in by virtual-tiff |

---

## 1. Overview

The script virtualizes a time series of Cloud-Optimized GeoTIFF (COG) files
into kerchunk-format parquet references. The test dataset is the GHRSST MUR
v2 daily SST product hosted on Source Cooperative, stored as single-band
ZSTD-compressed COGs with horizontal differencing (TIFF predictor=2).

The script uses `concurrent.futures.ThreadPoolExecutor` to parallelize the
per-file metadata reads, constructs a time-dimensioned xarray Dataset of
virtual references, and serializes to kerchunk parquet format.

Performance on a 30-day test (June 2002):

- Virtualization: 4.8–5.0s (0.16s/file, 8 threads)
- Concatenation: 0.1s
- Parquet write: 1.1s
- Total: ~6.1s

Projected full-catalogue (~8600 files, 2002–present) at 8 workers: ~25 minutes.
At 16–32 workers the I/O-bound metadata reads should scale near-linearly.


## 2. The dataset

URL pattern (S3):
```
s3://us-west-2.opendata.source.coop/ausantarctic/ghrsst-mur-v2/{YYYY}/{MM}/{DD}/{YYYYMMDD}090000-JPL-L4_GHRSST-SSTfnd-MUR-GLOB-v02.0-fv04.1_analysed_sst.tif
```

Properties:

- Single band (`analysed_sst`), int16, ZSTD-compressed with horizontal differencing
- 17999 × 36000 pixels, 512 × 512 internal tiles
- ~321 MB per file
- Daily from 2002-06-01; URL pattern is fully deterministic
- Public, anonymous S3 access, us-west-2

The int16 values encode SST with scale/offset applied during file creation.
Any residual netCDF-origin metadata suggesting Kelvin units is an artefact
from the original MUR source files and does not affect the stored values.


## 3. VirtualiZarr 2 architecture (as relevant here)

VirtualiZarr 2 differs substantially from v1:

- **Parsers**: File format support is pluggable. GeoTIFF/COG support is in a
  separate package, `virtual-tiff`, which provides the `VirtualTIFF` parser
  class. This parser uses `async-tiff` (Rust) to read TIFF IFDs.

- **Object stores**: `fsspec` is replaced by `obstore` (Rust-backed).
  An `ObjectStoreRegistry` maps URL prefixes to store instances.

- **Zarr v3 native**: Internal metadata uses Zarr v3's `ArrayV3Metadata`,
  including v3-style codec pipelines.

- **Output targets**: Icechunk (Zarr v3 native, transactional) is the primary
  recommended target. Kerchunk JSON and parquet (Zarr v2 format) are also
  supported via `to_kerchunk()`.

- **`open_virtual_mfdataset`**: Combines multiple virtual datasets, but
  defaults to `combine_by_coords` which requires dimension coordinates.
  GeoTIFFs lack these, so manual `xr.concat` is needed (see Section 5).


## 4. Problems encountered and workarounds

Writing kerchunk parquet references from virtual-tiff COGs required
monkeypatching three distinct issues in VirtualiZarr's v3-to-v2 metadata
conversion. All three arise because virtual-tiff represents the TIFF
codec pipeline as Zarr v3 codec objects, and `to_kerchunk()` must convert
these to Zarr v2 metadata — a translation path that is not currently
implemented for the imagecodecs-based codecs that virtual-tiff uses.

These patches are specific to the tested versions and may need updating or
may become unnecessary as the libraries evolve.


### 4.1 Recursive imagecodecs ZSTD config nesting

**Location**: `virtualizarr.codecs.zarr_codec_config_to_v2`

**Symptom**: `KeyError: 'configuration'` when the kerchunk writer encounters
the ZSTD codec.

**Cause**: virtual-tiff represents ZSTD via an imagecodecs wrapper that
produces a triply-nested config dict:

```python
ZstdCodec(
    codec_name='imagecodecs_zstd',
    codec_config={
        'id': 'zstd',
        'name': 'imagecodecs_zstd',
        'configuration': {
            'id': 'zstd',
            'name': 'imagecodecs_zstd',
            'configuration': {
                'id': 'zstd',
                'name': 'imagecodecs_zstd',
                'configuration': {
                    'id': 'zstd',
                    'level': 9
                }
            }
        }
    }
)
```

The v2 converter expects a single level of nesting and calls
`num_codec["configuration"]`, hitting a dict that itself contains another
`name`/`configuration` pair rather than the terminal `{'id': 'zstd', 'level': 9}`.

**Workaround**: Detect `imagecodecs_*` wrappers by name prefix and recursively
unwrap `configuration` dicts until reaching the leaf that has no further
`name` key. Return the leaf directly as the v2 codec config:

```python
if isinstance(conf, dict) and conf.get("name", "").startswith("imagecodecs_"):
    while isinstance(conf, dict) and "configuration" in conf:
        inner = conf["configuration"]
        if isinstance(inner, dict) and "configuration" in inner:
            conf = inner
        else:
            return inner  # {'id': 'zstd', 'level': 9}
```

The leaf dict `{'id': 'zstd', 'level': 9}` is valid Zarr v2 / numcodecs
format and requires no further transformation.


### 4.2 Non-numcodecs codecs passed as v2 filters

**Location**: `zarr.core.metadata.v2.parse_filters` → `numcodecs.registry.get_codec`

**Symptom**: `numcodecs.errors.UnknownCodecError: codec not available: 'bytes'`
(and similarly for `HorizontalDeltaCodec`).

**Cause**: The v3 codec pipeline for a ZSTD+predictor COG contains three codecs:

1. `HorizontalDeltaCodec` — TIFF horizontal predictor (differencing filter)
2. `BytesCodec` — byte endianness handling
3. `ZstdCodec` — compression

Only the third has a meaningful v2 equivalent. The first two are TIFF/Zarr
internal concerns with no representation in the numcodecs registry. The
default `convert_v3_to_v2_metadata` passes all codec configs through
`zarr_codec_config_to_v2` and places results as either compressor or filters,
which then fail numcodecs validation.

The `BytesCodec` config appears as `{'name': 'bytes'}` (not `'BytesCodec'`),
requiring matching on both name forms.

**Workaround**: Return `None` for these codecs and filter them out before
constructing `ArrayV2Metadata`:

```python
skip_names = {"HorizontalDeltaCodec", "BytesCodec", "bytes"}
skip_ids = {"bytes"}
if isinstance(conf, dict):
    if conf.get("name") in skip_names or conf.get("id") in skip_ids:
        return None
```

**Implications**: Dropping the horizontal predictor from the v2 metadata means
that a reader using these kerchunk references must handle TIFF decompression
natively. GDAL-based readers (including R packages via GDAL) handle this
transparently. Python readers using imagecodecs also handle it correctly.
Standard zarr-python with only numcodecs will not decode correctly without
imagecodecs installed. This is inherent to virtualizing TIFFs — the TIFF
decompression pipeline is not representable in the numcodecs filter chain.


### 4.3 Zarr v3 dtype object incompatible with ArrayV2Metadata constructor

**Location**: `zarr.core.metadata.v2.ArrayV2Metadata.__init__`

**Symptom**: `AttributeError: 'Int16' object has no attribute 'to_numpy_shortname'`
and subsequently `'numpy.dtypes.Int16DType' object has no attribute 'cast_scalar'`.

**Cause**: Zarr-python v3 uses its own `zarr.core.dtype.npy.int.Int16` class
for data types. The `ArrayV2Metadata` constructor expects this type or
compatible objects that support `.cast_scalar()`. Neither numpy dtype instances
nor numpy dtype classes nor plain strings satisfy this interface.

**Workaround**: Pass `zarray.data_type` directly (the zarr `Int16` object) and
cast `fill_value` to a plain Python int:

```python
ArrayV2Metadata(
    ...,
    dtype=zarray.data_type,
    fill_value=int(zarray.fill_value),
    ...
)
```


### 4.4 Monkeypatch placement

The patches must be applied **before** VirtualiZarr's modules bind the
functions they import. This is handled by importing and patching the
`virtualizarr.codecs`, `virtualizarr.utils`, and `virtualizarr.writers.kerchunk`
module namespaces directly, before importing `open_virtual_dataset` and other
high-level functions.

The `convert_v3_to_v2_metadata` function is completely replaced (not wrapped)
because the original function's iteration over codecs does not support `None`
returns from `zarr_codec_config_to_v2`. The replacement constructs
`ArrayV2Metadata` directly, placing the surviving codec (ZSTD) as compressor
with no filters.


## 5. GeoTIFF-specific considerations

### Time dimension

GeoTIFF files carry no CF-style time metadata. The time dimension must be
constructed from the filenames and injected during combination:

```python
vds = vds.expand_dims(time=[np.datetime64(date)])
combined = xr.concat(ordered, dim="time")
```

### Spatial coordinates

virtual-tiff produces bare `y`/`x` dimensions with no coordinate values.
The actual grid coordinates must be derived from the GeoTIFF geotransform
(available via `gdalinfo` or TIFF GeoKeys). For the MUR product this is a
regular 0.01° grid.

### Variable naming

The IFD index (0) becomes the variable name `"0"` in the virtual dataset.
The script renames this to `analysed_sst`.

### `open_virtual_mfdataset` limitations

`open_virtual_mfdataset` defaults to `combine_by_coords`, which fails for
GeoTIFFs lacking dimension coordinates:

```
ValueError: Could not find any dimension coordinates to use to order
the Dataset objects for concatenation
```

The manual loop with `expand_dims` + `xr.concat` is required.


## 6. Parallelism

`concurrent.futures.ThreadPoolExecutor` is effective here because:

- The bottleneck is network I/O: each `open_virtual_dataset` call reads
  TIFF IFD headers via HTTP range requests and waits for responses
- `obstore` releases the GIL during Rust-level I/O operations
- A single `obstore` store instance is safely shared across threads
- The `VirtualTIFF` parser is stateless (the `ifd=0` parameter is read-only)

Observed scaling: serial execution processes ~1 file/second; 8 threads
achieves ~6 files/second (~0.16s/file effective). The throughput is likely
network-limited at higher thread counts; 16–32 threads may improve further
depending on bandwidth and any request throttling on Source Cooperative.


## 7. Alternative approaches

### Icechunk output

Icechunk is VirtualiZarr's recommended persistence target and avoids the
entire v3-to-v2 conversion. The codec pipeline is stored as-is in Zarr v3
format. However, Icechunk stores currently require the `icechunk` Python
library to read, creating a Python/xarray dependency for all consumers.
Kerchunk parquet references are language-agnostic: the parquet files contain
offset/length/path columns readable by any parquet library.

### GDAL VRT

A GDAL VRT stack referencing the COGs provides an alternative for
GDAL-native consumers. A time-series VRT can be constructed with
`gdalbuildvrt -separate` or programmatically. This stays entirely within the
GDAL ecosystem and is accessible from R (gdalraster, terra, vapour), Python
(rasterio, osgeo), and any GDAL binding. It does not produce a Zarr-compatible
store and lacks Icechunk's versioning and transactional properties.

### Direct kerchunk reference construction

The information required for kerchunk references is: per chunk, a (path,
offset, length) tuple; plus per variable, a `.zarray` JSON dict specifying
shape, chunks, dtype, and compressor. The TIFF IFD tile offsets and byte
counts provide the (offset, length) pairs directly.

A lighter-weight approach would:

1. Read TIFF IFD metadata (tile offsets, byte counts) using async-tiff or
   tifffile or GDAL
2. Assemble the offset/length table directly as a dataframe
3. Write `.zarray` JSON with known dtype/chunks/compressor
4. Output as kerchunk parquet or JSON

This bypasses VirtualiZarr, xarray, and the Zarr v3 codec pipeline entirely.
The tradeoff is losing VirtualiZarr's xarray-based combination logic and
Icechunk integration, but for a dataset with uniform structure (identical
grid, single variable, systematic filenames) this logic is trivial.


## 8. Upstream issues worth reporting

1. **virtual-tiff → VirtualiZarr**: The recursive imagecodecs codec config
   nesting (Section 4.1) is arguably a bug in virtual-tiff's codec
   representation. The leaf-level `{'id': 'zstd', 'level': 9}` should
   not be wrapped in multiple layers.

2. **VirtualiZarr**: `convert_v3_to_v2_metadata` does not handle codecs that
   have no numcodecs equivalent (Section 4.2). A skip/passthrough mechanism
   for non-numcodecs v3 codecs would be needed for any TIFF virtualization.

3. **VirtualiZarr**: The deprecation of `ObjectStoreRegistry` from
   `virtualizarr.registry` in favour of `obspec_utils.registry` is in
   progress.

4. **virtual-tiff**: The variable naming (IFD index as string) and absence
   of GeoTIFF geotransform/CRS metadata in the virtual dataset are
   limitations that could be addressed with a richer parser that reads
   GeoKeys and constructs spatial coordinates.

5. **VirtualiZarr**: `open_virtual_mfdataset` could support a `concat_dim`
   parameter or `combine="nested"` mode to handle files without dimension
   coordinates.
