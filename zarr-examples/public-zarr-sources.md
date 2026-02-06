# Public Zarr Test Sources

A curated collection of Zarr stores for testing GDAL and generic Zarr openers, emphasizing variety in **zarr version**, **structure**, **producer**, and **access patterns**.

---

## 1. ZARR V3 WITH SHARDING (Cutting Edge)

These are the most interesting for testing GDAL's emerging v3 support.

### EOPF Explorer (Copernicus Sentinel - GeoZarr)
- **Format**: Zarr v3 with `sharding_indexed` codec, GeoZarr multiscales
- **Producer**: ESA/Copernicus EOPF
- **Auth**: Public HTTPS (no credentials)
- **Structure**: Hierarchical groups with pyramid levels (r10m, r20m, r60m, etc.)

**STAC Discovery**:
```bash
# STAC API
curl "https://api.explorer.eopf.copernicus.eu/stac/collections"
curl "https://api.explorer.eopf.copernicus.eu/stac/search?collections=sentinel-2-l2a&limit=1"
```

**Example Direct URLs** (from STAC results):
```
https://s3.explorer.eopf.copernicus.eu/esa-sentinel-explorer/products/sentinel-2-l2a/S2A_MSIL2A_20250106T095401_N0511_R079_T33UUP_20250106T120813.zarr/reflectance
```

**GDAL Access** (group-level, arrays not discovered due to consolidated metadata):
```bash
gdalmdiminfo "ZARR:\"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-sentinel-explorer/products/sentinel-2-l2a/S2A_MSIL2A_20250106T095401_N0511_R079_T33UUP_20250106T120813.zarr/reflectance\""
```

⚠️ **GDAL Limitation**: `sharding_indexed` codec not yet supported (PR expected soon, Jan 2026)

---

### OME-NGFF v0.5 / OME 2024 Challenge (Bioimaging)
- **Format**: Zarr v3 with sharding, OME-NGFF v0.5 metadata
- **Producer**: OME/IDR community
- **Auth**: Public HTTPS
- **Structure**: Multiscale pyramids, labels, HCS plates

**Sample Gallery**: https://ome.github.io/ome2024-ngff-challenge/
**IDR Samples**: https://idr.github.io/ome-ngff-samples/

**Direct URLs (v0.5 Zarr v3 with sharding)**:
```
# Images
https://uk1s3.embassy.ebi.ac.uk/idr/share/ome2024-ngff-challenge/idr0044/4007801.zarr
https://uk1s3.embassy.ebi.ac.uk/idr/share/ome2024-ngff-challenge/idr0011/Plate4-TS-Blue-B.ome.zarr

# Validator for any URL
https://ome.github.io/ome-ngff-validator/?source=<URL>
```

**Programmatic Discovery**:
```python
# napari example
napari --plugin napari-ome-zarr https://uk1s3.embassy.ebi.ac.uk/idr/share/ome2024-ngff-challenge/idr0044/4007801.zarr
```

---

### Dynamical.org NOAA HRRR (Icechunk Zarr v3)
- **Format**: Icechunk (Zarr v3 variant with version control)
- **Producer**: dynamical.org
- **Auth**: Public S3 (anonymous)
- **Structure**: Weather forecast cube with time/ensemble dimensions

**S3 Bucket**:
```
s3://dynamical-noaa-hrrr/
```

**CLI Access**:
```bash
aws s3 ls --no-sign-request s3://dynamical-noaa-hrrr/
```

**Docs**: https://dynamical.org/catalog/models/noaa-hrrr/

---

### Earthmover/Arraylake ERA5 Surface (Icechunk Zarr v3)
- **Format**: Icechunk Zarr v3, ~60TB
- **Producer**: Earthmover
- **Auth**: Requires Arraylake account (free tier available)
- **Structure**: Dual chunking strategies (spatial vs temporal groups)

**Access**:
```python
from arraylake import Client
client = Client()
repo = client.get_repo("earthmover-public/era5-surface-aws")
session = repo.readonly_session("main")
import xarray as xr
ds = xr.open_dataset(session.store, engine="zarr", consolidated=False, zarr_format=3, group="spatial")
```

**Docs**: https://docs.earthmover.io/sample-data/era5

