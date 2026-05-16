# pizzarr ↔ dynamical.org ↔ icechunk — exploration notes

## What we set out to do

Replicate two Python notebook idioms in R:

```python
plot_ds = ds.sel(init_time="2026-03-24T00",
                 latitude=52.52, longitude=13.40,
                 method="nearest")
plot_ds["temperature_2m"].plot(x="valid_time", figsize=(10, 5))
```

and

```python
ds["pressure_reduced_to_mean_sea_level"] \
    .sel(init_time="2026-02-10T00", lead_time="36h") \
    .plot(figsize=(11, 6), cmap="RdYlBu_r")
```

The dataset is dynamical.org's DWD ICON-EU forecast, accessed via their
`dynamical-catalog` Python convenience wrapper that just resolves a STAC
catalog entry and opens an icechunk repo.

## What `dynamical-catalog` actually is

About 200 lines of pure Python in three modules. Walks
`https://stac.dynamical.org/catalog.json`, parses the `icechunk` asset
(using the STAC `xarray-assets` extension plus a custom
`icechunk:virtual_chunk_containers` field), and constructs an icechunk
`Repository` from the `bucket / prefix / region` triplet. Hands you back
`session.store` for `xr.open_zarr()`.

Skip the wrapper and go through icechunk-python + STAC directly and you
lose nothing. The Python source itself uses only stdlib + icechunk + xarray.

Bucket naming pattern: `dynamical-<dataset-id>`, all `us-west-2`,
anonymous. **Path within the bucket is versioned**:

```
s3://dynamical-dwd-icon-eu/dwd-icon-eu-forecast-5-day/v0.2.0.icechunk/
```

Easy to miss — initial attempts with `prefix=""` give a misleading
"the repository doesn't exist" error.

## The Zarr we're trying to read

After resolving the icechunk session in Python:

```
DWD ICON-EU forecast, 5 day
  dimensions: (init_time=380, lead_time=93, latitude=657, longitude=1377)
  ~25 data variables, all shape (380, 93, 657, 1377), float32
  chunks: (1, 93, 219, 153)
```

The chunking tells the story: **one init_time per chunk, full lead_time
axis, 3×9 spatial tiling**. Every new 6-hourly forecast appends one slab.
Optimised for "view one forecast" reads, costly for "time series at a
point" reads (touches all 380 init_time chunks).

The append-friendly chunking shape `(20440,)` on coordinate arrays of
length 380 is the icechunk-enabled pattern: chunk capacity is sized for
a year or two of inits, and new init_times slot in without rewriting.

Other shape notes:

- `valid_time (380, 93)` is a 2-D non-dimension coordinate
  (`init_time + lead_time`), the forecast-archive idiom done right.
- `spatial_ref ()` is a zero-dim CF grid_mapping variable — modern xarray
  convention, attribute-bearing scalar.

## What we got working in R

Through pizzarr + zarrs (after fixing the build):

- ✅ S3 transport via `object_store` (`s3` feature in zarrs)
- ✅ Zarr v2 reads, metadata + subset, on a known public dataset (OME-Zarr bonsai)
- ✅ Layout/indexing convention nailed down empirically
  - ranges are `c(start, stop)` half-open, zero-based
  - `array(result$data, dim = result$shape)` is the correct reshape
  - first range arg → first R dim → outermost Zarr dim
- ❌ icechunk layout. zarrs has a separate `zarrs_icechunk` crate
  (LDeakin) but pizzarr doesn't expose it via extendr feature flags.
  This is the real remaining gap.

So we can address any plain Zarr v2/v3 store on S3, including public AWS
Open Data datasets. We can't yet read icechunk-wrapped stores — including
the entire dynamical.org catalog and most Earthmover-hosted weather data.

## Bugs filed (or to file)

1. **r-universe binary missing s3/gcs/blosc features.** The vignette
   implies these are part of the r-universe build (it's even the
   suggested upgrade path from CRAN), but the shipped binary disagrees.
   Either r-universe CI isn't setting `NOT_CRAN=true`, or the
   feature-enabled build is failing silently. Local source build with
   `NOT_CRAN=true PIZZARR_FEATURES=s3,gcs` works fine.

