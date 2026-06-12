"""
parq2ice.py — convert a kerchunk parquet virtual reference store to icechunk.

Requires:
    pip install icechunk pyarrow numpy

Usage:
    python parq2ice.py <parq_store> <ice_store>

Example:
    python parq2ice.py ocean_temp.zarr ocean_temp.icechunk
"""

import asyncio
import json
import math
import sys
from pathlib import Path

import icechunk as ic
from icechunk import (
    ManifestConfig,
    ManifestSplitCondition,
    ManifestSplitDimCondition,
    ManifestSplittingConfig,
    RepositoryConfig,
)
import numpy as np
import pyarrow.parquet as pq
import zarr.core.buffer.cpu
from zarr.core.sync import sync as zsync


# ── zarr V2 → V3 metadata translation ────────────────────────────────────────

def v2_dtype_to_v3(dtype: str) -> str:
    return {
        "f4": "float32", "f8": "float64", "f2": "float16",
        "i1": "int8",  "i2": "int16",  "i4": "int32",  "i8": "int64",
        "u1": "uint8", "u2": "uint16", "u4": "uint32", "u8": "uint64",
        "b1": "bool",
    }.get(dtype.lstrip("<>=|"), "float32")


def encode_fill_value(fill_value, dtype_str: str):
    """Encode fill_value correctly for zarr V3.
    Float dtypes require string-encoded fill values ("0.0", "NaN", "Infinity").
    Integer dtypes use raw numbers.
    """
    v3_dtype = v2_dtype_to_v3(dtype_str)
    is_float = v3_dtype in ("float32", "float64", "float16")

    if fill_value is None:
        return "0.0" if is_float else 0

    if is_float:
        try:
            fv = float(fill_value)
        except (TypeError, ValueError):
            return "0.0"
        if fv != fv:        return "NaN"
        if fv == float("inf"):  return "Infinity"
        if fv == float("-inf"): return "-Infinity"
        return str(fv)

    return fill_value


def build_codecs(zarray: dict) -> list:
    dtype  = zarray.get("dtype", "<f4")
    order  = zarray.get("order", "C")
    codecs = []

    if order == "F":
        ndim = len(zarray.get("shape", []))
        codecs.append({"name": "transpose",
                       "configuration": {"order": list(reversed(range(ndim)))}})

    endian = "little" if dtype.startswith("<") else "big" if dtype.startswith(">") else "little"
    codecs.append({"name": "bytes", "configuration": {"endian": endian}})

    for f in (zarray.get("filters") or []):
        fid = f.get("id", "")
        if fid in ("zlib", "gzip"):
            codecs.append({"name": "gzip",  "configuration": {"level": f.get("level", 1)}})
        elif fid == "blosc":
            codecs.append({"name": "blosc", "configuration": {
                "cname": f.get("cname", "lz4"), "clevel": f.get("clevel", 5),
                "shuffle": f.get("shuffle", 1)}})
        elif fid == "zstd":
            codecs.append({"name": "zstd",  "configuration": {"level": f.get("level", 3)}})

    comp = zarray.get("compressor")
    if comp:
        cid = comp.get("id", "")
        if cid in ("zlib", "gzip"):
            codecs.append({"name": "gzip",  "configuration": {"level": comp.get("level", 1)}})
        elif cid == "blosc":
            codecs.append({"name": "blosc", "configuration": {
                "cname": comp.get("cname", "lz4"), "clevel": comp.get("clevel", 5),
                "shuffle": comp.get("shuffle", 1)}})
        elif cid == "zstd":
            codecs.append({"name": "zstd",  "configuration": {"level": comp.get("level", 3)}})

    return codecs


