# pizzarr install — getting the full cloud feature set

## TL;DR

The CRAN binary is pure R (filesystem + HTTP only). The r-universe binary
adds the zarrs Rust backend but, as of May 2026, ships **without** the
`s3`, `gcs`, or `blosc` features compiled in — despite the vignette
implying otherwise. To get the full kit you need a local source build with
`NOT_CRAN=true` and `PIZZARR_FEATURES=s3,gcs`.

## Step 1: rustup (not apt)

zarrs requires `rustc >= 1.91`. Ubuntu 24.04 ships 1.75, Debian trixie
1.78 — neither will work. Use rustup, not the distro package.

**Note for gdal-r-ci:** This belongs in `gdal-r-python` (the layer where
reticulate + zarrs/icechunk live), not `gdal-r-full`. Dockerfile snippet:

```dockerfile
ENV CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    PATH=/opt/cargo/bin:$PATH

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --default-toolchain stable --profile minimal --no-modify-path \
 && rustc --version && cargo --version

# Optional: fail the build if the toolchain ever drifts below 1.91
RUN rustc --version \
    | awk '{ split($2, a, "."); if (a[1]*100 + a[2] < 191) exit 1 }'
```

System paths (not `$HOME/.cargo`) so all users in the image see the same
toolchain. Same logic as dual-PROJ/library-identity-principle scars.

## Step 2: install pizzarr from source with features

After rustup is on PATH:

```r
Sys.setenv(NOT_CRAN = "true",
           PIZZARR_FEATURES = "s3,gcs")

install.packages(
  "pizzarr",
  repos = c("https://zarr-developers.r-universe.dev",
            "https://cloud.r-project.org"),
  type = "source"
)
```

`type = "source"` forces compilation rather than grabbing the (under-featured)
prebuilt binary. `NOT_CRAN=true` is what flips the build script to consider
extra Cargo features at all. `PIZZARR_FEATURES=s3,gcs` adds those features
on top of the default set; `blosc` and `object_store` come along with `s3`.

For BuildKit-cached rebuilds (gdal-r-ci):

```dockerfile
RUN --mount=type=cache,target=/opt/cargo/registry \
    --mount=type=cache,target=/opt/cargo/git \
    NOT_CRAN=true PIZZARR_FEATURES=s3,gcs \
    R -e 'install.packages("pizzarr", \
            repos = "https://zarr-developers.r-universe.dev", \
            type = "source")'
```

First build pulls a few hundred crates (~3-5 min); subsequent rebuilds
seconds.

## Step 3: confirm the feature set

```r
library(pizzarr)
pizzarr:::.pizzarr_env$zarrs_available    # TRUE
pizzarr_compiled_features()
```

Expected output:

```
[1] "zarrs"        "filesystem"   "http_sync"    "gzip"
[5] "blosc"        "zstd"         "object_store" "s3"
[9] "gcs"
```

If `s3`/`gcs`/`blosc` are missing, the env vars didn't propagate to the build.
Check that `NOT_CRAN` is set in the R session that runs `install.packages`,
not just in the shell, and that `type = "source"` is honoured (otherwise
r-universe serves the prebuilt minimal binary).

## What's still missing

Even with the full kit above, `pizzarr_compiled_features()` will **not**
include `icechunk`. The zarrs-icechunk integration is a separate Rust crate
(LDeakin's `zarrs_icechunk`) that pizzarr doesn't currently expose via
extendr feature flags. That's the remaining gap for reading dynamical.org's
weather datasets and most other Earthmover-hosted stores.
