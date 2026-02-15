# Zarr V3 driver: unsupported extension data type `numpy.datetime64` from zarr-python 3.x

## Expected behaviour

GDAL should be able to read Zarr V3 arrays that use the `numpy.datetime64` extension data type, or at minimum gracefully handle them as Int64 when they appear as dimension coordinate arrays referenced by other arrays.

## Actual behaviour

GDAL emits `ERROR 1: Invalid or unsupported format for data_type: { "name": "numpy.datetime64", "configuration": { "unit": "ns", "scale_factor": 1 } }` and fails to open datasets that reference dimensions using this type.

## Impact

This blocks two common operations on Zarr V3 stores produced by zarr-python 3.x:

1. **Dimension-indexed access via the classic raster API.** The `:index` syntax (e.g., `:/CHLA:0,0`) triggers dimension coordinate resolution, which requires parsing the time array's data type. This fails, even though the spatial array itself is a supported type (Float32).

2. **Group-level subdataset listing.** `gdalinfo` on the group needs to resolve all dimensions to enumerate subdatasets, and fails on the same error.

Operations that do **not** require dimension resolution still work:

- `gdalmdiminfo` on the group (returns full multidimensional metadata)
- `gdalinfo` on an individual array without index syntax (flattens extra dims to bands)
- VRT band selection on the un-indexed array (`vrt://ZARR:...?bands=1`)

## Reproducer

The dataset is a publicly accessible Zarr V3 store on Google Cloud Storage:

```bash
export GS_NO_SIGN_REQUEST=YES

# Works: multidimensional metadata
gdalmdiminfo 'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr"'

# Works: array without dimension indexing (11,200 bands)
gdalinfo 'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr/CHLA"'

# Works: simple coordinate array without datetime type
gdalinfo 'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr":/lat'

# FAILS: dimension-indexed access
gdalinfo 'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr":/CHLA:0,0'
# ERROR 1: Invalid or unsupported format for data_type: { "name": "numpy.datetime64", "configuration": { "unit": "ns", "scale_factor": 1 } }
# ERROR 1: Wrong number of indices of extra dimensions

# FAILS: group-level subdataset listing
gdalinfo 'ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr"'
# Same error, then falls back to 512x512 default
```

The VRT band selection workaround opens successfully (with warnings):

```bash
gdalinfo 'vrt://ZARR:"/vsigs/nmfs_odp_nwfsc/CB/fish-pace-datasets/chla-z/zarr/CHLA"?bands=1'
# WARNING about the unsupported data_type, but opens and reports correct metadata
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

This is a Zarr V3 extension data type registered by zarr-python. The underlying storage is **int64 nanoseconds since the Unix epoch** (1970-01-01T00:00:00Z). The `scale_factor` of 1 with unit `ns` means the raw int64 values are nanosecond timestamps directly.

The extension type is defined in zarr-python 3.x: https://github.com/zarr-developers/zarr-python/blob/main/src/zarr/core/dtype/npy/time.py

## Why this matters for interoperability

zarr-python 3.x (the current mainline implementation, 3.0.8+) writes `numpy.datetime64` as the default data type for all `datetime64` and `numpy.datetime64` arrays. This means **every Zarr V3 store with a time coordinate produced by the Python scientific ecosystem will use this extension type**. This includes datasets from xarray, Pangeo workflows, and any tool that writes datetime coordinates via zarr-python 3.x.

The Zarr V3 spec allows extension data types (https://zarr-specs.readthedocs.io/en/latest/v3/core/v3.0.html#data-types), and `numpy.datetime64` is the first widely-deployed one in practice.

## Suggested resolution

When GDAL encounters the `numpy.datetime64` extension data type, it could:

1. Map it to GDT_Int64 for the raw array data.
2. Preserve the unit and epoch information as array attributes or metadata, similar to how CF-conventions `units` attributes (e.g., `"nanoseconds since 1970-01-01"`) are handled.
3. Use the decoded values for dimension coordinate resolution when the array serves as a dimension index.

The mapping from the extension type to a concrete type is deterministic:

| `unit` | Storage type | Interpretation |
|--------|-------------|----------------|
| `ns` | int64 | nanoseconds since epoch |
| `us` | int64 | microseconds since epoch |
| `ms` | int64 | milliseconds since epoch |
| `s` | int64 | seconds since epoch |
| `D` | int64 | days since epoch |

The epoch is always 1970-01-01T00:00:00Z (Unix epoch) for `numpy.datetime64`.

## Versions

- GDAL 3.13.0dev (commit 0fe882e240, 2026-02-11, debug build)
- Dataset produced by zarr-python 3.x (zarr_format: 3)
- Tested on Ubuntu 24