def zarray_to_zarr_json(zarray: dict, zattrs: dict) -> dict:
    shape  = zarray["shape"]
    chunks = zarray.get("chunks", shape)
    return {
        "zarr_format": 3,
        "node_type":   "array",
        "shape":       shape,
        "data_type":   v2_dtype_to_v3(zarray.get("dtype", "<f4")),
        "chunk_grid":  {"name": "regular",
                        "configuration": {"chunk_shape": chunks}},
        "chunk_key_encoding": {"name": "default", "separator": "/"},
        "fill_value":  encode_fill_value(
                           zarray.get("fill_value") if zarray.get("fill_value") is not None else 0,
                           zarray.get("dtype", "<f4")),
        "codecs":      build_codecs(zarray),
        "dimension_names": zattrs.get("_ARRAY_DIMENSIONS"),
        "attributes":  zattrs,
    }


# ── parquet reading ───────────────────────────────────────────────────────────

def make_s3fs(endpoint_url: str, access_key: str, secret_key: str):
    """Return an s3fs filesystem for reading parquet from object store."""
    import s3fs
    return s3fs.S3FileSystem(
        key=access_key,
        secret=secret_key,
        endpoint_url=endpoint_url,
        client_kwargs={"endpoint_url": endpoint_url},
    )


def ref_parquet_files_s3(fs, prefix: str) -> list[str]:
    """List refs.*.parq files under an S3 prefix using s3fs."""
    files = sorted(
        f for f in fs.ls(prefix, detail=False)
        if (f.split("/")[-1].startswith("refs.") and
            (f.endswith(".parq") or f.endswith(".parquet")))
    )
    return files


def ref_parquet_files(var_dir: Path) -> list[Path]:
    return sorted(
        f for f in var_dir.iterdir()
        if f.name.startswith("refs.") and f.suffix in (".parq", ".parquet")
    )


def read_shard(path, fs=None) -> tuple[list[str], np.ndarray, np.ndarray, int]:
    """
    Read one parquet shard.  Returns (locations, offsets, lengths, n_rows).
    path may be a local Path or an S3 key string; fs is an s3fs filesystem.
    """
    if fs is not None:
        with fs.open(path, "rb") as f:
            t = pq.read_table(f)
    else:
        t = pq.read_table(path)
    cols = t.schema.names
    length_col = "length" if "length" in cols else "size"
    has_raw = "raw" in cols

    locations = t.column("path").to_pylist()
    offsets   = t.column("offset").to_numpy(zero_copy_only=False).astype(np.uint64)
    lengths   = t.column(length_col).to_numpy(zero_copy_only=False).astype(np.uint64)
    raws      = t.column("raw").to_pylist() if has_raw else [None] * len(locations)

    for i, raw in enumerate(raws):
        if raw is not None:
            locations[i] = None

    return locations, offsets, lengths, len(locations)


# ── main conversion ───────────────────────────────────────────────────────────

def sanitise_nan(s: str) -> str:
    for tok, quoted in [("NaN", '"NaN"'), ("Infinity", '"Infinity"'), ("-Infinity", '"-Infinity"')]:
        for pat in (f": {tok}", f":{tok}", f"[{tok}", f",{tok}"):
            s = s.replace(pat, pat.replace(tok, quoted))
    return s


