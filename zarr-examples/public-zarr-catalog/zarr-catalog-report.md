  Probing pangeo-gpcp ... ok (3.18s)
  Probing cmip6-gcs-zos ... ok (15.61s)
  Probing cmip6-highres-psl ... ok (0.81s)
  Probing arco-era5-single-level ... ok (3.7s)
  Probing arco-era5-v3 ... ok (75.86s)
  Probing weatherbench2-era5 ... ok (1.52s)
  Probing cmip6-s3-hfls ... ok (2.13s)
  Probing mur-sst-aws ... ok (27.58s)
  Probing hrrr-zarr-aws ... ok (10.55s)
  Probing its-live ... ok (41.46s)
  Probing nasa-power ... ok (48.39s)
  Probing nwm-zarr ... ok (9.04s)
  Probing oisst-kerchunk ... ok (4.46s)
  Probing s1-coherence-kerchunk ... ok (13.37s)
  Probing pc-daymet ... ok (59.02s)
  Probing pc-era5 ... skip-auth (0.0s)
  Probing copernicus-marine-sla ... ok (4.42s)
  Probing nex-gddp-cmip6 ... ok (84.34s)

========================================================================
  Zarr Catalog Probe Report
  17 ok / 0 error / 1 skipped / 0 untested (of 18)