---

## 2. ZARR V2 (Mature, Wide Support)

### CMIP6 Climate Model Data (Pangeo)
- **Format**: Zarr v2, consolidated metadata
- **Producer**: CMIP6/Pangeo community
- **Auth**: Public (GCS anonymous, AWS anonymous)
- **Structure**: Single-variable datacubes per zarr store

**Google Cloud Bucket**:
```
gs://cmip6/
```

**AWS S3 Bucket**:
```
s3://cmip6-pds/
```

**Catalog CSV** (for discovering paths):
```
https://cmip6.storage.googleapis.com/pangeo-cmip6.csv
```

**Example Direct URLs**:
```
gs://cmip6/CMIP6/ScenarioMIP/NOAA-GFDL/GFDL-ESM4/ssp585/r1i1p1f1/Omon/zos/gn/v20180701/
s3://cmip6-pds/CMIP6/ScenarioMIP/NOAA-GFDL/GFDL-ESM4/ssp585/r1i1p1f1/Omon/zos/gn/v20180701/
```

**xarray Access**:
```python
import xarray as xr
ds = xr.open_zarr("gs://cmip6/CMIP6/ScenarioMIP/NOAA-GFDL/GFDL-ESM4/ssp585/r1i1p1f1/Omon/zos/gn/v20180701/", consolidated=True)
```

---

### NOAA HRRR Weather Model (University of Utah)
- **Format**: Zarr v2
- **Producer**: University of Utah / NOAA
- **Auth**: Public S3 (anonymous)
- **Structure**: Weather variables, time-stepped

**S3 Bucket**:
```
s3://hrrrzarr/
```

**Browse**: https://hrrrzarr.s3.amazonaws.com/index.html

---

### NOAA National Water Model Retrospective
- **Format**: Zarr v2
- **Producer**: NOAA/NCAR
- **Auth**: Public S3 (anonymous)
- **Structure**: Hydrological model output

**S3 Buckets**:
```
s3://noaa-nwm-retrospective-3-0-pds/   (v3.0, NetCDF + Zarr)
s3://noaa-nwm-retrospective-2-1-zarr-pds/  (v2.1 Zarr)
```

**Browse**: https://noaa-nwm-retrospective-3-0-pds.s3.amazonaws.com/index.html

---

### CESM2 Large Ensemble (NCAR)
- **Format**: Zarr v2
- **Producer**: NCAR
- **Auth**: Public S3 (anonymous)
- **Structure**: Climate model ensemble

**S3 Bucket**:
```
s3://ncar-cesm2-lens/
```

---

### OME-NGFF v0.4 (Bioimaging - IDR)
- **Format**: Zarr v2, OME-NGFF v0.4
- **Producer**: OME/EBI
- **Auth**: Public HTTPS
- **Structure**: Multiscale pyramids with channel/z/time dimensions

**Direct URLs**:
```
https://uk1s3.embassy.ebi.ac.uk/idr/zarr/v0.4/idr0062A/6001240.zarr
https://uk1s3.embassy.ebi.ac.uk/idr/zarr/v0.4/idr0101A/13457537.zarr
https://uk1s3.embassy.ebi.ac.uk/idr/zarr/v0.5/idr0062A/6001240_labels.zarr
https://uk1s3.embassy.ebi.ac.uk/idr/zarr/v0.3/9836842.zarr
```

---

### NOAA Water Column Sonar Data
- **Format**: Zarr v2
- **Producer**: NOAA NCEI
- **Auth**: Public S3 (anonymous)
- **Structure**: Acoustic backscatter data

**S3 Bucket**:
```
s3://noaa-wcsd-zarr-pds/
```

**Example Path**:
```
s3://noaa-wcsd-zarr-pds/level_2/Bell_M._Shimada/SH1507/EK60/SH1507.zarr
```

**Browse**: https://noaa-wcsd-zarr-pds.s3.amazonaws.com/index.html

---

## 3. KERCHUNK / VIRTUAL ZARR (Reference Files)

These aren't "real" zarr but present GRIB/NetCDF as Zarr v2 via reference files.

