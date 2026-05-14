# Evidence log — Phase 0 findings

*Companion to `rfc-mdim-chunk-references.md`. This log records what was actually
observed when `GetRawBlockInfo()` was probed against real datasets and one
controlled fixture, during Phase 0. It is the bridge that converts the RFC's
"open questions" section into "resolved, here's how" — and the record that lets
the eventual algorithm hull be transcription rather than discovery.*

*Method: probed via `osgeo.gdal` (the SWIG bindings maintained in the GDAL tree
— the same surface the algorithm compiles against), using
`probe_getrawblockinfo_v2.py`. One important boundary: these runs prove
**behaviour**, not C++ **accessor spellings** — see the dedicated section below.*

---

## Datasets probed

| label | source | driver | role |
|---|---|---|---|
| OISST | `/vsicurl/` NCEI AVHRR netCDF (`oisst-avhrr-v02r01.19810910.nc`) | netCDF | remote read; single-chunk control |
| CMEMS | `ZARR:/vsicurl/` CloudFerro S3, SEALEVEL SSH `timeChunked.zarr`, array `sla` | ZARR | real Zarr, non-even chunks, sparse chunks |
| BRAN-Parquet | `ZARR:/vsis3/` Pawsey, BRAN VirtualiZarr Parquet store `ocean_temp_2023.parq`, arrays `temp` / `xt_ocean` | ZARR | real VirtualiZarr reference store; inline coord array |
| BRAN-mosaic | `gdal mdim mosaic` VRT over 3 consecutive BRAN monthly netCDFs, arrays `temp` / `Time` | VRT | VRT forwarding behaviour |
| noneven | crafted local netCDF4, `make_noneven_netcdf.py`, array `data` | netCDF | controlled non-even chunking; Q1 for netCDF |

Not yet probed: a source opened directly by the **HDF5 driver** (BRAN is
netCDF4, HDF5-backed but opened via the netCDF driver). See "Driver coverage"
below — a gap in coverage, not an open question.

---

## The five questions

### Q1 — Partial edge chunks: is ceil-division the correct grid extent?

**RESOLVED — ceil-division is correct. Trailing partial chunks are individually
addressable.**

Confirmed for two driver families, in each case with corroborating size
evidence (the partial chunk returns a proportionally smaller payload, exactly
as its reduced extent predicts):

- **ZARR** (CMEMS `sla`): `longitude` 1440 at block 512 → ceil 3, floor 2.
  `GetRawBlockInfo((1059,0,2))` — the trailing partial chunk — returned a real
  file-backed result, size 310820, against the interior chunk `(1059,0,1)` at
  373488. Smaller, consistent with the 416-wide remainder strip.
- **netCDF** (crafted `noneven` fixture, array `data`): dims/chunks
  `time 10/3, y 100/30, x 250/64` → ceil grid `[4,4,4]`=64, floor `[3,3,3]`=27
  (floor would wrongly drop 37 chunks). `GetRawBlockInfo((3,3,3))` — the corner
  partial chunk, ground-truth extent `1×10×58` — returned a real file-backed
  result, size 1099, against interior chunks at ~9216. ~1/10 the data, ~1/10
  the bytes.

**Implication for the algorithm:** the chunk-grid enumeration uses
ceil-division: `n_chunks[i] = (dim_size[i] + block[i] - 1) // block[i]`.

**Residual:** confirmed for ZARR and netCDF; expected to hold for HDF5 by the
same mechanism but not yet exercised directly. "Confirmed, two families;
expected for HDF5."

### Q2 — Sparse chunks: how is a valid-but-absent chunk reported?

**RESOLVED — a valid-but-absent chunk returns success with null filename,
offset 0, size 0, null info. Absence is detected by the NULL FILENAME, never by
`offset == 0`.**

- **CMEMS `sla`**, origin `(0,0,0)`: returned `filename=None, offset=0, size=0,
  info=None, inline=None` — a genuine absent (sparse) chunk.
- The same run also returned **present** chunks with `offset=0` and a real
  filename: `(1059,0,1)` and `(1059,0,2)` both had `offset=0` (Zarr is
  one-file-per-chunk, so every chunk starts at offset 0 of its own file). This
  is the proof that `offset==0` is a legal offset and cannot be the absence
  signal — CMEMS would break an offset-based detector immediately.

**Implication for the algorithm:** detect absence via null/empty filename
(combined with no inline payload). Emit such chunks as features with
`present = false`; do not drop them — absent chunks are information.

### Q3 — Inline chunks: how does an inline payload surface?

**RESOLVED — an inline chunk returns null filename, null offset, NON-ZERO size,
and a populated inline payload. Distinguishable from sparse by size > 0 and the
presence of the inline payload.**

- **BRAN-Parquet `xt_ocean`** (a 1-D longitude coordinate array): origin `(0,)`
  returned `filename=None, offset=0, size=28800, inline=<28800 bytes>` —
  classified INLINE.

Three storage states now observed on real data, all cleanly distinguishable:
- **present (file-backed):** real filename, real offset
- **absent (sparse):** null filename, size 0, no inline payload
- **inline:** null filename, non-zero size, inline payload present

