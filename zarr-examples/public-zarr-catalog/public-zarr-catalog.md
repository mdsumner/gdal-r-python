# Public Zarr Examples Catalog

A curated corpus of publicly accessible Zarr endpoints for testing and illustrating
software. Intended to cover a broad range of providers, Zarr versions (V2, V3),
access protocols (HTTP, GCS, S3, Azure Blob), and virtual/reference stores
(kerchunk JSON, kerchunk Parquet, Icechunk).

**Validate with**: `python zarr_catalog.py --skip-slow --markdown results.md`

---

## Real Zarr V2 — HTTP/HTTPS

### 1. GPCP Daily Precipitation (Pangeo Forge)

- **ID**: `pangeo-gpcp`
- **Provider**: Pangeo / OSN
- **URL**: `https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/gpcp-feedstock/gpcp.zarr`
- **Description**: Global Precipitation Climatology Project daily, 1° grid, 1996–2021
- **Variable**: `precip`

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

### 2. Daymet V4 Daily Max Temperature (Planetary Computer, Azure Blob)

- **ID**: `pc-daymet`
- **Provider**: Microsoft Planetary Computer
- **URL**: `https://daymeteuwest.blob.core.windows.net/daymet-zarr/daily/na/tmax.zarr`
- **Variable**: `tmax`
- **Notes**: Planetary Computer also hosts ERA5, TerraClimate, and CMIP6 as Zarr on Azure.

```python
ds = xr.open_zarr(
    "https://daymeteuwest.blob.core.windows.net/daymet-zarr/daily/na/tmax.zarr",
    consolidated=True, chunks={}
)
```

### 3. ERA5 Monthly on Azure Blob (Planetary Computer)

- **ID**: `pc-era5`
- **Provider**: Microsoft Planetary Computer / ECMWF
- **URL**: `https://era5euwest.blob.core.windows.net/era5-pds/era5/2020/01/air_temperature_at_2_metres.zarr`
- **Variable**: `air_temperature_at_2_metres`

```python
ds = xr.open_zarr(
    "https://era5euwest.blob.core.windows.net/era5-pds/era5/2020/01/air_temperature_at_2_metres.zarr",
    consolidated=True, chunks={}
)
```

---

## Real Zarr V2 — Google Cloud Storage (anonymous)

### 4. CMIP6 Surface Air Temperature (TaiESM1, historical)

- **ID**: `cmip6-gcs-tas`
- **Provider**: CMIP6 / Google Cloud
- **URL**: `gs://cmip6/CMIP6/CMIP/AS-RCEC/TaiESM1/historical/r1i1p1f1/Amon/tas/gn/v20200225/`
- **Variable**: `tas`

```python
ds = xr.open_zarr(
    "gs://cmip6/CMIP6/CMIP/AS-RCEC/TaiESM1/historical/r1i1p1f1/Amon/tas/gn/v20200225/",
    consolidated=True, chunks={}
)
```

```bash
# GDAL needs HTTPS form for vsicurl
gdalmdiminfo 'ZARR:"/vsicurl/https://storage.googleapis.com/cmip6/CMIP6/CMIP/AS-RCEC/TaiESM1/historical/r1i1p1f1/Amon/tas/gn/v20200225"'
```

### 5. CMIP6 Sea Surface Height (GFDL-ESM4, ssp585)

- **ID**: `cmip6-gcs-zos`
- **Provider**: CMIP6 / Google Cloud
- **URL**: `gs://cmip6/CMIP6/ScenarioMIP/NOAA-GFDL/GFDL-ESM4/ssp585/r1i1p1f1/Omon/zos/gn/v20180701/`
- **Variable**: `zos`

```python
ds = xr.open_zarr(
    "gs://cmip6/CMIP6/ScenarioMIP/NOAA-GFDL/GFDL-ESM4/ssp585/r1i1p1f1/Omon/zos/gn/v20180701/",
    consolidated=True, chunks={}
)
```

### 6. CMIP6 Daily Precipitation (CESM2, historical)

