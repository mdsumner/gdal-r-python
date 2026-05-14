#!/usr/bin/env python3
"""
Phase 0 probe for the mdim chunk-reference RFC.  (v2)

Changes from v1, both driven by real-run findings:
  - dtype accessor fixed. v1 printed "data type :" empty on every run because
    GDALExtendedDataType.GetName() is only populated for COMPOUND/STRING types;
    numeric arrays need GetNumericDataType() -> gdal.GetDataTypeName(). v2 tries
    the numeric route first and falls back to GetName(). Evidence-log note: the
    correct accessor for a numeric mdim array's dtype is
    GetDataType().GetNumericDataType().
  - block-size-0 is now a *reported result*, not a fatal SystemExit. A mosaic
    VRT's synthesised coordinate arrays (e.g. 'Time') legitimately report block
    size 0 -- "no natural block size", i.e. not chunk-enumerable. That is a
    valid declined state the algorithm must handle cleanly, so the probe now
    prints it as a finding and exits 0, mirroring intended algorithm behaviour.

Purpose, the five questions it settles, and what it does NOT settle (the C++
accessor spellings) are unchanged from v1 -- see the original docstring. Quick
recap of the open items as of the latest runs:
  Q1  ceil-division: CONFIRMED for ZARR (CMEMS). Still wants a non-even *netCDF*
      to confirm the netCDF/HDF5 flavour -- use the companion generator script.
  Q2  sparse chunks: CONFIRMED (CMEMS sla origin; null filename is the signal,
      offset==0 is NOT).
  Q3  inline chunks: CONFIRMED (BRAN-as-Parquet xt_ocean: null path, size>0,
      inline payload present).
  Q4  info-key shape: CONFIRMED across three distinct shapes (flat scalars;
      COMPRESSOR=JSON-object; FILTERS=JSON-array). Pass through verbatim.
  Q5  VRT forwarding: CONFIRMED. Forwardability is *per-array* within one
      dataset -- mosaicked data arrays forward to source files; synthesised
      coordinate arrays report block size 0.

Usage
-----
  python3 probe_getrawblockinfo.py <dataset> <array_name> [--oo KEY=VAL ...]
  python3 probe_getrawblockinfo.py <dataset> --all   [--oo KEY=VAL ...]

  <array_name>   array name; '/'-prefixed = full path; bare = from root group;
                 omit (or pass --all) to walk every array in the root group,
                 reporting each (enumerable or not) without stopping.
"""

import sys
import argparse
from osgeo import gdal

gdal.UseExceptions()


def parse_args():
    p = argparse.ArgumentParser(description="Phase 0 GetRawBlockInfo probe (v2)")
    p.add_argument("dataset", help="dataset path/URI (multidim)")
    p.add_argument("array", nargs="?", default="",
                   help="array name; '/'-prefixed = full path; "
                        "omit or use --all to walk every root-group array")
    p.add_argument("--all", action="store_true",
                   help="probe every array in the root group, don't stop on "
                        "non-enumerable ones")
    p.add_argument("--oo", action="append", default=[], metavar="KEY=VAL",
                   help="open option, repeatable")
    return p.parse_args()


def open_root(dataset, open_options):
    """Open multidim, return (ds, root_group). ds kept alive by caller."""
    # NB: pass open_options through as-is. v1 had `open_options or None`; some
    # binding builds dislike the None, an empty list is the portable choice.
    ds = gdal.OpenEx(dataset, gdal.OF_MULTIDIM_RASTER | gdal.OF_READONLY,
                     open_options=open_options)
    if ds is None:
        raise SystemExit(f"could not open '{dataset}' as multidimensional")
    root = ds.GetRootGroup()
    if root is None:
        raise SystemExit(f"'{dataset}' has no root group "
                         f"(driver lacks multidimensional support)")
    return ds, root


def resolve_array(root, array_name):
    """Resolve one named array from the root group."""
    if array_name.startswith("/"):
        try:
            arr = root.OpenMDArrayFromFullname(array_name)
        except AttributeError:
            arr = _walk_to_array(root, array_name)
    else:
        arr = root.OpenMDArray(array_name)
    if arr is None:
        raise SystemExit(f"could not open array '{array_name}'")
    return arr


