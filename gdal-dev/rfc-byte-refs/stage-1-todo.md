# `gdal mdim get-refs` — Stage 1 TODO / known issues

*Captured at the point Stage 1 is functionally complete on the HDFEOS fixture
(`dummy_HDFEOS_swath_chunked.h5`, 392 chunks, all-partial trailing corner
verified against Phase 0 evidence log). Tracks remaining polish before
PR-readiness and known characteristics worth surfacing in the eventual RFC.*

---

## Must-do before PR

### Argument-surface coverage tests

Every declared arg needs at least one positive and one negative test:

- [ ] `--array` present (valid) — covered
- [ ] `--array` absent — confirm framework parse-time error fires
      (`SetRequired()` check)
- [ ] `--array` referencing non-existent path — error message correct,
      `gdal mdim info` suggestion fires
- [ ] `--of` present (valid) — covered (`GPKG`, `Parquet` exercised)
- [ ] `--of` absent — confirm framework parse-time error fires
- [ ] `--of` referencing a non-vector driver (e.g. `HFA`) — confirm "vector
      driver" wording in error fires
- [ ] `--of` referencing a non-existent driver — confirm "available drivers"
      suggestion fires
- [ ] `--open-option` passthrough — pick an option the input driver accepts
      (e.g. `IGNORE_XY_AXIS_NAME_CHECKS=YES` for some netCDF cases) and confirm
      it reaches the open
- [ ] `--input-format` constraint — pass a real format, confirm it constrains
      the open driver list correctly
- [ ] `<INPUT>` non-existent path — confirm framework-level open failure is
      clean
- [ ] `<OUTPUT>` to a location that can't be created (permissions, missing
      parent dir) — confirm error is readable
- [ ] `<INPUT>` and `<OUTPUT>` both absent — confirm framework reports missing
      positionals

### `--overwrite` support

The framework's overwrite protection currently catches existing output files
("Dataset 'X' already exists. You may specify the --overwrite option."), but
the algorithm does not actually wire `--overwrite`. Needs the appropriate
`AddArg` declaration (mirror `convert` or `footprint` for the convention) and
the corresponding flag bit when calling `GDALDriver::Create` (often
`GA_Update` flag families or driver-specific overwrite options). Confirm the
exact spelling against an existing algorithm that supports it.

### Progress callback

`pfnProgress` is enabled. 

### Remote-source end-to-end test

Once progress is wired, run against `/vsicurl` BRAN at scale. Verifies
network path, real-world timing, and progress UX in one test. Expected wall
time: minutes (per-call cost compounded over HTTP RTT). If unacceptable in
practice, that's a finding to surface — but Stage 1's contract is
correctness, not throughput.

### Documentation stub

`doc/source/programs/gdal_mdim_get_refs.rst` needs at least a skeleton page;
GDAL CI checks every registered algorithm has a doc page. Mirror
`gdal_mdim_info.rst` for the structure (synopsis, options, examples).

---

## Known characteristics worth noting in the RFC

### Performance against per-call API

Local-disk, netCDF-over-HDF5, 94,860 chunks (BRAN2023 one month): ~71s.
Same file via VirtualiZarr's bulk HDF5 index reader: ~4s. Gap is per-call
mdim API vs. bulk index walk. Closing it is naturally a driver-internal
concern (chunk-index cache populated on first access, e.g. via
`H5Dchunk_iter` for HDF5-family drivers) and orthogonal to Stage 1's contract.

### Column naming: `offset` is a SQL reserved word

Consumers querying the output via SQL must quote: `"offset"` in standard
SQL. Not a defect — `offset` is the correct semantic name — but worth
documenting so first-time SQL users aren't surprised. Renaming to
`byte_offset` or similar was considered and rejected: the rename
uglifies all consumers to spare SQL users a 5-character escape.

### `--array` name resolution: classic vs. mdim name conventions

Names in HDF5/netCDF with spaces (e.g. HDF-EOS `Data Fields` group) are
sanitized to underscores in the classic GDAL API (`gdalinfo` SUBDATASET
output uses `Data_Fields`), but preserved verbatim in the mdim API.
`get-refs` correctly consumes the mdim form. Users copy-pasting array
paths from `gdalinfo` will hit a "cannot find group" error. The error
message points at `gdal mdim info` as the correct discovery tool.
Not a defect; cross-API name mismatch is a wider GDAL UX issue.

### Layer name in Parquet output

The Parquet driver derives the layer name from the output file stem rather
than honoring the algorithm-supplied `CreateLayer` name. The array name
remains authoritative in `ARRAY_NAME` layer metadata. Consumers needing the
"real" name should consult metadata rather than the file's layer name.
GPKG honors the supplied name; this is a Parquet-driver characteristic.

### Codec hoist policy

`papszInfo` is currently both stored per-row (`info` field) *and* hoisted
to layer metadata as `CODEC_*` items on the first successful file-backed
chunk. Per-row redundancy is intentional for Stage 1 (open question in
RFC: should it survive?) — preserves information density for SQL queries
that group/filter by codec without joining layer metadata. Decision
deferred to RFC discussion.

### Classic netCDF is not supported