- **ID**: `cmip6-gcs-cesm2-pr`
- **Provider**: CMIP6 / Google Cloud (NCAR)
- **URL**: `gs://cmip6/CMIP6/CMIP/NCAR/CESM2/historical/r1i1p1f1/day/pr/gn/v20190308/`
- **Variable**: `pr`

```python
ds = xr.open_zarr(
    "gs://cmip6/CMIP6/CMIP/NCAR/CESM2/historical/r1i1p1f1/day/pr/gn/v20190308/",
    consolidated=True, chunks={}
)
```

### 7. CMIP6 Dissolved Nitrate (GFDL-ESM4, historical, ocean BGC)

- **ID**: `cmip6-gcs-no3`
- **Provider**: CMIP6 / Google Cloud
- **URL**: `gs://cmip6/CMIP6/CMIP/NOAA-GFDL/GFDL-ESM4/historical/r1i1p1f1/Omon/no3/gn/v20190726/`
- **Variable**: `no3`
- **Notes**: 3D ocean variable (lev + lat + lon + time).

```python
ds = xr.open_zarr(
    "gs://cmip6/CMIP6/CMIP/NOAA-GFDL/GFDL-ESM4/historical/r1i1p1f1/Omon/no3/gn/v20190726/",
    consolidated=True, chunks={}
)
```

### 8. CMIP6 HighResMIP Sea Level Pressure (CMCC-CM2-HR4, 6-hourly)

- **ID**: `cmip6-highres-psl`
- **Provider**: CMIP6 / Google Cloud (CMCC)
- **URL**: `gs://cmip6/CMIP6/HighResMIP/CMCC/CMCC-CM2-HR4/highresSST-present/r1i1p1f1/6hrPlev/psl/gn/v20170706/`
- **Variable**: `psl`

```python
ds = xr.open_zarr(
    "gs://cmip6/CMIP6/HighResMIP/CMCC/CMCC-CM2-HR4/highresSST-present/r1i1p1f1/6hrPlev/psl/gn/v20170706/",
    consolidated=True, chunks={}
)
```

```bash
gdalmdiminfo 'ZARR:"/vsicurl/https://storage.googleapis.com/cmip6/CMIP6/HighResMIP/CMCC/CMCC-CM2-HR4/highresSST-present/r1i1p1f1/6hrPlev/psl/gn/v20170706"/:psl.zarr/'
```

### 9. ARCO-ERA5 Analysis-Ready Reanalysis (Google Research)

- **ID**: `arco-era5-single-level`
- **Provider**: Google Research / ECMWF
- **URL**: `gs://gcp-public-data-arco-era5/ar/1959-2022-full_37-1h-0p25deg-chunk-1.zarr-v2`
- **Variable**: `2m_temperature`
- **Notes**: Very large (~2 PB logical). Opening metadata takes 30–90s due to hundreds of variables. The `-v3` variant (`gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3`) is a dataset versioning label, still Zarr V2 format internally.

```python
ds = xr.open_zarr(
    "gs://gcp-public-data-arco-era5/ar/1959-2022-full_37-1h-0p25deg-chunk-1.zarr-v2",
    consolidated=True, chunks={}
)
```

### 10. WeatherBench2 ERA5 (Google Research, 1.5° downsampled)

- **ID**: `weatherbench2-era5`
- **Provider**: Google Research / WeatherBench2
- **URL**: `gs://weatherbench2/datasets/era5/1959-2023_01_10-6h-64x32_equiangular_conservative.zarr`
- **Variable**: `2m_temperature`
- **Notes**: Curated for ML weather prediction benchmarking. Much faster to open than full ARCO-ERA5.

```python
ds = xr.open_zarr(
    "gs://weatherbench2/datasets/era5/1959-2023_01_10-6h-64x32_equiangular_conservative.zarr",
    chunks={}
)
```

---

## Real Zarr V2 — AWS S3 (anonymous)

### 11. CMIP6 Latent Heat Flux (TaiESM1, 1pctCO2)

