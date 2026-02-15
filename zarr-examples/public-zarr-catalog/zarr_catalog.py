#!/usr/bin/env python3
"""
zarr_catalog.py — Probe and validate public Zarr endpoints.

Each entry describes:
  - provider / catalog origin
  - zarr version (V2, V3, or virtual/kerchunk)
  - access method (direct HTTP, GCS, S3, kerchunk JSON, parquet refs)
  - a lightweight "smoke test" that opens metadata only (no bulk data)

Usage:
    python zarr_catalog.py              # run all probes, print report
    python zarr_catalog.py --json       # emit JSON
    python zarr_catalog.py --gdal       # also test via GDAL multidim CLI

Requires: xarray, zarr, fsspec, gcsfs, s3fs, aiohttp
"""

import argparse
import json
import time
import sys
import traceback
from dataclasses import dataclass, field, asdict
from typing import Optional

import xarray as xr

# ---------------------------------------------------------------------------
# Catalog entries
# ---------------------------------------------------------------------------

@dataclass
class ZarrEntry:
    """One public Zarr endpoint."""
    id: str
    name: str
    provider: str
    zarr_version: str          # "V2", "V3", "kerchunk-json", "kerchunk-parquet", "icechunk"
    access_protocol: str       # "https", "gs", "s3"
    store_url: str             # primary URL / URI
    open_kwargs: dict = field(default_factory=dict)   # extra kwargs for xr.open_zarr / open_dataset
    variable_hint: str = ""    # a variable name expected to exist
    description: str = ""
    notes: str = ""
    gdal_dsn: str = ""         # GDAL multidim DSN if applicable
    status: str = "untested"
    elapsed_s: float = 0.0
    error: str = ""
    dims_found: dict = field(default_factory=dict)


