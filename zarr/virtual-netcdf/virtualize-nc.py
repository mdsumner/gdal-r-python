#!/usr/bin/env python3
"""
Virtualize NetCDF4/HDF5 files to kerchunk parquet using VirtualiZarr.

Usage:
    # Single file
    python virtualize_nc.py ocean_temp_2024_06.nc

    # Multiple files, concatenated along time
    python virtualize_nc.py ocean_temp_2024_*.nc --concat-dim time

    # Specify output directory
    python virtualize_nc.py ocean_temp_2024_06.nc -o ~/my-vrt

    # Remote file
    python virtualize_nc.py https://www.ncei.noaa.gov/.../oisst-avhrr-v02r01.20251118.nc

Requires:
    pip install virtualizarr h5py xarray zarr numcodecs fastparquet

Notes:
    The output is a directory of parquet files in kerchunk format,
    readable by GDAL >= 3.11 via:
        ZARR:"/path/to/output_dir"

    The refs.{n}.parq files within each variable subdirectory are
    batches of chunk references (controlled by --record-size), NOT
    per-input-file. A single large NetCDF4 with many chunks may
    produce multiple refs.*.parq files per variable.
"""
import warnings
warnings.filterwarnings("ignore", message=".*decode_timedelta.*", category=FutureWarning)

import argparse
import os
import sys
import tempfile
from pathlib import Path

import xarray as xr
from virtualizarr import open_virtual_dataset, open_virtual_mfdataset
from virtualizarr.parsers import HDFParser
from obspec_utils.registry import ObjectStoreRegistry
import obstore


def make_registry(urls):
    """Build an ObjectStoreRegistry that can resolve all input URLs."""
    registry = ObjectStoreRegistry()
    schemes_registered = set()

    for url in urls:
        if url.startswith("http://") or url.startswith("https://"):
            scheme = url.split("://")[0] + "://"
            if scheme not in schemes_registered:
                # Extract base URL (scheme + host)
                from urllib.parse import urlparse
                parsed = urlparse(url)
                base = f"{parsed.scheme}://{parsed.netloc}"
                store = obstore.store.HTTPStore(url=base)
                registry.register(base + "/", store)
                schemes_registered.add(base + "/")

        elif url.startswith("s3://"):
            bucket = url.split("/")[2]
            s3_prefix = f"s3://{bucket}"
            if s3_prefix not in schemes_registered:
                store = obstore.store.S3Store(
                    bucket=bucket,
                    skip_signature=True,
                    region="us-east-1",
                )
                registry.register(s3_prefix + "/", store)
                schemes_registered.add(s3_prefix)

        else:
            # Local file — ensure file:// prefix
            if "file:///" not in schemes_registered:
                store = obstore.store.LocalStore()
                registry.register("file:///", store)
                schemes_registered.add("file:///")

    return registry


def normalise_url(path):
    """Ensure path has a scheme. Local files get file:// prefix."""
    if path.startswith(("http://", "https://", "s3://", "file://")):
        return path
    # Local file — make absolute and add file:// scheme
    abspath = str(Path(path).resolve())
    return f"file://{abspath}"