### NOAA NODD Kerchunk References
- **Format**: Zarr v2 reference files (kerchunk)
- **Producer**: RPS Group for NOAA
- **Auth**: Public S3
- **Structure**: Virtual zarr pointing to GRIB/NetCDF chunks

**S3 Bucket**:
```
s3://noaa-nodd-kerchunk-pds/
```

**Example Aggregations**:
```
nos.dbofs.fields.best.nc.zarr  (continuously updated best forecast)
nos.dbofs.fields.forecast.YYYYMMDD.tCCz.nc.zarr
```

---

## 4. STAC CATALOGS FOR DISCOVERY

### EOPF Explorer STAC
```
https://api.explorer.eopf.copernicus.eu/stac
https://api.explorer.eopf.copernicus.eu/stac/collections
https://api.explorer.eopf.copernicus.eu/stac/search
```

### NASA VEDA STAC
```
https://openveda.cloud/api/stac/
```

### Microsoft Planetary Computer STAC
```
https://planetarycomputer.microsoft.com/api/stac/v1
```
(Has some Zarr assets, mostly COG)

---

## 5. STRUCTURAL VARIETY SUMMARY

| Source | Zarr Ver | Sharding | Domain | Dims | CRS/Geo |
|--------|----------|----------|--------|------|---------|
| EOPF Explorer | v3 | ✅ | Satellite imagery | y,x,band | Native UTM |
| EOPF Samples (EODC) | v3 | ✅ | Satellite imagery | y,x,band | Native UTM |
| OME 2024 Challenge | v3 | ✅ | Bioimaging | c,z,y,x,t | Pixel coords |
| Dynamical HRRR | v3 (Icechunk) | ❓ | Weather | time,y,x | Lambert Conformal |
| Earthmover ERA5 | v3 (Icechunk) | ❓ | Climate | time,lat,lon | WGS84 |
| AODN Sea Level | v2 | ❌ | Oceanography | time,lat,lon | WGS84 |
| AODN HF Radar | v2 | ❌ | Oceanography | time,lat,lon | WGS84 |
| CMEMS Sea Level | v2 | ❌ | Oceanography | time,lat,lon | WGS84 |
| CMIP6 | v2 | ❌ | Climate | time,lat,lon,lev | Various |
| HRRR Utah | v2 | ❌ | Weather | time,y,x | Lambert Conformal |
| NWM | v2 | ❌ | Hydrology | time,feature_id | Various |
| IDR OME-NGFF | v2 | ❌ | Bioimaging | c,z,y,x | Pixel coords |
| NOAA Sonar | v2 | ❌ | Acoustics | time,range | Depth |

---

## 6. QUICK TEST COMMANDS

### GDAL Multidim (v3 groups)
```bash
# Works for v3 group metadata (arrays may not be discovered)
gdalmdiminfo "ZARR:\"/vsicurl/https://uk1s3.embassy.ebi.ac.uk/idr/zarr/v0.4/idr0062A/6001240.zarr\""

# v2 with consolidated metadata
gdalmdiminfo "ZARR:\"/vsicurl/https://uk1s3.embassy.ebi.ac.uk/idr/zarr/v0.4/idr0062A/6001240.zarr\"" -oo USE_ZMETADATA=YES
```

### Python zarr-python v3
```python
import zarr
store = zarr.storage.FsspecStore.from_url("https://uk1s3.embassy.ebi.ac.uk/idr/zarr/v0.4/idr0062A/6001240.zarr")
root = zarr.open_group(store)
print(root.tree())
```

### xarray
```python
import xarray as xr
ds = xr.open_zarr("https://uk1s3.embassy.ebi.ac.uk/idr/zarr/v0.4/idr0062A/6001240.zarr", consolidated=True)
```

---

## 7. AUTH PATTERNS

| Source | Auth Method |
|--------|-------------|
| EBI/IDR | None (public HTTPS) |
| EOPF Explorer | None (public HTTPS) |
| EOPF Samples (EODC) | None (public HTTPS, anonymous S3) |
| AWS Open Data | `--no-sign-request` or `anon=True` |
| AODN (AWS ap-southeast-2) | `--no-sign-request` + `--region ap-southeast-2` |
| Google Cloud | `token='anon'` |
| Earthmover/Arraylake | Arraylake account + `Client().login()` |
| NASA Earthdata | Earthdata Login (bearer token or netrc) |
| Microsoft Planetary Computer | `planetary_computer.sign()` for some assets |
| Copernicus Marine | CMEMS account + `copernicusmarine login` |

