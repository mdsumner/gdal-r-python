# BRAN2023 Virtual Zarr / Icechunk — Session Notes

## What we built

A complete pipeline from NetCDF → kerchunk parquet → icechunk on Pawsey Acacia:

```
NetCDF (NCI THREDDS/GADI)
    ↓ GDAL mdim mosaic + rhdf5 H5Dchunk_iter  (R, blocklist package)
kerchunk parquet store          s3://aad-index/vzarr/BRAN2023/ocean_temp.zarr
    ↓ parq2ice.py  (Python, icechunk 2.0.6)
icechunk store                  s3://aad-index/vzarr/BRAN2023/ocean_temp.icechunk
    ↓ /vsiicechunk / zarr-python / future zaro
data access (GDAL ✅  xarray ⚠️  R/zaro 🔜)
```

## Parquet store schema (kerchunk/VirtualiZarr format)

```
path    : string   — URI to source file (HTTPS, S3, file://)
offset  : int64    — byte offset
size    : int64    — byte length  ("length" in VirtualiZarr, "size" in kerchunk)
raw     : binary   — nullable; non-null = inline data (skip for virtual refs)
```

**No key column.** Chunk keys are implicit: row N across lexicographically
sorted `refs.*.parq` files = flat C-order chunk index N.
`record_size: 100000` in `.zmetadata` = rows per shard file.

## Icechunk 2.0 key facts

- Rust crate is **internal** — Python is the supported API
- `session.store` is a zarr-compatible `IcechunkStore`
- `store.set(key, Buffer)` is **async** — use `zsync()` from `zarr.core.sync`
- `store.set_virtual_ref(key, url, offset=, length=, validate_container=False)` is **sync**
- `store.set_virtual_refs_arr(array_path, chunk_grid_shape, locs, offsets, lengths)` is **sync** but requires `len(locs) == product(chunk_grid_shape)` — designed for full-array writes, not shards
- Zarr chunk key format: `/{var}/c/{i0}/{i1}/...` — note the `c/` prefix for V3 default encoding
- `session.store["key"] = bytes` does **not** work — use `store.set()`
- `Repository.writable_session("main")` is **sync** in 2.0
- Commits on local filesystem are slow for many small manifests (S3 is much better)

## Manifest splitting

Without splitting, 17.8M refs × ~135 bytes/ref (full URL per ref) exceeds
the flatbuffers 2GB in-memory limit at commit time.

```python
from icechunk import (ManifestSplittingConfig, ManifestSplitCondition,
                      ManifestSplitDimCondition, ManifestConfig, RepositoryConfig)

split_config = ManifestSplittingConfig.from_dict({
    ManifestSplitCondition.name_matches("temp"): {
        ManifestSplitDimCondition.DimensionName("Time"): 30  # ~monthly
    }
})
config = RepositoryConfig(manifest=ManifestConfig(splitting=split_config))
```

- `DimensionName("Time")` requires `dimension_names` in the array's `zarr.json`
- `dimension_names` comes from `_ARRAY_DIMENSIONS` in zarr V2 `.zattrs`
- Without `dimension_names`, splitting silently does nothing (all refs in one manifest)
- Split size 30 → `ceil(5844/30) = 195` manifests for temp, ~91,800 refs each
- `AnyArray()` applies to all arrays including coordinates — use `name_matches()` to target only large arrays

## Zarr V2 → V3 translation gotchas

| Issue | V2 | V3 fix |
|-------|-----|--------|
| fill_value for floats | raw number `1e+20` | string `"1e+20"` |
| fill_value null | `null` | `"0.0"` for float, `0` for int |
| NaN fill_value | bare `NaN` (invalid JSON) | string `"NaN"` |
| dimension names | in `.zattrs._ARRAY_DIMENSIONS` | `dimension_names` field in `zarr.json` |
| shuffle filter | `{"id": "shuffle", "elementsize": 2}` | absorbed into `bytes` codec — omit |
| chunk key separator | `.` (V2) | `/` with `c/` prefix (V3 default) |
| compressor style | `"compressor": {"id": "zlib"}` | `"codecs": [{"name": "gzip"}]` |
| filters style | `"filters": [{"id": "zlib"}]` | same codecs list |

