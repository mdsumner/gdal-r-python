#!/usr/bin/env python3
"""
zarr_catalog.py — Probe and validate public Zarr endpoints.

Each entry describes:
  - provider / catalog origin
  - zarr version (V2, V3, or virtual/kerchunk)
  - access method (direct HTTP, GCS, S3, kerchunk JSON/Parquet)
  - a lightweight "smoke test" that opens metadata only (no bulk data)

Usage:
    python zarr_catalog.py              # run all probes, print report
    python zarr_catalog.py --json       # emit JSON
    python zarr_catalog.py --gdal       # also test via GDAL multidim CLI
    python zarr_catalog.py --ids pangeo-gpcp mur-sst-aws   # specific entries
    python zarr_catalog.py --skip-slow  # skip entries known to take >30s

Requires: xarray, zarr, fsspec, gcsfs, s3fs, aiohttp
Optional: imagecodecs (for S1 coherence), planetary_computer (for PC entries),
          cftime (for CMIP6 calendar decoding)
"""

import argparse
import json
import time
import sys
import traceback
from dataclasses import dataclass, field, asdict
from typing import Optional

# Register imagecodecs numcodecs plugins if available (needed for S1 coherence)
try:
    import imagecodecs.numcodecs
    imagecodecs.numcodecs.register_codecs()
except ImportError:
    pass

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
    zarr_version: str          # "V2", "V3", "kerchunk-json", "kerchunk-parquet"
    access_protocol: str       # "https", "gs", "s3", "reference-json", "reference-parquet"
    store_url: str             # primary URL / URI
    open_kwargs: dict = field(default_factory=dict)
    variable_hint: str = ""
    description: str = ""
    notes: str = ""
    gdal_dsn: str = ""
    # --- populated by probe ---
    status: str = "untested"
    elapsed_s: float = 0.0
    error: str = ""
    dims_found: dict = field(default_factory=dict)


