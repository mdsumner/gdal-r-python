# Public Zarr Examples Catalog

A curated corpus of publicly accessible Zarr endpoints for testing and illustrating
software. Covers a broad range of providers, Zarr versions (V2, V3), access protocols
(HTTP, GCS, S3, Azure Blob), and virtual/reference stores (kerchunk JSON).

**Validate with**: `python zarr_catalog.py` or `python zarr_catalog.py --ids pangeo-gpcp mur-sst-aws`

**Last validated**: February 2026 — 17 OK / 0 errors / 1 retired (of 18 entries)

---

## Dependencies

Core: `xarray zarr fsspec gcsfs s3fs aiohttp cftime h5netcdf`

Optional (for specific entries):

| Package | Entries |
|---|---|
| `imagecodecs` | s1-coherence-kerchunk (imagecodecs_tiff codec) |
| `planetary-computer` | pc-daymet |
| `adlfs` | pc-daymet (Azure Blob filesystem) |

If using `imagecodecs`, register its numcodecs plugins before opening zarr stores:

```python
import imagecodecs.numcodecs
imagecodecs.numcodecs.register_codecs()
```

---

## Lessons Learned

These are the recurring pitfalls we hit while building and maintaining this catalog.

**URL rot is the #1 failure mode.** ITS_LIVE moved from `v02/` to `v2/`, NASA POWER
renamed its bucket entirely, Planetary Computer decommissioned the ERA5 storage
account, Daymet changed from `na/tmax.zarr` to `na.zarr`. Every entry needs periodic
re-probing.

**`consolidated=True` vs `False` matters.** Stores with `.zmetadata` (CMEMS ARCO,
Pangeo Forge, ARCO-ERA5) should use `consolidated=True` — one HTTP request for all
metadata. Stores without it (ITS_LIVE, NASA POWER, HRRR, Daymet) need
`consolidated=False`, which triggers per-variable metadata fetches.

**`zarr_format=2` is sometimes required.** When zarr-python 3 is installed, some V2
stores fail with `GroupNotFoundError` unless you explicitly pass `zarr_format=2`.

**CMIP6 noleap/360_day calendars need cftime.** Modern xarray uses
`decode_times=xr.coders.CFDatetimeCoder(use_cftime=True)`. Older xarray uses
`use_cftime=True` as a direct kwarg.

**Planetary Computer SAS tokens must propagate to sub-requests.** Signing the base
HTTPS URL doesn't work — the token doesn't carry to `.zmetadata` / `.zarray` / chunk
fetches. Extract the SAS token and pass it to `adlfs.AzureBlobFileSystem(sas_token=...)`.

**Kerchunk codecs aren't always installed.** The Sentinel-1 coherence store needs
`imagecodecs_tiff`, which requires both `imagecodecs` and explicit numcodecs
registration at import time.

---

## Discovering New Sources

These tools and catalogs are good places to find public Zarr stores to add:

- **Pangeo Forge**: <https://pangeo-forge.org/catalog> — community-built ARCO datasets
- **Google Cloud Public Datasets**: `gsutil ls gs://cmip6/`, `gs://gcp-public-data-arco-era5/`
- **AWS Open Data Registry**: <https://registry.opendata.aws> — search for "zarr"
- **Microsoft Planetary Computer**: <https://planetarycomputer.microsoft.com/catalog> — STAC API + Azure Blob
- **CMEMS STAC catalog**: <https://stac.marine.copernicus.eu/metadata/catalog.stac.json> — authoritative CMEMS URL discovery
- **`copernicusmarine describe`**: official Python toolbox for Copernicus Marine product/dataset/version listing
- **hypertidy/cmemsarco**: <https://github.com/hypertidy/cmemsarco> — R package with STAC-walking catalog builder for CMEMS ARCO stores
- **bowerbird/blueant**: <https://github.com/AustralianAntarcticDivision/blueant> — R catalog of Antarctic/Southern Ocean data sources (download-based, potential migration targets)

---

## Real Zarr V2 — HTTP/HTTPS

### 1. GPCP Daily Precipitation (Pangeo Forge)

- **ID**: `pangeo-gpcp`
- **Provider**: Pangeo / OSN
- **URL**: `https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/gpcp-feedstock/gpcp.zarr`
- **Description**: Global Precipitation Climatology Project daily, 1° grid, 1996–2021
- **Variable**: `precip` — shape (9226, 180, 360)
- **Dims**: latitude=180, longitude=360, time=9226