def build_icechunk_store(parq_store, storage,
                         split_var: str = "temp",
                         split_dim: str = "Time",
                         split_size: int = 30,
                         source_fs=None,
                         source_prefix: str = None):

    # ── parse .zmetadata ──────────────────────────────────────────────────────
    if source_fs is not None:
        with source_fs.open(f"{source_prefix}/.zmetadata", "rb") as f:
            raw = f.read().decode()
    else:
        raw = (parq_store / ".zmetadata").read_text()
    raw = sanitise_nan(raw)
    zmetadata = json.loads(raw)["metadata"]

    arrays = {}
    for key, val in zmetadata.items():
        if key.endswith("/.zarray"):
            var = key[:-len("/.zarray")]
            arrays.setdefault(var, {})["zarray"] = val
        elif key.endswith("/.zattrs"):
            var = key[:-len("/.zattrs")]
            arrays.setdefault(var, {})["zattrs"] = val

    # Only vars that have a parquet directory
    if source_fs is not None:
        def var_exists(var):
            return source_fs.exists(f"{source_prefix}/{var}")
    else:
        def var_exists(var):
            return (parq_store / var).is_dir()

    arrays = {
        var: info for var, info in arrays.items()
        if "zarray" in info and var_exists(var)
    }
    print(f"Found {len(arrays)} variables: {sorted(arrays)}")

    # ── create icechunk repository ────────────────────────────────────────────
    # Split on dimension index 0 (Time) with size 1 — one manifest per time
    # step, each covering all spatial chunks for that day.
    # For temp [5844,51,1500,3600]/[1,1,300,300]: 51×5×12 = 3060 refs/manifest.
    split_config = ManifestSplittingConfig.from_dict({
        ManifestSplitCondition.name_matches(split_var): {
            ManifestSplitDimCondition.DimensionName(split_dim): split_size
        }
    })
    print(f"Manifest splitting: {split_var!r} on dim {split_dim!r} "
          f"every {split_size} chunks")
    config  = RepositoryConfig(manifest=ManifestConfig(splitting=split_config))
    repo    = ic.Repository.create(storage, config=config)

    # ── root group ────────────────────────────────────────────────────────────
    session = repo.writable_session("main")
    store   = session.store
    zsync(store.set("zarr.json", zarr.core.buffer.cpu.Buffer.from_bytes(
        json.dumps({"zarr_format": 3, "node_type": "group"}).encode())))
    session.commit("Create root group", allow_empty=True)
    print("Root group committed.")

    # ── per-variable import ───────────────────────────────────────────────────
    for var, info in sorted(arrays.items()):
        zarray = info["zarray"]
        zattrs = info.get("zattrs", {})

        shape        = zarray["shape"]
        chunks       = zarray.get("chunks", shape)
        chunk_counts = tuple(math.ceil(s / c) for s, c in zip(shape, chunks))
        total_refs   = math.prod(chunk_counts)

        print(f"\n{var}: shape={shape} chunk_counts={chunk_counts} "
              f"total_refs={total_refs:,}")

        session = repo.writable_session("main")
        store   = session.store

        # Write array metadata
        zsync(store.set(f"{var}/zarr.json", zarr.core.buffer.cpu.Buffer.from_bytes(
            json.dumps(zarray_to_zarr_json(zarray, zattrs)).encode())))

        if source_fs is not None:
            files = ref_parquet_files_s3(source_fs, f"{source_prefix}/{var}")
        else:
            files = ref_parquet_files(parq_store / var)
        flat_offset = 0   # running flat C-order index across shards
        n_written   = 0

        for f in files:
            locations, offsets, lengths, n_rows = read_shard(f, fs=source_fs)

            # Filter to non-null rows and build parallel arrays
            valid_idx   = [i for i, loc in enumerate(locations) if loc is not None]
            if not valid_idx:
                flat_offset += n_rows
                continue

            valid_locs    = [locations[i] for i in valid_idx]
            valid_offsets = offsets[valid_idx].astype(np.uint64)
            valid_lengths = lengths[valid_idx].astype(np.uint64)

            for i, (loc, off, lng) in enumerate(zip(valid_locs, valid_offsets, valid_lengths)):
                nd = flat_to_nd(flat_offset + valid_idx[i], chunk_counts)
                key = f"/{var}/c/" + "/".join(str(x) for x in nd)
                store.set_virtual_ref(key, loc, offset=int(off), length=int(lng),
                                      validate_container=False)

            n_written   += len(valid_idx)
            flat_offset += n_rows

            if n_written % 500_000 == 0 and n_written > 0:
                print(f"  {n_written:,} / {total_refs:,} refs written...")

        snap = session.commit(f"Import {var}")
        print(f"  {n_written:,} refs committed → {snap}")

    print(f"\nDone.")