def main():
    parser = argparse.ArgumentParser(
        description="Virtualize NetCDF4/HDF5 files to kerchunk parquet.",
        epilog="Output is GDAL-readable via: ZARR:\"/path/to/output\"",
    )
    parser.add_argument(
        "inputs", nargs="+",
        help="Input NetCDF4/HDF5 file(s). Local paths or URLs.",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output directory for kerchunk parquet. "
             "Defaults to tempdir if not specified.",
    )
    parser.add_argument(
        "--concat-dim", default=None,
        help="Dimension to concatenate along (e.g. 'time'). "
             "Required when multiple input files are given.",
    )
    parser.add_argument(
        "--record-size", type=int, default=100_000,
        help="Chunk references per parquet file (default: 100000).",
    )
    parser.add_argument(
        "--drop-variables", nargs="*", default=None,
        help="Variables to exclude from virtualisation.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Also write a refs.json file for inspection.",
    )

    args = parser.parse_args()

    # --- Resolve output directory ---
    if args.output:
        outdir = Path(args.output).expanduser().resolve()
    else:
        outdir = Path(tempfile.mkdtemp(prefix="vrt_"))
    outdir.mkdir(parents=True, exist_ok=True)

    # --- Normalise input URLs ---
    urls = [normalise_url(p) for p in args.inputs]

    print(f"Input files: {len(urls)}")
    for u in urls[:5]:
        print(f"  {u}")
    if len(urls) > 5:
        print(f"  ... and {len(urls) - 5} more")

    # --- Build registry ---
    registry = make_registry(urls)
    hdf_parser = HDFParser()

    # --- Open virtual dataset(s) ---
    common_kwargs = dict(
        registry=registry,
        parser=hdf_parser,
    )
    if args.drop_variables:
        common_kwargs["drop_variables"] = args.drop_variables

    if len(urls) == 1:
        print(f"\nOpening single file...")
        vds = open_virtual_dataset(urls[0], **common_kwargs)
    else:
        if args.concat_dim is None:
            print("\nMultiple files without --concat-dim: trying combine_by_coords")
            vds = open_virtual_mfdataset(
                urls,
                combine="by_coords",
                **common_kwargs,
            )
        else:
            print(f"\nConcatenating {len(urls)} files along '{args.concat_dim}'...")
            vds = open_virtual_mfdataset(
                urls,
                concat_dim=args.concat_dim,
                combine="nested", coords = "minimal", compat = "override",
                **common_kwargs,
            )

    # --- Report what we got ---
    print(f"\nVirtual dataset:")
    print(f"  Dimensions: {dict(vds.sizes)}")
    print(f"  Variables:  {list(vds.data_vars)}")
    print(f"  Coords:     {list(vds.coords)}")

    for name, var in vds.data_vars.items():
        print(f"\n  {name}:")
        print(f"    shape:  {var.shape}")
        print(f"    dims:   {var.dims}")
        print(f"    dtype:  {var.dtype}")

    # --- Write kerchunk parquet ---
    print(f"\nWriting kerchunk parquet to: {outdir}")
    vds.virtualize.to_kerchunk(
        str(outdir),
        format="parquet",
        record_size=args.record_size,
    )

    # --- Optionally write JSON ---
    if args.json:
        import json
        json_path = outdir / "refs.json"
        refs = vds.virtualize.to_kerchunk(format="dict")
        with open(json_path, "w") as f:
            json.dump(refs, f, indent=2)
        print(f"Also wrote: {json_path}")

    # --- Show output structure ---
    print(f"\nOutput structure:")
    total_size = 0
    for root, dirs, files in os.walk(outdir):
        level = root.replace(str(outdir), '').count(os.sep)
        indent = ' ' * 2 * level
        print(f'{indent}{os.path.basename(root)}/')
        subindent = ' ' * 2 * (level + 1)
        for f in sorted(files):
            fpath = os.path.join(root, f)
            size = os.path.getsize(fpath)
            total_size += size
            print(f'{subindent}{f}  ({size:,} bytes)')
    print(f"\nTotal: {total_size:,} bytes")

    # --- Usage hints ---
    print(f"""
Open from GDAL (Python):
  from osgeo import gdal
  ds = gdal.OpenEx('ZARR:"{outdir}"', gdal.OF_MULTIDIM_RASTER)
  rg = ds.GetRootGroup()
  print(rg.GetMDArrayFullNamesRecursive())

Open from R (gdalraster gdalmultidim-api branch):
  library(gdalraster)
  dsn <- 'ZARR:"{outdir}"'
  ds <- new(GDALMultiDimRaster, dsn, TRUE, character(), FALSE)

Open with xarray:
  import xarray as xr
  ds = xr.open_zarr("reference://", storage_options={{
      "fo": "{outdir}",
      "remote_protocol": "https"
  }})
""")


if __name__ == "__main__":
    main()
