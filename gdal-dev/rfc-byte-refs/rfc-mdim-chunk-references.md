# RFC (draft): Multidimensional array chunk-reference extraction

**Status:** draft for discussion
**Author:** Michael Sumner
**Proposed target:** a `gdal mdim` subcommand + supporting C++ algorithm class
**Depends on:** `GDALMDArray::GetRawBlockInfo()` (GDAL 3.12+)

---

## 1. Summary

This RFC proposes a new GDAL algorithm that converts a *multidimensional array*
into a *vector layer*: one feature per chunk, carrying the raw storage reference
for that chunk (containing file/object, byte offset, byte size, codec
description, chunk index). The canonical output is a Parquet table, but because
the sink is an ordinary `OGRLayer`, any OGR-writable format works.

The proposal is deliberately staged. Stage 1 is a minimal, self-contained
extractor that is useful on its own. Each subsequent stage adds exactly one
capability and remains shippable if later stages are never built. The end of the
roadmap is a read-side "metadriver" in which a reference table plus array
metadata is consumable as a virtual multidimensional store — a kerchunk /
VirtualiZarr equivalent expressed in GDAL's own primitives.

## 2. Motivation

`GDALMDArray::GetRawBlockInfo()` (added in 3.12, implemented for HDF5, netCDF,
ZARR and VRT) exposes, per chunk, the information needed to locate that chunk's
*encoded* bytes without decoding them: the containing file or object, the byte
offset and length, any inline payload, and a driver-dependent codec description.

That primitive answers a per-chunk question. What users repeatedly need is the
*whole-array* answer: the complete set of chunk references as a single
addressable artifact. This is exactly what the kerchunk / VirtualiZarr ecosystem
produces — a reference store mapping every chunk of a dataset to
`{path, offset, size, codec}` — and it underpins cloud-native access to archival
array data (netCDF/HDF5 collections exposed as if they were Zarr, without
rewriting the bytes).

Today GDAL can *read* such reference stores (the ZARR driver consumes kerchunk
JSON/Parquet) but has no native way to *produce* one. The reference scan has to
be done outside GDAL, by tools that re-implement format knowledge GDAL already
has. A GDAL-native extractor closes that loop: the same library that reads every
one of these array formats can emit the reference table for any of them, through
one code path.

Three properties make this worth doing in GDAL core rather than as an external
tool:

- **Format-agnostic by construction.** It is built on `GetRawBlockInfo()`, so it
  works for every driver that implements that method — present and future — with
  no per-format code in the algorithm itself.
- **The output is immediately a GDAL citizen.** An `OGRLayer` of chunk references
  is queryable by `ogr2ogr`, by spatial filters, by every OGR consumer. The
  reference table stops being an opaque sidecar and becomes data.
- **It is the natural counterpart to existing mdim tooling.** GDAL already has
  `gdal mdim convert` (mdim → mdim). This is mdim → vector: a different category,
  and one GDAL does not currently span.

## 3. Why a new algorithm, not a flag on `mdim convert`

`gdal mdim convert` is array-in / array-out. The operation proposed here is
array-in / *vector*-out. Although the underlying iteration (walk the chunk grid)
is adjacent, the output category is different, and the contract is different:
`mdim convert` materialises or transcodes payload; this extracts references and
touches no payload at all.

Conflating the two under one command would repeat a category error worth
avoiding: the classic-API `ReadCompressedData` and the mdim-API
`GetRawBlockInfo` were kept separate precisely because "fetch the bytes" and
"describe where the bytes are" are different operations. The extractor should be
its own named algorithm so that boundary stays legible to users.

## 4. Staged plan

Each stage is independently shippable and independently useful. A reviewer
should be able to accept Stage 1 without committing to Stage 4.

### Stage 1 — minimum viable extractor

**Scope.** One named array in a multidimensional dataset. Iterate every chunk of
the array's block grid. For each chunk, call `GetRawBlockInfo()` and emit one
feature. Write to an attribute-only layer; canonical output Parquet.

**Per-chunk attributes.**