---

## 8. EOPF SENTINEL ZARR SAMPLES (ESA)

The EOPF Sentinel Zarr Samples project provides reprocessed Sentinel data in the new harmonized Zarr format.

### Object Storage (EODC)
- **Format**: Zarr v3 with sharding, GeoZarr conventions
- **Producer**: ESA EOPF via EODC
- **Auth**: Public HTTPS (anonymous)
- **Structure**: Hierarchical groups (measurements/reflectance/r10m, r20m, r60m, conditions, quality)

**Direct URLs**:
```
# Sentinel-2 L2A (latest CPM version)
https://objects.eodc.eu/e05ab01a9d56408d82ac32d69a5aae2a:202506-s02msil2a/10/products/cpm_v256/S2C_MSIL2A_20250610T103641_N0511_R008_T32UMD_20250610T132001.zarr

# Sentinel-2 L1C (tutorial data)
https://objects.eodc.eu/e05ab01a9d56408d82ac32d69a5aae2a:sample-data/tutorial_data/cpm_v253/S2B_MSIL1C_20250113T103309_N0511_R108_T32TLQ_20250113T122458.zarr
```

**xarray Access**:
```python
import xarray as xr
url = 'https://objects.eodc.eu/e05ab01a9d56408d82ac32d69a5aae2a:202506-s02msil2a/10/products/cpm_v256/S2C_MSIL2A_20250610T103641_N0511_R008_T32UMD_20250610T132001.zarr'
dt = xr.open_datatree(url, engine="zarr")
print(dt)
# measurements/reflectance/r10m, r20m, r60m
# quality/mask, quality/atmosphere
# conditions/geometry, meteorology
```

**s3fs Access (listing)**:
```python
import s3fs
fs = s3fs.S3FileSystem(anon=True, client_kwargs={"endpoint_url": "https://objects.eodc.eu"})
bucket = "e05ab01a9d56408d82ac32d69a5aae2a:sample-data"
paths = fs.glob(f"s3://{bucket}/tutorial_data/cpm_v253/S2*_MSIL2A_*.zarr")
```

**GDAL Access (with EOPF driver)**:
```bash
# Requires GDAL with EOPFZARR driver
gdalinfo 'EOPFZARR:"/vsicurl/https://objects.eodc.eu/e05ab01a9d56408d82ac32d69a5aae2a:sample-data/tutorial_data/cpm_v253/S2B_MSIL1C_20250113T103309_N0511_R108_T32TLQ_20250113T122458.zarr"'
```

**Resources**:
- EOPF Toolkit: https://eopf-toolkit.github.io/eopf-101/
- Sample Notebooks: https://eopf-sample-service.github.io/eopf-sample-notebooks/
- STAC API: https://api.explorer.eopf.copernicus.eu/stac
- Main site: https://zarr.eopf.copernicus.eu/

---

## 9. COPERNICUS MARINE DATA STORE (CMEMS)

CMEMS provides oceanographic data in Analysis-Ready Cloud-Optimized (ARCO) Zarr format.

### Sea Level - allsat_phy (SEALEVEL_GLO_PHY_L4_MY_008_047)
- **Format**: Zarr v2 (ARCO)
- **Producer**: CLS (France) / Copernicus Marine Service
- **Auth**: Copernicus Marine account required (free registration)
- **Structure**: Daily gridded sea level anomalies, 0.125° global
- **Variables**: sla, adt, ugos, vgos, ugosa, vgosa, err_*
- **Temporal**: 1993 - present

**Dataset IDs**:
```
cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1D   (daily)
cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1M-m (monthly)
```

**STAC Discovery**:
```bash
# STAC catalog root
curl "https://stac.marine.copernicus.eu/metadata/catalog.stac.json"

# Product metadata (via toolbox)
copernicusmarine describe --include-datasets -c SEALEVEL_GLO_PHY_L4_MY_008_047
```

