#!/usr/bin/env python3
"""
Make a tiny non-even-chunked netCDF4 fixture for Phase 0 question Q1.

Why
---
Q1 -- "are trailing partial edge chunks individually addressable, i.e. is
ceil-division the correct chunk-grid extent?" -- has been CONFIRMED for the
ZARR driver (CMEMS sla: longitude 1440 at block 512, the (1059,0,2) partial
chunk returned a real file-backed result). It has NOT been exercised for the
netCDF / HDF5 driver, because every netCDF probed so far happened to have
evenly-dividing chunk dimensions.

This script writes a deliberately *non-even* netCDF4 file: dimensions whose
sizes are not integer multiples of their chunk sizes, so the trailing partial
chunk genuinely exists and the netCDF driver's GetRawBlockInfo() can be tested
against it.

Ground truth (so the probe output can be checked against known values)
----------------------------------------------------------------------
  dimension   size   chunk   ceil   floor   trailing partial extent
  time         10      3      4      3       1   (10 - 3*3)
  y           100     30      4      3      10   (100 - 3*30)
  x           250     64      4      3      58   (250 - 3*64)

  full chunk grid (ceil)  : [4, 4, 4]  = 64 chunks
  full chunk grid (floor) : [3, 3, 3]  = 27 chunks   <- would WRONGLY drop
                                                        every partial chunk
  The last-ceil coordinate (3, 3, 3) is the corner partial chunk, extent
  1 x 10 x 58. If GetRawBlockInfo((3,3,3)) returns a real file-backed result,
  ceil-division is confirmed for the netCDF driver. If it declines or returns
  absent, the valid range is floor -- a significant and surprising finding.

The data is written with zlib compression and the shuffle filter, so the
fixture also exercises a normal netCDF codec chain (the 'info' field should
come back like ['COMPRESSION=DEFLATE','FILTER=SHUFFLE','ENDIANNESS=LITTLE']).

Usage
-----
  python3 make_noneven_netcdf.py [output_path]

  default output: ./fixtures/noneven_chunks.nc

  then probe it:
  python3 probe_getrawblockinfo_v2.py ./fixtures/noneven_chunks.nc data

Requires
--------
  netCDF4 (pip install netCDF4) and numpy. This uses the netCDF4 library
  directly to write the file rather than GDAL, so chunk sizes can be set
  explicitly and unambiguously.
"""

import sys
import os

try:
    import numpy as np
    from netCDF4 import Dataset
except ImportError as e:
    raise SystemExit(
        f"missing dependency: {e}\n"
        "install with: pip install netCDF4 numpy")


# (name, size, chunk) -- sizes deliberately NOT multiples of chunk
DIMS = [
    ("time", 10, 3),
    ("y", 100, 30),
    ("x", 250, 64),
]


def make_fixture(path):
    sizes = [d[1] for d in DIMS]
    chunks = [d[2] for d in DIMS]
    names = [d[0] for d in DIMS]

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    ds = Dataset(path, "w", format="NETCDF4")
    try:
        for nm, sz, _ in DIMS:
            ds.createDimension(nm, sz)

        # coordinate variables -- small, 1D, so the writer may store them
        # compactly / inline. Useful incidental coverage.
        for nm, sz, _ in DIMS:
            cv = ds.createVariable(nm, "f8", (nm,))
            cv[:] = np.arange(sz, dtype="f8")
            cv.long_name = f"{nm} coordinate"

        # the main data array: explicitly chunked at the non-even sizes,
        # zlib + shuffle so the codec chain is realistic
        var = ds.createVariable(
            "data", "i2", tuple(names),
            zlib=True, complevel=4, shuffle=True,
            chunksizes=tuple(chunks),
        )
        # fill with something non-constant so chunks actually compress to
        # different sizes (and nothing collapses to all-fill / sparse)
        rng = np.random.default_rng(42)
        var[:] = rng.integers(-1000, 1000, size=tuple(sizes), dtype="i2")
        var.long_name = "non-even chunked test array"
        var.units = "1"

        ds.title = "Phase 0 Q1 fixture: non-even chunk dimensions"
        ds.summary = (
            "Deliberately non-even chunking to test whether the netCDF "
            "driver's GetRawBlockInfo() exposes trailing partial chunks "
            "(ceil-division) or only full interior chunks (floor-division).")
    finally:
        ds.close()

    # report the ground truth so it can be pasted into the evidence log
    n_ceil = [(s + c - 1) // c for s, c in zip(sizes, chunks)]
    n_floor = [s // c for s, c in zip(sizes, chunks)]
    print(f"wrote {path}")
    print()
    print("ground truth for the evidence log:")
    print(f"  dimensions (name, size, chunk): {DIMS}")
    print(f"  chunk grid ceil  : {n_ceil}  = {_prod(n_ceil)} chunks")
    print(f"  chunk grid floor : {n_floor}  = {_prod(n_floor)} chunks")
    print(f"  last-ceil coordinate (the corner partial chunk): "
          f"{tuple(n - 1 for n in n_ceil)}")
    trailing = [s - (nf * c) for s, c, nf in zip(sizes, chunks, n_floor)]
    print(f"  that partial chunk's extent: {tuple(trailing)}")
    print()
    print("next: python3 probe_getrawblockinfo_v2.py "
          f"{path} data")
    print("  -- a real file-backed result at the last-ceil coordinate "
          "confirms ceil for netCDF.")


def _prod(xs):
    out = 1
    for x in xs:
        out *= x
    return out


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "./fixtures/noneven_chunks.nc"
    make_fixture(out)