- **ID**: `cmip6-s3-hfls`
- **Provider**: CMIP6 / AWS S3
- **URL**: `s3://cmip6-pds/CMIP6/CMIP/AS-RCEC/TaiESM1/1pctCO2/r1i1p1f1/Amon/hfls/gn/v20200225/`
- **Variable**: `hfls`

```python
ds = xr.open_zarr(
    "s3://cmip6-pds/CMIP6/CMIP/AS-RCEC/TaiESM1/1pctCO2/r1i1p1f1/Amon/hfls/gn/v20200225/",
    consolidated=True, chunks={}, storage_options={"anon": True}
)
```

### 12. ERA5 Monthly on AWS S3 (single variable)

- **ID**: `era5-aws-pds`
- **Provider**: ECMWF / AWS Open Data
- **URL**: `s3://era5-pds/2020/01/data/air_pressure_at_mean_sea_level.zarr`
- **Variable**: `air_pressure_at_mean_sea_level`
- **Notes**: One Zarr store per variable per month. Non-consolidated metadata.

```python
ds = xr.open_zarr(
    "s3://era5-pds/2020/01/data/air_pressure_at_mean_sea_level.zarr",
    consolidated=False, chunks={}, storage_options={"anon": True}
)
```

### 13. MUR SST L4 Global (NASA JPL)

- **ID**: `mur-sst-aws`
- **Provider**: NASA JPL / AWS Open Data
- **URL**: `s3://mur-sst/zarr`
- **Variable**: `analysed_sst`
- **Notes**: 0.01° daily SST, ~54 TB total, 2002–present.

```python
ds = xr.open_zarr(
    "s3://mur-sst/zarr",
    consolidated=True, chunks={}, storage_options={"anon": True}
)
```

### 14. HRRR Weather Model on AWS (surface analysis)