```python
import xarray as xr
ds = xr.open_zarr(
    "https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/gpcp-feedstock/gpcp.zarr",
    consolidated=True, chunks={}
)
```

```bash
gdalmdiminfo 'ZARR:"/vsicurl/https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/gpcp-feedstock/gpcp.zarr"'
```

### 2. Copernicus Marine Sea Level (ARCO timeChunked)

- **ID**: `copernicus-marine-sla`
- **Provider**: Copernicus Marine / Mercator Ocean
- **URL**: `https://s3.waw3-1.cloudferro.com/mdl-arco-time-045/arco/SEALEVEL_GLO_PHY_L4_MY_008_047/cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1D_202411/timeChunked.zarr`
- **Description**: Sea level anomaly, 0.125° global, daily. ARCO timeChunked for spatial access.
- **Variable**: `sla` — shape (11902, 1440, 2880)
- **Dims**: time=11902, latitude=1440, longitude=2880

No auth needed for ARCO stores (`mdl-arco-time-*`, `mdl-arco-geo-*`). Native stores
(`mdl-native-*`) need Copernicus Marine credentials. CMEMS provides two chunking
strategies: **timeChunked** (1×720×512, spatial slices) and **geoChunked** (138×32×64,
time series at a point). Wrong choice = order-of-magnitude more HTTP requests.