| field | OGR type | source |
|---|---|---|
| `dim_0` … `dim_n` | `OFTInteger` / `OFTInteger64` | chunk coordinate per dimension |
| `present` | `OFTInteger` (boolean subtype) | `false` for valid-but-absent chunks |
| `path` | `OFTString` | containing file/object; null if absent or inline |
| `offset` | `OFTInteger64` | byte offset; null if absent or inline |
| `size` | `OFTInteger64` | encoded byte length; null if absent or inline |
| `info` | `OFTString` | codec description, joined |

**Array-level facts go in layer metadata,** not in every row: dimension names
and sizes, block shape, data type, endianness, nodata/fill, and — importantly —
the codec chain, which is a property of the array, not of each chunk. (`info`
per-row is retained for the case where a driver legitimately reports per-chunk
variation, but for the common uniform case the layer-level codec metadata is the
authoritative copy.)

**Explicitly out of scope for Stage 1:**

- *Inline chunk data.* Chunks stored inline (no file/offset) are reported with
  `present = true` but null `path`/`offset`/`size`. Stage 1 does **not** emit the
  inline bytes. This is a deliberate, documented limitation: every other field is
  still correct and useful, and a dataset with no inline chunks (the common case
  for archival netCDF/HDF5) is fully served. Carrying the bytes is Stage 1b/2
  work.
- *Geometry.* Stage 1 output has no geometry column. The chunk-index columns
  carry the spatial information in attribute form.
- *Multiple arrays.* One array per invocation. Whole-dataset traversal is a thin
  loop on top, deferred until the single-array contract is settled.

**Stop-here value.** Even at Stage 1 with no inline support and no geometry, the
output is a complete, queryable reference table for any array whose chunks live
in addressable files — which covers the archival netCDF/HDF5 collections that
are the primary motivation.

### Stage 1b — inline chunk data

Add a binary field (`OFTBinary`) carrying the inline payload for chunks that
have one. Small, isolated addition; separated from Stage 1 only so that Stage 1
can ship without binary-field handling.

### Stage 2 — index-space geometry

Add a geometry column: a polygon bounding box per chunk, expressed in **array
index coordinates**. Chunk `(i, j, …)` occupies a well-defined box in index
space regardless of whether the array is georeferenced.

This makes the output a genuine vector layer rather than a table: chunk-grid
queries ("all chunks intersecting this index window") become
`OGRLayer::SetSpatialFilter()` calls. It requires no CRS and works for every
mdim array, including non-georeferenced arrays and curvilinear-grid arrays where
no affine geotransform exists.

For Parquet output specifically, the bounding box can also be written in a
compact bbox-column form rather than full polygon WKB, when that is the more
economical encoding.

### Stage 3 — projected geometry

Add the option to emit the per-chunk bounding box in the **array's CRS** instead
of index space. This answers the high-value query directly: "which chunks cover
my area of interest" becomes a polygon intersection.

This stage is **conditional on a well-defined geotransform.** Where the relevant
dimensions do not form a geotransform (curvilinear grids, non-spatial
dimensions), the algorithm must either decline the projected mode or fall back
to index-space geometry with a warning — never silently emit fictitious
polygons. This stage should build on the geotransform-detection primitives
(`GuessGeoTransform` / `IsRegularlySpaced`) rather than assuming an affine
mapping exists.

### Stage 4 — read-side metadriver

Generalise from *producing* a reference table to *consuming* one: a reference
table plus the array metadata is sufficient to reconstruct a virtual
multidimensional array. This is the kerchunk / VirtualiZarr round trip expressed
natively — "table + metadata == Zarr" — closing the loop back through the same
mdim API the references were extracted from. This stage is the most speculative
and is included to show the roadmap's direction, not as a near-term commitment.

## 5. Proposed interface

A `gdal mdim` subcommand (working name `gdal mdim get-refs`; final name to be
discussed — it must not suggest mdim→mdim convert semantics), backed by a
`GDALAlgorithm` subclass so it is reachable from the C++ API and language
bindings, not only the CLI.

Indicative arguments:

- input dataset (positional)
- `--array` — name of the array to extract (required while Stage 1 is
  single-array)
