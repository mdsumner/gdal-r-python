"""
Parallel virtualization of GHRSST MUR COGs -> kerchunk parquet refs.

Includes monkeypatch for virtual-tiff imagecodecs ZSTD recursive config bug.

Usage:
    python virtualize_ghrsst.py
    python virtualize_ghrsst.py -w 16
"""

import argparse
import warnings
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings(
    "ignore",
    message="Imagecodecs codecs are not in the Zarr version 3 specification",
)

# ---------------------------------------------------------------------------
# Monkeypatch: fix recursive imagecodecs codec config for kerchunk v2 output.
#
# virtual-tiff wraps ZSTD config as:
#   {'id':'zstd','name':'imagecodecs_zstd','configuration':
#     {'id':'zstd','name':'imagecodecs_zstd','configuration':
#       {'id':'zstd','name':'imagecodecs_zstd','configuration':
#         {'id':'zstd','level':9}}}}
#
# The kerchunk v2 writer expects a flat {'id':'zstd','level':9}.
# We unwrap the nesting before handing it to the original converter.
# ---------------------------------------------------------------------------

import virtualizarr.codecs as _vz_codecs
import virtualizarr.utils as _vz_utils
import numpy as _np

_orig_zarr_codec_config_to_v2 = _vz_codecs.zarr_codec_config_to_v2

def _unwrap_and_convert(codec_config):
    conf = codec_config
    # For imagecodecs wrappers, drill to the leaf v2 dict
    if isinstance(conf, dict) and conf.get("name", "").startswith("imagecodecs_"):
        while isinstance(conf, dict) and "configuration" in conf:
            inner = conf["configuration"]
            if isinstance(inner, dict) and "configuration" in inner:
                conf = inner
            else:
                return inner
        return conf
    # Skip codecs that are Zarr-internal, not numcodecs filters
    skip_names = {"HorizontalDeltaCodec", "BytesCodec", "bytes"}
    skip_ids = {"bytes"}
    if isinstance(conf, dict):
        if conf.get("name") in skip_names or conf.get("id") in skip_ids:
            return None
    try:
        return _orig_zarr_codec_config_to_v2(conf)
    except (KeyError, TypeError):
        if isinstance(conf, dict) and "id" in conf:
            return conf
        return None

# Patch in both namespaces
_vz_codecs.zarr_codec_config_to_v2 = _unwrap_and_convert
_vz_utils.zarr_codec_config_to_v2 = _unwrap_and_convert

# Now patch convert_v3_to_v2_metadata to tolerate None returns (skipped codecs)
import virtualizarr.writers.kerchunk as _vz_kerchunk
_orig_convert_v3_to_v2 = _vz_utils.convert_v3_to_v2_metadata

def _patched_convert(metadata):
    from virtualizarr.codecs import get_codec_config
    from zarr.core.metadata.v2 import ArrayV2Metadata
    zarray = metadata
    v2_codecs = []
    for codec in zarray.codecs:
        conf = get_codec_config(codec)
        v2 = _vz_utils.zarr_codec_config_to_v2(conf)
        if v2 is not None:
            v2_codecs.append(v2)
    compressor = v2_codecs[-1] if v2_codecs else None
    filters = v2_codecs[:-1] if len(v2_codecs) > 1 else None
    return ArrayV2Metadata(
        shape=zarray.shape,
        dtype=zarray.data_type,
        chunks=zarray.chunk_grid.chunk_shape,
        fill_value=int(zarray.fill_value),
        order="C",
        compressor=compressor,
        filters=filters,
    )

_vz_utils.convert_v3_to_v2_metadata = _patched_convert
_vz_kerchunk.convert_v3_to_v2_metadata = _patched_convert

# ---------------------------------------------------------------------------
# Now safe to import the rest
# ---------------------------------------------------------------------------

import numpy as np
import pandas as pd
import xarray as xr
from obstore.store import from_url
from virtualizarr import open_virtual_dataset
from virtualizarr.registry import ObjectStoreRegistry
from virtual_tiff import VirtualTIFF

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BUCKET_URL = "s3://us-west-2.opendata.source.coop/"
BASE_PATH = "ausantarctic/ghrsst-mur-v2"
START_DATE = "2002-06-01"
END_DATE = "2026-02-14"
OUTPUT_PATH = "ghrsst_mur_june2002.parquet"


def ghrsst_url(date):
    fname = (
        f"{date.strftime('%Y%m%d')}090000-JPL-L4_GHRSST-SSTfnd-MUR-GLOB"
        f"-v02.0-fv04.1_analysed_sst.tif"
    )
    return (
        f"{BUCKET_URL}{BASE_PATH}/"
        f"{date.year:04d}/{date.month:02d}/{date.day:02d}/{fname}"
    )


def virtualize_one(url, date, parser, registry):
    vds = open_virtual_dataset(url=url, parser=parser, registry=registry)
    vds = vds.rename({"0": "analysed_sst"})
    vds = vds.expand_dims(time=[np.datetime64(date)])
    return vds


def main():
    ap = argparse.ArgumentParser(description="Virtualize GHRSST MUR COGs")
    ap.add_argument("-w", "--workers", type=int, default=8)
    args = ap.parse_args()

    store = from_url(BUCKET_URL, region="us-west-2", skip_signature=True)
    registry = ObjectStoreRegistry({BUCKET_URL: store})
    parser = VirtualTIFF(ifd=0)

    dates = pd.date_range(START_DATE, END_DATE, freq="D")
    urls = [ghrsst_url(d) for d in dates]
    print(f"Virtualizing {len(urls)} files with {args.workers} workers...")

    # --- Parallel virtualization ---
    t0 = time.time()
    results = {}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(virtualize_one, url, date, parser, registry): date
            for url, date in zip(urls, dates)
        }
        for future in as_completed(futures):
            date = futures[future]
            try:
                results[date] = future.result()
                print(f"  {date.date()} done")
            except Exception as e:
                print(f"  {date.date()} FAILED: {e}")

    elapsed_virt = time.time() - t0
    print(f"\nVirtualized {len(results)}/{len(urls)} files in {elapsed_virt:.1f}s "
          f"({elapsed_virt/len(results):.2f}s per file)")

    # --- Combine in date order ---
    t1 = time.time()
    ordered = [results[d] for d in sorted(results.keys())]
    combined = xr.concat(ordered, dim="time")
    elapsed_concat = time.time() - t1
    print(f"Concatenated in {elapsed_concat:.1f}s")
    print(f"\n{combined}\n")

    # --- Write kerchunk parquet ---
    t2 = time.time()
    combined.vz.to_kerchunk(OUTPUT_PATH, format="parquet")
    elapsed_write = time.time() - t2
    print(f"Wrote parquet refs to {OUTPUT_PATH} in {elapsed_write:.1f}s")

    print(f"\nTotal time: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
