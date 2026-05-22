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


# Evidence log addendum — HDF5 driver direct probe (Q1 coverage gap closed)

*Appends to `evidence-log-phase0.md`. Closes the one open behavioural item in
that log's status table: "HDF5 driver direct — open — coverage gap." It is no
longer a gap.*

---

## Dataset probed

| label | source | driver | role |
|---|---|---|---|
| HDFEOS-swath | `autotest/gdrivers/data/hdf5/dummy_HDFEOS_swath_chunked.h5`, array `/HDFEOS/SWATHS/MySwath/Data Fields/MyDataField` | **HDF5** | native HDF5-driver read; non-even chunking on all three dimensions |

Provenance note: this is a fixture shipped in the GDAL autotest tree, opened by
the **HDF5 driver** (not netCDF-over-HDF5 — distinct from the BRAN datasets in
the main log, which are netCDF4 opened via the netCDF driver). A
project-provided fixture is preferable to a crafted one for evidence — no
question of the fixture being shaped to the expected answer.

---

## Q1 — Partial edge chunks: is ceil-division correct? — CONFIRMED for HDF5

**RESOLVED for the HDF5 driver — ceil-division is correct. Trailing partial
chunks are individually addressable, with size evidence corroborating.**

Array `MyDataField`, dims/blocks `Band 20/3, AlongTrack 30/4, CrossTrack 40/6`
— all three dimensions non-even. Ceil grid `[7,8,7]` = 392 chunks; floor grid
`[6,7,6]` = 252. Floor-division would wrongly drop 140 chunks (>1/3 of the
array).

`GetRawBlockInfo` results:

- origin `(0,0,0)` — full `3×4×6` chunk — file-backed, offset 45112, **size 144**
- interior corner `(5,6,5)` — full `3×4×6` chunk — file-backed, offset 111983,
  **size 142** (≈ origin, as expected for another full chunk)
- trailing corner `(6,7,6)` — the all-partial corner chunk, extent `2×2×4` —
  file-backed, offset 121797, **size 64**

The trailing partial chunk returned a real file-backed result, not a decline
or an absent. Its compressed size (64) is substantially smaller than a full
chunk (~143), in the direction a reduced extent predicts: full chunk volume 72
elements, corner partial 16 elements. The ratio is not linear (DEFLATE on tiny
blocks does not scale linearly with element count) but the result is
unambiguous — a real, smaller, file-backed trailing chunk.

**Implication:** ceil-division (`n_chunks[i] = (dim_size[i] + block[i] - 1) //
block[i]`) is now confirmed for **all three implementing driver families** —
ZARR, netCDF, and HDF5 directly — each with corroborating size evidence. The
"HDF5 expected by the same mechanism" caveat in the main log is discharged.

## Bonus — out-of-range coordinate, HDF5

`(7,0,0)`, one past the ceil extent in dim 0, raised
`RuntimeError: GetRawBlockInfo() failed: array MyDataField: invalid block
coordinate (7) for dimension 0` — the same loud, clean failure recorded for
netCDF and ZARR in the main log. The "no in-loop bounds paranoia needed"
conclusion now has HDF5 backing.

## Codec shape, HDF5 (Q4 corroboration)

`info` came back `['COMPRESSION=DEFLATE', 'ENDIANNESS=LITTLE']` — flat scalar
`KEY=VALUE` strings, the same shape as the netCDF case in the main log's Q4.
Consistent with "pass through verbatim, treat as opaque." Note `gdal mdim info`
surfaced `structural_info: {"COMPRESSION": "DEFLATE"}` at the array level —
worth remembering as a cross-check that the codec chain is an array-level
property, reinforcing the "hoist to layer metadata" decision.

---

## Updated status table row

| question | status |
|---|---|
| Q1 partial edge chunks (ceil) | **resolved** — ZARR + netCDF + **HDF5**, all size-corroborated |
| HDF5 driver direct | **resolved** — `dummy_HDFEOS_swath_chunked.h5`, non-even on all 3 dims |

The only remaining "open" item in the main log is the C++ accessor-spelling
check against `gdal_priv.h` — which belongs at the start of writing the hull,
not in Phase 0 probing. Phase 0 behavioural probing is now complete across all
implementing drivers.



# Evidence log addendum — C++ accessor reconciliation (Commit 1)

*Appends to `evidence-log-phase0.md`. Closes the last "open" row in that log's
status table: "C++ accessor spellings — open — confirm against the source tree
before the hull compiles." Confirmed against the **v3.12.4 tag**. This is
Commit 1 on the `feature/mdim-get-refs` integration branch — gated before the
hull, so the hull is transcription.*

---

## Headers (corrected)

The companion notes guessed `gcore/gdal_priv.h`. That was wrong — the
multidimensional C++ classes are not there. Actual locations on v3.12.4:

- **`gcore/gdal_multidim.h`** — the `GDALMDArray` C++ class, including the
  `GetRawBlockInfo` C++ method.
- **`gdal.h`** — the `GDALMDArrayRawBlockInfo` struct definition itself (inside
  an `extern "C++"` block so both the C and C++ APIs share one struct), plus
  the C-API entry points.

## The C++ method

```cpp
virtual bool GDALMDArray::GetRawBlockInfo(
    const uint64_t *panBlockCoordinates,
    GDALMDArrayRawBlockInfo &info) const;
```