CATALOG = [

    # -----------------------------------------------------------------------
    # 1. PANGEO / OSN — classic Zarr V2, HTTP (consolidated)
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
        description="Global Precipitation Climatology Project daily, 1deg grid, 1996-2021.",
        gdal_dsn='ZARR:"/vsicurl/https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/gpcp-feedstock/gpcp.zarr"',
    ),

    # -----------------------------------------------------------------------
    # 2-3. CMIP6 on Google Cloud — Zarr V2, GCS anonymous
    # These stores use noleap calendars -> need decode_times=False or cftime
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="cmip6-gcs-zos",
        name="CMIP6 Sea Surface Height (GFDL-ESM4, ssp585)",
        provider="CMIP6 / Google Cloud",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://cmip6/CMIP6/ScenarioMIP/NOAA-GFDL/GFDL-ESM4/ssp585/r1i1p1f1/Omon/zos/gn/v20180701/",
        # noleap calendar: use CFDatetimeCoder for modern xarray
        open_kwargs=dict(consolidated=True, chunks={}, _needs_cftime=True),
        variable_hint="zos",
        description="Monthly sea surface height, SSP5-8.5, 2015-2100.",
    ),
    ZarrEntry(
        id="cmip6-highres-psl",
        name="CMIP6 HighResMIP Sea Level Pressure (CMCC-CM2-HR4)",
        provider="CMIP6 / Google Cloud",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://cmip6/CMIP6/HighResMIP/CMCC/CMCC-CM2-HR4/highresSST-present/r1i1p1f1/6hrPlev/psl/gn/v20170706/",
        # noleap calendar: use CFDatetimeCoder for modern xarray
        open_kwargs=dict(consolidated=True, chunks={}, _needs_cftime=True),
        variable_hint="psl",
        description="6-hourly sea level pressure from high-resolution CMCC model.",
    ),

    # -----------------------------------------------------------------------
    # ARCO-ERA5 on Google Cloud
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
        description="ERA5 hourly reanalysis, 0.25deg global, 1959-2022. Very large (~2 PB logical).",
        notes="Opening metadata can be slow (~8s) due to hundreds of variables.",
    ),
    ZarrEntry(
        id="arco-era5-v3",
        name="ARCO-ERA5 Full 37-level (dataset version 3)",
        provider="Google Research / ECMWF",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3",
        open_kwargs=dict(chunks={}, consolidated=True),
        variable_hint="2m_temperature",
        description="ERA5 full 37-level, hourly, 0.25deg — dataset version 3 (still Zarr V2 format).",
        notes="Very slow to open metadata (~90s). Test with --skip-slow.",
    ),

    # -----------------------------------------------------------------------
    # WeatherBench2
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="weatherbench2-era5",
        name="WeatherBench2 ERA5 (6-hourly, 1.5deg)",
        provider="Google Research / WeatherBench2",
        zarr_version="V2",
        access_protocol="gs",
        store_url="gs://weatherbench2/datasets/era5/1959-2023_01_10-6h-64x32_equiangular_conservative.zarr",
        open_kwargs=dict(chunks={}, consolidated=True),
        variable_hint="2m_temperature",
        description="ERA5 reanalysis downsampled to 1.5deg for ML weather benchmarking.",
    ),

    # -----------------------------------------------------------------------
    # S3 stores — use s3fs mapper for robustness
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="cmip6-s3-hfls",
        name="CMIP6 Surface Upward Latent Heat Flux (TaiESM1, 1pctCO2)",
        provider="CMIP6 / AWS S3",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://cmip6-pds/CMIP6/CMIP/AS-RCEC/TaiESM1/1pctCO2/r1i1p1f1/Amon/hfls/gn/v20200225/",
        # noleap calendar: needs cftime for time decoding
        open_kwargs=dict(consolidated=True, chunks={}, _needs_cftime=True),
        variable_hint="hfls",
        description="Monthly latent heat flux from 1pctCO2 experiment.",
    ),

    ZarrEntry(
        id="mur-sst-aws",
        name="MUR SST L4 Global (JPL, NASA)",
        provider="NASA JPL / AWS Open Data",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://mur-sst/zarr-v1",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="analysed_sst",
        description="MUR 0.01deg daily SST, ~54 TB total. 2002-present.",
        gdal_dsn='ZARR:"/vsicurl/https://mur-sst.s3.us-west-2.amazonaws.com/zarr-v1"',
    ),

    ZarrEntry(
        id="hrrr-zarr-aws",
        name="HRRR Weather Model (surface analysis, TMP)",
        provider="NOAA / AWS Open Data",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://hrrrzarr/sfc/20200801/20200801_00z_anl.zarr",
        open_kwargs=dict(consolidated=False, chunks={}, group="surface/TMP"),
        variable_hint="TMP",
        description="HRRR 3km CONUS weather model. Each variable in a subgroup.",
        notes="Use group='surface/VARNAME' to select variable.",
    ),

    ZarrEntry(
        id="its-live",
        name="ITS_LIVE Ice Velocity Datacube",
        provider="NASA MEaSUREs / AWS",
        zarr_version="V2",
        access_protocol="s3",
        # Path uses v2 (not v02). Tile/EPSG/coords vary by region.
        # Use catalog_v02.json or STAC API to discover valid tile paths.
        store_url="s3://its-live-data/datacubes/v2/N00E020/ITS_LIVE_vel_EPSG32735_G0120_X750000_Y10050000.zarr",
        open_kwargs=dict(consolidated=False, chunks={}, zarr_format=2),
        variable_hint="v",
        description="ITS_LIVE glacier velocity datacube, 120m grid, multi-sensor.",
        notes="Tile path needs verification: run fs.ls('s3://its-live-data/datacubes/v2/N00E020/').",
    ),

    ZarrEntry(
        id="nasa-power",
        name="NASA POWER Daily Meteorology (MERRA-2, spatial)",
        provider="NASA POWER / AWS Open Data",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://nasa-power/merra2/spatial/power_merra2_daily_spatial_utc.zarr",
        # no .zmetadata; force zarr v2 format detection
        open_kwargs=dict(consolidated=False, chunks={}, zarr_format=2),
        variable_hint="T2M",
        description="NASA POWER MERRA-2 daily meteorology, spatial chunking, global 0.5deg grid.",
        notes="Bucket moved from power-analysis-ready-datastore to nasa-power in 2024.",
    ),

    ZarrEntry(
        id="nwm-zarr",
        name="National Water Model Reanalysis v2.1",
        provider="NOAA / AWS Open Data",
        zarr_version="V2",
        access_protocol="s3",
        store_url="s3://noaa-nwm-retro-v2-zarr-pds",
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="streamflow",
        description="NWM retrospective hourly streamflow, ~5 TB. Covers CONUS.",
    ),

    # -----------------------------------------------------------------------
    # Kerchunk JSON references — use xr.open_dataset("reference://", ...)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="oisst-kerchunk",
        name="NOAA OISST CDR (via Kerchunk JSON reference)",
        provider="Pangeo Forge / NOAA",
        zarr_version="kerchunk-json",
        access_protocol="reference-json",
        store_url="https://ncsa.osn.xsede.org/Pangeo/pangeo-forge/pangeo-forge/aws-noaa-oisst-feedstock/aws-noaa-oisst-avhrr-only.zarr/reference.json",
        open_kwargs=dict(
            remote_protocol="s3",
            remote_options=dict(anon=True),
        ),
        variable_hint="sst",
        description="NOAA 0.25deg daily OISST AVHRR-only CDR, via kerchunk JSON reference.",
        notes="Underlying data is NetCDF on S3. remote_options={'anon': True} required.",
    ),

    ZarrEntry(
        id="s1-coherence-kerchunk",
        name="Sentinel-1 Global Coherence (kerchunk, Earth Big Data)",
        provider="Earth Big Data / ASF",
        zarr_version="kerchunk-json",
        access_protocol="reference-json",
        store_url="https://sentinel-1-global-coherence-earthbigdata.s3.us-west-2.amazonaws.com/data/wrappers/zarr-all.json",
        open_kwargs=dict(
            target_protocol="http",
            remote_protocol="http",
        ),
        variable_hint="coherence",
        description="Global Sentinel-1 coherence dataset, multi-season. Virtual Zarr via kerchunk.",
        notes="Requires: pip install imagecodecs (for imagecodecs_tiff codec).",
    ),

    # -----------------------------------------------------------------------
    # Planetary Computer — needs pystac signing
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="pc-daymet",
        name="Daymet V4 Daily (Planetary Computer, Azure)",
        provider="Microsoft Planetary Computer",
        zarr_version="V2",
        access_protocol="pc-azure",
        store_url="https://daymeteuwest.blob.core.windows.net/daymet-zarr/daily/na.zarr",
        open_kwargs=dict(consolidated=False, chunks={}, zarr_format=2),
        variable_hint="tmax",
        description="Daymet V4 daily maximum temperature, North America, on Azure Blob.",
        notes="Requires planetary_computer and adlfs. pip install planetary-computer adlfs",
    ),
    ZarrEntry(
        id="pc-era5",
        name="ERA5 (Planetary Computer, Azure) [RETIRED]",
        provider="Microsoft Planetary Computer / ECMWF",
        zarr_version="V2",
        access_protocol="auth-required",  # skip: storage account gone
        store_url="https://era5euwest.blob.core.windows.net/era5-pds/era5/2020/01/air_temperature_at_2_metres.zarr",
        open_kwargs=dict(consolidated=False, chunks={}),
        variable_hint="air_temperature_at_2_metres",
        description="ERA5 single month on Azure, one variable per Zarr store.",
        notes="RETIRED: era5euwest storage account no longer resolves. Use arco-era5 on GCS instead.",
    ),

    # -----------------------------------------------------------------------
    # Copernicus Marine — ARCO stores (no auth, direct HTTPS)
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="copernicus-marine-sla",
        name="Copernicus Marine Sea Level (ARCO timeChunked)",
        provider="Copernicus Marine / Mercator Ocean",
        zarr_version="V2",
        access_protocol="https",
        # ARCO timeChunked: spatial slices (1×720×512 in time×lat×lon)
        # Also available: geoChunked (138×32×64) for time series at a point
        store_url="https://s3.waw3-1.cloudferro.com/mdl-arco-time-045/arco/SEALEVEL_GLO_PHY_L4_MY_008_047/cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1D_202411/timeChunked.zarr",
        # Has .zmetadata so consolidated=True works; no S3 LIST needed
        open_kwargs=dict(consolidated=True, chunks={}),
        variable_hint="sla",
        description="Sea level anomaly, 0.125deg global, daily. ARCO timeChunked for spatial access.",
        notes=(
            "No auth needed for ARCO stores (mdl-arco-time-*, mdl-arco-geo-*). "
            "Native stores (mdl-native-*) need Copernicus Marine credentials. "
            "Use STAC catalog stac.marine.copernicus.eu for URL discovery. "
            "See hypertidy/cmemsarco R package for GDAL-based access patterns."
        ),
    ),

    # -----------------------------------------------------------------------
    # NetCDF source for kerchunk/VirtualiZarr demonstration
    # -----------------------------------------------------------------------
    ZarrEntry(
        id="nex-gddp-cmip6",
        name="NEX-GDDP-CMIP6 (bias-corrected daily, NetCDF on S3)",
        provider="NASA / AWS Open Data",
        zarr_version="netcdf-source",
        access_protocol="s3-netcdf",
        store_url="s3://nex-gddp-cmip6/NEX-GDDP-CMIP6/ACCESS-CM2/historical/r1i1p1f1/tasmax/tasmax_day_ACCESS-CM2_historical_r1i1p1f1_gn_1950.nc",
        open_kwargs={},
        variable_hint="tasmax",
        description="Bias-corrected daily max temperature. Source NetCDF for kerchunk/VirtualiZarr.",
        notes="Not a Zarr store. Open with fsspec + h5netcdf.",
    ),

]