**S3 Access (CloudFerro)**:
```python
# Via copernicusmarine toolbox (recommended)
import copernicusmarine
# Login first: copernicusmarine login

# Subset download as Zarr
copernicusmarine.subset(
    dataset_id="cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1D",
    variables=["sla", "adt"],
    start_datetime="2024-01-01",
    end_datetime="2024-01-31",
    minimum_longitude=-180,
    maximum_longitude=180,
    minimum_latitude=-90,
    maximum_latitude=90,
    output_filename="sealevel.zarr",
    file_format="zarr"
)
```

**xarray via STAC URL** (after auth):
```python
from copernicusmarine import open_dataset
ds = open_dataset(
    dataset_id="cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1D"
)
```

**Julia Access**:
```julia
using STAC, ZarrDatasets
stac_url = "https://stac.marine.copernicus.eu/metadata/catalog.stac.json"
cat = STAC.Catalog(stac_url)
product_id = "SEALEVEL_GLO_PHY_L4_MY_008_047"
dataset_id = "cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1D"
# Get URL from STAC item assets["timeChunked"]
ds = ZarrDataset(url)
```

**Infrastructure**:
- S3 endpoints: `https://s3.waw3-1.cloudferro.com`, `https://s3.waw4-1.cloudferro.com`
- STAC metadata: `https://stac.marine.copernicus.eu`
- Web portal: https://data.marine.copernicus.eu/product/SEALEVEL_GLO_PHY_L4_MY_008_047/description

### Other Notable CMEMS Products

| Product ID | Dataset ID | Domain |
|------------|------------|--------|
| SEALEVEL_GLO_PHY_L4_NRT_008_046 | cmems_obs-sl_glo_phy-ssh_nrt_allsat-l4-duacs-0.125deg_P1D | Sea level NRT |
| GLOBAL_ANALYSISFORECAST_PHY_001_024 | cmems_mod_glo_phy_anfc_0.083deg_P1D-m | Global physics forecast |
| SST_GLO_SST_L4_NRT_OBSERVATIONS_010_001 | METOFFICE-GLO-SST-L4-NRT-OBS-SST-V2 | SST |
| OCEANCOLOUR_GLO_BGC_L4_MY_009_108 | cmems_obs-oc_glo_bgc-plankton_my_l4-gapfree-multi-4km_P1D | Ocean color |

---

## 11. AODN (Australian Ocean Data Network) - IMOS

AODN provides cloud-optimised Australian ocean data on AWS, managed from Tasmania. All datasets are in `ap-southeast-2` region.

### S3 Bucket
```
s3://aodn-cloud-optimised/
```

**CLI Access** (no auth required):
```bash
aws s3 ls --no-sign-request --region ap-southeast-2 s3://aodn-cloud-optimised/
```

### Gridded Zarr Datasets

| Dataset | S3 Path | Domain |
|---------|---------|--------|
| Sea Level Anomaly (NRT) | `model_sea_level_anomaly_gridded_realtime.zarr` | Altimetry / Currents |
| SST L4 GAMSSA (global) | `satellite_ghrsst_l4_gamssa_1day_multi_sensor_world.zarr` | SST |
| SST Himawari-8 L3C (day) | `satellite_ghrsst_l3c_1day_daytime_himawari8.zarr` | SST |
| SST Himawari-8 L3C (night) | `satellite_ghrsst_l3c_1day_nighttime_himawari8.zarr` | SST |
| Chlorophyll-a OCI (MODIS) | `satellite_chlorophylla_oci_1day_aqua.zarr` | Ocean Colour |
| Chlorophyll-a OCI (NOAA20) | `satellite_chlorophylla_oci_1day_noaa20.zarr` | Ocean Colour |
| Chlorophyll-a OC3 (MODIS) | `satellite_chlorophylla_oc3_1day_aqua.zarr` | Ocean Colour |
| NPP OC3 MODIS 1-day | `satellite_net_primary_productivity_oc3_1day_aqua.zarr` | Ocean Colour |
| NPP GSM MODIS 1-day | `satellite_net_primary_productivity_gsm_1day_aqua.zarr` | Ocean Colour |
| Diffuse Attenuation k490 | `satellite_diffuse_attenuation_coefficent_1day_aqua.zarr` | Ocean Colour |
| HF Radar Newcastle | `radar_Newcastle_velocity_hourly_averaged_delayed_qc.zarr` | Currents |
| HF Radar Coral Coast | `radar_CoralCoast_velocity_hourly_averaged_delayed_qc.zarr` | Currents |
| HF Radar SA Gulfs | `radar_SouthAustraliaGulfs_velocity_hourly_averaged_delayed_qc.zarr` | Currents |