- block coordinates: a raw `const uint64_t *`, **not** a `std::vector` — the
  hull builds a `uint64_t` array (or `std::vector<uint64_t>` + `.data()`) sized
  to the array's dimension count, one per chunk. The ceil-enumeration produces
  exactly these.
- result: filled via the `GDALMDArrayRawBlockInfo &` out-reference.
- `bool` return: success/failure.
- `virtual` — confirms it is the per-driver-overridden primitive.
- `const` — does not mutate the array.

## The struct — `GDALMDArrayRawBlockInfo` (since 3.12)

Plain-old-data struct with **public members** (not a class with accessors —
the Python proxy's `Get*()` names are a SWIG wrapper, not the C++ surface):

| Python proxy (what `probe_getrawblockinfo_v2.py` read) | **C++ member (what the hull writes)** | type | notes |
|---|---|---|---|
| `info.GetFilename()` | `info.pszFilename` | `char *` | **nullable** — null is the absence/inline signal |
| `info.GetOffset()` | `info.nOffset` | `uint64_t` | `0` is a legal value, never an absence signal |
| `info.GetSize()` | `info.nSize` | `uint64_t` | encoded byte length; **also** the inline-buffer length |
| `info.GetInfo()` | `info.papszInfo` | `char **` | null, or null-terminated string list — codec description |
| `info.GetInlineData()` | `info.pabyInlineData` | `GByte *` | null when not inline; buffer of `nSize` bytes when set |

## Questions this closes

### Inline-data length (flagged by name in the main log as unproven)

**RESOLVED — there is no separate length field. `pabyInlineData` is a buffer
of `nSize` bytes.** The struct doc comment states it explicitly: "In-memory
buffer of nSize bytes. When this is set, pszFilename and nOffset are set to
NULL." So `nSize` serves double duty — encoded byte length for file-backed
chunks, inline-buffer length for inline chunks. Stage 1b reads
`pabyInlineData` for exactly `nSize` bytes; no second field to consult.

### Three-state classification (Q2 / Q3) against the real fields

- **present (file-backed):** `pszFilename != nullptr`
- **absent (sparse):** `pszFilename == nullptr && pabyInlineData == nullptr`
  (and `nSize == 0`)
- **inline:** `pszFilename == nullptr && pabyInlineData != nullptr`
  (and `nSize > 0`)

The main log's detection rule — null filename, **never** `offset == 0` — is
correct against the real struct: `nOffset` is a plain `uint64_t` with no
sentinel semantics; `pszFilename` is the nullable one.

### Copy-semantics hazard (newly surfaced by the struct definition)

The struct owns heap memory (`pszFilename`, `papszInfo`, `pabyInlineData` are
owned pointers; the struct has a destructor, `clear()`, and full copy/move
constructors and assignment operators). Two consequences for the hull:

- **The copy constructor has a sharp documented failure mode:** on allocation
  failure during copy, `pabyInlineData` becomes NULL **but `nSize` stays
  non-zero**. Therefore the hull classifies inline chunks by
  `pabyInlineData != nullptr`, **not** by `nSize > 0`. Stage 1 does not touch
  inline bytes, but writing the Stage 1 classification this way means Stage 1b
  inherits the safe check for free.
- **Enumeration loop:** reuse one `GDALMDArrayRawBlockInfo`, call `clear()`
  between chunks (or rely on per-iteration destruction). Do not accumulate
  filled structs.

### papszInfo is a GDAL string list (Q4 corroboration)

`char **`, null-terminated — `CSL*` territory. The struct itself makes no
attempt to type it, which vindicates the "pass through verbatim, treat as
opaque" decision at the type level. Per-row field: join with a `CSL` helper.
Layer metadata: hoist as-is. The algorithm never parses it.

### C-API lifetime helpers — not the hull's concern

`GDALMDArrayRawBlockInfoCreate()` / `GDALMDArrayRawBlockInfoRelease()` exist
for C-side lifetime management. The C++ algorithm stack-allocates the struct
and lets RAII handle it. Noted for completeness; irrelevant to the hull.

### dtype accessor (C++ side)

Still to confirm in `gdal_multidim.h`: the C++ equivalent of the Python
`GetDataType().GetNumericDataType()` → name route (the main log resolved the
Python side; `GetName()` is empty for numeric types). `GDALExtendedDataType`
is in the same header — confirm its numeric-type accessor while writing the
array-level-metadata part of the hull. Low risk; not a blocker for the chunk
enumeration itself.

---

## Updated status table row

| question | status |
|---|---|
| C++ accessor spellings | **resolved** — `gdal.h` struct + `gdal_multidim.h` method, v3.12.4 |

**Phase 0 is complete.** Every behavioural question (Q1–Q5 + bonus) is
resolved across all three implementing drivers; the C++ surface the hull
compiles against is now confirmed. The hull is transcription:

- enumeration: ceil-division
- method: `GetRawBlockInfo(const uint64_t*, GDALMDArrayRawBlockInfo&) const`,
  `bool` return
- fields: `pszFilename` / `nOffset` / `nSize` / `papszInfo` /
  `pabyInlineData`
- absence: `pszFilename == nullptr`, never `nOffset == 0`
- inline classification: `pabyInlineData != nullptr` (not `nSize > 0` —
  copy-failure hazard)
- inline length: `nSize` (no separate field)
- `papszInfo`: opaque `char **`, forwarded verbatim
- one struct reused + `clear()`ed per chunk
