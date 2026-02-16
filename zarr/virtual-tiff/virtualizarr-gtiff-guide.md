# Virtualizing GHRSST MUR COGs with VirtualiZarr 2

## Current state of play (Feb 2026)

VirtualiZarr has undergone a major API rewrite since v1. The current release is **v2.2.1** (the project now styles itself "VirtualiZarr2"). The key changes from the v1 API you may have seen before:

- **No more `filetype=` argument** — replaced by a pluggable `parser=` system
- **No more fsspec** — replaced by `obstore` (Rust-backed, async, much faster) and `ObjectStoreRegistry`
- **GeoTIFF/COG support** is now handled by a **separate package**: [`virtual-tiff`](https://github.com/virtual-zarr/virtual-tiff) (v0.2.1), which provides the `VirtualTIFF` parser
- **Icechunk** (v1.0+ stable as of July 2025) is the recommended persistence backend, replacing kerchunk JSON/parquet refs
- The old kerchunk-based TIFF reader was broken (see [#291](https://github.com/zarr-developers/VirtualiZarr/issues/291)); `virtual-tiff` is the proper replacement using `async-tiff` under the hood

Note: you're listed as a contributor on virtual-tiff, so you may already have some context on its internals.

## The GHRSST MUR dataset on Source Cooperative

The URL pattern for the `ausantarctic/ghrsst-mur-v2` COGs is completely systematic:

```
s3://us-west-2.opendata.source.coop/ausantarctic/ghrsst-mur-v2/{YYYY}/{MM}/{DD}/{YYYYMMDD}090000-JPL-L4_GHRSST-SSTfnd-MUR-GLOB-v02.0-fv04.1_analysed_sst.tif
```

or via HTTPS:

```
https://data.source.coop/ausantarctic/ghrsst-mur-v2/{YYYY}/{MM}/{DD}/{YYYYMMDD}090000-JPL-L4_GHRSST-SSTfnd-MUR-GLOB-v02.0-fv04.1_analysed_sst.tif
```

Coverage runs from 2002-06-01 (daily) onwards. Each file is ~321 MB, single-band `analysed_sst`.

## Environment setup

### Option A: pip (minimal)

```bash
python -m venv vz-env
source vz-env/bin/activate

# Core virtualizarr with tiff parser support and icechunk writer
pip install "virtualizarr[tiff, icechunk]"
```

This pulls in: `virtualizarr` (v2.2.1), `virtual-tiff` (v0.2.1), `obstore`, `zarr` (v3+), `xarray`, `icechunk` (>=1.1.2), `numcodecs`, `numpy`.

### Option B: pip (with kerchunk output too)

```bash
pip install "virtualizarr[tiff, icechunk, kerchunk]"
```

### Option C: pixi / conda

```bash
pixi init vz-ghrsst
cd vz-ghrsst
pixi add virtualizarr virtual-tiff icechunk obstore xarray
```

`pixi` is what the virtual-tiff project itself uses for development, and it'll handle the solver better than conda for these fast-moving packages. The VirtualiZarr ESIP 2025 examples repo also uses pixi.

### Verify the install

```python
import virtualizarr
print(virtualizarr.__version__)  # should be >= 2.2.1

from virtual_tiff import VirtualTIFF
from virtualizarr import open_virtual_dataset, open_virtual_mfdataset
from virtualizarr.registry import ObjectStoreRegistry
import obstore
import icechunk
```

## Virtualizing a single GHRSST COG

The data is on S3 (us-west-2, public, anonymous access). Use the S3 URL form for best performance with range requests:

```python
import obstore
from obstore.store import from_url
from virtualizarr import open_virtual_dataset
from virtualizarr.registry import ObjectStoreRegistry
from virtual_tiff import VirtualTIFF

# S3 bucket base (Source Cooperative open data)
bucket_url = "s3://us-west-2.opendata.source.coop/"

# Create an anonymous S3 store
store = from_url(bucket_url, region="us-west-2", skip_signature=True)
registry = ObjectStoreRegistry({bucket_url: store})

# One file
file_url = (
    f"{bucket_url}ausantarctic/ghrsst-mur-v2/2002/06/01/"
    "20020601090000-JPL-L4_GHRSST-SSTfnd-MUR-GLOB-v02.0-fv04.1_analysed_sst.tif"
)

# ifd=0 means the full-resolution image (first IFD in the TIFF)
parser = VirtualTIFF(ifd=0)

vds = open_virtual_dataset(
    url=file_url,
    parser=parser,
    registry=registry,
)

print(vds)
```

This should give you an xarray.Dataset with ManifestArray variables pointing at byte ranges in the original COG — no data copied.

### Direct load test (to verify it actually works)

```python
import xarray as xr

# Use the parser as a callable to get a ManifestStore directly
manifest_store = parser(url=file_url, registry=registry)
ds = xr.open_zarr(manifest_store, zarr_format=3, consolidated=False)

# Load a small subset to verify
subset = ds.isel(y=slice(0, 100), x=slice(0, 100))
subset.load()
print(subset)
```

## Virtualizing the full daily catalogue

Since the URL pattern is entirely deterministic, you can generate all URLs programmatically:

```python
import pandas as pd

def ghrsst_urls(start="2002-06-01", end="2025-12-31"):
    """Generate all GHRSST MUR COG URLs for the given date range."""
    dates = pd.date_range(start, end, freq="D")
    base = "s3://us-west-2.opendata.source.coop/ausantarctic/ghrsst-mur-v2"
    urls = []
    for d in dates:
        fname = (
            f"{d.strftime('%Y%m%d')}090000-JPL-L4_GHRSST-SSTfnd-MUR-GLOB"
            f"-v02.0-fv04.1_analysed_sst.tif"
        )
        url = f"{base}/{d.year:04d}/{d.month:02d}/{d.day:02d}/{fname}"
        urls.append(url)
    return urls

urls = ghrsst_urls()
print(f"Total files: {len(urls)}")  # ~8600+
```

### Using open_virtual_mfdataset

VirtualiZarr v2 provides `open_virtual_mfdataset` which handles combining multiple virtual datasets (analogous to `xr.open_mfdataset`):

```python
from virtualizarr import open_virtual_mfdataset

# Start small — try a month first
test_urls = ghrsst_urls("2002-06-01", "2002-06-30")

parser = VirtualTIFF(ifd=0)

vds = open_virtual_mfdataset(
    test_urls,
    parser=parser,
    registry=registry,
)

print(vds)
```

**Caveat**: `open_virtual_mfdataset` will open each file sequentially to read its metadata. For 8000+ files you'll want to think about parallelism — either batching with Dask/Coiled, or writing a loop that processes files individually and concatenates with `xr.concat`. The virtual-tiff demos include a Coiled serverless example for this pattern.

### Batch approach (for scale)

```python
from virtualizarr import open_virtual_dataset
import xarray as xr

parser = VirtualTIFF(ifd=0)
virtual_datasets = []

for url in test_urls:
    vds = open_virtual_dataset(
        url=url,
        parser=parser,
        registry=registry,
    )
    virtual_datasets.append(vds)

# Combine along a new time dimension
# (you'll need to add time coordinates — the TIFF files don't inherently
# carry CF time metadata, so you'll extract dates from filenames)
combined = xr.concat(virtual_datasets, dim="time")
```

The date extraction from filenames is straightforward given the systematic naming — parse the `YYYYMMDD` prefix of each filename into a datetime coordinate before concatenation.

## Persisting to Icechunk

Once you have a combined virtual dataset, write it to Icechunk for fast subsequent access:

```python
import icechunk

# Local filesystem store (for testing)
storage = icechunk.local_filesystem_storage("./ghrsst-icechunk")
repo = icechunk.Repository.create(storage)
session = repo.writable_session("main")

combined.vz.to_icechunk(session.store)
session.commit("Initial GHRSST MUR virtualization")

# Subsequent reads are instant:
ds = xr.open_zarr(
    icechunk.Repository.open(storage).readonly_session("main").store,
    zarr_format=3,
    consolidated=False,
)
```

For production, you'd use S3-backed Icechunk storage instead of local filesystem.

### Alternative: kerchunk JSON refs

If you want the simpler (but less capable) kerchunk reference format:

```python
combined.vz.to_kerchunk("ghrsst_mur_refs.json", format="json")

# Read back:
ds = xr.open_dataset("ghrsst_mur_refs.json", engine="kerchunk")
```

## Key considerations and gotchas

**obstore vs fsspec**: VirtualiZarr v2 has moved entirely to `obstore` for I/O. The `ObjectStoreRegistry` is the new way to map URL prefixes to store instances. The `from_url` convenience function in obstore auto-detects the store type from the URL scheme (s3://, gs://, https://, file://).

**HTTPS vs S3 access**: Both work. S3 with `skip_signature=True` is the better choice for Source Cooperative because `obstore`'s S3Store handles range requests natively and efficiently. If you need to use HTTPS, you'd construct an `HTTPStore` instead, but note that the `ObjectStoreRegistry` prefix matching needs to line up with the URL scheme used in your file URLs.

**ifd parameter on VirtualTIFF**: `ifd=0` is the full-resolution image. Higher IFD indices correspond to overview levels. The virtual-tiff library also exposes overviews via xarray DataTree if you want multi-resolution access.

**Time coordinates**: GeoTIFFs don't carry time metadata in the way netCDF does. You'll need to construct time coordinates from the filenames and assign them during concatenation. This is the same problem you'd have with any TIFF-based time series.

**virtual-tiff maturity**: The README notes that some tests still fail — it's an active development library. The core COG virtualization path works, but edge cases around compression codecs and non-standard TIFF layouts may still have issues. Worth testing against your specific MUR files early.

**Comparison with GDAL VRT approach**: For your hypertidy work, the alternative to this entire pipeline is a GDAL VRT stack — building a time-series VRT referencing the COGs and accessing via gdalraster/terra/vapour. That stays in the R/GDAL world and avoids the Python dependency entirely, though you lose the Zarr-native access pattern and Icechunk versioning. For the estinel-style pipeline where you want to serve data as Zarr over the web, VirtualiZarr+Icechunk is the more natural fit. For R-side analysis, a VRT might still be simpler.

**Relationship to starc**: Your STAC package won't be directly involved here since the URL generation is deterministic and doesn't need a STAC catalogue. But if you later publish a STAC catalogue for this dataset, the virtual Zarr references could be linked from STAC items as an alternative access method.

## Summary of packages and versions

| Package | Version | Role |
|---------|---------|------|
| `virtualizarr` | 2.2.1 | Core virtual dataset creation + xarray integration |
| `virtual-tiff` | 0.2.1 | GeoTIFF/COG parser for VirtualiZarr |
| `obstore` | 0.5.x | Rust-backed object storage I/O (replaces fsspec) |
| `icechunk` | >=1.1.2 | Transactional virtual Zarr storage backend |
| `zarr` | 3.x | Zarr v3 Python library |
| `xarray` | latest | Dataset manipulation and concatenation |

## References

- VirtualiZarr docs: https://virtualizarr.readthedocs.io/en/latest/
- VirtualiZarr v1→v2 migration guide: in the docs under "Migration"
- virtual-tiff repo: https://github.com/virtual-zarr/virtual-tiff
- virtual-tiff docs: https://virtual-tiff.readthedocs.io/en/latest/
- obstore docs: https://developmentseed.org/obstore/latest/
- Icechunk docs + COG FAQ: https://icechunk.io/en/latest/faq/
- ESIP 2025 VirtualiZarr examples: https://virtual-zarr.github.io/esip-2025/