## Virtual chunk containers (not yet used)

The per-ref URL repetition (89 bytes × 17.8M = 1.5GB) is why the flatbuffers
limit is hit. `VirtualChunkContainer` in `RepositoryConfig` would register a
named base URL so each ref stores only `(container_name, offset, length)`.
In icechunk 2.0 `VirtualChunkLocation` still stores the full URL internally —
containers affect read-time resolution, not manifest storage size. This remains
an open issue for bulk virtual ref import at this scale.

## Compatibility matrix (June 2026)

| Format | GDAL | zarr-python | xarray | R/zaro |
|--------|------|-------------|--------|--------|
| kerchunk parquet (V2) | ✅ | ✅ | ✅ (engine="kerchunk") | ✅ (zaro) |
| icechunk (V3) | ✅ /vsiicechunk | ✅ zarr.open() | ⚠️ fill_value bug | 🔜 |

xarray bug: `xr.open_zarr(session.store, consolidated=False)` fails with
`TypeError: Failed to decode fill_value: expected str or bytes for dtype float64, got float`
— zarr-python 3.2.1 decodes fill_value to numpy scalar before xarray sees it,
but xarray's zarr V3 codepath expects the raw string. Workaround:
`mask_and_scale=False`.

## VirtualiZarr KerchunkParquetParser issue

Opening a GDAL-produced kerchunk parquet store via VirtualiZarr fails:
```
TypeError: Expected codec config to be a dict, but got value id of type str
```
The shuffle filter `{"id": "shuffle", "elementsize": 2}` is not handled by
VirtualiZarr's V2→V3 codec translator. Shuffle is a numcodecs concept that
has no direct zarr V3 equivalent — it's absorbed into the `bytes` codec.
This is a VirtualiZarr bug; the shuffle filter should be silently dropped
during translation.

## Acacia S3 config

```python
ic.s3_storage(
    bucket="aad-index",
    prefix="vzarr/BRAN2023/ocean_temp.icechunk",
    endpoint_url="https://projects.pawsey.org.au",
    access_key_id=os.environ["PAWSEY_AWS_ACCESS_KEY_ID"],
    secret_access_key=os.environ["PAWSEY_AWS_SECRET_ACCESS_KEY"],
    force_path_style=True,
    region="",
)
```

pyarrow S3FileSystem for reading parquet from Acacia — use `s3fs` not pyarrow:
```python
import s3fs
fs = s3fs.S3FileSystem(
    key=access_key, secret=secret_key,
    endpoint_url="https://projects.pawsey.org.au",
    client_kwargs={"endpoint_url": "https://projects.pawsey.org.au"},
)
# paths include bucket: "aad-index/vzarr/BRAN2023/ocean_temp.zarr/temp/refs.0.parq"
```

## Open questions / next steps

1. **VirtualiZarr shuffle filter bug** — PR to drop shuffle in
   `zarr_codec_config_to_v3` when translating V2→V3
2. **xarray fill_value bug** — zarr-python 3.2.1 decodes before xarray;
   needs coordination between zarr-python and xarray V3 codepath
3. **blocklist → zarr V3 directly?** — Currently writes kerchunk parquet (V2
   metadata). Writing V3 zarr.json directly would bypass the V2→V3 translation
   entirely, but requires zarr V3 codec names in the R/GDAL layer
4. **Round-trip test** — one BRAN NetCDF → blocklist parquet → VirtualiZarr
   ManifestStore → icechunk → xarray, comparing against VirtualiZarr's own
   NetCDF→icechunk path to identify all differences
5. **Virtual chunk containers** — once `VirtualChunkLocation` stores relative
   paths, the flatbuffers limit goes away and the batching workaround is
   unnecessary
6. **zaro** — R package reading icechunk stores via zarrs; the missing piece
   for the R side of this pipeline
