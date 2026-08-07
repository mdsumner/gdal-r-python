"""Step 0: run one STAC search and persist the item set two ways.

- items.parquet  (stac-geoparquet) -> consumed by lazycogs
- items.json     (FeatureCollection) -> consumed by the odc-stac and GDAL
  runners, so every tool operates on the *identical* item set.

Usage:
    python 00_search_items.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import rustac

from common import load_config, bbox_4326


async def main() -> None:
    cfg = load_config()
    search_bbox = bbox_4326(cfg)
    flt = (
        f"eo:cloud_cover < {cfg.max_cloud_cover}"
        if cfg.max_cloud_cover is not None
        else None
    )
    print(f"search {cfg.collection} {cfg.datetime} bbox={search_bbox}")

    await rustac.search_to(
        cfg.items_parquet,
        cfg.stac_api,
        collections=[cfg.collection],
        datetime=cfg.datetime,
        bbox=search_bbox,
        filter=flt,
    )

    # Read the parquet back so the JSON view is guaranteed to match it.
    value = await rustac.read(cfg.items_parquet)
    features = value["features"] if isinstance(value, dict) else value
    # Deterministic order for the non-parquet consumers: acquisition time,
    # then id. (lazycogs orders per-chunk items with its own DuckDB query;
    # see the report for how mosaic order interacts with results.)
    features.sort(key=lambda f: (f["properties"]["datetime"], f["id"]))
    Path(cfg.items_json).write_text(
        json.dumps({"type": "FeatureCollection", "features": features})
    )
    days = sorted({f["properties"]["datetime"][:10] for f in features})
    print(
        f"wrote {cfg.items_parquet} and {cfg.items_json}: "
        f"{len(features)} items across {len(days)} days"
    )


if __name__ == "__main__":
    asyncio.run(main())