def flat_to_nd(flat: int, counts: tuple) -> tuple:
    indices = []
    for c in reversed(counts):
        indices.append(flat % c)
        flat //= c
    return tuple(reversed(indices))


def main():
    import argparse
    import os
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("parq_store", help="kerchunk parquet store directory")
    parser.add_argument("ice_store",  help="icechunk store name (local path or S3 prefix suffix)")
    parser.add_argument("--split-var",  default="temp",
                        help="variable to split manifests for (default: temp)")
    parser.add_argument("--split-dim",  default="Time",
                        help="dimension name to split along (default: Time)")
    parser.add_argument("--split-size", default=30, type=int,
                        help="chunks per manifest along split-dim (default: 30 = ~monthly)")
    parser.add_argument("--acacia-source", action="store_true",
                        help="read parquet store from Acacia S3 (uses same credentials)")
    parser.add_argument("--source-bucket", default="aad-index",
                        help="S3 source bucket (default: aad-index)")
    parser.add_argument("--source-prefix", default=None,
                        help="S3 source prefix (default: vzarr/BRAN2023/<parq_store>)")
    parser.add_argument("--acacia", action="store_true",
                        help="write to Pawsey Acacia S3 (uses PAWSEY_AWS_ACCESS_KEY_ID/SECRET)")
    parser.add_argument("--bucket", default="aad-index",
                        help="S3 dest bucket (default: aad-index)")
    parser.add_argument("--prefix", default=None,
                        help="S3 dest prefix (default: vzarr/BRAN2023/<ice_store>)")
    args = parser.parse_args()

    access_key = os.environ.get("PAWSEY_AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("PAWSEY_AWS_SECRET_ACCESS_KEY")

    # ── source storage ────────────────────────────────────────────────────────
    source_fs     = None
    source_prefix = None
    parq_store    = None

    if args.acacia_source:
        if not access_key or not secret_key:
            print("Error: PAWSEY_AWS_ACCESS_KEY_ID and PAWSEY_AWS_SECRET_ACCESS_KEY must be set")
            sys.exit(1)
        source_prefix = f"{args.source_bucket}/{args.source_prefix or f'vzarr/BRAN2023/{args.parq_store}'}"
        source_fs = make_s3fs(
            endpoint_url="https://projects.pawsey.org.au",
            access_key=access_key,
            secret_key=secret_key,
        )
        print(f"Reading from Acacia s3://{source_prefix}")
    else:
        parq_store = Path(args.parq_store)
        if not parq_store.exists():
            print(f"Error: {parq_store} does not exist"); sys.exit(1)

    # ── destination storage ───────────────────────────────────────────────────
    if args.acacia:
        if not access_key or not secret_key:
            print("Error: PAWSEY_AWS_ACCESS_KEY_ID and PAWSEY_AWS_SECRET_ACCESS_KEY must be set")
            sys.exit(1)
        dest_prefix = args.prefix or f"vzarr/BRAN2023/{args.ice_store}"
        storage = ic.s3_storage(
            bucket=args.bucket,
            prefix=dest_prefix,
            endpoint_url="https://projects.pawsey.org.au",
            access_key_id=access_key,
            secret_access_key=secret_key,
            force_path_style=True,
            region="",
        )
        print(f"Writing to Acacia s3://{args.bucket}/{dest_prefix}")
    else:
        ice_path = Path(args.ice_store)
        if ice_path.exists():
            print(f"Error: {ice_path} already exists — remove it first"); sys.exit(1)
        storage = ic.local_filesystem_storage(str(ice_path))
        print(f"Writing to local: {ice_path}")

    build_icechunk_store(parq_store, storage,
                         split_var=args.split_var,
                         split_dim=args.split_dim,
                         split_size=args.split_size,
                         source_fs=source_fs,
                         source_prefix=source_prefix)


if __name__ == "__main__":
    main()