Documented in the netCDF driver source: classic (non-HDF5-backed) netCDF
files cannot be addressed by `GetRawBlockInfo`. The Stage B3 block-size-zero
guard catches this: classic files report a block size that fails the
guard and the algorithm declines cleanly with "no natural block size, not
chunk-enumerable." User sees a clear message; not a corrupt run. The
same guard correctly handles mosaic-VRT synthesized coordinate arrays
(Phase 0 Q5/bonus).

### Single-array contract (deferred whole-dataset traversal)

`--array` is required. Whole-dataset traversal (extracting refs for all
arrays in one invocation) is deferred to a future stage. The required-arg
discipline keeps Stage 1's contract narrow and observable.

### Verified cases of raw block info 

- from parquet pre-virtualized Zarr
- from material Zarr (out or range block is info-get error)
- from material Zarr but block is missing (null, 0 values for path, size, offset)
- from mdim VRT of netcdf, indirection to remote source

see here for details: https://github.com/mdsumner/gdal-r-python/blob/main/gdal-dev/rfc-byte-refs/format-generality-verification.md

Note however that performance is limiting when GetRawBlockInfo works per-chunk. In HDF5 there's an `H5Dchunk_iter` which gets all the
refs very quickly, even via remote. For parquet stores a similar shortcut would have to be enabled to hit the underlying parquet store, and to normalize
across multiple sources referenced in a VRT. 

---

## Open design questions (RFC discussion items)

These are *open*, not bugs — items where Stage 1 has a working answer but
the RFC review should validate or refine:

- [ ] `dim_*` field naming: integer-indexed (`dim_0`, `dim_1`) vs.
      dimension-named (`dim_time`, `dim_yt_ocean`). Currently indexed for
      schema stability and to sidestep sanitization. Names live in
      `DIM_N_NAME` layer metadata.
- [ ] Per-row `info` field vs. layer-metadata codec hoist: keep both?
      Just one? Currently both.
- [ ] Default output format. Currently `--of` required (no default).
      Eventual default `Parquet` is reasonable but deferred until there's
      evidence of common usage.
- [ ] Three-state classification surface: `present` as `Integer(Boolean)`
      with `path`/`offset`/`size` nullable feels right. Inline-data is
      Stage 1b (not in Stage 1's schema yet — no `OFTBinary` payload
      field). Worth confirming with reviewers whether Stage 1 should
      include the field declaration with null payload as a forward-compat
      gesture, or wait for Stage 1b to add it.

---

## Out of scope for Stage 1 (deferred)

- Index-space geometry column (Stage 2)
- Projected/native-CRS geometry (Stage 3)
- Whole-dataset traversal (post-Stage-3)
- Inline-data payload as `OFTBinary` field (Stage 1b)
- Latent classic-API widening (post-mdim consolidation)
- Pipeline-step integration (separate stage; mechanically transposable
  from current `RunImpl` body into a `RunStep` if/when needed)

## Example output and downstream use

local file or remote doesn't care


```R
# MDIM-GET-REFS: array /temp: dims=[31, 51, 1500, 3600], blocks=[1, 1, 300, 300], chunks=[31, 51, 5, 12], total=94860, dtype=Int16
# MDIM-GET-REFS: created layer 'temp' with 9 fields, ready for 94860 features
# MDIM-GET-REFS: chunk 0 coords=[0, 0, 0, 0] path=bluelink/ocean_temp_2010_01.nc offset=46353 size=74186
# MDIM-GET-REFS: chunk 1 coords=[0, 0, 0, 1] path=bluelink/ocean_temp_2010_01.nc offset=120539 size=66578

# tibble::as_tibble(arrow::read_parquet("~/temp.parquet"))
# # A tibble: 94,860 × 9
# dim_0 dim_1 dim_2 dim_3 present path                            offset  size info                                                  
# <int> <int> <int> <int> <lgl>   <chr>                          <int64> <int> <chr>                                                 
# 1     0     0     0     0 TRUE    bluelink/ocean_temp_2010_01.nc   46353 74186 COMPRESSION=DEFLATE; FILTER=SHUFFLE; ENDIANNESS=LITTLE
# 2     0     0     0     1 TRUE    bluelink/ocean_temp_2010_01.nc  120539 66578 COMPRESSION=DEFLATE; FILTER=SHUFFLE; ENDIANNESS=LITTLE

size <- 74186
offset <- 46353
#con <- file("~/bluelink/ocean_temp_2010_01.nc", "rb")
#seek(con, offset)

con <- url("https://thredds.nci.org.au/thredds/fileServer/gb6/BRAN/BRAN2023/daily/ocean_temp_2010_01.nc", "rb")
seekjunk <- readBin(con, raw(), n = offset)
raw_chunk <- readBin(con, raw(), n = size)
close(con)
deflated  <- memDecompress(raw_chunk, type = "gzip")   # or "deflate" depending on header
# unshuffle: itemsize=2, n_elements = length(deflated)/2
unshuffle <- function(x, itemsize) {
  n <- length(x) %/% itemsize
  idx <- as.vector(t(matrix(seq_along(x), nrow = itemsize, byrow = FALSE)))
  # interleave byte planes back
  x[order(idx)]
}
vals <- readBin(unshuffle(deflated, 2L), integer(), n = length(deflated)/2, size = 2, endian = "little")
ximage::ximage(matrix(vals, 300, byrow = T))

```
