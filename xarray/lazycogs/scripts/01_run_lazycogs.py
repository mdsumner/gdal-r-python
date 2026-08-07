"""Step 1: load the (band, time, y, x) stack with lazycogs.

lazycogs reads the stac-geoparquet file directly; each chunk read issues its
own DuckDB spatial/temporal query, then reads COG windows with async-geotiff
and reprojects with its pyproj+numpy nearest-neighbour warp map.

Usage:
    python 01_run_lazycogs.py
"""

from __future__ import annotations

import lazycogs

from common import PhaseTimer, dst_bbox_from_aoi, load_config, save_result


def main() -> None:
    cfg = load_config()
    timer = PhaseTimer("lazycogs")
    bbox = dst_bbox_from_aoi(cfg)

    with timer.phase("open"):
        da = lazycogs.open(
            cfg.items_parquet,
            bbox=bbox,
            crs=cfg.dst_crs,
            resolution=cfg.resolution,
            bands=list(cfg.bands),
            datetime=cfg.datetime,
            # Explicit contract so all three tools agree by construction
            # rather than by inference:
            dtype=cfg.dtype,
            nodata=cfg.nodata,
            # Default P1D time grouping and FirstMethod mosaic (first valid
            # pixel in item order wins) are what the other runners emulate.
            time_period="P1D",
            chunks=cfg.lazycogs_chunks,
            max_concurrent_reads=cfg.lazycogs_max_concurrent_reads,
        )
    print(f"[lazycogs] lazy array: dims={da.dims} shape={tuple(da.shape)}")

    with timer.phase("compute"):
        da = da.compute()

    with timer.phase("write"):
        save_result(da, cfg, "lazycogs")

    timer.write(cfg.out_dir, extra={"shape": [int(s) for s in da.shape]})


if __name__ == "__main__":
    main()
