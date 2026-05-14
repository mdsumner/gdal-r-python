#!/usr/bin/env python3
"""
Phase 0 probe for the mdim chunk-reference RFC.

Purpose
-------
Answer the RFC's open questions about GDALMDArray::GetRawBlockInfo() behaviour
*before* any C++ is written, using osgeo.gdal (the SWIG bindings maintained in
the GDAL tree — i.e. the same surface the eventual algorithm compiles against).

This script does NOT write any output layer. It enumerates a chosen array's
chunk grid, computes the grid two ways (ceil vs floor division), and calls
GetRawBlockInfo() on a handful of diagnostic coordinates, printing what comes
back. It is the instrument that turns RFC "open questions" into evidence-log
"resolved, here's how" entries.

What it is meant to settle
--------------------------
  Q1  Partial edge chunks: for a dimension whose size is not an integer
      multiple of its block size, is the trailing partial chunk addressable?
      (i.e. is ceil-division the correct grid extent, not floor?)
  Q2  Sparse chunks: does a valid-but-absent chunk return True with null/zero
      fields? Is the null filename the reliable "absent" signal (NOT offset==0,
      which is a legal offset)?
  Q3  Inline chunks: when present, how does the inline payload surface, and is
      path/offset/size null in that case?
  Q4  The info key shape per driver: netCDF flat COMPRESSION/FILTER strings vs
      Zarr COMPRESSOR(JSON)/FILTERS/TRANSPOSE_ORDER — is downstream handling
      general or secretly netCDF-shaped?
  Q5  VRT forwarding: a passthrough VRT should forward the source's references;
      a mosaic/re-chunking VRT should decline cleanly per-chunk.

What it explicitly does NOT settle
----------------------------------
  - The C++ accessor names on GDALMDArrayRawBlockInfo. This script reads the
    *Python* proxy surface (info.GetFilename(), info.GetOffset(), ...). The
    *C++* spellings still need confirming against gcore/gdal_priv.h. Keep these
    as separate evidence-log lines: "behaviour seen in Python" is not "C++
    method name known".

Usage
-----
  python3 probe_getrawblockinfo.py <dataset> <array_name> [--oo KEY=VAL ...]

  <dataset>      path or URI, opened with GDAL_OF_MULTIDIM_RASTER
  <array_name>   array name; leading '/' treated as fully-qualified path,
                 bare name opened from the root group; if omitted and the root
                 group holds exactly one array, that one is used
  --oo KEY=VAL   open option, repeatable (e.g. auth / vsicurl tuning)

  Config options (GDAL_HTTP_*, auth, etc.) are read from the environment as
  usual — set them before invoking, the script does not manage them.

Examples (the Phase 0 corpus — run the same script against each)
  # control: known geometry, remote read + THREDDS throttling
  python3 probe_getrawblockinfo.py \
    /vsicurl/https://thredds.nci.org.au/.../ocean_temp_2010_01.nc temp

  # crafted tiny local file with deliberately non-even dims (Q1, ground truth)
  python3 probe_getrawblockinfo.py ./fixtures/partial_chunks.nc data

  # CMEMS Zarr — real Zarr, varied shapes, Zarr-shaped info keys (Q4)
  python3 probe_getrawblockinfo.py ZARR:"/vsicurl/https://.../cmems.zarr" thetao

  # passthrough VRT over netCDF (Q5, should forward) and mosaic VRT (should decline)
  python3 probe_getrawblockinfo.py ./fixtures/passthrough.vrt temp
  python3 probe_getrawblockinfo.py ./fixtures/mosaic.vrt temp
"""

import sys
import argparse
from osgeo import gdal

gdal.UseExceptions()


def parse_args():
    p = argparse.ArgumentParser(description="Phase 0 GetRawBlockInfo probe")
    p.add_argument("dataset", help="dataset path/URI (multidim)")
    p.add_argument("array", nargs="?", default="",
                   help="array name; '/'-prefixed = full path; "
                        "omit if root group has exactly one array")
    p.add_argument("--oo", action="append", default=[], metavar="KEY=VAL",
                   help="open option, repeatable")
    return p.parse_args()