# ---------------------------------------------------------------------------
# Probe logic
# ---------------------------------------------------------------------------

def _open_s3_zarr(url, **kwargs):
    """Open an S3 Zarr store via s3fs mapper."""
    import s3fs
    fs = s3fs.S3FileSystem(anon=True)
    mapper = fs.get_mapper(url)
    return xr.open_zarr(mapper, **kwargs)


def _open_reference_json(fo, remote_protocol="s3", remote_options=None,
                         target_protocol=None, target_options=None):
    """Open a kerchunk JSON reference store."""
    storage_options = dict(fo=fo, remote_protocol=remote_protocol)
    if remote_options:
        storage_options["remote_options"] = remote_options
    if target_protocol:
        storage_options["target_protocol"] = target_protocol
    if target_options:
        storage_options["target_options"] = target_options
    return xr.open_dataset(
        "reference://", engine="zarr",
        backend_kwargs=dict(consolidated=False, storage_options=storage_options),
        chunks={}
    )


def _open_reference_parquet(fo, remote_protocol="s3", remote_options=None,
                            target_protocol=None, target_options=None):
    """Open a kerchunk Parquet reference store (e.g. NODD)."""
    storage_options = dict(fo=fo, remote_protocol=remote_protocol)
    if remote_options:
        storage_options["remote_options"] = remote_options
    if target_protocol:
        storage_options["target_protocol"] = target_protocol
    if target_options:
        storage_options["target_options"] = target_options
    return xr.open_dataset(
        "reference://", engine="zarr",
        backend_kwargs=dict(consolidated=False, storage_options=storage_options),
        chunks={}
    )


