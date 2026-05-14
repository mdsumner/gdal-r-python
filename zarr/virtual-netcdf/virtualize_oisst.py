"""
Virtualize a remote NetCDF4 file to kerchunk parquet.

Usage:
    python virtualize_oisst.py

Requires:
    pip install kerchunk h5py fsspec xarray zarr fastparquet
"""

import json
import os
from pathlib import Path

import fsspec
from kerchunk.hdf import SingleHdf5ToZarr

#url = "https://thredds.nci.org.au/thredds/fileServer/gb6/BRAN/BRAN2023/daily/ocean_temp_2024_06.nc"
url = "/perm_storage/home/mdsumner/bluelink/ocean_temp_2024_06.nc"
outdir = Path.home() / "bluelink-virtual"
outdir.mkdir(exist_ok=True)

# --- Step 1: Generate references from the remote NetCDF4 ---
# kerchunk reads the HDF5/NetCDF4 chunk index without downloading the whole file.
# It uses fsspec's HTTP filesystem for range reads on the remote file.

print(f"Reading chunk references from:\n  {url}")

so = {"anon": True}  # no auth needed
with fsspec.open(url) as f:
    h5chunks = SingleHdf5ToZarr(f, url)
    refs = h5chunks.translate()

# refs is a dict with:
#   "version": 1
#   "refs": {
#     ".zgroup": "...",
#     "sst/.zarray": "...",
#     "sst/.zattrs": "...",
#     "sst/0.0.0.0": ["https://...nc", offset, length],
#     ...
#   }

# --- Step 2: Inspect what we got ---
ref_keys = [k for k in refs["refs"] if not k.startswith(".")]
array_keys = sorted(set(k.split("/")[0] for k in ref_keys))
chunk_keys = [k for k in ref_keys if "/" in k and not k.endswith((".zarray", ".zattrs", ".zgroup"))]

print(f"\nVariables found: {array_keys}")
print(f"Total chunk references: {len(chunk_keys)}")

for var in array_keys:
    zarray_key = f"{var}/.zarray"
    if zarray_key in refs["refs"]:
        meta = json.loads(refs["refs"][zarray_key])
        #print(f"  {var}: shape={meta.get('shape')}, chunks={meta.get('chunks')}, "
        #      f"dtype={meta.get('dtype')}, compressor={meta.get('compressor',{}).get('id','none')}")

# Show a sample chunk reference
for k in chunk_keys[:3]:
    v = refs["refs"][k]
    if isinstance(v, list) and len(v) == 3:
        print(f"\n  Sample ref: {k}")
        print(f"    file:   {v[0]}")
        print(f"    offset: {v[1]}")
        print(f"    length: {v[2]}")
        break

# --- Step 3: Write to kerchunk parquet ---
# Two options: JSON (single file, simple) or parquet (what GDAL reads).
# GDAL's ZARR driver reads the parquet format via:
#   ZARR:"/path/to/output.parq"

from kerchunk.df import refs_to_dataframe

print(f"\nWriting kerchunk parquet to:\n  {outdir}")

# refs_to_dataframe writes partitioned parquet (one file per variable)
# fo = reference dict, url = output directory
refs_to_dataframe(refs, str(outdir))

print(f"Done. Written to {outdir}")

# Also write the JSON for inspection / debugging
json_path = outdir / "refs.json"
with open(json_path, "w") as f:
    json.dump(refs, f, indent=2)
print(f"Also wrote JSON references to {json_path} for inspection")

# --- Step 4: Show how to open it ---
print(f"""
To open from GDAL (Python):
  from osgeo import gdal
  ds = gdal.OpenEx('ZARR:"{outdir}"', gdal.OF_MULTIDIM_RASTER)
  rg = ds.GetRootGroup()
  print(rg.GetMDArrayFullNamesRecursive())

To open from R (gdalraster):
  library(gdalraster)
  dsn <- 'ZARR:"{outdir}"'
  ds <- new(GDALMultiDimRaster, dsn, TRUE, character(), FALSE)
  arr <- ds$openArray("sst", "character")
  info <- arr$getRawBlockInfo(c(0L, 0L, 0L, 0L))

To open with xarray (for verification):
  import xarray as xr
  ds = xr.open_zarr("reference://", storage_options={{
      "fo": "{outdir}",
      "remote_protocol": "https"
  }})
""")