2. **`zarrs_get_subset` has a required positional argument with no
   default.** `concurrent_target` has no default in the function
   signature, even though `pizzarr_config()` exposes a global value
   that would be the natural fallback. The vignette example fails as
   written.

Both follow directly from the vignette. The framing is "the vignette
doesn't run end-to-end on the r-universe binary".

## R-side parity check

| Concern                           | Python                   | R (current)                              |
|-----------------------------------|--------------------------|------------------------------------------|
| STAC discovery                    | `pystac-client` or urllib| `rstac` / `httr2 + jsonlite` — trivial   |
| Zarr v2/v3 reader                 | `zarr-python` 3.x        | `pizzarr` + zarrs (extendr)              |
| Cloud transport (S3, GCS)         | obstore / s3fs           | `pizzarr` zarrs `object_store` features  |
| Icechunk session/manifest         | `icechunk` (Rust)        | **gap** — `zarrs_icechunk` exists, not exposed |
| Convenience opener (id → store)   | `dynamical-catalog`      | natural shape: `zaro` + small STAC walker|
| Labelled-array layer              | xarray                   | `stars`, in-progress `ndr`               |

Most of the stack is now genuinely close. The icechunk gap is the
substantive remaining piece, and `zarrs_icechunk` makes it a finite
project — extendr already wraps zarrs; extending the same pattern to
expose `AsyncIcechunkStore` would give R a read-only icechunk client
basically for free. Read-only handles ~all real-world consumption
use cases.

## What didn't survive contact with reality

- Two of my early sub-assumptions were wrong.
  - I read `c(0L, 4L)` as `(start, length)`. It's `(start, stop)`. The
    earlier example didn't reveal this because the test region was
    uniformly 40.
  - I assumed `dynamical-catalog` would have a hardcoded bucket lookup
    table. It's a STAC client — more idiomatic and more interesting.
- The r-universe build claim ("install from r-universe for the full
  feature set") was aspirational rather than factual at the binary level.
- The storage-order question turned out to be a non-issue: pizzarr
  already does the column-major reshape internally. Pleasant surprise.

## What to do next, in rough priority order

1. **Find a plain Zarr v3 dataset on S3** to validate the V3 codepath end
   to end. Bonsai is V2. ICON-EU is V3 (with icechunk wrapping). There
   may be V3/codec/key-format differences worth shaking out before they
   bite mid-build.

2. **Coordinate decoding + nearest-index helpers.** The
   `.sel(latitude=52.52, ..., method="nearest")` pattern is:
   - read 1-D coord arrays in full (they're tiny)
   - parse `units` attribute for time axes
   - `which.min(abs(coord - target))` for each axis
   - integer slice the data array
   Not hard, but worth a tidy helper.

3. **The icechunk layout barrier itself.** Three angles:
   - Wait for `pizzarr` to expose the icechunk feature flag. Possibly
     the right path long-term; nothing for us to do immediately.
   - Export an ICON-EU subset from Python as plain Zarr v3 to local
     filesystem or a writable S3 location, and explore in pure R.
     Decouples the layout problem from the array-handling work entirely.
   - Build an R-side icechunk session resolver from scratch. The
     indirection is just sequential JSON reads ending in a manifest
     fetch; manifests resolve virtual zarr keys to physical S3 object
     keys, which pizzarr can then read normally. Finite project, maybe
     a few hundred lines, no Rust required.

4. **For gdal-r-ci:** rustup added to `gdal-r-python` so the source build
   is reproducible inside the image. Snippet in the install doc.

5. **For ISC proposal:** the icechunk-in-R story is a clean example of
   exactly the "shared protocol, divergent implementations" coordination
   gap the proposal is about. zarrs / zarrs_icechunk on the Rust side
   are a working reference implementation; the R binding is the missing
   piece. Worth citing concretely.