CATALOG = [

    # -----------------------------------------------------------------------
    # 1. PANGEO / OSN — classic Zarr V2, HTTP
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="pangeo-gpcp",
        name="GPCP Daily Precipitation (Pangeo Forge)",
        provider="Pangeo / OSN",
        zarr_version="V2",
        access_protocol="https",
        store_url="https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/gpcp-feedstock/gpcp.zarr",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="precip",
        description="Global Precipitation Climatology Project daily, 1° grid, 1996–2021.",
        gdal_dsn='ZARR:"/vsicurl/https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/gpcp-feedstock/gpcp.zarr"',
    ),

    # -----------------------------------------------------------------------
    # 2. CMIP6 on Google Cloud — Zarr V2, GCS anonymous
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="cmip6-gcs-tas",
        name="CMIP6 Surface Air Temperature (TaiESM1, historical)",
        provider="CMIP6 / Google Cloud",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://cmip6/CMIP6/CMIP/AS-RCEC/TaiESM1/historical/r1i1p1f1/Amon/tas/gn/v20200225/",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="tas",
        description="Monthly surface air temperature, 1850–2014, from TaiESM1.",
    ),
    ZarrEntry(
        id="cmip6-gcs-zos",
        name="CMIP6 Sea Surface Height (GFDL-ESM4, ssp585)",
        provider="CMIP6 / Google Cloud",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://cmip6/CMIP6/ScenarioMIP/NOAA-GFDL/GFDL-ESM4/ssp585/r1i1p1f1/Omon/zos/gn/v20180701/",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="zos",
        description="Monthly sea surface height, SSP5-8.5, 2015–2100.",
    ),

    # -----------------------------------------------------------------------
    # 3. CMIP6 on AWS S3 — Zarr V2, S3 anonymous
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="cmip6-s3-hfls",
        name="CMIP6 Surface Upward Latent Heat Flux (TaiESM1)",
        provider="CMIP6 / AWS S3",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://cmip6-pds/CMIP6/CMIP/AS-RCEC/TaiESM1/1pctCO2/r1i1p1f1/Amon/hfls/gn/v20200225/",
        open_kwargs=dict(consolidated=True, chunks={},
                         storage_options=dict(anon=True)),
        variable_hint="hfls",
        description="Monthly latent heat flux from 1pctCO2 experiment.",
    ),

    # -----------------------------------------------------------------------
    # 4. ARCO-ERA5 on Google Cloud — Zarr V2 (analysis-ready)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="arco-era5-single-level",
        name="ARCO-ERA5 Single-Level Reanalysis",
        provider="Google Research / ECMWF",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://gcp-public-data-arco-era5/ar/1959-2022-full_37-1h-0p25deg-chunk-1.zarr-v2",
        open_kwargs=dict(chunks={}, consolidated=True),
        variable_hint="2m_temperature",
        description="ERA5 hourly reanalysis, 0.25° global, 1959–2022. Very large (~2 PB logical).",
        notes="Opening metadata is slow (~30–90s) due to hundreds of variables.",
    ),

    # -----------------------------------------------------------------------
    # 5. ARCO-ERA5 Zarr V3 variant
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="arco-era5-v3",
        name="ARCO-ERA5 Full 37-level (Zarr V3 naming)",
        provider="Google Research / ECMWF",
        zarr_version="V2",  # still V2 internally, the '-v3' is a dataset version
        access_protocol="gs",
        store_url="gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3",
        open_kwargs=dict(chunks={}),
        variable_hint="2m_temperature",
        description="ERA5 full 37-level, hourly, 0.25° — dataset version 3 (still Zarr V2 format).",
        notes="Very slow to open metadata (~90s). Test with care.",
    ),

    # -----------------------------------------------------------------------
    # 6. ERA5 on AWS S3 (raw NetCDF-based Zarr-like, via s3://era5-pds)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="era5-aws-pds",
        name="ERA5 Reanalysis on AWS (single month)",
        provider="ECMWF / AWS Open Data",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://era5-pds/2020/01/data/air_pressure_at_mean_sea_level.zarr",
        open_kwargs=dict(consolidated=False, chunks={},
                         storage_options=dict(anon=True)),
        variable_hint="air_pressure_at_mean_sea_level",
        description="ERA5 single-variable monthly Zarr, Jan 2020, on AWS.",
    ),

    # -----------------------------------------------------------------------
    # 7. MUR SST on AWS — Zarr V2, S3 anonymous
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="mur-sst-aws",
        name="MUR SST L4 Global (JPL, NASA)",
        provider="NASA JPL / AWS Open Data",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://mur-sst/zarr",
        open_kwargs=dict(consolidated=True, chunks={},
                         storage_options=dict(anon=True)),
        variable_hint="analysed_sst",
        description="MUR 0.01° daily SST, ~54 TB total. 2002–present.",
    ),

    # -----------------------------------------------------------------------
    # 8. HRRR Zarr on AWS — unusual layout
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="hrrr-zarr-aws",
        name="HRRR Weather Model (surface, analysis)",
        provider="NOAA / AWS Open Data",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://hrrrzarr/sfc/20200801/20200801_00z_anl.zarr/surface/GUST",
        open_kwargs=dict(consolidated=False, chunks={},
                         storage_options=dict(anon=True)),
        variable_hint="GUST",
        description="HRRR 3km CONUS weather model. Non-standard layout: each variable is a separate Zarr group.",
        notes="Requires combining group and subgroup URLs with xr.open_mfdataset.",
    ),

    # -----------------------------------------------------------------------
    # 9. Pangeo Forge OISST (kerchunk reference JSON)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="oisst-kerchunk",
        name="NOAA OISST CDR (via Kerchunk JSON reference)",
        provider="Pangeo Forge / NOAA",
        zarr_version="kerchunk-json",
        access_protocol="https",
        store_url="https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/pangeo-forge/aws-noaa-oisst-feedstock/aws-noaa-oisst-avhrr-only.zarr/reference.json",
        open_kwargs=dict(
            engine="zarr",
            backend_kwargs=dict(
                consolidated=False,
                storage_options=dict(
                    fo="https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/pangeo-forge/aws-noaa-oisst-feedstock/aws-noaa-oisst-avhrr-only.zarr/reference.json",
                    remote_options=dict(anon=True),
                    remote_protocol="s3",
                ),
            ),
            chunks={},
        ),
        variable_hint="sst",
        description="NOAA 0.25° daily OISST AVHRR-only CDR, accessed via kerchunk JSON reference file.",
        notes="Uses reference:// protocol. Underlying data is NetCDF on S3.",
    ),

    # -----------------------------------------------------------------------
    # 10. NOAA NWM Reanalysis on AWS — Zarr V2
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="nwm-zarr",
        name="National Water Model Reanalysis v2.1",
        provider="NOAA / AWS Open Data",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://noaa-nwm-retro-v2-zarr-pds",
        open_kwargs=dict(consolidated=True, chunks={},
                         storage_options=dict(anon=True)),
        variable_hint="streamflow",
        description="NWM retrospective hourly streamflow, ~5 TB. Covers CONUS.",
        notes="May require group= kwarg to navigate hierarchy.",
    ),

    # -----------------------------------------------------------------------
    # 11. Sentinel-1 Global Coherence (kerchunk JSON, HTTP)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="s1-coherence-kerchunk",
        name="Sentinel-1 Global Coherence (kerchunk, Earth Big Data)",
        provider="Earth Big Data / ASF",
        zarr_version="kerchunk-json",
        access_protocol="https",
        store_url="https://sentinel-1-global-coherence-earthbigdata.s3.us-west-2.amazonaws.com/data/wrappers/zarr-all.json",
        open_kwargs=dict(
            engine="zarr",
            backend_kwargs=dict(
                consolidated=False,
                storage_options=dict(
                    fo="https://sentinel-1-global-coherence-earthbigdata.s3.us-west-2.amazonaws.com/data/wrappers/zarr-all.json",
                    target_protocol="http",
                    remote_protocol="http",
                ),
            ),
            chunks={},
        ),
        variable_hint="coherence",
        description="Global Sentinel-1 coherence dataset, multi-season. Virtual Zarr via kerchunk.",
    ),

    # -----------------------------------------------------------------------
    # 12. Copernicus Marine (requires toolbox, but direct Zarr endpoint test)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="copernicus-marine",
        name="Copernicus Marine Global Ocean Physics (via STAC/S3)",
        provider="Copernicus Marine / Mercator Ocean",
        zarr_version="V2",
        access_protocol="https",
        store_url="https://s3.waw3-1.cloudferro.com/mdl-native-14/native/GLOBAL_ANALYSISFORECAST_PHY_001_024/cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m_202406",
        open_kwargs=dict(consolidated=False, chunks={}),
        variable_hint="thetao",
        description="Global ocean temperature analysis/forecast. May require Copernicus Marine credentials.",
        notes="Public catalog browsable via copernicusmarine toolbox; direct Zarr access may need auth.",
    ),

    # -----------------------------------------------------------------------
    # 13. NOAA NODD Kerchunk (OFS - Delaware Bay)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="noaa-nodd-dbofs",
        name="NOAA DBOFS Operational Forecast (NODD Kerchunk)",
        provider="NOAA / IOOS / NODD",
        zarr_version="kerchunk-json",
        access_protocol="s3",
        store_url="s3://noaa-nos-ofs-pds/dbofs/nos.dbofs.fields.best.nc.zarr",
        open_kwargs=dict(
            engine="zarr",
            consolidated=False,
            chunks={},
            storage_options=dict(anon=True),
        ),
        variable_hint="temp",
        description="Delaware Bay OFS, best available forecast aggregation, cloud-optimized via kerchunk.",
        notes="Produced by RPS/Tetra Tech. New references generated within minutes of source upload.",
    ),

    # -----------------------------------------------------------------------
    # 14. ITS_LIVE Ice Velocity on AWS
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="its-live",
        name="ITS_LIVE Ice Velocity Mosaics",
        provider="NASA MEaSUREs / AWS",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://its-live-data/datacubes/v02/N60W040/ITS_LIVE_vel_EPSG32622_G0120_X-350000_Y6750000.zarr",
        open_kwargs=dict(consolidated=True, chunks={},
                         storage_options=dict(anon=True)),
        variable_hint="v",
        description="ITS_LIVE glacier velocity datacube, 120m grid, multi-sensor.",
        notes="Datacubes organized by EPSG tile. See its-live.jpl.nasa.gov for index.",
    ),

    # -----------------------------------------------------------------------
    # 15. Pangeo CMIP6 on GCS — different model (CESM2)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="cmip6-gcs-cesm2-pr",
        name="CMIP6 Precipitation (CESM2, historical, daily)",
        provider="CMIP6 / Google Cloud",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://cmip6/CMIP6/CMIP/NCAR/CESM2/historical/r1i1p1f1/day/pr/gn/v20190308/",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="pr",
        description="Daily precipitation from CESM2 historical run.",
    ),

    # -----------------------------------------------------------------------
    # 16. NASA POWER on AWS
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="nasa-power",
        name="NASA POWER Meteorology/Solar/Wind",
        provider="NASA POWER / AWS Open Data",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://power-analysis-ready-datastore/power_901_annual_meteorology_utc.zarr",
        open_kwargs=dict(consolidated=True, chunks={},
                         storage_options=dict(anon=True)),
        variable_hint="T2M",
        description="NASA POWER global meteorological parameters, annual aggregation.",
    ),

    # -----------------------------------------------------------------------
    # 17. Google Cloud CMIP6 — ocean biogeochemistry
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="cmip6-gcs-no3",
        name="CMIP6 Dissolved Nitrate (GFDL-ESM4, historical)",
        provider="CMIP6 / Google Cloud",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://cmip6/CMIP6/CMIP/NOAA-GFDL/GFDL-ESM4/historical/r1i1p1f1/Omon/no3/gn/v20190726/",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="no3",
        description="Monthly 3D dissolved nitrate concentration, historical run.",
    ),

    # -----------------------------------------------------------------------
    # 18. Google Cloud CMIP6 — high-res atmosphere (HighResMIP)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="cmip6-highres-psl",
        name="CMIP6 HighResMIP Sea Level Pressure (CMCC-CM2-HR4)",
        provider="CMIP6 / Google Cloud",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://cmip6/CMIP6/HighResMIP/CMCC/CMCC-CM2-HR4/highresSST-present/r1i1p1f1/6hrPlev/psl/gn/v20170706/",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="psl",
        description="6-hourly sea level pressure from high-resolution CMCC model.",
        gdal_dsn='ZARR:"/vsicurl/https://storage.googleapis.com/cmip6/CMIP6/HighResMIP/CMCC/CMCC-CM2-HR4/highresSST-present/r1i1p1f1/6hrPlev/psl/gn/v20170706"/:psl.zarr/',
    ),

    # -----------------------------------------------------------------------
    # 19. Weatherbench2 on GCS
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="weatherbench2-era5",
        name="WeatherBench2 ERA5 (6-hourly, 1.5°)",
        provider="Google Research / WeatherBench2",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://weatherbench2/datasets/era5/1959-2023_01_10-6h-64x32_equiangular_conservative.zarr",
        open_kwargs=dict(chunks={}),
        variable_hint="2m_temperature",
        description="ERA5 reanalysis downsampled to 1.5° for ML weather benchmarking.",
    ),

    # -----------------------------------------------------------------------
    # 20. NEX-GDDP-CMIP6 on AWS (NetCDF source for VirtualiZarr)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="nex-gddp-cmip6",
        name="NEX-GDDP-CMIP6 (bias-corrected daily, on S3)",
        provider="NASA / AWS Open Data",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://nex-gddp-cmip6/NEX-GDDP-CMIP6/ACCESS-CM2/historical/r1i1p1f1/tasmax/tasmax_day_ACCESS-CM2_historical_r1i1p1f1_gn_1950.nc",
        open_kwargs=dict(engine="h5netcdf", chunks={},
                         storage_options=dict(anon=True)),
        variable_hint="tasmax",
        description="Bias-corrected daily max temperature. Source NetCDF — good target for kerchunk/VirtualiZarr.",
        notes="Not a Zarr store itself; included to demonstrate kerchunk/VirtualiZarr pipeline source.",
    ),

    # -----------------------------------------------------------------------
    # 21. Pangeo Forge CESM-LE on OSN
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="pangeo-cesm-le",
        name="CESM Large Ensemble (Pangeo, monthly SST)",
        provider="Pangeo / NCAR / OSN",
        zarr_version="V2",
        access_protocol="https",
        store_url="https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/cesm-le-feedstock/cesm-le.zarr",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="SST",
        description="CESM Large Ensemble Community Project, various variables.",
        notes="May be large; check available variables at open time.",
    ),

    # -----------------------------------------------------------------------
    # 22. NOAA Global RTOFS (kerchunk, NODD)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="noaa-nodd-rtofs",
        name="NOAA Global RTOFS Forecast (NODD Kerchunk)",
        provider="NOAA / NODD",
        zarr_version="kerchunk-json",
        access_protocol="s3",
        store_url="s3://noaa-nws-rtofs-pds/rtofs.best.nc.zarr",
        open_kwargs=dict(
            engine="zarr",
            consolidated=False,
            chunks={},
            storage_options=dict(anon=True),
        ),
        variable_hint="temperature",
        description="Global Real-Time Ocean Forecast System, best available via NODD kerchunk.",
    ),

    # -----------------------------------------------------------------------
    # 23. Microsoft Planetary Computer — Daymet (Zarr on Azure)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="pc-daymet",
        name="Daymet V4 Daily (Planetary Computer, Azure)",
        provider="Microsoft Planetary Computer",
        zarr_version="V2",
        access_protocol="https",
        store_url="https://daymeteuwest.blob.core.windows.net/daymet-zarr/daily/na/tmax.zarr",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="tmax",
        description="Daymet V4 daily maximum temperature, North America, on Azure Blob.",
        notes="Planetary Computer also hosts ERA5, TerraClimate, and others as Zarr.",
    ),

    # -----------------------------------------------------------------------
    # 24. Microsoft Planetary Computer — ERA5 on Azure
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="pc-era5",
        name="ERA5 (Planetary Computer, Azure)",
        provider="Microsoft Planetary Computer / ECMWF",
        zarr_version="V2",
        access_protocol="https",
        store_url="https://era5euwest.blob.core.windows.net/era5-pds/era5/2020/01/air_temperature_at_2_metres.zarr",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="air_temperature_at_2_metres",
        description="ERA5 single month on Azure Blob, one variable per Zarr store.",
    ),

]