def _open_netcdf_s3(url):
    """Open a NetCDF file on S3 via fsspec file handle."""
    import s3fs
    fs = s3fs.S3FileSystem(anon=True)
    f = fs.open(url)
    return xr.open_dataset(f, engine="h5netcdf", chunks={})


def _open_pc_zarr(url, **kwargs):
    """Open a Planetary Computer Zarr store via adlfs with SAS token.

    Signs one URL to extract the SAS token, then passes it directly
    to AzureBlobFileSystem so every request is authenticated.
    """
    import planetary_computer
    import adlfs
    from urllib.parse import urlparse, parse_qs

    # Sign a URL to extract the SAS token
    signed_url = planetary_computer.sign(url)
    parsed_signed = urlparse(signed_url)
    sas_token = parsed_signed.query  # everything after '?'

    # Parse original URL for account/container/path
    parsed = urlparse(url)
    account_name = parsed.hostname.split(".")[0]
    parts = parsed.path.lstrip("/").split("/", 1)
    container = parts[0]
    path = parts[1] if len(parts) > 1 else ""

    fs = adlfs.AzureBlobFileSystem(account_name=account_name, sas_token=sas_token)
    store_path = f"{container}/{path}" if path else container
    mapper = fs.get_mapper(store_path)
    return xr.open_zarr(mapper, **kwargs)


