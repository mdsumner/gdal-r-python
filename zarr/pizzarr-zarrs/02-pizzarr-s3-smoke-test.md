# pizzarr S3 smoke test

Goal: confirm the full transport-and-decode chain works against a known
public Zarr store on S3 (AWS Open Data → object_store → extendr → R), and
establish the layout/indexing conventions before doing anything serious.

## Dataset

OME-Zarr bonsai, AWS Open Data. Zarr v2, uint8, zstd-compressed,
256³ scalar volume.

```
s3://ome-zarr-scivis/v0.4/64x0/bonsai.ome.zarr
```

(Cited in the pizzarr `zarrs-backend` vignette. Has variation in the
voxel interior; the corner at index `(0,0,0)` is uniformly 40, so avoid
that region for diagnostics.)

## Smoke test

```r
library(pizzarr)

s3_url <- "s3://ome-zarr-scivis/v0.4/64x0/bonsai.ome.zarr"

# 1. Metadata read
meta <- zarrs_open_array_metadata(s3_url, "scale0/bonsai")
str(meta[c("shape", "dtype", "zarr_format")])
# List of 3
#  $ shape      : int [1:3] 256 256 256
#  $ dtype      : chr "uint8 / |u1"
#  $ zarr_format: int 2

# 2. Subset read — note semantics below
cube <- zarrs_get_subset(
  s3_url, "scale0/bonsai",
  list(c(100L, 102L), c(100L, 102L), c(100L, 102L)),
  concurrent_target = 1L
)
str(cube)
# List of 2
#  $ data : int [1:8] 38 27 35 14 36 30 34 22
#  $ shape: int [1:3] 2 2 2

# 3. Reshape to an R array — no transposition needed
a <- array(cube$data, dim = cube$shape)
a
# , , 1
#      [,1] [,2]
# [1,]   38   35
# [2,]   27   14
# , , 2
#      [,1] [,2]
# [1,]   36   34
# [2,]   30   22
```

If you got the metadata block and a non-empty `cube$data` with values
38 27 35 14 36 30 34 22, the chain works.

## Conventions (verified empirically against bonsai)

### Range semantics: `c(start, stop)`, half-open, zero-based

`c(100L, 102L)` reads indices **100 and 101** — two cells. Python `arr[100:102]`,
not R `arr[100:102]`. A single cell is `c(i, i + 1L)`.

`c(160L, 2L)` is **empty** (start ≥ stop), not "two cells starting at 160".

### `concurrent_target` is currently required

No default in the function signature, despite a global default in
`pizzarr_config()`. Always pass it explicitly until that's fixed
(filed as bug).

```r
zarrs_get_subset(..., concurrent_target = 1L)   # safe / serial
zarrs_get_subset(..., concurrent_target = zarrs_runtime_info()$codec_concurrent_target)  # use config
```

### Storage order: `array(data, dim = shape)` Just Works

pizzarr returns `$data` in column-major (Fortran) layout, so a naive
`array(result$data, dim = result$shape)` reconstructs correctly:

- First range argument → first R dimension → outermost Zarr dimension
- Last range argument  → last R dimension  → innermost (fastest-varying)

For a 4-D forecast array requested as
`list(init_time_range, lead_time_range, lat_range, lon_range)`, the
result has `dim = c(n_init, n_lead, n_lat, n_lon)`. No `aperm`, no `rev`.

### Helper

```r
zarr_array <- function(result) {
  if (length(result$shape) <= 1) return(result$data)
  array(result$data, dim = result$shape)
}
```

## Verifying anonymous access

object_store will try environment AWS credentials if present, and the
resulting permission errors look like generic failures. Before debugging
anything else, check:

```r
Sys.getenv(c("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE", "AWS_REGION"))
```

If any credential variables are set unintentionally, unset them:

```r
Sys.unsetenv(c("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE"))
```

For public buckets, `AWS_REGION` may need to be set to the bucket's region
(e.g. `us-west-2` for dynamical.org buckets).