# ---------------------------------------------------------------------------
# Probe logic
# ---------------------------------------------------------------------------

def probe_xarray(entry: ZarrEntry, timeout: float = 60.0) -> ZarrEntry:
    """Try to open the Zarr store with xarray and extract basic metadata."""
    t0 = time.time()
    try:
        kw = dict(entry.open_kwargs)
        engine = kw.pop("engine", None)
        backend_kwargs = kw.pop("backend_kwargs", None)

        if entry.zarr_version.startswith("kerchunk"):
            # kerchunk / reference filesystem
            ds = xr.open_dataset(
                "reference://",
                engine=engine or "zarr",
                backend_kwargs=backend_kwargs,
                **{k: v for k, v in kw.items() if k not in ("engine", "backend_kwargs")},
            )
        elif engine and engine != "zarr":
            # e.g. h5netcdf for raw NetCDF on S3
            ds = xr.open_dataset(
                entry.store_url,
                engine=engine,
                **kw,
            )
        else:
            ds = xr.open_zarr(entry.store_url, **kw)

        entry.dims_found = {str(k): int(v) for k, v in ds.dims.items()}
        entry.status = "ok"
        if entry.variable_hint and entry.variable_hint in ds:
            shape = ds[entry.variable_hint].shape
            entry.notes += f" Variable '{entry.variable_hint}' shape={shape}."
        ds.close()

    except Exception as exc:
        entry.status = "error"
        entry.error = f"{type(exc).__name__}: {exc}"

    entry.elapsed_s = round(time.time() - t0, 2)
    return entry


