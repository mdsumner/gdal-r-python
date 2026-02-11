# Accessing Zarr V3 Data on Google Cloud Storage with Python and GDAL

## Problem

Given a URL like `https://storage.googleapis.com/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr`, how do you open a cloud-hosted Zarr store from both Python (xarray) and GDAL/R? The specific dataset is CHLA-Z, a 2TB global gridded chlorophyll-a vertical profile dataset from NOAA NWFSC.

The naive approach — wrapping the HTTPS URL in a GDAL descriptor or passing it directly to xarray — fails for several interconnected reasons that this document walks through.

## Why the obvious things don't work

### GDAL with /vsicurl/

The descriptor `ZARR:"/vsicurl/https://storage.googleapis.com/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr"` looks correct per the GDAL docs but is problematic because:

1. **`/vsicurl/` cannot reliably list directories.** Zarr is a directory-based format — it needs to discover metadata files (`.zgroup`, `.zarray`, `.zmetadata`, or `zarr.json`) by listing the store root. HTTP servers don't provide reliable directory listings, so `/vsicurl/` often can't find what it needs.

2. **The `ZARR:` prefix is a partial workaround.** GDAL's docs say the `ZARR:` prefix helps when "file listing is not reliable, as often with /vsicurl/" — but this only reliably works when consolidated metadata (`.zmetadata`) is present, giving GDAL everything in a single file. This store has no `.zmetadata`.

3. **This is a Zarr V3 store, not V2.** The root contains `zarr.json`, not `.zgroup` or `.zmetadata`. GDAL's V3 support is experimental and version-dependent (requires >= 3.8, with ongoing improvements in later releases).

### Python/xarray with zarr-python 2.x

```python
xr.open_zarr("gs://nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr",
             storage_options={"token": "anon"})
```

This fails with `KeyError: '.zmetadata'` followed by `GroupNotFoundError` because zarr-python 2.x only understands Zarr V2 — it looks for `.zmetadata` and `.zgroup`, neither of which exist in a V3 store.

## Decomposing the URL into bucket and prefix

The HTTPS URL `https://storage.googleapis.com/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr` maps to:

| Component | Value |
|-----------|-------|
| **Bucket** | `nmfs_odp_nwfsc` |
| **Prefix** | `CB/fish-pace-datasets/chla-z/zarr` |
| **GCS URI** | `gs://nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr` |
| **GDAL /vsigs/ path** | `/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr` |

The pattern is: `https://storage.googleapis.com/{bucket}/{prefix}` maps to `gs://{bucket}/{prefix}` and `/vsigs/{bucket}/{prefix}`.

## Authentication for public GCS buckets