### Example Access

**xarray**:
```python
import xarray as xr
ds = xr.open_zarr(
    "s3://aodn-cloud-optimised/model_sea_level_anomaly_gridded_realtime.zarr",
    storage_options={"anon": True}
)
```

**GDAL**:
```bash
gdalmdiminfo "ZARR:\"/vsis3/aodn-cloud-optimised/model_sea_level_anomaly_gridded_realtime.zarr\"" \
  --config AWS_NO_SIGN_REQUEST YES \
  --config AWS_REGION ap-southeast-2
```

### Registry Links

| Product | Registry URL |
|---------|--------------|
| Sea Level Anomaly | https://registry.opendata.aws/aodn_model_sea_level_anomaly_gridded_realtime/ |
| SST L4 GAMSSA | https://registry.opendata.aws/aodn_satellite_ghrsst_l4_gamssa_1day_multi_sensor_world/ |
| SST Himawari-8 Night | https://registry.opendata.aws/aodn_satellite_ghrsst_l3c_1day_nighttime_himawari8/ |
| Chlorophyll-a OCI (MODIS) | https://registry.opendata.aws/aodn_satellite_chlorophylla_oci_1day_aqua/ |
| Chlorophyll-a OCI (NOAA20) | https://registry.opendata.aws/aodn_satellite_chlorophylla_oci_1day_noaa20/ |
| Chlorophyll-a OC3 | https://registry.opendata.aws/aodn_satellite_chlorophylla_oc3_1day_aqua/ |
| NPP OC3 | https://registry.opendata.aws/aodn_satellite_net_primary_productivity_oc3_1day_aqua/ |
| NPP GSM | https://registry.opendata.aws/aodn_satellite_net_primary_productivity_gsm_1day_aqua/ |
| Diffuse Attenuation | https://registry.opendata.aws/aodn_satellite_diffuse_attenuation_coefficent_1day_aqua/ |
| HF Radar Newcastle | https://registry.opendata.aws/aodn_radar_newcastle_velocity_hourly_averaged_delayed_qc/ |
| HF Radar Coral Coast | https://registry.opendata.aws/aodn_radar_coralcoast_velocity_hourly_averaged_delayed_qc/ |
| HF Radar SA Gulfs | https://registry.opendata.aws/aodn_radar_southaustraliagulfs_velocity_hourly_averaged_delayed_qc/ |
| Altimetry Cal/Val | https://registry.opendata.aws/aodn_mooring_satellite_altimetry_calibration_validation/ |

### Other Formats (Parquet)

AODN also provides tabular data in Parquet format:
- Animal acoustic tracking
- Ocean gliders
- Moorings (hourly timeseries, CTD profiles)
- XBT profiles

### Resources

- **GitHub**: https://github.com/aodn/aodn_cloud_optimised
- **Notebooks**: https://github.com/aodn/aodn_cloud_optimised/tree/main/notebooks
- **Docs**: https://aodn-cloud-optimised.readthedocs.io/
- **IMOS Catalogue**: https://catalogue-imos.aodn.org.au/

---

## 12. RESOURCES

- **GeoZarr Examples**: https://developmentseed.org/geozarr-examples/
- **Cloud-Native Geo Guide**: https://guide.cloudnativegeo.org/
- **OME-NGFF Spec**: https://ngff.openmicroscopy.org/
- **Zarr v3 Spec**: https://zarr-specs.readthedocs.io/en/latest/v3/core/index.html
- **Sharding Codec Spec**: https://zarr-specs.readthedocs.io/en/latest/v3/codecs/sharding-indexed/v1.0.html
- **AWS Open Data (Zarr tag)**: https://registry.opendata.aws/tag/zarr/
