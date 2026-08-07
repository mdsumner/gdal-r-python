# lazycogs vs odc-stac vs manual GDAL: comparison harness

Companion code for `assessment.md`. Runs the same task three ways against
real Sentinel-2 C1 L2A COGs on AWS and compares timings and pixel-level
results:

- `01_run_lazycogs.py`  - lazycogs.open on a stac-geoparquet index
- `02_run_odc_stac.py`  - odc.stac.load on the identical item set
- `03_run_gdal_manual.py` - hand-driven osgeo.gdal.Warp mosaic loop
- `04_compare.py`       - pixel-for-pixel agreement + timing summary

The task: one month of imagery over an AOI that straddles the UTM 54/55
zone boundary (144E, west of Melbourne), loaded to a common 20 m EPSG:3577
grid as a (band, time, y, x) cube of per-UTC-day first-valid mosaics,
bands red + nir, explicit dtype=uint16 and nodata=0 everywhere.

## Setup

Python >= 3.12 (lazycogs requirement). GDAL Python bindings must match your
libgdal; everything else is pip-installable:

    python -m venv env && . env/bin/activate
    pip install -r requirements.txt
    pip install "gdal==$(gdal-config --version)"

## Run

    cd scripts
    ./run_all.sh          # or run the five scripts individually

Step 00 runs one STAC search (earth-search) and persists the item set twice:
`items.parquet` (stac-geoparquet, consumed by lazycogs) and `items.json`
(the same features, consumed by the odc and GDAL runners). All tools
therefore see the identical item list. Outputs land in `../outputs/`:
`result_<tool>.nc`, `timings_<tool>.json`, and `comparison.md`.

## Configuration

Defaults live in `scripts/common.py` (`Config`). Override any field by
pointing `BENCH_CONFIG` at a JSON file, e.g.:

    {
      "datetime": "2026-02-01/2026-02-28",
      "resolution": 40.0,
      "bands": ["red", "green", "nir"],
      "max_cloud_cover": 60.0,
      "gdal_error_threshold": 0,
      "gdal_overview_level": "NONE"
    }

## Reading the results

Do not expect 100 percent pixel agreement with default settings; the
differences are structural and explained in `assessment.md` section 2.2:

- odc-stac (and gdalwarp by default) use GDAL's approximated transformer
  (0.125 source-pixel tolerance), lazycogs transforms every pixel exactly.
  Expect roughly 3-4 percent nearest-neighbour pixel flips between them on
  a UTM -> Albers warp. Set `gdal_error_threshold: 0` to remove this for
  the GDAL runner.
- Near integer resolution ratios (e.g. a 20 m Albers grid over 10 m UTM
  sources, where the target pixel measures ~19.8 m in the source CRS) the
  engines can pick different overview levels: lazycogs and odc never read
  an overview coarser than the target, gdalwarp AUTO estimates and may
  round the other way. Set `gdal_overview_level: "NONE"` to pin the GDAL
  runner to full-resolution reads.
- With both knobs set, lazycogs and the manual GDAL mosaic are expected to
  be bit-identical (verified on a local multi-zone fixture; see the
  assessment appendix).
- Mosaic order: gdal.Warp composites last-valid-wins, so the GDAL runner
  reverses each day's source list to emulate the first-valid-wins
  semantics of lazycogs' FirstMethod and odc's paste loop. Within-day
  overlaps of Sentinel-2 are usually same-orbit duplicates, so residual
  order effects are small but not always zero.
- Time grouping: lazycogs buckets by UTC calendar day (P1D), odc by
  solar_day, the GDAL runner by UTC day. Over Australia these coincide for
  Sentinel-2 (acquisitions ~00:00-01:00 UTC); they do not everywhere.

Timing notes: repeat runs and discard the first (object-store and DNS
warmup dominate cold starts); the GDAL runner's numbers reflect a
straightforward warp loop with sensible VSI settings, not a maximally
tuned GDAL pipeline; each tool uses its own practically recommended chunk
shape (lazycogs: time-only chunks; odc: 1024x1024 spatial chunks) because
identical chunking handicaps one architecture or the other.

## Files

    assessment.md            the report this code accompanies
    requirements.txt
    scripts/
      common.py              shared config, target grid, timers, writers
      00_search_items.py     one STAC search -> items.parquet + items.json
      01_run_lazycogs.py
      02_run_odc_stac.py
      03_run_gdal_manual.py
      04_compare.py
      run_all.sh
