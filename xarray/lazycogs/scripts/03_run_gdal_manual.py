"""Step 3: the same task done manually against the GDAL warp API.

For each (UTC day, band) this warps every matching COG into the common
target grid with osgeo.gdal.Warp. Passing multiple sources to one Warp call
composites them in order with "last valid pixel wins"; lazycogs' FirstMethod
and odc-stac's paste loop are "first valid wins", so sources are REVERSED
here to make the mosaics comparable.

Everything the xarray front-ends do implicitly has to be spelled out:
grouping items into days, pairing assets to bands, mosaic order, nodata
handling, and assembling the (band, time, y, x) cube.

Usage:
    python 03_run_gdal_manual.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import xarray as xr
from osgeo import gdal

from common import PhaseTimer, dst_bbox_from_aoi, dst_grid, item_day, load_config, save_result

gdal.UseExceptions()

# Sensible defaults for HTTP range reads of COGs.
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": str(64 * 1024 * 1024),
}


def gdal_path(href: str) -> str:
    if href.startswith(("http://", "https://")):
        return "/vsicurl/" + href
    if href.startswith("file://"):
        return href[len("file://"):]
    return href


def warp_one(
    sources: list[str],
    bbox: tuple[float, float, float, float],
    cfg,
    width: int,
    height: int,
) -> np.ndarray:
    """Warp+mosaic one (day, band) group of COGs onto the target grid."""
    kwargs = {}
    if cfg.gdal_error_threshold is not None:
        # 0 => exact transformer; default (None) keeps gdalwarp's 0.125 px
        # approximated transformer.
        kwargs["errorThreshold"] = cfg.gdal_error_threshold
    if cfg.gdal_overview_level != "AUTO":
        kwargs["overviewLevel"] = cfg.gdal_overview_level
    out = gdal.Warp(
        "",
        sources,
        format="MEM",
        dstSRS=cfg.dst_crs,
        outputBounds=bbox,
        xRes=cfg.resolution,
        yRes=cfg.resolution,
        resampleAlg="near",
        srcNodata=cfg.nodata,
        dstNodata=cfg.nodata,
        outputType=gdal.GetDataTypeByName(
            {"uint8": "Byte", "uint16": "UInt16", "int16": "Int16",
             "float32": "Float32", "float64": "Float64"}[cfg.dtype]
        ),
        multithread=True,
        warpOptions=["NUM_THREADS=ALL_CPUS"],
        **kwargs,
    )
    arr = out.GetRasterBand(1).ReadAsArray()
    assert arr.shape == (height, width)
    return arr


def main() -> None:
    cfg = load_config()
    timer = PhaseTimer("gdal")
    for k, v in GDAL_ENV.items():
        gdal.SetConfigOption(k, v)

    with timer.phase("read_items"):
        fc = json.loads(Path(cfg.items_json).read_text())
        features = fc["features"]

    # --- group items by UTC day, keep acquisition order within the day ----
    by_day: dict[str, list[dict]] = defaultdict(list)
    for f in features:  # features are pre-sorted by datetime in step 00
        by_day[item_day(f)].append(f)
    days = sorted(by_day)
    print(f"[gdal] {len(features)} items across {len(days)} days")

    bbox = dst_bbox_from_aoi(cfg)
    transform, width, height = dst_grid(cfg)

    # --- warp every (day, band) group ------------------------------------
    def task(day: str, band: str) -> tuple[str, str, np.ndarray]:
        hrefs = [
            gdal_path(f["assets"][band]["href"])
            for f in by_day[day]
            if band in f["assets"]
        ]
        # Reverse: gdal.Warp is last-valid-wins, we want first-valid-wins.
        arr = warp_one(hrefs[::-1], bbox, cfg, width, height)
        return day, band, arr

    with timer.phase("warp_all"):
        jobs = [(d, b) for d in days for b in cfg.bands]
        results: dict[tuple[str, str], np.ndarray] = {}
        with ThreadPoolExecutor(max_workers=cfg.gdal_workers) as pool:
            for day, band, arr in pool.map(lambda j: task(*j), jobs):
                results[(day, band)] = arr

    # --- assemble (band, time, y, x) cube --------------------------------
    with timer.phase("assemble"):
        cube = np.stack(
            [
                np.stack([results[(d, b)] for d in days], axis=0)
                for b in cfg.bands
            ],
            axis=0,
        )
        xs = transform.c + (np.arange(width) + 0.5) * transform.a
        ys = transform.f + (np.arange(height) + 0.5) * transform.e
        da = xr.DataArray(
            cube,
            dims=("band", "time", "y", "x"),
            coords={
                "band": list(cfg.bands),
                "time": np.array(days, dtype="datetime64[ns]"),
                "y": ys,
                "x": xs,
            },
        )

    with timer.phase("write"):
        save_result(da, cfg, "gdal")

    timer.write(cfg.out_dir, extra={"shape": [int(s) for s in da.shape]})


if __name__ == "__main__":
    main()