def probe_xarray(entry: ZarrEntry) -> ZarrEntry:
    """Try to open the Zarr store with xarray and extract basic metadata."""
    t0 = time.time()
    try:
        kw = dict(entry.open_kwargs)

        # --- Suppress timedelta FutureWarning ---
        # xarray will change timedelta decoding default; be explicit
        kw.setdefault("decode_timedelta", False)

        # --- Handle _needs_cftime flag ---
        # Modern xarray uses CFDatetimeCoder; older uses use_cftime kwarg
        if kw.pop("_needs_cftime", False):
            try:
                kw["decode_times"] = xr.coders.CFDatetimeCoder(use_cftime=True)
            except AttributeError:
                # older xarray without coders module
                kw["use_cftime"] = True

        # --- Dispatch by access_protocol ---

        if entry.access_protocol == "auth-required":
            entry.status = "skip-auth"
            entry.elapsed_s = round(time.time() - t0, 2)
            return entry

        elif entry.access_protocol == "pc-azure":
            try:
                ds = _open_pc_zarr(entry.store_url, **kw)
            except ImportError:
                entry.status = "skip-deps"
                entry.error = "planetary_computer and/or adlfs not installed"
                entry.elapsed_s = round(time.time() - t0, 2)
                return entry

        elif entry.access_protocol == "reference-json":
            remote_protocol = kw.pop("remote_protocol", "s3")
            remote_options = kw.pop("remote_options", None)
            target_protocol = kw.pop("target_protocol", None)
            target_options = kw.pop("target_options", None)
            ds = _open_reference_json(
                entry.store_url,
                remote_protocol=remote_protocol,
                remote_options=remote_options,
                target_protocol=target_protocol,
                target_options=target_options,
            )

        elif entry.access_protocol == "reference-parquet":
            remote_protocol = kw.pop("remote_protocol", "s3")
            remote_options = kw.pop("remote_options", None)
            target_protocol = kw.pop("target_protocol", None)
            target_options = kw.pop("target_options", None)
            ds = _open_reference_parquet(
                entry.store_url,
                remote_protocol=remote_protocol,
                remote_options=remote_options,
                target_protocol=target_protocol,
                target_options=target_options,
            )

        elif entry.access_protocol == "s3-netcdf":
            ds = _open_netcdf_s3(entry.store_url)

        elif entry.access_protocol == "s3":
            ds = _open_s3_zarr(entry.store_url, **kw)

        elif entry.access_protocol in ("gs", "https"):
            ds = xr.open_zarr(entry.store_url, **kw)

        else:
            entry.status = "skip-unknown"
            entry.error = f"Unknown access_protocol: {entry.access_protocol}"
            entry.elapsed_s = round(time.time() - t0, 2)
            return entry

        # --- Extract metadata ---
        entry.dims_found = {str(k): int(v) for k, v in ds.sizes.items()}
        entry.status = "ok"
        if entry.variable_hint and entry.variable_hint in ds:
            shape = ds[entry.variable_hint].shape
            entry.notes += f" Variable '{entry.variable_hint}' shape={shape}."
        ds.close()

    except ImportError as exc:
        entry.status = "skip-deps"
        entry.error = f"{type(exc).__name__}: {exc}"
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

STATUS_ICONS = {
    "ok": "OK",
    "error": "FAIL",
    "skip-auth": "SKIP(auth)",
    "skip-deps": "SKIP(deps)",
    "skip-unknown": "SKIP(?)",
    "untested": "----",
}


def print_report(catalog):
    ok = sum(1 for e in catalog if e.status == "ok")
    err = sum(1 for e in catalog if e.status == "error")
    skip = sum(1 for e in catalog if e.status.startswith("skip"))
    untested = sum(1 for e in catalog if e.status == "untested")
    total = len(catalog)
    print(f"\n{'='*72}")
    print(f"  Zarr Catalog Probe Report")
    print(f"  {ok} ok / {err} error / {skip} skipped / {untested} untested (of {total})")
    print(f"{'='*72}\n")
    for e in catalog:
        icon = STATUS_ICONS.get(e.status, e.status)
        print(f"  [{icon:>10}] {e.id}")
        print(f"             {e.name}")
        print(f"             {e.provider} | {e.zarr_version} | {e.access_protocol}")
        print(f"             {e.store_url}")
        if e.status == "ok":
            print(f"             dims={e.dims_found}  ({e.elapsed_s}s)")
        elif e.status == "error":
            print(f"             {e.error}")
            print(f"             ({e.elapsed_s}s)")
        if e.notes.strip():
            print(f"             note: {e.notes.strip()}")
        print()


def emit_markdown(catalog):
    """Print a Markdown summary table."""
    print("| Status | ID | Provider | Protocol | Time |")
    print("|--------|----|----------|----------|------|")
    for e in catalog:
        icon = {"ok": "✅", "error": "❌"}.get(e.status, "⏭️")
        t = f"{e.elapsed_s}s" if e.elapsed_s else "-"
        print(f"| {icon} | `{e.id}` | {e.provider} | {e.access_protocol} | {t} |")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Probe public Zarr endpoints")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--gdal", action="store_true", help="Also test GDAL multidim")
    parser.add_argument("--skip-slow", action="store_true",
                        help="Skip entries known to be slow (>30s)")
    parser.add_argument("--ids", nargs="*", help="Only probe these IDs")
    parser.add_argument("--markdown", action="store_true", help="Emit Markdown table")
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
    elif args.markdown:
        emit_markdown(CATALOG)
    else:
        print_report(CATALOG)

    if args.gdal:
        print("\n--- GDAL Multidim Probes ---\n")
        for e in CATALOG:
            if e.gdal_dsn:
                print(f"  {e.id}: {e.gdal_dsn}")
                out = probe_gdal(e)
                print(f"  {out[:500]}\n")


if __name__ == "__main__":
    main()