def _walk_to_array(root, fullpath):
    parts = [p for p in fullpath.split("/") if p]
    grp = root
    for p in parts[:-1]:
        grp = grp.OpenGroup(p)
        if grp is None:
            raise SystemExit(f"could not open group '{p}' in '{fullpath}'")
    return grp.OpenMDArray(parts[-1])


def dtype_name(arr):
    """Correct dtype accessor. v1 used GetDataType().GetName(), empty for
    numeric arrays. Numeric route first, GetName() fallback for compound/string."""
    dt = arr.GetDataType()
    if dt is None:
        return "<no data type>"
    # numeric: GetNumericDataType() -> GDT_* enum -> name
    try:
        gdt = dt.GetNumericDataType()
        if gdt is not None and gdt != gdal.GDT_Unknown:
            return gdal.GetDataTypeName(gdt)
    except Exception:
        pass
    # compound / string: GetName() carries it
    try:
        nm = dt.GetName()
        if nm:
            return nm
    except Exception:
        pass
    # class as last resort
    try:
        return f"<class {dt.GetClass()}>"
    except Exception:
        return "<unknown data type>"


def describe_array(arr):
    """Print array-level facts. In the algorithm these belong in *layer
    metadata*, not per-feature. Returns (dim_names, dim_sizes, block)."""
    dims = arr.GetDimensions()
    dim_sizes = [d.GetSize() for d in dims]
    dim_names = [d.GetName() or f"dim_{i}" for i, d in enumerate(dims)]
    block = arr.GetBlockSize()

    print("=" * 70)
    print(f"array            : {arr.GetFullName()}")
    print(f"dimension count  : {len(dims)}")
    for i, (nm, sz, bs) in enumerate(zip(dim_names, dim_sizes, block)):
        print(f"  dim[{i}] {nm!r:24} size={sz:<12} block={bs}")
    print(f"data type        : {dtype_name(arr)}")
    print("=" * 70)
    return dim_names, dim_sizes, block