def probe_gdal(entry: ZarrEntry) -> str:
    """Return gdalmdiminfo output for the entry's GDAL DSN, if set."""
    import subprocess
    if not entry.gdal_dsn:
        return ""
    try:
        result = subprocess.run(
            ["gdalmdiminfo", entry.gdal_dsn],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout[:2000] if result.returncode == 0 else f"ERROR: {result.stderr[:500]}"
    except FileNotFoundError:
        return "gdalmdiminfo not found"
    except Exception as e:
        return f"GDAL error: {e}"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(catalog):
    ok = sum(1 for e in catalog if e.status == "ok")
    err = sum(1 for e in catalog if e.status == "error")
    skip = sum(1 for e in catalog if e.status == "untested")
    print(f"\n{'='*72}")
    print(f"  Zarr Catalog Probe Report — {ok} ok / {err} error / {skip} untested")
    print(f"{'='*72}\n")
    for e in catalog:
        icon = "✅" if e.status == "ok" else "❌" if e.status == "error" else "⏭️"
        print(f"  {icon} [{e.id}] {e.name}")
        print(f"     Provider: {e.provider}  |  Version: {e.zarr_version}  |  Protocol: {e.access_protocol}")
        print(f"     URL: {e.store_url}")
        if e.status == "ok":
            print(f"     Dims: {e.dims_found}  ({e.elapsed_s}s)")
        elif e.status == "error":
            print(f"     Error: {e.error}  ({e.elapsed_s}s)")
        print()


def generate_markdown(catalog) -> str:
    """Produce a structured markdown catalog document."""
    lines = []
    lines.append("# Public Zarr Examples Catalog\n")
    lines.append("A curated corpus of publicly accessible Zarr endpoints for testing and illustration.\n")
    lines.append("Generated by `zarr_catalog.py`. Each entry has been probed with xarray.\n")
    lines.append(f"Total entries: {len(catalog)} | "
                 f"OK: {sum(1 for e in catalog if e.status == 'ok')} | "
                 f"Error: {sum(1 for e in catalog if e.status == 'error')} | "
                 f"Untested: {sum(1 for e in catalog if e.status == 'untested')}\n")

    # Group by zarr_version
    groups = {}
    for e in catalog:
        groups.setdefault(e.zarr_version, []).append(e)

    for ver, entries in groups.items():
        lines.append(f"\n## {ver} Stores\n")
        for e in entries:
            icon = "✅" if e.status == "ok" else "❌" if e.status == "error" else "⏭️"
            lines.append(f"### {icon} {e.name}\n")
            lines.append(f"- **ID**: `{e.id}`")
            lines.append(f"- **Provider**: {e.provider}")
            lines.append(f"- **Protocol**: `{e.access_protocol}`")
            lines.append(f"- **URL**: `{e.store_url}`")
            if e.description:
                lines.append(f"- **Description**: {e.description}")
            if e.variable_hint:
                lines.append(f"- **Example variable**: `{e.variable_hint}`")
            if e.status == "ok":
                lines.append(f"- **Dimensions**: {e.dims_found}")
                lines.append(f"- **Probe time**: {e.elapsed_s}s")
            elif e.status == "error":
                lines.append(f"- **Error**: `{e.error}`")
            if e.gdal_dsn:
                lines.append(f"- **GDAL DSN**: `{e.gdal_dsn}`")
            if e.notes:
                lines.append(f"- **Notes**: {e.notes.strip()}")

            # xarray snippet
            lines.append(f"\n```python")
            if e.zarr_version.startswith("kerchunk"):
                lines.append(f"import xarray as xr")
                lines.append(f'ds = xr.open_dataset("reference://", **{json.dumps(e.open_kwargs, indent=2)})')
            elif e.open_kwargs.get("engine") and e.open_kwargs["engine"] != "zarr":
                lines.append(f"import xarray as xr")
                kw = dict(e.open_kwargs)
                lines.append(f'ds = xr.open_dataset("{e.store_url}", **{json.dumps(kw, indent=2)})')
            else:
                lines.append(f"import xarray as xr")
                kw = dict(e.open_kwargs)
                lines.append(f'ds = xr.open_zarr("{e.store_url}", **{json.dumps(kw, indent=2)})')
            lines.append(f"```\n")

    # Appendix: GDAL
    gdal_entries = [e for e in catalog if e.gdal_dsn]
    if gdal_entries:
        lines.append("\n## GDAL Multidimensional CLI Examples\n")
        for e in gdal_entries:
            lines.append(f"### {e.name}\n")
            lines.append(f"```bash")
            lines.append(f"gdalmdiminfo '{e.gdal_dsn}'")
            lines.append(f"```\n")

    # Appendix: catalog interrogation
    lines.append("\n## How to Discover More Zarr Endpoints\n")
    lines.append("### CMIP6 Catalog (Google Cloud)\n")
    lines.append("```python")
    lines.append("import pandas as pd")
    lines.append('df = pd.read_csv("https://storage.googleapis.com/cmip6/pangeo-cmip6.csv")')
    lines.append("print(f'Total stores: {len(df)}')")
    lines.append("# Filter for a specific variable and experiment:")
    lines.append('subset = df.query("variable_id==\'tas\' & experiment_id==\'historical\' & table_id==\'Amon\'")')
    lines.append("print(subset[['source_id', 'zstore']].head(10))")
    lines.append("```\n")

    lines.append("### Pangeo Catalog (Intake)\n")
    lines.append("```python")
    lines.append("from intake import open_catalog")
    lines.append('cat = open_catalog("https://raw.githubusercontent.com/pangeo-data/pangeo-datastore/master/intake-catalogs/master.yaml")')
    lines.append("print(list(cat))")
    lines.append("```\n")

    lines.append("### AWS Registry of Open Data\n")
    lines.append("Search https://registry.opendata.aws/ for 'zarr' to find datasets including MUR SST, ")
    lines.append("NASA POWER, HRRR, NOAA NODD Kerchunk references, National Water Model, and more.\n")

    lines.append("### Microsoft Planetary Computer\n")
    lines.append("Browse https://planetarycomputer.microsoft.com/catalog?filter=zarr for ERA5, Daymet, ")
    lines.append("TerraClimate, and CMIP6 collections stored as Zarr on Azure.\n")

    lines.append("### NOAA NODD Kerchunk References\n")
    lines.append("See https://registry.opendata.aws/noaa-nodd-kerchunk/ for continuously updated ")
    lines.append("virtual Zarr references to NOAA operational forecast systems.\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Probe public Zarr endpoints")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--gdal", action="store_true", help="Also test GDAL multidim")
    parser.add_argument("--skip-slow", action="store_true", help="Skip entries known to be slow (>30s)")
    parser.add_argument("--ids", nargs="*", help="Only probe these IDs")
    parser.add_argument("--markdown", type=str, help="Write markdown catalog to file")
    args = parser.parse_args()

    slow_ids = {"arco-era5-single-level", "arco-era5-v3"}
    entries_to_probe = CATALOG
    if args.ids:
        entries_to_probe = [e for e in CATALOG if e.id in args.ids]
    if args.skip_slow:
        entries_to_probe = [e for e in entries_to_probe if e.id not in slow_ids]

    for entry in entries_to_probe:
        print(f"  Probing {entry.id} ...", end=" ", flush=True)
        probe_xarray(entry)
        print(f"{entry.status} ({entry.elapsed_s}s)")

    if args.json:
        print(json.dumps([asdict(e) for e in CATALOG], indent=2))
    else:
        print_report(CATALOG)

    if args.markdown:
        md = generate_markdown(CATALOG)
        with open(args.markdown, "w") as f:
            f.write(md)
        print(f"\nMarkdown catalog written to {args.markdown}")


if __name__ == "__main__":
    main()
