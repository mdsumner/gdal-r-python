"""Shared configuration and helpers for the lazycogs / odc-stac / GDAL comparison.

All three runners consume the same item set (produced by 00_search_items.py)
and target exactly the same output grid, so results are directly comparable
pixel-for-pixel.

Configuration can be overridden by pointing the environment variable
BENCH_CONFIG at a JSON file whose keys mirror the Config fields.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path

from affine import Affine
from pyproj import CRS, Transformer

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "outputs"


@dataclass
class Config:
    # --- STAC search -------------------------------------------------------
    stac_api: str = "https://earth-search.aws.element84.com/v1"
    collection: str = "sentinel-2-c1-l2a"
    datetime: str = "2026-01-01/2026-01-31"
    # AOI in lon/lat; the default straddles 144E (UTM zone 54/55 boundary)
    # over agricultural land west of Melbourne, so the mosaic genuinely
    # crosses UTM zones.
    aoi_lonlat: tuple[float, float, float, float] = (143.7, -37.9, 144.3, -37.4)
    max_cloud_cover: float | None = None  # e.g. 60.0 to filter

    # --- target grid -------------------------------------------------------
    dst_crs: str = "EPSG:3577"  # GDA94 Australian Albers
    resolution: float = 20.0
    bands: tuple[str, ...] = ("red", "nir")
    nodata: float = 0
    dtype: str = "uint16"

    # --- tool knobs --------------------------------------------------------
    lazycogs_chunks: dict | None = field(default_factory=lambda: {"time": 1})
    lazycogs_max_concurrent_reads: int = 32
    odc_chunks: dict = field(
        default_factory=lambda: {"time": 1, "x": 1024, "y": 1024}
    )
    gdal_workers: int = 4
    gdal_error_threshold: float | None = None  # None => GDAL default (0.125)
    # "AUTO" (gdalwarp default), "NONE" (force full-res reads), or an int
    # overview level. Near integer resolution ratios AUTO can pick a
    # different level than lazycogs/odc's strict never-coarser-than-target
    # policies -- see the assessment report. "NONE" plus
    # gdal_error_threshold=0 makes the GDAL runner byte-comparable with
    # lazycogs whenever the other two read full resolution.
    gdal_overview_level: str | int = "AUTO"

    # --- paths -------------------------------------------------------------
    out_dir: str = str(DEFAULT_OUT)
    items_parquet: str = str(DEFAULT_OUT / "items.parquet")
    items_json: str = str(DEFAULT_OUT / "items.json")


def load_config() -> Config:
    cfg = Config()
    override = os.environ.get("BENCH_CONFIG")
    if override:
        data = json.loads(Path(override).read_text())
        for k, v in data.items():
            if not hasattr(cfg, k):
                raise KeyError(f"Unknown config key {k!r}")
            setattr(cfg, k, v)
    Path(cfg.out_dir).mkdir(parents=True, exist_ok=True)
    return cfg


# --------------------------------------------------------------------------
# Grid helpers: single source of truth for the target grid, using the same
# convention as lazycogs.compute_output_grid (origin at top-left corner,
# negative y scale, width/height by round()).
# --------------------------------------------------------------------------

def dst_bbox_from_aoi(cfg: Config) -> tuple[float, float, float, float]:
    """AOI lon/lat -> bbox in dst CRS, snapped outward to the resolution."""
    t = Transformer.from_crs(
        CRS.from_epsg(4326), CRS.from_user_input(cfg.dst_crs), always_xy=True
    )
    lons = [cfg.aoi_lonlat[0], cfg.aoi_lonlat[2]]
    lats = [cfg.aoi_lonlat[1], cfg.aoi_lonlat[3]]
    xs, ys = t.transform(
        [lons[0], lons[1], lons[0], lons[1]],
        [lats[0], lats[0], lats[1], lats[1]],
    )
    r = cfg.resolution
    import math

    minx = math.floor(min(xs) / r) * r
    miny = math.floor(min(ys) / r) * r
    maxx = math.ceil(max(xs) / r) * r
    maxy = math.ceil(max(ys) / r) * r
    return (minx, miny, maxx, maxy)


def bbox_4326(cfg: Config) -> list[float]:
    """Search bbox in EPSG:4326 derived from the dst-CRS bbox (not the raw
    AOI), so the STAC query covers exactly what the grid covers."""
    bbox = dst_bbox_from_aoi(cfg)
    t = Transformer.from_crs(
        CRS.from_user_input(cfg.dst_crs), CRS.from_epsg(4326), always_xy=True
    )
    xs, ys = t.transform(
        [bbox[0], bbox[2], bbox[0], bbox[2]],
        [bbox[1], bbox[1], bbox[3], bbox[3]],
    )
    return [min(xs), min(ys), max(xs), max(ys)]


def dst_grid(cfg: Config) -> tuple[Affine, int, int]:
    """(transform, width, height) of the common target grid."""
    minx, miny, maxx, maxy = dst_bbox_from_aoi(cfg)
    r = cfg.resolution
    width = max(1, round((maxx - minx) / r))
    height = max(1, round((maxy - miny) / r))
    transform = Affine(r, 0.0, minx, 0.0, -r, maxy)
    return transform, width, height


# --------------------------------------------------------------------------
# Timing + result persistence
# --------------------------------------------------------------------------

class PhaseTimer:
    """Collects named wall-clock phases and writes them to JSON."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        self.phases: dict[str, float] = {}
        self._t0 = time.perf_counter()

    @contextmanager
    def phase(self, name: str):
        t = time.perf_counter()
        yield
        self.phases[name] = time.perf_counter() - t
        print(f"[{self.tool}] {name}: {self.phases[name]:.2f}s", flush=True)

    def write(self, out_dir: str | Path, extra: dict | None = None) -> None:
        payload = {
            "tool": self.tool,
            "phases": self.phases,
            "total": time.perf_counter() - self._t0,
        }
        if extra:
            payload.update(extra)
        p = Path(out_dir) / f"timings_{self.tool}.json"
        p.write_text(json.dumps(payload, indent=2))
        print(f"[{self.tool}] total: {payload['total']:.2f}s -> {p}", flush=True)


def save_result(da, cfg: Config, tool: str) -> None:
    """Persist a (band, time, y, x) uint16 DataArray to NetCDF.

    The time coordinate is normalised to date precision so the comparison
    script can align lazycogs (P1D labels), odc (solar_day labels) and the
    manual GDAL loop (UTC-date labels) without fuzz.
    """
    import numpy as np
    import xarray as xr

    da = da.transpose("band", "time", "y", "x")
    days = da["time"].values.astype("datetime64[D]").astype("datetime64[ns]")
    da = da.assign_coords(time=days)
    ds = xr.Dataset({"data": da.astype(cfg.dtype)})
    # Drop non-serialisable / tool-specific attrs; keep the essentials.
    ds["data"].attrs = {"nodata": cfg.nodata}
    enc = {"data": {"zlib": True, "complevel": 1}}
    path = Path(cfg.out_dir) / f"result_{tool}.nc"
    ds.to_netcdf(path, encoding=enc)
    print(f"[{tool}] wrote {path} shape={tuple(da.shape)}")


def item_day(item: dict) -> str:
    """UTC calendar day of a STAC item dict, e.g. '2026-01-14'.

    Matches lazycogs' default time_period='P1D' bucketing. For Sentinel-2
    over Australia the UTC day is also the solar day (acquisitions are
    ~00:00-01:00 UTC), so odc's groupby='solar_day' produces the same
    grouping; this does NOT hold everywhere on the globe.
    """
    dt = item["properties"]["datetime"]
    return dt[:10]