def open_array(dataset, array_name, open_options):
    """Open the dataset multidim, resolve the named array (or the sole array)."""
    ds = gdal.OpenEx(dataset, gdal.OF_MULTIDIM_RASTER | gdal.OF_READONLY,
                     open_options=open_options)
    if ds is None:
        raise SystemExit(f"could not open '{dataset}' as multidimensional")

    root = ds.GetRootGroup()
    if root is None:
        raise SystemExit(f"'{dataset}' has no root group "
                         f"(driver lacks multidimensional support)")

    if array_name and array_name.startswith("/"):
        # fully-qualified path; OpenMDArrayFromFullname if available, else walk
        try:
            arr = root.OpenMDArrayFromFullname(array_name)
        except AttributeError:
            arr = _walk_to_array(root, array_name)
    elif array_name:
        arr = root.OpenMDArray(array_name)
    else:
        names = root.GetMDArrayNames()
        if len(names) == 1:
            arr = root.OpenMDArray(names[0])
            print(f"[info] no array given; using sole array '{names[0]}'")
        else:
            raise SystemExit(
                f"array name required; root group holds {len(names)} arrays: "
                f"{', '.join(names)}")

    if arr is None:
        raise SystemExit(f"could not open array '{array_name}' in '{dataset}'")
    # keep ds alive by attaching it; arr holds a ref in C++ but be safe in py
    arr._keep_ds = ds
    return arr


def _walk_to_array(root, fullpath):
    """Fallback: manual traversal of '/grp/sub/array' when
    OpenMDArrayFromFullname is unavailable."""
    parts = [p for p in fullpath.split("/") if p]
    grp = root
    for p in parts[:-1]:
        grp = grp.OpenGroup(p)
        if grp is None:
            raise SystemExit(f"could not open group '{p}' in path '{fullpath}'")
    return grp.OpenMDArray(parts[-1])


def describe_array(arr):
    """Print array-level facts: dims, sizes, block size, dtype. These are the
    things that in the eventual algorithm belong in *layer metadata*, not in
    every feature row."""
    dims = arr.GetDimensions()
    dim_sizes = [d.GetSize() for d in dims]
    dim_names = [d.GetName() or f"dim_{i}" for i, d in enumerate(dims)]
    block = arr.GetBlockSize()  # list, one per dimension; 0 == no natural block
    dt = arr.GetDataType()

    print("=" * 70)
    print(f"array            : {arr.GetFullName()}")
    print(f"dimension count  : {len(dims)}")
    for i, (nm, sz, bs) in enumerate(zip(dim_names, dim_sizes, block)):
        print(f"  dim[{i}] {nm!r:24} size={sz:<12} block={bs}")
    print(f"data type        : {dt.GetName() if dt else '?'}")
    print("=" * 70)

    return dim_names, dim_sizes, block