def chunk_grid(dim_sizes, block):
    """Compute the chunk-grid extent ceil and floor.

    Returns (n_ceil, n_floor) or None if the array is not chunk-enumerable
    (any dimension reports block size 0 -- "no natural block size"). v2 returns
    None instead of raising: block-size-0 is a valid 'declined' state (seen on
    mosaic-VRT synthesised coordinate arrays), not an error to crash on. The
    algorithm should likewise skip such arrays with a clear message.
    """
    if any(bs == 0 for bs in block):
        return None
    n_ceil, n_floor = [], []
    for sz, bs in zip(dim_sizes, block):
        n_ceil.append((sz + bs - 1) // bs)
        n_floor.append(sz // bs)
    return n_ceil, n_floor


def fmt_blockinfo(info):
    """Render a GDALMDArrayRawBlockInfo proxy. These are the *Python* proxy
    accessor names -- the C++ names on GDALMDArrayRawBlockInfo still need
    confirming against gcore/gdal_priv.h."""
    if info is None:
        return "    <GetRawBlockInfo returned None>"

    def safe(getter, label):
        try:
            return getter()
        except Exception as e:
            return f"<{label} error: {e}>"

    fn = safe(info.GetFilename, "GetFilename")
    off = safe(info.GetOffset, "GetOffset")
    sz = safe(info.GetSize, "GetSize")
    nfo = safe(info.GetInfo, "GetInfo")
    inline = safe(info.GetInlineData, "GetInlineData")

    has_file = bool(fn) and fn not in (None, "")
    has_inline = (inline is not None and not isinstance(inline, str)
                  and len(inline) > 0)
    # absence signal is the null filename, NOT offset==0 (a legal offset)
    absent = (not has_file) and (not has_inline)

    lines = [
        f"    filename : {fn!r}",
        f"    offset   : {off}",
        f"    size     : {sz}",
        f"    info     : {nfo!r}",
        f"    inline   : "
        f"{'<%d bytes>' % len(inline) if has_inline else inline!r}",
    ]
    if absent:
        verdict = "ABSENT (sparse)"
    elif has_inline:
        verdict = "INLINE"
    else:
        verdict = "PRESENT (file-backed)"
    lines.append(f"    -> interpreted: {verdict}")
    if off == 0 and has_file:
        lines.append("    -> NOTE: offset==0 with a real filename -- legal; "
                     "confirms offset is not an absence signal")
    return "\n".join(lines)


def probe_coordinate(arr, coord, label):
    """Call GetRawBlockInfo at one coordinate, print result. Returns True if
    the call succeeded (regardless of present/absent/inline)."""
    print(f"\n[{label}] block coordinate {tuple(coord)}")
    try:
        info = arr.GetRawBlockInfo(coord)
    except Exception as e:
        print(f"    GetRawBlockInfo raised: {type(e).__name__}: {e}")
        print(f"    -> interpreted: METHOD DECLINED for this block")
        return False
    print(fmt_blockinfo(info))
    return True


def probe_array(arr):
    """Full diagnostic pass over one array. Returns without raising even when
    the array is not chunk-enumerable."""
    dim_names, dim_sizes, block = describe_array(arr)
    ndim = len(dim_sizes)

    grid = chunk_grid(dim_sizes, block)
    if grid is None:
        print("\n  >>> block size 0 on at least one dimension: this array has "
              "NO natural block structure.")
        print("  >>> NOT chunk-enumerable. This is a VALID declined state "
              "(e.g. a mosaic-VRT synthesised")
        print("  >>> coordinate array). The algorithm should skip such arrays "
              "with a clear message,")
        print("  >>> not treat this as an error.")
        return

    n_ceil, n_floor = grid
    print(f"\nchunk grid (ceil)  : {n_ceil}   total = {_prod(n_ceil)}")
    print(f"chunk grid (floor) : {n_floor}   total = {_prod(n_floor)}")
    has_partial = n_ceil != n_floor
    print(f"non-even dims      : {has_partial}  "
          f"{'<-- partial edge chunks exist; Q1 is live' if has_partial else ''}")

    origin = [0] * ndim
    probe_coordinate(arr, origin, "ORIGIN")

    last_floor = [max(n - 1, 0) for n in n_floor]
    if last_floor != origin:
        probe_coordinate(arr, last_floor, "LAST-FLOOR (interior corner)")

    if has_partial:
        last_ceil = [n - 1 for n in n_ceil]
        probe_coordinate(arr, last_ceil,
                         "LAST-CEIL (trailing partial chunk) <-- Q1")
        print("\n    >>> Q1 verdict input: a real file-backed result here "
              "means CEIL is correct.")
        print("    >>> A decline or absent result means the valid range is "
              "FLOOR.")
        print("    >>> Record which, against this driver, in the evidence log.")

    # one past the ceil extent in dim 0: invalid either way
    over = [0] * ndim
    over[0] = n_ceil[0]
    print(f"\n[OUT-OF-RANGE] {tuple(over)} -- one past ceil extent in dim 0 "
          f"(expect error/decline)")
    try:
        info = arr.GetRawBlockInfo(over)
        print(fmt_blockinfo(info))
        print("    -> NOTE: did NOT error on an out-of-range coordinate; "
              "worth recording")
    except Exception as e:
        print(f"    GetRawBlockInfo raised: {type(e).__name__}: {e}  (expected)")


def _prod(xs):
    out = 1
    for x in xs:
        out *= x
    return out


def main():
    args = parse_args()
    ds, root = open_root(args.dataset, args.oo)

    names = root.GetMDArrayNames()
    walk_all = args.all or not args.array

    if walk_all:
        if not names:
            raise SystemExit("root group holds no arrays")
        print(f"[info] walking all {len(names)} root-group arrays: "
              f"{', '.join(names)}")
        for nm in names:
            arr = root.OpenMDArray(nm)
            if arr is None:
                print(f"\n[warn] could not open array {nm!r}, skipping")
                continue
            probe_array(arr)
    else:
        arr = resolve_array(root, args.array)
        probe_array(arr)

    print("\n" + "=" * 70)
    print("Phase 0 probe complete. Transcribe Q1-Q5 observations into the")
    print("evidence log before writing the algorithm hull.")
    print("Remember: this proves BEHAVIOUR, not C++ accessor SPELLINGS.")
    print("=" * 70)


if __name__ == "__main__":
    main()
