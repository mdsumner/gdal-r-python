# Zarr V3: support `numpy.datetime64` extension data type

## Feature description

GDAL's Zarr driver does not support reading Zarr V3 arrays that use the `numpy.datetime64` extension data type. This is a registered Zarr V3 extension: https://github.com/zarr-developers/zarr-extensions/tree/main/data-types/numpy.datetime64

Related: #13782 (string extension data types)

## Context

Support for datetime64 in the Zarr driver was noted as out of scope in the original driver PR (#3896):

> datetime64 ("M") and timedelta64 ("m") data types aren't supported (we could likely implement them as double if needed)

For Zarr V2 this was rarely a problem in practice because xarray writes time coordinates using CF-convention encoding (int64 + `units` attribute), so GDAL never encounters the raw numpy dtype. In Zarr V3, zarr-python 3.x writes time coordinates directly as the `numpy.datetime64` extension data type by default. This means every Zarr V3 store with a time coordinate produced by xarray or zarr-python 3.x will contain this extension type.

## Observed behaviour

Three effects, increasing in severity:

### 1. Dimension name and coordinate value loss (all arrays)

When a data array references a `numpy.datetime64` coordinate via `_ARRAY_DIMENSIONS`, GDAL falls back to anonymous dimension names. Band metadata shows `DIM_dim0_INDEX=0` instead of `DIM_time_INDEX=0` and time coordinate values are absent from band metadata entirely.

### 2. Hard failure for 4D+ dimension-indexed access

For arrays with 2 or more extra dimensions (e.g. time × z × lat × lon), the dimension index syntax (`:0,0`) fails:

```
ERROR 1: Wrong number of indices of extra dimensions
```

GDAL cannot resolve the `time` dimension, which appears to cause a miscount of extra dimensions. The `:0,0` indices are rejected even though there genuinely are 2 extra dimensions (time and z).

3D arrays (1 extra dimension) degrade more gracefully — `:0` works, just with anonymous dimension names.

### 3. Time coordinate array completely inaccessible

Opening the time array directly fails:

```
ERROR 1: Invalid or unsupported format for data_type: { "name": "numpy.datetime64", "configuration": { "unit": "ns", "scale_factor": 1 } }
```

## Reproducer

A self-contained bash script creates a minimal local Zarr V3 store using zarr-python 3.x and exercises all access patterns. Requires GDAL ≥ 3.8 and Python 3 with zarr ≥ 3.0.

The script is at: https://gist.github.com/mdsumner/GIST_ID_HERE

The store contains:
- `time` coordinate: 3 timestamps as `numpy.datetime64[ns]`
- `z` coordinate: 2 depths as `float64`
- `lat`, `lon` coordinates: `float64`
- `sst`: Float32, shape (time=3, lat=4, lon=5) — 1 extra dim
- `chla`: Float32, shape (time=3, z=2, lat=4, lon=5) — 2 extra dims

### Results summary

| Test | 3D sst | 4D chla |
|------|--------|---------|
| Bare array (extra dims → bands) | ✓ degraded dims | ✓ degraded dims |
| Dimension index `:0` / `:0,0` | ✓ degraded dims | **FAIL** |
| `gdal_translate` slice to GeoTIFF | ✓ | **FAIL** |
| VRT band selection | ✓ | ✓ |

Other results:
- `gdalinfo` on group: succeeds with errors on stderr, subdatasets listed
- `gdalmdiminfo` on group: succeeds with errors on stderr, dimensions anonymous
- `gdalinfo` on time array: **FAIL**
- Coordinate arrays (lat, lon, z): all succeed

The 4D failure matches what is observed on a real-world cloud dataset (NOAA/NWFSC CHLA-Z, public Zarr V3 on GCS):

```bash
export GS_NO_SIGN_REQUEST=YES
# FAILS: "Wrong number of indices of extra dimensions"
gdalinfo 'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr":/CHLA:0,0'
```

## The extension data type

The `time` array's `zarr.json` contains:

```json
{
  "data_type": {
    "name": "numpy.datetime64",
    "configuration": {
      "unit": "ns",
      "scale_factor": 1
    }
  }
}
```

The underlying storage is int64. The mapping is deterministic:

| `unit` | Storage | Interpretation |
|--------|---------|----------------|
| `ns`   | int64   | nanoseconds since 1970-01-01T00:00:00Z |
| `us`   | int64   | microseconds since epoch |
| `ms`   | int64   | milliseconds since epoch |
| `s`    | int64   | seconds since epoch |
| `D`    | int64   | days since epoch |

The `scale_factor` multiplies the unit (e.g., `scale_factor: 10` with `unit: "s"` means 10-second intervals). In practice, zarr-python writes `unit: "ns"`, `scale_factor: 1` by default.

Extension spec: https://github.com/zarr-developers/zarr-extensions/tree/main/data-types/numpy.datetime64
zarr-python implementation: https://github.com/zarr-developers/zarr-python/blob/main/src/zarr/core/dtype/npy/time.py
Also supported by zarrs (Rust): https://docs.rs/zarrs/latest/zarrs/

## Suggested resolution

Map `numpy.datetime64` to GDT_Int64, preserving the unit and epoch as metadata (similar to CF-convention `units` attributes like `"nanoseconds since 1970-01-01"`). Use the decoded values for dimension coordinate resolution when the array serves as a dimension index.

## Versions

- GDAL 3.13.0dev (commit 0fe882e240, 2026-02-11, debug build)
- zarr-python 3.1.5
- numpy (tested with current release)
- Ubuntu 24