- **ID**: `hrrr-zarr-aws`
- **Provider**: NOAA / AWS Open Data
- **URL**: `s3://hrrrzarr/sfc/20200801/20200801_00z_anl.zarr/surface/GUST`
- **Variable**: `GUST`
- **Notes**: Non-standard layout — each variable is a separate Zarr group. Requires combining group + subgroup URLs with `xr.open_mfdataset`. See [HRRR Zarr docs](https://mesowest.utah.edu/html/hrrr/zarr_documentation/html/zarr_HowToDownload.html).

```python
import s3fs, xarray as xr
group_url = "s3://hrrrzarr/sfc/20200801/20200801_00z_anl.zarr/surface/GUST"
subgroup_url = f"{group_url}/surface"
fs = s3fs.S3FileSystem(anon=True)
ds = xr.open_mfdataset(
    [s3fs.S3Map(url, s3=fs) for url in [group_url, subgroup_url]],
    engine="zarr"
)
```

### 15. ITS_LIVE Ice Velocity Datacubes

- **ID**: `its-live`
- **Provider**: NASA MEaSUREs / AWS
- **URL**: `s3://its-live-data/datacubes/v02/N60W040/ITS_LIVE_vel_EPSG32622_G0120_X-350000_Y6750000.zarr`
- **Variable**: `v`
- **Notes**: 120m grid, multi-sensor. Datacubes organized by EPSG tile. Index at [its-live.jpl.nasa.gov](https://its-live.jpl.nasa.gov).

```python
ds = xr.open_zarr(
    "s3://its-live-data/datacubes/v02/N60W040/ITS_LIVE_vel_EPSG32622_G0120_X-350000_Y6750000.zarr",
    consolidated=True, chunks={}, storage_options={"anon": True}
)
```

### 16. NASA POWER Meteorology

- **ID**: `nasa-power`
- **Provider**: NASA POWER / AWS Open Data
- **URL**: `s3://power-analysis-ready-datastore/power_901_annual_meteorology_utc.zarr`
- **Variable**: `T2M`

```python
ds = xr.open_zarr(
    "s3://power-analysis-ready-datastore/power_901_annual_meteorology_utc.zarr",
    consolidated=True, chunks={}, storage_options={"anon": True}
)
```

### 17. National Water Model Reanalysis v2.1

- **ID**: `nwm-zarr`
- **Provider**: NOAA / AWS Open Data
- **URL**: `s3://noaa-nwm-retro-v2-zarr-pds`
- **Variable**: `streamflow`
- **Notes**: ~5 TB, hourly, CONUS. May require `group=` kwarg to navigate hierarchy.

```python
ds = xr.open_zarr(
    "s3://noaa-nwm-retro-v2-zarr-pds",
    consolidated=True, chunks={}, storage_options={"anon": True}
)
```

---

## Virtual Zarr — Kerchunk JSON References

### 18. NOAA OISST CDR (Pangeo Forge kerchunk)

- **ID**: `oisst-kerchunk`
- **Provider**: Pangeo Forge / NOAA
- **Reference URL**: `https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/pangeo-forge/aws-noaa-oisst-feedstock/aws-noaa-oisst-avhrr-only.zarr/reference.json`
- **Variable**: `sst`
- **Notes**: 0.25° daily OISST AVHRR-only CDR. Underlying data is NetCDF on S3.

```python
ds = xr.open_dataset(
    "reference://",
    engine="zarr",
    backend_kwargs={
        "consolidated": False,
        "storage_options": {
            "fo": "https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/pangeo-forge/aws-noaa-oisst-feedstock/aws-noaa-oisst-avhrr-only.zarr/reference.json",
            "remote_options": {"anon": True},
            "remote_protocol": "s3",
        },
    },
    chunks={},
)
```

```bash
# GDAL >= 3.11 can read kerchunk JSON references directly:
gdalmdiminfo 'ZARR:"/vsicurl/https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/pangeo-forge/aws-noaa-oisst-feedstock/aws-noaa-oisst-avhrr-only.zarr/reference.json"'
```

### 19. Sentinel-1 Global Coherence (Earth Big Data)

- **ID**: `s1-coherence-kerchunk`
- **Provider**: Earth Big Data / ASF
- **Reference URL**: `https://sentinel-1-global-coherence-earthbigdata.s3.us-west-2.amazonaws.com/data/wrappers/zarr-all.json`
- **Variable**: `coherence`
- **Notes**: Multi-season global coherence. Virtual Zarr via kerchunk. Data served over HTTP.

```python
import fsspec, xarray as xr
mapper = fsspec.get_mapper(
    "reference://",
    fo="https://sentinel-1-global-coherence-earthbigdata.s3.us-west-2.amazonaws.com/data/wrappers/zarr-all.json",
    target_protocol="http",
    remote_protocol="http",
)
ds = xr.open_dataset(mapper, engine="zarr", backend_kwargs={"consolidated": False})
```

### 20. NOAA DBOFS Forecast (NODD Kerchunk)

- **ID**: `noaa-nodd-dbofs`
- **Provider**: NOAA / IOOS / NODD
- **URL**: `s3://noaa-nos-ofs-pds/dbofs/nos.dbofs.fields.best.nc.zarr`
- **Variable**: `temp`
- **Notes**: Delaware Bay OFS, best available forecast aggregation. Produced by RPS/Tetra Tech. References regenerated within minutes of source data upload. See [registry](https://registry.opendata.aws/noaa-nodd-kerchunk/).

```python
ds = xr.open_zarr(
    "s3://noaa-nos-ofs-pds/dbofs/nos.dbofs.fields.best.nc.zarr",
    consolidated=False, chunks={}, storage_options={"anon": True}
)
```

### 21. NOAA Global RTOFS Forecast (NODD Kerchunk)

- **ID**: `noaa-nodd-rtofs`
- **Provider**: NOAA / NODD
- **URL**: `s3://noaa-nws-rtofs-pds/rtofs.best.nc.zarr`
- **Variable**: `temperature`

```python
ds = xr.open_zarr(
    "s3://noaa-nws-rtofs-pds/rtofs.best.nc.zarr",
    consolidated=False, chunks={}, storage_options={"anon": True}
)
```

---

## NetCDF Sources Suitable for Kerchunk / VirtualiZarr

### 22. NEX-GDDP-CMIP6 (NASA, bias-corrected daily)

- **ID**: `nex-gddp-cmip6`
- **Provider**: NASA / AWS Open Data
- **URL pattern**: `s3://nex-gddp-cmip6/NEX-GDDP-CMIP6/{model}/{experiment}/{member}/{variable}/{variable}_day_{model}_{experiment}_{member}_gn_{year}.nc`
- **Example**: `s3://nex-gddp-cmip6/NEX-GDDP-CMIP6/ACCESS-CM2/historical/r1i1p1f1/tasmax/tasmax_day_ACCESS-CM2_historical_r1i1p1f1_gn_1950.nc`
- **Notes**: Not Zarr itself — ideal target for kerchunk or VirtualiZarr pipelines. See [VirtualiZarr docs](https://virtualizarr.readthedocs.io/) for worked example creating an Icechunk store from this data.

```python
# Direct (slow, not cloud-optimized):
ds = xr.open_dataset(
    "s3://nex-gddp-cmip6/NEX-GDDP-CMIP6/ACCESS-CM2/historical/r1i1p1f1/tasmax/tasmax_day_ACCESS-CM2_historical_r1i1p1f1_gn_1950.nc",
    engine="h5netcdf", chunks={}, storage_options={"anon": True}
)

# Via VirtualiZarr + Icechunk (fast, recommended):
from virtualizarr import open_virtual_dataset
vds = open_virtual_dataset(
    "s3://nex-gddp-cmip6/NEX-GDDP-CMIP6/ACCESS-CM2/ssp126/r1i1p1f1/tasmax/tasmax_day_ACCESS-CM2_ssp126_r1i1p1f1_gn_2015_v2.0.nc",
    indexes={},
)
```

### 23. NOAA NWM Retro (NetCDF on S3, kerchunk target)

- **ID**: `nwm-netcdf-s3`
- **Provider**: NOAA / AWS
- **URL pattern**: `s3://noaa-nwm-retro-v2.0-pds/full_physics/{year}/{yyyymmddHHMM}.CHRTOUT_DOMAIN1.comp`
- **Notes**: Very large archive. Kerchunk quick-start example uses these files.

```python
import fsspec
fs = fsspec.filesystem("s3", anon=True)
urls = ["s3://" + p for p in fs.glob("s3://noaa-nwm-retro-v2.0-pds/full_physics/2017/201704010*.CHRTOUT_DOMAIN1.comp")]
```

---

## Copernicus Marine (auth often required)

### 24. Copernicus Marine Global Ocean Physics

- **Provider**: Copernicus Marine / Mercator Ocean
- **Notes**: Direct Zarr endpoints exist on S3 (Cloudferro), but most require authentication via the `copernicusmarine` Python toolbox. The catalog is browsable, and the toolbox can download subsets as local Zarr.

```python
import copernicusmarine
# List available datasets:
# copernicusmarine.describe()

# Download a subset as local Zarr:
copernicusmarine.subset(
    dataset_id="cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m",
    variables=["thetao"],
    minimum_longitude=6, maximum_longitude=7,
    minimum_latitude=41, maximum_latitude=42,
    start_datetime="2025-01-01", end_datetime="2025-01-14",
    output_filename="data.zarr"
)
```

---

## Discovering More Endpoints

### CMIP6 Catalog (5+ million Zarr stores on Google Cloud)

```python
import pandas as pd
df = pd.read_csv("https://storage.googleapis.com/cmip6/pangeo-cmip6.csv")
print(f"Total stores: {len(df)}")
# Filter for specific variable/experiment:
subset = df.query("variable_id=='tas' & experiment_id=='historical' & table_id=='Amon'")
print(subset[["source_id", "zstore"]].head(10))
```

AWS mirror (may lag): `https://cmip6-pds.s3-us-west-2.amazonaws.com/pangeo-cmip6.csv`

### Pangeo Catalog (Intake)

```python
from intake import open_catalog
cat = open_catalog(
    "https://raw.githubusercontent.com/pangeo-data/pangeo-datastore/master/intake-catalogs/master.yaml"
)
print(list(cat))
# Most datasets stored as Zarr on OSN or GCS
```

### AWS Registry of Open Data

Search [registry.opendata.aws](https://registry.opendata.aws/) for "zarr". Notable datasets
include MUR SST, NASA POWER, HRRR, NOAA NODD Kerchunk, National Water Model,
ITS_LIVE, GOES-16/17, and many more.

### Microsoft Planetary Computer

Browse [planetarycomputer.microsoft.com/catalog?filter=zarr](https://planetarycomputer.microsoft.com/catalog?filter=zarr)
for ERA5, Daymet, TerraClimate, CMIP6, and others on Azure Blob.

### NOAA NODD Kerchunk References

See [registry.opendata.aws/noaa-nodd-kerchunk](https://registry.opendata.aws/noaa-nodd-kerchunk/)
for continuously updated virtual Zarr references to NOAA operational forecast systems
(OFS models, Global RTOFS, NWM short-range).

### OME-Zarr / Bioimaging

[idr.github.io/ome-ngff-samples](https://idr.github.io/ome-ngff-samples/) — Image Data Resource
samples in OME-NGFF (Zarr V2 with OME metadata). Different domain but useful for testing
multiscale/pyramid Zarr readers.

### EOPF Sentinel (Zarr V3, coming 2025)

The Copernicus EOPF framework will distribute Sentinel products in Zarr format (likely V3).
A GDAL driver plugin exists at [github.com/EOPF-Sample-Service/GDAL-ZARR-EOPF](https://github.com/EOPF-Sample-Service/GDAL-ZARR-EOPF).
Watch this space for public V3 endpoints.

---

## GDAL Multidimensional CLI Reference

For any HTTPS-accessible Zarr V2 store, the general pattern is:

```bash
gdalmdiminfo 'ZARR:"/vsicurl/https://example.org/path/to/store.zarr"'
```

For GCS stores, use the HTTPS gateway:

```bash
gdalmdiminfo 'ZARR:"/vsicurl/https://storage.googleapis.com/cmip6/CMIP6/CMIP/AS-RCEC/TaiESM1/historical/r1i1p1f1/Amon/tas/gn/v20200225"'
```

For S3 stores with anonymous access, set `AWS_NO_SIGN_REQUEST=YES`:

```bash
AWS_NO_SIGN_REQUEST=YES gdalmdiminfo 'ZARR:"/vsis3/mur-sst/zarr"'
```

For kerchunk JSON references (GDAL >= 3.11):

```bash
gdalmdiminfo 'ZARR:"/vsicurl/https://some-host/path/to/reference.json"'
```

For kerchunk Parquet references (GDAL >= 3.11):

```bash
# Point to the directory containing .zmetadata and parquet subdirectories
gdalmdiminfo 'ZARR:"/vsicurl/https://some-host/path/to/parquet-ref-dir"'
```

---

## Notes on R Access

For R, the primary routes are `stars::read_mdim()` (wrapping GDAL multidim API) and the
hypertidy/zarrr package (wrapping xarray via reticulate). The `gdalraster` package also
provides multidimensional access. The DSN strings for `read_mdim()` are exactly the GDAL
DSN strings shown above.

```r
library(stars)
dsn <- 'ZARR:"/vsicurl/https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/gpcp-feedstock/gpcp.zarr"'
r <- read_mdim(dsn, count = c(NA, NA, 10))  # first 10 time steps
```

The `tidync` and `ndr` packages (hypertidy) can also work with Zarr once GDAL or xarray
backends are configured. For xarray-via-reticulate, the `zarrr` package provides helpers:

```r
library(zarrr)
z <- xarray_zarr("https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/gpcp-feedstock/gpcp.zarr")
```