- output dataset + `--of` output format (default Parquet)
- open-option passthrough for the input
- (Stage 2+) `--geometry index` — emit index-space geometry
- (Stage 3+) `--geometry crs` — emit projected geometry, with the documented
  fallback behaviour

## 6. Implementation notes

- **Chunk-grid enumeration** comes from `GetDimensions()` and `GetBlockSize()`.
  The valid block-coordinate range per dimension needs to be pinned against the
  partial-edge-chunk case: a dimension whose size is not an integer multiple of
  the block size still has a real, addressable trailing chunk, and the
  enumeration must include it. This should be confirmed by test against the
  implementing drivers rather than inferred from the doc wording.
- **A dimension reporting block size 0** ("no natural block size") must be a
  clear error, not a divide-by-zero.
- **64-bit fields.** `offset` and `size` are `uint64`; OGR's `OFTInteger64`
  carries them directly with no marshalling workaround.
- **Sparse chunks.** `GetRawBlockInfo()` returns success with zeroed/null fields
  for a valid-but-absent chunk. These features are retained (`present = false`),
  not dropped — absent chunks are information. Detection keys off the null
  filename, not off `offset == 0`, since 0 is a legal offset.
- **VRT caveat.** `GetRawBlockInfo()` on VRT only works when the VRT declares a
  block size and each VRT block maps to exactly one source block of matching
  size. The algorithm should surface the failure clearly and the docs should
  state the constraint.
- **Codec description** is driver-dependent text. The algorithm passes it
  through verbatim; it does not parse or normalise it (normalisation across
  drivers, if wanted, is separate future work).
- **Chunk-coordinate encoding** Stage 1 emits per-dimension chunk coordinates as separate typed integer columns `(dim_0..dim_n, all Integer64)`,
  with dimension names recorded in layer metadata `(DIM_N_NAME)`. The alternative of a single string-encoded column (e.g. '0.0.0.0' matching Zarr's on-disk key convention)
  was not adopted: per-dimension typed columns preserve Parquet predicate pushdown for spatial-and-temporal range queries, which is the strongest
  performance characteristic of the chosen output format. The trade-off is N columns instead of one and a schema whose width varies with array rank.
  The dimension names are not used as column names directly to avoid sanitization concerns with arbitrary HDF5/netCDF names; named-column variants (e.g. --dim-names) could be added in a future stage.
- **Shared helpers** in `get_refs_common.h` include both `LinearToCoords` (used by the enumeration loop) and inverse `CoordsToLinear` (currently unused but
  anticipated for Stage 2. 
  
## 7. Backward compatibility

Purely additive. A new algorithm, a new optional `gdal mdim` subcommand, and new
C++ API surface. No existing behaviour, format, or API changes. No new hard
dependency: Parquet output requires the (Geo)Parquet driver, but the algorithm
itself writes to any `OGRLayer`, so the Parquet driver is a recommendation, not
a requirement.

## 8. Open questions

- Final subcommand name (must not read as mdim→mdim convert).
- Whether per-row `info` should be kept once array-level codec metadata exists,
  or dropped in favour of the layer-metadata copy for all drivers.
- Whether Stage 2's compact-bbox Parquet encoding should be the default for
  Parquet output or an explicit option.
- The exact metadata schema for Stage 4 — what, beyond the reference table, must
  travel alongside for the virtual store to be reconstructable, and whether that
  metadata should itself be standardised.
- Multi-array / whole-dataset traversal: thin loop in the same algorithm, or a
  separate wrapper.

## 9. Relationship to existing ecosystem work

This formalises, inside GDAL, the reference-extraction step that kerchunk and
VirtualiZarr perform externally. It is complementary to those projects rather
than competitive: GDAL already consumes their reference stores; this lets GDAL
also produce reference data through the same library that reads the source
formats. Prototyping is underway in R bindings (gdalraster fork) where
`GetRawBlockInfo()` is already exposed and the per-chunk call is confirmed
working against netCDF over `/vsicurl/`; that prototype can inform the algorithm
surface, but the algorithm itself is proposed for GDAL core so every binding and
the CLI get it.
