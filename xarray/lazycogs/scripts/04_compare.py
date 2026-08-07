"""Step 4: compare the three results pixel-for-pixel and summarise timings.

Reads result_{lazycogs,odc-stac,gdal}.nc plus timings_*.json from the
output directory, aligns on common days, and reports per band/day:

  - exact-match fraction over all pixels
  - exact-match fraction over pixels valid in both
  - valid-mask disagreement counts

Writes comparison.md next to the inputs.

Usage:
    python 04_compare.py
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import xarray as xr

from common import load_config

TOOLS = ["lazycogs", "odc-stac", "gdal"]


def main() -> None:
    cfg = load_config()
    out = Path(cfg.out_dir)

    data: dict[str, xr.DataArray] = {}
    for tool in TOOLS:
        p = out / f"result_{tool}.nc"
        if not p.exists():
            print(f"!! missing {p}, skipping {tool}")
            continue
        data[tool] = xr.open_dataset(p)["data"].load()

    if len(data) < 2:
        raise SystemExit("need at least two results to compare")

    # align on the intersection of days
    common_days = None
    for da in data.values():
        days = set(np.asarray(da["time"].values))
        common_days = days if common_days is None else common_days & days
    common_days = sorted(common_days)
    for tool in data:
        data[tool] = data[tool].sel(time=common_days)
        print(f"{tool}: shape={tuple(data[tool].shape)}")

    nodata = cfg.nodata
    lines = [
        "# Result comparison",
        "",
        f"- days compared: {len(common_days)}",
        f"- bands: {list(cfg.bands)}",
        f"- nodata: {nodata}",
        "",
    ]

    for a, b in itertools.combinations(data, 2):
        da, db = data[a], data[b]
        if da.shape != db.shape:
            lines.append(f"## {a} vs {b}: SHAPE MISMATCH {da.shape} vs {db.shape}")
            continue
        lines += [f"## {a} vs {b}", "",
                  "| band | day | match (all px) | match (both valid) | "
                  f"only {a} valid | only {b} valid |",
                  "|---|---|---|---|---|---|"]
        for band in da["band"].values:
            for day in common_days:
                x = da.sel(band=band, time=day).values
                y = db.sel(band=band, time=day).values
                vx, vy = x != nodata, y != nodata
                both = vx & vy
                n = x.size
                match_all = (x == y).sum() / n
                match_valid = (
                    (x[both] == y[both]).mean() if both.any() else np.nan
                )
                lines.append(
                    f"| {band} | {np.datetime_as_string(day, unit='D')} "
                    f"| {match_all:.6f} | {match_valid:.6f} "
                    f"| {(vx & ~vy).sum()} | {(vy & ~vx).sum()} |"
                )
        # overall
        x, y = da.values, db.values
        vx, vy = x != nodata, y != nodata
        both = vx & vy
        lines += [
            "",
            f"overall: match(all)={np.mean(x == y):.6f}  "
            f"match(both valid)={np.mean(x[both] == y[both]):.6f}  "
            f"mask-disagreement={(vx != vy).sum()} px",
            "",
        ]

    # timings
    lines += ["## Timings", "", "| tool | phase | seconds |", "|---|---|---|"]
    for tool in TOOLS:
        p = out / f"timings_{tool}.json"
        if not p.exists():
            continue
        t = json.loads(p.read_text())
        for phase, secs in t["phases"].items():
            lines.append(f"| {t['tool']} | {phase} | {secs:.2f} |")
        lines.append(f"| {t['tool']} | **total** | **{t['total']:.2f}** |")

    report = "\n".join(lines) + "\n"
    (out / "comparison.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