Use the STAC catalog at `stac.marine.copernicus.eu` for URL discovery, or see the
[hypertidy/cmemsarco](https://github.com/hypertidy/cmemsarco) R package for
GDAL-based access patterns. The blueant catalog at AAD uses the same CMEMS product IDs
(`SEALEVEL_GLO_PHY_L4_MY_008_047`, `SEALEVEL_GLO_PHY_L4_NRT_008_046`) but downloads
NetCDFs via the subsetting API — this is the cloud-native equivalent.

```python
import xarray as xr
ds = xr.open_zarr(
    "https://s3.waw3-1.cloudferro.com/mdl-arco-time-045/arco/"
    "SEALEVEL_GLO_PHY_L4_MY_008_047/"
    "cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1D_202411/"
    "timeChunked.zarr",
    consolidated=True, chunks={}
)
```

```bash
gdalmdiminfo 'ZARR:"/vsicurl/https://s3.waw3-1.cloudferro.com/mdl-arco-time-045/arco/SEALEVEL_GLO_PHY_L4_MY_008_047/cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1D_202411/timeChunked.zarr"'
```

---

## Real Zarr V2 — Google Cloud Storage

### 3. CMIP6 Sea Surface Height (GFDL-ESM4, ssp585)

- **ID**: `cmip6-gcs-zos`
- **Provider**: CMIP6 / Google Cloud
- **URL**: `gs://cmip6/CMIP6/ScenarioMIP/NOAA-GFDL/GFDL-ESM4/ssp585/r1i1p1f1/Omon/zos/gn/v20180701/`
- **Description**: Monthly sea surface height, SSP5-8.5, 2015–2100
- **Variable**: `zos` — shape (1032, 576, 720)
- **Dims**: time=1032, y=576, x=720

Uses a noleap calendar — requires cftime for time decoding.

```python
import xarray as xr
ds = xr.open_zarr(
    "gs://cmip6/CMIP6/ScenarioMIP/NOAA-GFDL/GFDL-ESM4/ssp585/r1i1p1f1/Omon/zos/gn/v20180701/",
    consolidated=True, chunks={},
    decode_times=xr.coders.CFDatetimeCoder(use_cftime=True)
)
```

### 4. CMIP6 HighResMIP Sea Level Pressure (CMCC-CM2-HR4)

- **ID**: `cmip6-highres-psl`
- **Provider**: CMIP6 / Google Cloud
- **URL**: `gs://cmip6/CMIP6/HighResMIP/CMCC/CMCC-CM2-HR4/highresSST-present/r1i1p1f1/6hrPlev/psl/gn/v20170706/`
- **Description**: 6-hourly sea level pressure from high-resolution CMCC model
- **Variable**: `psl` — shape (97820, 192, 288)
- **Dims**: lat=192, lon=288, time=97820

```python
import xarray as xr
ds = xr.open_zarr(
    "gs://cmip6/CMIP6/HighResMIP/CMCC/CMCC-CM2-HR4/highresSST-present/r1i1p1f1/6hrPlev/psl/gn/v20170706/",
    consolidated=True, chunks={},
    decode_times=xr.coders.CFDatetimeCoder(use_cftime=True)
)
```

### 5. ARCO-ERA5 Single-Level Reanalysis

- **ID**: `arco-era5-single-level`
- **Provider**: Google Research / ECMWF
- **URL**: `gs://gcp-public-data-arco-era5/ar/1959-2022-full_37-1h-0p25deg-chunk-1.zarr-v2`
- **Description**: ERA5 hourly reanalysis, 0.25° global, 1959–2022. Very large (~2 PB logical).
- **Variable**: `2m_temperature` — shape (552264, 721, 1440)
- **Dims**: time=552264, latitude=721, longitude=1440, level=37
- **Note**: Opening metadata can be slow (~8s) due to hundreds of variables.

```python
import xarray as xr
ds = xr.open_zarr(
    "gs://gcp-public-data-arco-era5/ar/1959-2022-full_37-1h-0p25deg-chunk-1.zarr-v2",
    consolidated=True, chunks={}
)
```

### 6. ARCO-ERA5 Full 37-level (dataset version 3)

- **ID**: `arco-era5-v3`
- **Provider**: Google Research / ECMWF
- **URL**: `gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3`
- **Description**: ERA5 full 37-level, hourly, 0.25° — dataset version 3 (still Zarr V2 format).
- **Variable**: `2m_temperature` — shape (1323648, 721, 1440)
- **Dims**: time=1323648, latitude=721, longitude=1440, level=37
- **Note**: Very slow to open metadata (~90s). Use `--skip-slow` for routine testing.

```python
import xarray as xr
ds = xr.open_zarr(
    "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3",
    consolidated=True, chunks={}
)
```

### 7. WeatherBench2 ERA5 (6-hourly, 1.5°)

- **ID**: `weatherbench2-era5`
- **Provider**: Google Research / WeatherBench2
- **URL**: `gs://weatherbench2/datasets/era5/1959-2023_01_10-6h-64x32_equiangular_conservative.zarr`
- **Description**: ERA5 reanalysis downsampled to 1.5° for ML weather benchmarking.
- **Variable**: `2m_temperature` — shape (93544, 64, 32)
- **Dims**: time=93544, longitude=64, latitude=32, level=13

```python
import xarray as xr
ds = xr.open_zarr(
    "gs://weatherbench2/datasets/era5/1959-2023_01_10-6h-64x32_equiangular_conservative.zarr",
    consolidated=True, chunks={}
)
```

---

## Real Zarr V2 — AWS S3

### 8. CMIP6 Surface Upward Latent Heat Flux (TaiESM1, 1pctCO2)

- **ID**: `cmip6-s3-hfls`
- **Provider**: CMIP6 / AWS S3
- **URL**: `s3://cmip6-pds/CMIP6/CMIP/AS-RCEC/TaiESM1/1pctCO2/r1i1p1f1/Amon/hfls/gn/v20200225/`
- **Description**: Monthly latent heat flux from 1pctCO2 experiment.
- **Variable**: `hfls` — shape (1800, 192, 288)
- **Dims**: time=1800, lat=192, lon=288

Uses a noleap calendar — requires cftime.

```python
import xarray as xr
import s3fs
fs = s3fs.S3FileSystem(anon=True)
mapper = fs.get_mapper("s3://cmip6-pds/CMIP6/CMIP/AS-RCEC/TaiESM1/1pctCO2/r1i1p1f1/Amon/hfls/gn/v20200225/")
ds = xr.open_zarr(
    mapper, consolidated=True, chunks={},
    decode_times=xr.coders.CFDatetimeCoder(use_cftime=True)
)
```

### 9. MUR SST L4 Global (JPL, NASA)

- **ID**: `mur-sst-aws`
- **Provider**: NASA JPL / AWS Open Data
- **URL**: `s3://mur-sst/zarr-v1`
- **Description**: MUR 0.01° daily SST, ~54 TB total, 2002–present.
- **Variable**: `analysed_sst` — shape (6443, 17999, 36000)
- **Dims**: time=6443, lat=17999, lon=36000

```python
import xarray as xr
import s3fs
fs = s3fs.S3FileSystem(anon=True)
mapper = fs.get_mapper("s3://mur-sst/zarr-v1")
ds = xr.open_zarr(mapper, consolidated=True, chunks={})
```

```bash
gdalmdiminfo 'ZARR:"/vsicurl/https://mur-sst.s3.us-west-2.amazonaws.com/zarr-v1"'
```

### 10. HRRR Weather Model (surface analysis)

- **ID**: `hrrr-zarr-aws`
- **Provider**: NOAA / AWS Open Data
- **URL**: `s3://hrrrzarr/sfc/20200801/20200801_00z_anl.zarr`
- **Description**: HRRR 3km CONUS weather model. Each variable in a subgroup.
- **Variable**: `TMP` (via `group="surface/TMP"`)
- **Dims**: projection_x_coordinate=1799, projection_y_coordinate=1059

```python
import xarray as xr
import s3fs
fs = s3fs.S3FileSystem(anon=True)
mapper = fs.get_mapper("s3://hrrrzarr/sfc/20200801/20200801_00z_anl.zarr")
ds = xr.open_zarr(mapper, consolidated=False, chunks={}, group="surface/TMP")
```

### 11. ITS_LIVE Ice Velocity Datacube

- **ID**: `its-live`
- **Provider**: NASA MEaSUREs / AWS
- **URL**: `s3://its-live-data/datacubes/v2/N00E020/ITS_LIVE_vel_EPSG32735_G0120_X750000_Y10050000.zarr`
- **Description**: ITS_LIVE glacier velocity datacube, 120m grid, multi-sensor.
- **Variable**: `v` — shape (119, 833, 833)
- **Dims**: mid_date=119, y=833, x=833

The path uses `v2` (not `v02`). Tile/EPSG/coordinates vary by region. Use
`s3://its-live-data/datacubes/catalog_v02.json` or the STAC API to discover valid
tile paths. To list available tiles: `fs.ls("s3://its-live-data/datacubes/v2/N00E020/")`.

```python
import xarray as xr
import s3fs
fs = s3fs.S3FileSystem(anon=True)
mapper = fs.get_mapper(
    "s3://its-live-data/datacubes/v2/N00E020/"
    "ITS_LIVE_vel_EPSG32735_G0120_X750000_Y10050000.zarr"
)
ds = xr.open_zarr(mapper, consolidated=False, chunks={}, zarr_format=2)
```

### 12. NASA POWER Daily Meteorology (MERRA-2)

- **ID**: `nasa-power`
- **Provider**: NASA POWER / AWS Open Data
- **URL**: `s3://nasa-power/merra2/spatial/power_merra2_daily_spatial_utc.zarr`
- **Description**: NASA POWER MERRA-2 daily meteorology, spatial chunking, global 0.5° grid.
- **Variable**: `T2M` — shape (17898, 361, 576)
- **Dims**: time=17898, lat=361, lon=576
- **Note**: Bucket moved from `power-analysis-ready-datastore` to `nasa-power` in 2024.

```python
import xarray as xr
import s3fs
fs = s3fs.S3FileSystem(anon=True)
mapper = fs.get_mapper("s3://nasa-power/merra2/spatial/power_merra2_daily_spatial_utc.zarr")
ds = xr.open_zarr(mapper, consolidated=False, chunks={}, zarr_format=2)
```

### 13. National Water Model Reanalysis v2.1

- **ID**: `nwm-zarr`
- **Provider**: NOAA / AWS Open Data
- **URL**: `s3://noaa-nwm-retro-v2-zarr-pds`
- **Description**: NWM retrospective hourly streamflow, ~5 TB. Covers CONUS.
- **Variable**: `streamflow` — shape (227904, 2729077)
- **Dims**: time=227904, feature_id=2729077

```python
import xarray as xr
import s3fs
fs = s3fs.S3FileSystem(anon=True)
mapper = fs.get_mapper("s3://noaa-nwm-retro-v2-zarr-pds")
ds = xr.open_zarr(mapper, consolidated=True, chunks={})
```

---

## Virtual Stores — Kerchunk JSON References

These are not Zarr stores on disk. A JSON file describes chunk locations
pointing back to the original files (typically NetCDF on S3). Opened via
`xr.open_dataset("reference://", engine="zarr", ...)`.

### 14. NOAA OISST CDR (Kerchunk JSON)

- **ID**: `oisst-kerchunk`
- **Provider**: Pangeo Forge / NOAA
- **URL**: `https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/pangeo-forge/aws-noaa-oisst-feedstock/aws-noaa-oisst-avhrr-only.zarr/reference.json`
- **Description**: NOAA 0.25° daily OISST AVHRR-only CDR, via kerchunk JSON reference.
- **Variable**: `sst` — shape (15044, 1, 720, 1440)
- **Dims**: time=15044, zlev=1, lat=720, lon=1440
- **Note**: Underlying data is NetCDF on S3. `remote_options={"anon": True}` required.

```python
import xarray as xr
ds = xr.open_dataset(
    "reference://", engine="zarr",
    backend_kwargs=dict(
        consolidated=False,
        storage_options=dict(
            fo="https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/pangeo-forge/"
               "aws-noaa-oisst-feedstock/aws-noaa-oisst-avhrr-only.zarr/reference.json",
            remote_protocol="s3",
            remote_options={"anon": True},
        )
    ),
    chunks={}
)
```

### 15. Sentinel-1 Global Coherence (Kerchunk JSON)

- **ID**: `s1-coherence-kerchunk`
- **Provider**: Earth Big Data / ASF
- **URL**: `https://sentinel-1-global-coherence-earthbigdata.s3.us-west-2.amazonaws.com/data/wrappers/zarr-all.json`
- **Description**: Global Sentinel-1 coherence dataset, multi-season. Virtual Zarr via kerchunk.
- **Variable**: `coherence` — shape (6,)
- **Dims**: season=4, polarization=4, latitude=193200, longitude=432000, coherence=6, flightdirection=2, orbit=175
- **Requires**: `pip install imagecodecs` — the `imagecodecs_tiff` codec must be registered.

```python
import imagecodecs.numcodecs
imagecodecs.numcodecs.register_codecs()

import xarray as xr
ds = xr.open_dataset(
    "reference://", engine="zarr",
    backend_kwargs=dict(
        consolidated=False,
        storage_options=dict(
            fo="https://sentinel-1-global-coherence-earthbigdata.s3.us-west-2.amazonaws.com/"
               "data/wrappers/zarr-all.json",
            target_protocol="http",
            remote_protocol="http",
        )
    ),
    chunks={}
)
```

---

## Planetary Computer — Azure Blob with SAS Signing

Planetary Computer hosts Zarr stores on Azure Blob Storage. Anonymous access
isn't available — each request needs a short-lived SAS token. The
`planetary_computer` package handles token acquisition, but the token must
propagate to every sub-request (metadata, arrays, chunks). The reliable
approach is to extract the SAS token and pass it to `adlfs` directly.

### 16. Daymet V4 Daily (North America)

- **ID**: `pc-daymet`
- **Provider**: Microsoft Planetary Computer
- **URL**: `https://daymeteuwest.blob.core.windows.net/daymet-zarr/daily/na.zarr`
- **Description**: Daymet V4 daily maximum temperature, North America, on Azure Blob.
- **Variable**: `tmax` — shape (14965, 8075, 7814)
- **Dims**: time=14965, y=8075, x=7814
- **Requires**: `pip install planetary-computer adlfs`

```python
import xarray as xr
import planetary_computer
import adlfs
from urllib.parse import urlparse

url = "https://daymeteuwest.blob.core.windows.net/daymet-zarr/daily/na.zarr"

# Sign one URL to extract the SAS token
signed_url = planetary_computer.sign(url)
parsed = urlparse(signed_url)
sas_token = parsed.query

# Build authenticated Azure filesystem
account_name = urlparse(url).hostname.split(".")[0]
fs = adlfs.AzureBlobFileSystem(account_name=account_name, sas_token=sas_token)
mapper = fs.get_mapper("daymet-zarr/daily/na.zarr")

ds = xr.open_zarr(mapper, consolidated=False, chunks={}, zarr_format=2)
```

Also available: `daily/hi.zarr` (Hawaii), `daily/pr.zarr` (Puerto Rico), plus
`monthly/` and `annual/` aggregations.

### 17. ERA5 (Planetary Computer) — RETIRED

- **ID**: `pc-era5`
- **Provider**: Microsoft Planetary Computer / ECMWF
- **URL**: `https://era5euwest.blob.core.windows.net/era5-pds/era5/2020/01/air_temperature_at_2_metres.zarr`
- **Status**: **RETIRED** — the `era5euwest` storage account no longer resolves.
- **Alternative**: Use ARCO-ERA5 on Google Cloud (entries 5–6 above).

---

## NetCDF on S3 — fsspec + h5netcdf

Not a Zarr store, but included as a candidate for kerchunk/VirtualiZarr
virtualisation. Demonstrates opening remote NetCDF directly via fsspec.

### 18. NEX-GDDP-CMIP6 (bias-corrected daily)

- **ID**: `nex-gddp-cmip6`
- **Provider**: NASA / AWS Open Data
- **URL**: `s3://nex-gddp-cmip6/NEX-GDDP-CMIP6/ACCESS-CM2/historical/r1i1p1f1/tasmax/tasmax_day_ACCESS-CM2_historical_r1i1p1f1_gn_1950.nc`
- **Description**: Bias-corrected daily max temperature. Source NetCDF for kerchunk/VirtualiZarr demonstration.
- **Variable**: `tasmax` — shape (365, 600, 1440)
- **Dims**: time=365, lat=600, lon=1440

```python
import xarray as xr
import s3fs
fs = s3fs.S3FileSystem(anon=True)
f = fs.open(
    "s3://nex-gddp-cmip6/NEX-GDDP-CMIP6/ACCESS-CM2/historical/"
    "r1i1p1f1/tasmax/tasmax_day_ACCESS-CM2_historical_r1i1p1f1_gn_1950.nc"
)
ds = xr.open_dataset(f, engine="h5netcdf", chunks={})
```

---

## Probe Summary

Last full run (February 2026):

| # | ID | Provider | Protocol | Time | Status |
|---|---|---|---|---|---|
| 1 | pangeo-gpcp | Pangeo / OSN | https | 3.2s | OK |
| 2 | copernicus-marine-sla | Copernicus Marine | https | 4.4s | OK |
| 3 | cmip6-gcs-zos | CMIP6 / Google Cloud | gs | 15.6s | OK |
| 4 | cmip6-highres-psl | CMIP6 / Google Cloud | gs | 0.8s | OK |
| 5 | arco-era5-single-level | Google Research / ECMWF | gs | 3.7s | OK |
| 6 | arco-era5-v3 | Google Research / ECMWF | gs | 75.9s | OK |
| 7 | weatherbench2-era5 | Google Research | gs | 1.5s | OK |
| 8 | cmip6-s3-hfls | CMIP6 / AWS S3 | s3 | 2.1s | OK |
| 9 | mur-sst-aws | NASA JPL / AWS | s3 | 27.6s | OK |
| 10 | hrrr-zarr-aws | NOAA / AWS | s3 | 10.6s | OK |
| 11 | its-live | NASA MEaSUREs / AWS | s3 | 41.5s | OK |
| 12 | nasa-power | NASA POWER / AWS | s3 | 48.4s | OK |
| 13 | nwm-zarr | NOAA / AWS | s3 | 9.0s | OK |
| 14 | oisst-kerchunk | Pangeo Forge / NOAA | reference-json | 4.5s | OK |
| 15 | s1-coherence-kerchunk | Earth Big Data / ASF | reference-json | 13.4s | OK |
| 16 | pc-daymet | Microsoft Planetary Computer | pc-azure | 59.0s | OK |
| 17 | pc-era5 | Microsoft Planetary Computer | — | — | RETIRED |
| 18 | nex-gddp-cmip6 | NASA / AWS | s3-netcdf | 84.3s | OK |

---

## Related Projects

- **[hypertidy/cmemsarco](https://github.com/hypertidy/cmemsarco)** — R package for CMEMS ARCO Zarr stores via GDAL. Walks the CMEMS STAC catalog to build a product/dataset/URL table. Same "thin wrapper, correct URLs" philosophy as this catalog.
- **[bowerbird](https://github.com/ropensci/bowerbird) / [blueant](https://github.com/AustralianAntarcticDivision/blueant)** — R packages for automated environmental data downloads. blueant's `bb_source()` pattern is structurally identical to `ZarrEntry` (name, id, source_url, method, description). Same catalog curation problem, but `bb_sync` downloads files while `xr.open_zarr` reads in place. Potential migration/bridge project.
- **[CopernicusMarine](https://github.com/pepijn-devries/CopernicusMarine)** — R interface for CMEMS (WMTS, native files, subsetting API)
- **[copernicusmarine](https://pypi.org/project/copernicusmarine/)** — Official Python toolbox for Copernicus Marine
- **[ZarrDatasets.jl](https://github.com/JuliaGeo/ZarrDatasets.jl)** — Julia equivalent with clear STAC examples
- **[vapour](https://github.com/hypertidy/vapour)** — Lightweight GDAL bindings for R (used by cmemsarco)