**Note — inline is the normal state of coordinate variables.** `xt_ocean` is a
coordinate axis; VirtualiZarr/kerchunk routinely inline small coordinate arrays
rather than referencing them. So Stage 1's deferral of inline data primarily
affects **coordinate variables** — Stage 1 fully handles the *data* arrays of a
VirtualiZarr store but under-serves its *coordinate* arrays. Stage 1b (inline
support) is what completes coordinate-variable coverage. This should be stated
precisely in the RFC.

**Implication for the algorithm:** Stage 1 reports an inline chunk as
`present = true, path = null, offset = null, size = <n>` and does NOT emit the
bytes (deferred to Stage 1b). Every other field stays correct.

### Q4 — Info-key shape: what does the codec description look like across drivers?

**RESOLVED — `info` is a list of `KEY=VALUE` strings; the keys differ by driver
and the VALUE may itself be structured (JSON). It must be passed through
VERBATIM — any attempt to parse it into typed columns is doomed.**

Three distinct shapes observed:

- **netCDF** (OISST, noneven): `['COMPRESSION=DEFLATE', 'FILTER=SHUFFLE',
  'ENDIANNESS=LITTLE']` — flat scalar values.
- **CMEMS Zarr** (`sla`): `['COMPRESSOR={ "blocksize": 0, "clevel": 5, "cname":
  "lz4", "id": "blosc", "shuffle": 1 }', 'ENDIANNESS=LITTLE']` — the
  `COMPRESSOR` value is a JSON **object**.
- **BRAN-Parquet Zarr** (`temp`): `['FILTERS=[ { "id": "shuffle",
  "elementsize": 2 }, { "id": "zlib", "level": 1 } ]', 'ENDIANNESS=LITTLE']` —
  the `FILTERS` value is a JSON **array** of codec objects.

Keys vary (`COMPRESSION`/`FILTER` vs `COMPRESSOR` vs `FILTERS`); value types
vary (scalar vs object vs array); `=` appears *inside* the JSON values. A
`KEY=VALUE` parser would choke immediately.

**Implication for the algorithm:** treat `info` as opaque text. Pass it through
verbatim — joined for a per-row field, or (preferred) hoisted as-is into layer
metadata, since the codec chain is an array-level property. The RFC's existing
"passes it through verbatim; does not parse or normalise" line is not merely
conservative — these three strings are the evidence that it is the *only*
correct choice. Normalisation across drivers, if ever wanted, is a separate and
hard future effort.

### Q5 — VRT forwarding: when does GetRawBlockInfo() work through a VRT?

**RESOLVED — VRT support is FORWARDING, and forwardability is PER-ARRAY within a
single dataset. A mosaicked data array (mosaic axis chunked at 1) forwards
cleanly to the underlying source files; a synthesised coordinate array reports
block size 0 and is not chunk-enumerable.**

From one `gdal mdim mosaic` VRT over three consecutive BRAN monthly netCDFs:

- **`temp`** (the data array, 90 timesteps = 31+28+31): `GetRawBlockInfo` per
  chunk returns references that point *through* to the original source files —
  origin chunk → `ocean_temp_2010_01.nc` offset 46353, last chunk →
  `ocean_temp_2010_03.nc` offset 5119018053. The VRT forwards; it does not
  claim the bytes. Works because the mosaic axis (`Time`) chunks at block size
  1, so every VRT block maps to exactly one source block in one source file —
  the "one VRT block ← one source block" precondition holds.
- **`Time`** (the coordinate array the mosaic had to synthesise from three
  files): reports **block size 0** — "no natural block size". The VRT has no
  single source block to forward to. Not chunk-enumerable. The probe correctly
  reported this as a valid declined state rather than crashing.

**Implications for the algorithm and the RFC:**
- The VRT caveat is an **array-level** property, not a dataset-level one — the
  same VRT has both enumerable and non-enumerable arrays. The RFC's VRT caveat
  should be reworded accordingly.
- This is another nail in the per-driver `DCAP` flag idea: VRT can't advertise
  "it depends" even at the *dataset* level — it varies array by array. Any
  capability introspection must be answerable **per-array**.
- The algorithm must handle both within one dataset: enumerate `temp`, and when
  pointed at `Time`, decline cleanly with a clear message.

---

## Bonus findings (not among Q1–Q5, surfaced by the runs)

### Out-of-range coordinate → clean RuntimeError

Confirmed three times across both driver families (OISST netCDF, noneven
netCDF, CMEMS Zarr, BRAN-Parquet Zarr): a block coordinate past the ceil extent
raises `RuntimeError: invalid block coordinate (N) for dimension D`, not a
silent success or garbage.

**Implication:** the enumeration loop does not need in-loop bounds paranoia. If
the ceil arithmetic is correct, every generated coordinate is valid; if it is
wrong, GDAL errors loudly rather than corrupting silently.

### 64-bit offsets are real, not precautionary