def chunk_grid(dim_sizes, block):
    """Compute the chunk-grid extent two ways. The RFC's Q1 is which one is
    correct — the C++ docs phrase the valid coord range as
    (GetSize()/GetBlockSize())-1, which read as integer division is FLOOR and
    would drop the trailing partial chunk. Ceil is almost certainly right but
    must be confirmed empirically; this function just lays both out so the
    GetRawBlockInfo() probe below can decide."""
    n_ceil = []
    n_floor = []
    for sz, bs in zip(dim_sizes, block):
        if bs == 0:
            raise SystemExit(
                "a dimension reports block size 0 ('no natural block size'); "
                "chunk enumeration is undefined for this array — in the "
                "algorithm this must be a clear error, not a div-by-zero")
        n_ceil.append((sz + bs - 1) // bs)
        n_floor.append(sz // bs)
    return n_ceil, n_floor


def fmt_blockinfo(info):
    """Render a GDALMDArrayRawBlockInfo proxy. NB: these are the *Python* proxy
    accessor names. The C++ method names on GDALMDArrayRawBlockInfo still need
    confirming against gcore/gdal_priv.h — do not assume they match."""
    if info is None:
        return "    <GetRawBlockInfo returned None>"
    try:
        fn = info.GetFilename()
    except Exception as e:
        fn = f"<GetFilename error: {e}>"
    try:
        off = info.GetOffset()
    except Exception as e:
        off = f"<GetOffset error: {e}>"
    try:
        sz = info.GetSize()
    except Exception as e:
        sz = f"<GetSize error: {e}>"
    try:
        nfo = info.GetInfo()
    except Exception as e:
        nfo = f"<GetInfo error: {e}>"
    try:
        inline = info.GetInlineData()
    except Exception as e:
        inline = f"<GetInlineData error: {e}>"

    has_file = bool(fn) and fn not in (None, "")
    has_inline = inline is not None and not isinstance(inline, str) \
        and len(inline) > 0

    # Q2: 'absent' must be detected via null filename, NOT offset==0.
    # offset 0 is a legal offset. Flag the interpretation explicitly.
    absent = (not has_file) and (not has_inline)

    lines = []
    lines.append(f"    filename : {fn!r}")
    lines.append(f"    offset   : {off}")
    lines.append(f"    size     : {sz}")
    lines.append(f"    info     : {nfo!r}")
    lines.append(f"    inline   : {'<%d bytes>' % len(inline) if has_inline else inline!r}")
    lines.append(f"    -> interpreted: "
                 f"{'ABSENT (sparse)' if absent else 'INLINE' if has_inline else 'PRESENT (file-backed)'}")
    if off == 0 and has_file:
        lines.append("    -> NOTE: offset==0 with a real filename — legal; "
                     "confirms offset is not an absence signal")
    return "\n".join(lines)


def probe_coordinate(arr, coord, label):
    """Call GetRawBlockInfo at one chunk coordinate and print the result.
    Returns True if the call itself succeeded (regardless of present/absent)."""
    print(f"\n[{label}] block coordinate {tuple(coord)}")
    try:
        info = arr.GetRawBlockInfo(coord)
    except Exception as e:
        # A clean per-chunk decline (e.g. mosaic VRT) should land here.
        print(f"    GetRawBlockInfo raised: {type(e).__name__}: {e}")
        print(f"    -> interpreted: METHOD DECLINED for this block")
        return False
    print(fmt_blockinfo(info))
    return True


def main():
    args = parse_args()
    arr = open_array(args.dataset, args.array, args.oo)
    dim_names, dim_sizes, block = describe_array(arr)
    ndim = len(dim_sizes)

    n_ceil, n_floor = chunk_grid(dim_sizes, block)
    print(f"\nchunk grid (ceil)  : {n_ceil}   "
          f"total = {_prod(n_ceil)}")
    print(f"chunk grid (floor) : {n_floor}   "
          f"total = {_prod(n_floor)}")
    has_partial = n_ceil != n_floor
    print(f"non-even dims      : {has_partial}  "
          f"{'<-- partial edge chunks exist; Q1 is live' if has_partial else ''}")

    # ---- diagnostic coordinates -------------------------------------------
    # origin: always valid, the baseline
    origin = [0] * ndim
    probe_coordinate(arr, origin, "ORIGIN")

    # last chunk under FLOOR interpretation: definitely-valid interior corner
    last_floor = [max(n - 1, 0) for n in n_floor]
    if last_floor != origin:
        probe_coordinate(arr, last_floor, "LAST-FLOOR (interior corner)")

    # last chunk under CEIL interpretation: the trailing partial chunk.
    # THIS IS Q1. If ceil != floor and this call returns a real offset/size,
    # ceil-division is correct and partial edge chunks ARE addressable.
    # If it declines or returns absent, the valid range is floor — a
    # significant finding (partial chunks not individually addressable).
    if has_partial:
        last_ceil = [n - 1 for n in n_ceil]
        ok = probe_coordinate(arr, last_ceil,
                              "LAST-CEIL (trailing partial chunk) <-- Q1")
        print("\n    >>> Q1 verdict input: a real file-backed result here "
              "means CEIL is correct.\n"
              "    >>> A decline or absent result means the valid range is "
              "FLOOR.\n"
              "    >>> Record which, against this driver, in the evidence log.")

    # one past the ceil extent in dim 0: should be invalid either way —
    # confirms the upper bound behaviour (error vs silent)
    over = list(n_ceil)
    over[0] = n_ceil[0]  # one past the last valid ceil index in dim 0
    over = [c if i != 0 else n_ceil[0] for i, c in enumerate([0] * ndim)]
    print("\n[OUT-OF-RANGE] one past ceil extent in dim 0 "
          "(expect error/decline either way)")
    try:
        info = arr.GetRawBlockInfo(over)
        print(fmt_blockinfo(info))
        print("    -> NOTE: did not error on an out-of-range coordinate; "
              "worth recording")
    except Exception as e:
        print(f"    GetRawBlockInfo raised: {type(e).__name__}: {e}  (expected)")

    print("\n" + "=" * 70)
    print("Phase 0 probe complete. Transcribe the Q1/Q2/Q3/Q4/Q5 observations")
    print("into the evidence log before writing the algorithm hull.")
    print("Remember: this proves BEHAVIOUR, not C++ accessor SPELLINGS.")
    print("=" * 70)


def _prod(xs):
    out = 1
    for x in xs:
        out *= x
    return out


if __name__ == "__main__":
    main()
