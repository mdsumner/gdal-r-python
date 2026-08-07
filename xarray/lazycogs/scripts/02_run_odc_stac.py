"""Step 2: load the same stack with odc-stac.

odc-stac builds a dask graph over the item set; each task reads an asset
with rasterio and either pastes (grids align) or reprojects with
rasterio.warp.reproject -- i.e. the GDAL warp API with its default
approximated transformer (tolerance 0.125 source pixels).

Usage:
    python 02_run_odc_stac.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pystac
from odc.geo.geobox import GeoBox
from odc import stac as odc_stac

from common import PhaseTimer, dst_grid, load_config, save_result


def main() -> None:
    cfg = load_config()
    timer = PhaseTimer("odc-stac")

    with timer.phase("read_items"):
        fc = json.loads(Path(cfg.items_json).read_text())
        items = [pystac.Item.from_dict(f) for f in fc["features"]]
    print(f"[odc-stac] {len(items)} items")

    transform, width, height = dst_grid(cfg)
    geobox = GeoBox((height, width), transform, cfg.dst_crs)

    # Force the band contract (dtype/nodata) instead of relying on STAC
    # metadata, mirroring the explicit dtype=/nodata= given to lazycogs.
    stac_cfg = {
        "*": {"assets": {"*": {"data_type": cfg.dtype, "nodata": cfg.nodata}}}
    }

    with timer.phase("load_graph"):
        ds = odc_stac.load(
            items,
            bands=list(cfg.bands),
            geobox=geobox,
            groupby="solar_day",
            resampling="nearest",
            chunks=cfg.odc_chunks,
            stac_cfg=stac_cfg,
        )

    with timer.phase("compute"):
        ds = ds.compute()

    with timer.phase("write"):
        da = ds.to_array(dim="band")
        save_result(da, cfg, "odc-stac")

    timer.write(cfg.out_dir, extra={"shape": [int(s) for s in da.shape]})


if __name__ == "__main__":
    main()
