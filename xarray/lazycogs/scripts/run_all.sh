#!/usr/bin/env bash
# Run the full comparison end-to-end.
set -euo pipefail
cd "$(dirname "$0")"

python 00_search_items.py
python 01_run_lazycogs.py
python 02_run_odc_stac.py
python 03_run_gdal_manual.py
python 04_compare.py