**BRAN-Parquet `temp`**, chunk `(5478,50,4,11)`: `offset = 5034647080` — ~5 GB,
well past 32-bit signed range. **BRAN-mosaic `temp`**, chunk `(89,50,4,11)`:
`offset = 5119018053`.

**Implication:** `OFTInteger64` for the `offset` (and `size`) fields is
mandatory, with live evidence — not a theoretical safeguard. Citable concrete
numbers for the RFC.

### Zarr vs netCDF populate the reference triple differently

- **netCDF / HDF5:** many chunks at different offsets *within* one shared file
  (OISST origin: offset 1029553 into the file; BRAN `temp` origin: 46353).
- **Zarr:** one file per chunk, so every chunk is `{distinct path, offset 0,
  full file size}` (CMEMS, BRAN-Parquet).

The `{path, offset, size}` schema expresses both; they just fill it in
differently. Worth noting in the RFC as evidence the reference record is
genuinely format-general.

### Block size 0 is a valid state, not just an error to guard

A `gdal mdim mosaic` VRT's synthesised coordinate array (`Time`) naturally
reports block size 0. This is not corruption — it is the honest answer "this
array has no natural block structure." The algorithm should treat block-size-0
as a clean **"not chunk-enumerable, skip with a clear message"** outcome, not
abort. (The probe v2 was changed to do exactly this.)

### Cross-file structure of a VirtualiZarr store

BRAN-Parquet `temp` is one logical 5479-timestep array physically spanning ~180
monthly netCDF files; the extractor walking it naturally emits features whose
`path` column spans the whole collection (origin → `ocean_temp_2010_01.nc`,
last → `ocean_temp_2024_12.nc`). Concrete illustration for the RFC that the
extractor output *is* the kerchunk-style reference table, expressed as an OGR
layer — GDAL producing what it currently only consumes.

### Driver-level metadata probing generates failed requests

Opening the CMEMS Zarr produced `403` warnings on `zarr.json`, `.zarray`,
`crs/.zarray` — GDAL probing for both Zarr V2 and V3 metadata layouts. Harmless
here (it found a working layout and the array opened), but on an auth-restricted
or rate-limited object store this probing could matter. Not a bug in the
extractor; GDAL being thorough about format detection. Worth awareness.

---

## Behaviour proven vs C++ accessor spellings NOT proven

**Proven (behaviour, via the Python proxy):** everything in Q1–Q5 and the bonus
findings above.

**NOT proven (still to confirm against `gcore/gdal_priv.h` in a GDAL 3.12+
source tree):**

- The exact C++ method names on `GDALMDArrayRawBlockInfo`. The probe read the
  *Python* proxy surface (`info.GetFilename()`, `info.GetOffset()`,
  `info.GetSize()`, `info.GetInfo()`, `info.GetInlineData()`). The C++ spellings
  may differ and must be checked before the algorithm compiles.
- Specifically how `GetInlineData()` (or its C++ equivalent) returns the payload
  length.
- The C++ signature of `GetRawBlockInfo()` itself and the
  `GDALMDArrayRawBlockInfo` struct layout.

**Confirmed accessor (Python side):** the correct dtype accessor for a numeric
mdim array is `GetDataType().GetNumericDataType()` → `gdal.GetDataTypeName()`,
**not** `GetDataType().GetName()` (which is empty for numeric types; populated
only for compound/string). The C++ equivalent should be confirmed but the
distinction is now known.

---

## Status summary

| question | status |
|---|---|
| Q1 partial edge chunks (ceil) | **resolved** — ZARR + netCDF, size-corroborated; HDF5 expected |
| Q2 sparse chunks (null filename = absent) | **resolved** — CMEMS |
| Q3 inline chunks (size>0 + payload) | **resolved** — BRAN-Parquet |
| Q4 info-key shape (verbatim, opaque) | **resolved** — three distinct shapes |
| Q5 VRT forwarding (per-array) | **resolved** — BRAN-mosaic |
| out-of-range → RuntimeError | resolved (bonus) |
| 64-bit offsets necessary | resolved (bonus) |
| block-size-0 = valid declined state | resolved (bonus) |
| dtype accessor (Python side) | resolved (bonus) |
| HDF5 driver direct | **open** — coverage gap, low priority, not a blocker |
| C++ accessor spellings | **open** — confirm against gdal_priv.h before the hull compiles |

Phase 0 is effectively complete. Every RFC open question answerable by
observation has been answered — on real target datasets plus one controlled
fixture — and the answers came out clean and mutually consistent. The remaining
two "open" items are a low-priority coverage run (HDF5) and a source-tree check
(C++ spellings) that belongs at the start of writing the hull, not before it.

The algorithm hull is now transcription, not discovery:
- enumeration uses ceil-division
- absence detected by null filename, never offset==0
- inline classified by null path + size>0 + payload present
- `info` passed through verbatim, treated as opaque (preferably hoisted to
  layer metadata)
- block-size-0 arrays skipped with a clear message, not an error
- out-of-range coordinates trusted to error from GDAL, no in-loop guard needed
- `offset` / `size` fields are `OFTInteger64`