========================================================================

  [        OK] pangeo-gpcp
             GPCP Daily Precipitation (Pangeo Forge)
             Pangeo / OSN | V2 | https
             https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/gpcp-feedstock/gpcp.zarr
             dims={'latitude': 180, 'nv': 2, 'longitude': 360, 'time': 9226}  (3.18s)
             note: Variable 'precip' shape=(9226, 180, 360).

  [        OK] cmip6-gcs-zos
             CMIP6 Sea Surface Height (GFDL-ESM4, ssp585)
             CMIP6 / Google Cloud | V2 | gs
             gs://cmip6/CMIP6/ScenarioMIP/NOAA-GFDL/GFDL-ESM4/ssp585/r1i1p1f1/Omon/zos/gn/v20180701/
             dims={'bnds': 2, 'y': 576, 'x': 720, 'vertex': 4, 'time': 1032}  (15.61s)
             note: Variable 'zos' shape=(1032, 576, 720).

  [        OK] cmip6-highres-psl
             CMIP6 HighResMIP Sea Level Pressure (CMCC-CM2-HR4)
             CMIP6 / Google Cloud | V2 | gs
             gs://cmip6/CMIP6/HighResMIP/CMCC/CMCC-CM2-HR4/highresSST-present/r1i1p1f1/6hrPlev/psl/gn/v20170706/
             dims={'lat': 192, 'bnds': 2, 'lon': 288, 'time': 97820}  (0.81s)
             note: Variable 'psl' shape=(97820, 192, 288).

  [        OK] arco-era5-single-level
             ARCO-ERA5 Single-Level Reanalysis
             Google Research / ECMWF | V2 | gs
             gs://gcp-public-data-arco-era5/ar/1959-2022-full_37-1h-0p25deg-chunk-1.zarr-v2
             dims={'time': 552264, 'latitude': 721, 'longitude': 1440, 'level': 37}  (3.7s)
             note: Opening metadata can be slow (~8s) due to hundreds of variables. Variable '2m_temperature' shape=(552264, 721, 1440).

  [        OK] arco-era5-v3
             ARCO-ERA5 Full 37-level (dataset version 3)
             Google Research / ECMWF | V2 | gs
             gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3
             dims={'time': 1323648, 'latitude': 721, 'longitude': 1440, 'level': 37}  (75.86s)
             note: Very slow to open metadata (~90s). Test with --skip-slow. Variable '2m_temperature' shape=(1323648, 721, 1440).

  [        OK] weatherbench2-era5
             WeatherBench2 ERA5 (6-hourly, 1.5deg)
             Google Research / WeatherBench2 | V2 | gs
             gs://weatherbench2/datasets/era5/1959-2023_01_10-6h-64x32_equiangular_conservative.zarr
             dims={'time': 93544, 'longitude': 64, 'latitude': 32, 'level': 13}  (1.52s)
             note: Variable '2m_temperature' shape=(93544, 64, 32).

  [        OK] cmip6-s3-hfls
             CMIP6 Surface Upward Latent Heat Flux (TaiESM1, 1pctCO2)
             CMIP6 / AWS S3 | V2 | s3
             s3://cmip6-pds/CMIP6/CMIP/AS-RCEC/TaiESM1/1pctCO2/r1i1p1f1/Amon/hfls/gn/v20200225/
             dims={'time': 1800, 'lat': 192, 'lon': 288, 'bnds': 2}  (2.13s)
             note: Variable 'hfls' shape=(1800, 192, 288).

  [        OK] mur-sst-aws
             MUR SST L4 Global (JPL, NASA)
             NASA JPL / AWS Open Data | V2 | s3
             s3://mur-sst/zarr-v1
             dims={'time': 6443, 'lat': 17999, 'lon': 36000}  (27.58s)
             note: Variable 'analysed_sst' shape=(6443, 17999, 36000).

  [        OK] hrrr-zarr-aws
             HRRR Weather Model (surface analysis, TMP)
             NOAA / AWS Open Data | V2 | s3
             s3://hrrrzarr/sfc/20200801/20200801_00z_anl.zarr
             dims={'projection_x_coordinate': 1799, 'projection_y_coordinate': 1059}  (10.55s)
             note: Use group='surface/VARNAME' to select variable.

  [        OK] its-live
             ITS_LIVE Ice Velocity Datacube
             NASA MEaSUREs / AWS | V2 | s3
             s3://its-live-data/datacubes/v2/N00E020/ITS_LIVE_vel_EPSG32735_G0120_X750000_Y10050000.zarr
             dims={'mid_date': 119, 'y': 833, 'x': 833}  (41.46s)
             note: Tile path needs verification: run fs.ls('s3://its-live-data/datacubes/v2/N00E020/'). Variable 'v' shape=(119, 833, 833).

  [        OK] nasa-power
             NASA POWER Daily Meteorology (MERRA-2, spatial)
             NASA POWER / AWS Open Data | V2 | s3
             s3://nasa-power/merra2/spatial/power_merra2_daily_spatial_utc.zarr
             dims={'time': 17898, 'lat': 361, 'lon': 576}  (48.39s)
             note: Bucket moved from power-analysis-ready-datastore to nasa-power in 2024. Variable 'T2M' shape=(17898, 361, 576).

  [        OK] nwm-zarr
             National Water Model Reanalysis v2.1
             NOAA / AWS Open Data | V2 | s3
             s3://noaa-nwm-retro-v2-zarr-pds
             dims={'time': 227904, 'feature_id': 2729077}  (9.04s)
             note: Variable 'streamflow' shape=(227904, 2729077).

  [        OK] oisst-kerchunk
             NOAA OISST CDR (via Kerchunk JSON reference)
             Pangeo Forge / NOAA | kerchunk-json | reference-json
             https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/pangeo-forge/aws-noaa-oisst-feedstock/aws-noaa-oisst-avhrr-only.zarr/reference.json
             dims={'time': 15044, 'zlev': 1, 'lat': 720, 'lon': 1440}  (4.46s)
             note: Underlying data is NetCDF on S3. remote_options={'anon': True} required. Variable 'sst' shape=(15044, 1, 720, 1440).

  [        OK] s1-coherence-kerchunk
             Sentinel-1 Global Coherence (kerchunk, Earth Big Data)
             Earth Big Data / ASF | kerchunk-json | reference-json
             https://sentinel-1-global-coherence-earthbigdata.s3.us-west-2.amazonaws.com/data/wrappers/zarr-all.json
             dims={'season': 4, 'polarization': 4, 'latitude': 193200, 'longitude': 432000, 'coherence': 6, 'flightdirection': 2, 'orbit': 175}  (13.37s)
             note: Requires: pip install imagecodecs (for imagecodecs_tiff codec). Variable 'coherence' shape=(6,).

  [        OK] pc-daymet
             Daymet V4 Daily (Planetary Computer, Azure)
             Microsoft Planetary Computer | V2 | pc-azure
             https://daymeteuwest.blob.core.windows.net/daymet-zarr/daily/na.zarr
             dims={'time': 14965, 'y': 8075, 'x': 7814, 'nv': 2}  (59.02s)
             note: Requires planetary_computer and adlfs. pip install planetary-computer adlfs Variable 'tmax' shape=(14965, 8075, 7814).

  [SKIP(auth)] pc-era5
             ERA5 (Planetary Computer, Azure) [RETIRED]
             Microsoft Planetary Computer / ECMWF | V2 | auth-required
             https://era5euwest.blob.core.windows.net/era5-pds/era5/2020/01/air_temperature_at_2_metres.zarr
             note: RETIRED: era5euwest storage account no longer resolves. Use arco-era5 on GCS instead.

  [        OK] copernicus-marine-sla
             Copernicus Marine Sea Level (ARCO timeChunked)
             Copernicus Marine / Mercator Ocean | V2 | https
             https://s3.waw3-1.cloudferro.com/mdl-arco-time-045/arco/SEALEVEL_GLO_PHY_L4_MY_008_047/cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1D_202411/timeChunked.zarr
             dims={'time': 11902, 'latitude': 1440, 'longitude': 2880, 'nv': 2}  (4.42s)
             note: No auth needed for ARCO stores (mdl-arco-time-*, mdl-arco-geo-*). Native stores (mdl-native-*) need Copernicus Marine credentials. Use STAC catalog stac.marine.copernicus.eu for URL discovery. See hypertidy/cmemsarco R package for GDAL-based access patterns. Variable 'sla' shape=(11902, 1440, 2880).

  [        OK] nex-gddp-cmip6
             NEX-GDDP-CMIP6 (bias-corrected daily, NetCDF on S3)
             NASA / AWS Open Data | netcdf-source | s3-netcdf
             s3://nex-gddp-cmip6/NEX-GDDP-CMIP6/ACCESS-CM2/historical/r1i1p1f1/tasmax/tasmax_day_ACCESS-CM2_historical_r1i1p1f1_gn_1950.nc
             dims={'time': 365, 'lat': 600, 'lon': 1440}  (84.34s)
             note: Not a Zarr store. Open with fsspec + h5netcdf. Variable 'tasmax' shape=(365, 600, 1440).