| Context | Setting |
|---------|---------|
| **GDAL /vsigs/** | `GS_NO_SIGN_REQUEST=YES` |
| **Python gcsfs** | `storage_options={"token": "anon"}` |
| **GDAL /vsis3/ (GCS S3 compat)** | `AWS_NO_SIGN_REQUEST=YES` + `AWS_S3_ENDPOINT=storage.googleapis.com` + `AWS_VIRTUAL_HOSTING=FALSE` |

For public buckets, the only requirement is telling the client not to attempt authentication. No API keys or credentials are needed.

## Diagnosing the store: what's actually in there?

The critical diagnostic step is listing the store contents directly. Using `gcsfs`:

```python
import gcsfs
fs = gcsfs.GCSFileSystem(token="anon")
fs.ls("nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr")
```

This revealed:

```
['nmfs_odp_nwfsc/CB/.../zarr/CHLA',
 'nmfs_odp_nwfsc/CB/.../zarr/CHLA_depth_center_of_mass',
 'nmfs_odp_nwfsc/CB/.../zarr/CHLA_int_0_200',
 'nmfs_odp_nwfsc/CB/.../zarr/CHLA_peak',
 'nmfs_odp_nwfsc/CB/.../zarr/CHLA_peak_depth',
 'nmfs_odp_nwfsc/CB/.../zarr/lat',
 'nmfs_odp_nwfsc/CB/.../zarr/lon',
 'nmfs_odp_nwfsc/CB/.../zarr/time',
 'nmfs_odp_nwfsc/CB/.../zarr/z',
 'nmfs_odp_nwfsc/CB/.../zarr/z_end',
 'nmfs_odp_nwfsc/CB/.../zarr/z_start',
 'nmfs_odp_nwfsc/CB/.../zarr/z_thickness',
 'nmfs_odp_nwfsc/CB/.../zarr/zarr.json']
```

The presence of `zarr.json` (not `.zgroup` or `.zmetadata`) confirms this is **Zarr V3**. The subdirectories are the arrays within the group.

### How to tell V2 from V3

| File at store root | Zarr version |
|--------------------|-------------|
| `.zgroup` | V2 |
| `.zmetadata` | V2 (consolidated) |
| `.zarray` | V2 (bare array, no group) |
| `zarr.json` | **V3** |

## Solution 1: Python with zarr-python 3.x and xarray

### Python version requirement

zarr-python 3.0.8+ requires **Python >= 3.11**. Earlier 3.0.x releases were yanked due to data corruption bugs (including `zarr.load()` deleting data). Do not use them.

### Installation

```bash
# Create a dedicated environment with Python 3.12
uv venv --python 3.12 /tmp/zarr3env
source /tmp/zarr3env/bin/activate
uv pip install zarr xarray gcsfs
```

The `gcsfs` package is required for `gs://` URI support. It pulls in `google-auth`, `google-cloud-storage`, `aiohttp`, and ~20 other dependencies.

### Opening the dataset

```python
import xarray as xr

ds = xr.open_zarr(
    "gs://nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr",
    storage_options={"token": "anon"},
    consolidated=False,
    zarr_format=3
)
```

Key arguments:

- **`consolidated=False`**: Required because there is no `.zmetadata` file. Without this, xarray tries to read consolidated metadata first and fails.
- **`zarr_format=3`**: Tells zarr-python to expect V3 layout (`zarr.json` instead of `.zgroup`).
- **`storage_options={"token": "anon"}`**: Anonymous access to the public GCS bucket via gcsfs.

### Result

```
<xarray.Dataset> Size: 2TB
Dimensions:                    (time: 560, lat: 4320, lon: 8640, z: 20)
Coordinates:
  * time                       (time) datetime64[ns] 4kB 2024-03-05 ...
  * lat                        (lat) float32 17kB 89.98 89.94 ... -89.98
  * lon                        (lon) float32 35kB -180.0 -179.9 ... 180.0
  * z                          (z) float32 80B 5.0 15.0 25.0 ... 185.0 195.0
    z_end                      (z) float32 80B ...
    z_start                    (z) float32 80B ...
Data variables:
    CHLA                       (time, z, lat, lon) float32 2TB ...
    CHLA_depth_center_of_mass  (time, lat, lon) float32 84GB ...
    CHLA_peak                  (time, lat, lon) float32 84GB ...
    CHLA_peak_depth            (time, lat, lon) float32 84GB ...
    CHLA_int_0_200             (time, lat, lon) float32 84GB ...
    z_thickness                (time, z) float32 45kB ...
```

### Quick inspection without xarray

```python
import zarr, fsspec

store = fsspec.get_mapper(
    "gs://nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr",
    token="anon"
)
z = zarr.open(store, mode="r", zarr_format=3)
print(type(z))          # <class 'zarr.core.group.Group'>
print(list(z.keys()))   # ['CHLA', 'CHLA_int_0_200', 'time', 'lon', ...]
```

### Note on aiohttp shutdown noise

When using gcsfs in short-lived scripts you may see `RuntimeError: Task ... attached to a different loop` tracebacks at interpreter exit. This is harmless — it's aiohttp/asyncio cleanup during shutdown and doesn't affect data integrity.

## Solution 2: GDAL with /vsigs/

This approach bypasses Python's zarr ecosystem entirely, accessing the data through GDAL's native Zarr driver. This is the path to using the data from R via GDAL-based packages.

### Why /vsigs/ instead of /vsicurl/

| Feature | /vsicurl/ | /vsigs/ |
|---------|-----------|---------|
| Protocol | HTTP/HTTPS | GCS JSON/XML API |
| Directory listing | Unreliable (HTML parsing) | Native `ReadDir` via API |
| Auth for public buckets | N/A (no sign needed) | `GS_NO_SIGN_REQUEST=YES` |
| Zarr compatibility | Poor (can't discover arrays) | Good (proper listing) |

The Zarr format requires directory listing to discover metadata files and array chunks. `/vsigs/` provides this natively through the GCS API, while `/vsicurl/` has to fall back to HTML directory listing which most object stores don't provide.

### GDAL version requirements

- **GDAL >= 3.8**: Basic Zarr V3 support (compatible with spec as of 2023-May-7)
- **GDAL >= 3.11**: Kerchunk reference file support
- **GDAL >= 3.12**: Improved `ZARR:` prefix handling for unreliable directory listing
- **GDAL 3.13dev**: Latest V3 improvements (used in this session)

Note: GDAL's V3 support is marked experimental and the spec alignment depends on build version. Earlier GDAL versions are not interoperable with V3 datasets produced by later versions.

### Descriptor syntax

The GDAL connection string has a specific format:

```
ZARR:"/vsigs/{bucket}/{prefix}"
```

The `ZARR:` prefix is the driver identifier. The path inside the double quotes is a GDAL virtual filesystem path. In shell contexts, the whole thing needs single-quote protection:

```bash
# Bash: single quotes protect the inner double quotes
gdalmdiminfo 'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr"'
```

### Multidimensional metadata

```bash
GS_NO_SIGN_REQUEST=YES gdalmdiminfo \
  'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr"'
```

### Accessing individual arrays

Simple arrays (those without datetime dimensions) work directly:

```bash
# lat array — works fine, returns 1D coordinate data as raster
GS_NO_SIGN_REQUEST=YES gdalinfo \
  'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr":/lat'
```

Opening the CHLA array without dimension indexing also works — GDAL flattens the extra dimensions (time, z) into bands:

```bash
# Opens successfully: 560 time steps × 20 z levels = 11,200 bands
GS_NO_SIGN_REQUEST=YES gdalinfo \
  'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr":/CHLA'
```

### The numpy.datetime64 problem

Attempting to use GDAL's dimension indexing syntax to select specific time/z slices fails:

```bash
# FAILS with: Invalid or unsupported format for data_type:
#   { "name": "numpy.datetime64", "configuration": { "unit": "ns", "scale_factor": 1 } }
GS_NO_SIGN_REQUEST=YES gdalinfo \
  'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr":/CHLA:0,0'
```

The `:0,0` index syntax triggers GDAL's dimension coordinate resolution, which requires parsing the `time` array's data type. The `time` array uses a Zarr V3 extension data type:

```json
{
  "name": "numpy.datetime64",
  "configuration": {
    "unit": "ns",
    "scale_factor": 1
  }
}
```

This is a zarr-python 3.x convention — it writes all datetime64 coordinates using this extension type by default. The underlying data is int64 nanoseconds since the Unix epoch, but GDAL does not recognise the extension type wrapper and cannot resolve dimension indices that depend on it.

This affects the group-level `gdalinfo` (which needs to resolve dimensions for subdataset listing) and the `:index` syntax, but does **not** affect `gdalmdiminfo` or direct array access without indexing.

### Workaround: VRT band selection

Since the un-indexed array opens successfully with all extra dimensions flattened to bands, the `vrt://` protocol can select specific bands without triggering dimension resolution:

```bash
# Surface chlorophyll at first time step (time=0, z=0 → band 1)
GS_NO_SIGN_REQUEST=YES gdalinfo \
  'vrt://ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr/CHLA"?bands=1'
```

The datetime error still appears as a warning but the dataset opens successfully. Band metadata confirms the z dimension is resolved:

```
Band 1 Block=480x480 Type=Float32, ColorInterp=Undefined
  NoData Value=nan
  Unit Type: mg m-3
  Metadata:
    DIM_time_INDEX=0
    DIM_z_INDEX=0
    DIM_z_UNIT=m
    DIM_z_VALUE=5
```

CHLA has dimensions (time=560, z=20, lat=4320, lon=8640). The band numbering is z-fastest, so the formula is:

```
band = time_index × 20 + z_index + 1
```

Examples:

| Desired slice | Band number |
|---------------|-------------|
| time=0, z=0 (surface, first time) | 1 |
| time=0, z=5 (55m depth, first time) | 6 |
| time=3, z=0 (surface, 4th time) | 61 |
| time=559, z=19 (deepest, last time) | 11200 |

### Full warper access

With the VRT workaround, the full power of `gdalwarp` is available against the cloud Zarr V3 store:

```bash
# Spatial subset with reprojection — Antarctic stereographic
GS_NO_SIGN_REQUEST=YES gdalwarp \
  -t_srs EPSG:3031 \
  -te -3000000 -3000000 3000000 3000000 \
  -tr 10000 10000 \
  'vrt://ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr/CHLA"?bands=1' \
  /tmp/chla_antarctic.tif

# Extract a single slice to GeoTIFF
GS_NO_SIGN_REQUEST=YES gdal_translate \
  'vrt://ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr/CHLA"?bands=1' \
  /tmp/chla_surface_t0.tif
```

### From R

With GDAL properly configured, the data is accessible from R without any Python dependency:

```r
Sys.setenv(GS_NO_SIGN_REQUEST = "YES")

dsn <- 'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr"'

## Multidimensional info (returns JSON)
library(gdalraster)
gdalraster::mdim_info(dsn)

## Parse to list for programmatic access
jsinfo <- jsonlite::fromJSON(gdalraster::mdim_info(dsn, cout = FALSE))
names(jsinfo$arrays)

## Access a single 2D slice via VRT band selection
chla_dsn <- 'vrt://ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr/CHLA"?bands=1'
ds <- new(gdalraster::GDALRaster, chla_dsn, read_only = TRUE)
ds$info()
```

## Summary of the access patterns

| Method | Zarr Version | Auth | Directory Listing | Python Version |
|--------|-------------|------|-------------------|---------------|
| `xr.open_zarr("gs://...", consolidated=False, zarr_format=3)` | V3 | `token="anon"` | gcsfs (native GCS API) | >= 3.11 |
| `xr.open_zarr("gs://...", storage_options={"token": "anon"})` | V2 only | `token="anon"` | gcsfs | any |
| `ZARR:"/vsigs/..."` via GDAL | V2 + V3 (experimental) | `GS_NO_SIGN_REQUEST=YES` | Native GCS API | N/A |
| `ZARR:"/vsicurl/https://..."` via GDAL | V2 (with `.zmetadata`) | None needed | Unreliable | N/A |

## Known issues

### GDAL: numpy.datetime64 extension data type not supported

GDAL does not recognise the Zarr V3 extension data type `numpy.datetime64` used by zarr-python 3.x for datetime coordinates. This prevents dimension-indexed access (`:0,0` syntax) for any array with a datetime dimension, and prevents group-level subdataset listing via `gdalinfo`. The workaround is VRT band selection on the un-indexed array. See the companion GDAL issue report for details.

## Lessons

1. **Always check the store root first.** A 30-second `fs.ls()` call immediately reveals whether you're dealing with V2 or V3 and whether consolidated metadata exists. This saves hours of debugging opaque error messages.

2. **Zarr V3 is still a rough edge.** The V2-to-V3 transition means version mismatches across the ecosystem: zarr-python 2.x can't read V3, zarr-python 3.x requires Python 3.11+, GDAL V3 support is experimental, and xarray needs specific flags (`consolidated=False`, `zarr_format=3`).

3. **`/vsigs/` is fundamentally better than `/vsicurl/` for GCS Zarr.** The ability to do proper directory listing is not optional for Zarr — it's how the format discovers its own structure. Use the cloud-native virtual filesystem, not the HTTP wrapper.

4. **The GCS URL anatomy matters.** Knowing that `https://storage.googleapis.com/{bucket}/{prefix}` maps to `gs://{bucket}/{prefix}` and `/vsigs/{bucket}/{prefix}` lets you move fluidly between Python and GDAL access patterns for the same data.

5. **VRT band selection bypasses dimension resolution.** When GDAL can't parse a dimension's data type but can open the raw array, `vrt://...?bands=N` provides a workaround that preserves full warper/translate capability.
