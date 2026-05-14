# Companion notes — the thinking around the chunk-reference RFC

*Partner document to the RFC draft (`rfc-mdim-chunk-references.md`) and its
figures. The RFC is the artefact; this is the reasoning that produced it and the
discipline for carrying it forward. Written to be picked up cold in a fresh
session.*

---

## What this is, in one line

A GDAL algorithm that turns a multidimensional array into a vector layer of
per-chunk storage references — built on `GDALMDArray::GetRawBlockInfo()` (3.12+),
staged so every stage ships independently, shaped so it can later generalise
across the whole library.

## How the idea arrived

It started from a narrower question — exposing `GetRawBlockInfo()` iteration in
the gdalraster R package — and kept widening as the right framing came into
focus:

1. **Method binding** — the fork already exposes `arr$getRawBlockInfo(coords)`
   working against netCDF over `/vsicurl/`. That part is done.
2. **Standalone C++ enumeration** — the real want was a single function that
   walks the whole chunk grid internally and crosses the R boundary once with a
   finished table, not a per-chunk round trip from R.
3. **A GDAL capability, not an R feature** — the decisive realisation. Because
   `GetRawBlockInfo()` is implemented across HDF5, netCDF, ZARR and VRT in one
   stroke, anything built on it is format-agnostic by construction. It belongs
   in GDAL core so every binding and the CLI get it, not in one R package.
4. **mdim-in, vector-out** — the output is naturally an `OGRLayer`: one feature
   per chunk, reference record as attributes, optional geometry. That makes it a
   category GDAL doesn't currently span (`gdal mdim convert` is mdim→mdim).

The lesson worth keeping: the narrow framing was a stepping stone, not the
destination. Letting the scope find its own correct level — without
over-reaching past what's proven — is the same discipline that shows up
everywhere below.

## The key design decisions, and why

**A new algorithm, not a flag on `gdal mdim convert`.** `convert` is array-in,
array-out, and its contract is about *payload* (materialise/transcode). This is
array-in, vector-out, and its contract is about *references* (describe location,
touch no payload). Conflating them repeats the exact category error that keeps
the classic-API `ReadCompressedData` separate from the mdim-API
`GetRawBlockInfo` — "fetch the bytes" and "describe where the bytes are" are
different operations. The category boundary should stay legible in the command
surface.

**Three sink modes, same feature stream** (Figure 1). The reference attributes
are identical across all three; only the geometry choice differs:
- *attribute-only* — the primitive. Faithful one-row-per-chunk dump, no
  interpretation. Most likely to land in core because it claims nothing beyond
  what `GetRawBlockInfo()` reports. Chunk-index columns carry the spatial
  information in attribute form.
- *index-space geometry* — bbox polygon in array index coordinates. Needs no
  CRS, works for every array including non-georeferenced and curvilinear grids.
  Makes the output a genuine vector layer (spatial filter over the chunk grid).
  The quietly clever one.
- *projected geometry* — bbox polygon in the array's CRS. The most immediately
  useful ("which chunks cover my AOI") and the most treacherous: only valid when
  a real geotransform exists. Must decline or fall back to index space when
  georeferencing is ill-defined — never silently emit fictitious polygons.

**Index-space and projected are two stages, not one.** They feel like one
"geometry" feature but they're not. Index-space needs no CRS and is universally
safe; projected depends on a well-defined geotransform and has a real failure
mode. Bundling them would make the universally-safe capability hostage to the
conditional one. (This is a defensible thing to revisit — a reviewer might
prefer one geometry stage with a mode flag — but the split is the safer default.)

**Array-level facts go in layer metadata, not every row.** The codec chain,
dimension names, dtype, fill value, endianness are properties of the *array*,
not of each chunk. They belong on the `OGRLayer` via metadata, set once. Keeps
the per-feature schema narrow. (Per-row `info` is retained only for the case
where a driver legitimately reports per-chunk variation — an open question is
whether to drop it entirely once layer metadata exists.)

**OGR types remove a marshalling problem.** `offset` and `size` are `uint64`;
`OFTInteger64` carries them natively. This is a real, if small, advantage of the
vector-table framing over the data.frame framing that the R-package version
would have needed (where 64-bit offsets meant `bit64` juggling).

## Implementation gotchas surfaced (and still open)

These came up in discussion and need confirming against a real GDAL 3.12+ build
before they're settled:

- **Partial edge chunks.** A dimension whose size isn't an integer multiple of
  the block size still has a real, addressable trailing chunk. The C++ docs
  phrase the valid block-coordinate range as `(GetSize()/GetBlockSize())-1`,
  which read as integer division would *drop* that trailing chunk — almost
  certainly loose wording, but the enumeration's ceil-division assumption must
  be confirmed empirically, not inferred. This is the one not to guess on.
- **`GDALMDArrayRawBlockInfo` accessor names.** `GetFilename()`, `GetOffset()`,
  `GetSize()`, `GetInfo()`, `GetInlineData()` are taken from the Python proxy
  surface — verify exact C++ spellings (and how `GetInlineData()` returns
  length) against `gcore/gdal_priv.h`.
- **Block size 0.** A dimension reporting "no natural block size" must be a clear
  error, not a divide-by-zero.
- **Sparse chunks.** `GetRawBlockInfo()` returns success with null/zeroed fields
  for a valid-but-absent chunk. Keep these features (`present = false`), don't
  drop them — absent chunks are information. Detect via null filename, *not*
  `offset == 0`, since 0 is a legal offset.
- **VRT caveat.** `GetRawBlockInfo()` on VRT only works when the VRT declares a
  block size and each VRT block maps to exactly one source block of matching
  size. Surface the failure clearly; document the constraint.
- **Inline data is deferred** (Stage 1b). Stage 1 reports inline chunks as
  `present = true` with null path/offset/size but does not emit the bytes. A
  dataset with no inline chunks — the common archival netCDF/HDF5 case — is
  fully served without it.

## The discipline for carrying it forward

This is the part that matters most, and it's three instances of one principle.

**Stage, don't wish.** Do not take this to the PSC as a proposal to evaluate in
the abstract. Build stages 1, 2, 2.1 first, with proven implementation, then
bring it as "here's a working thing and its roadmap." An RFC-as-wish invites the
whole design to be relitigated before any byte has moved; an RFC-as-evidence
shifts the conversation from "should we" to "do you agree with where the seams
fall." The staging in the RFC is therefore not just presentation — it's a
*build-order commitment*. "Each stage independently shippable" must be a
demonstrated fact about the code by the time anyone looks, not a promise. Keep
the build order matching the document order.

**The RFC is two documents in one cover.** Right now it's the *working*
roadmap — it keeps your own implementation honest about scope, stops Stage 1
quietly accreting Stage 3 concerns. Later, lightly revised, it's the
*PSC-facing* RFC — same spine, with "prototyping is underway" swapped for
"stages 1–2.1 are implemented, here are the links." That it can be both without
restructuring is the sign the staging is sound. Treat the current copy as the
former; circulation is not imminent.

**Latent generality, not announced unification.** If accepted, this becomes *the
reason* to integrate chunk references across the whole library — classic and
mdim, every driver that can describe its encoded bytes without decoding them
(GTiff tile offsets, JPEG-in-TIFF, COG, classic tiled formats generally). That
is the real prize: the move from *feature* to *organizing principle* — an
accepted RFC becomes a criterion the project can ask of every relevant part of
itself. But an organizing principle is only earned *retroactively*, by something
concrete being undeniably good. It is fatal to *write* it as a grand unification
up front — that invites exactly the skepticism a steering committee exists to
apply. The generality should be *latent in the design*, visible to whoever's
paying attention, but the document's claims stay scoped to what's proven. Let
the PSC be the ones who say "this should apply to classic drivers too." If they
say it, it's doctrine. If you say it first, it's a wish in doctrine's clothes.

**The concrete responsibility that falls out of this — into Stage 1, now:** name
and shape the feature stream and algorithm surface so they don't read as
mdim-specific, even though mdim is all they implement today. Express the output
schema and the algorithm's contract in terms true of *any* chunked format —
"containing resource, offset, extent, codec description" — not anything that
smells of `GDALMDArray` specifically. Then the eventual classic-API extension is
a cheap *widening of an existing contract* rather than a *parallel thing to be
reconciled*. You don't build the classic path in Stage 1; you build Stage 1 so
that the classic path, when someone reaches for it, finds the door already the
right shape.

## Suggested: keep an evidence log

Because the PSC version is a while off, add a private running log (separate from
the RFC proper) where each open question gets its answer recorded *as it's hit*:
partial-edge-chunk behaviour confirmed against which drivers; the real
`GDALMDArrayRawBlockInfo` accessor names; whether `FILTER` takes a codec list;
etc. By the time you're a couple of stages in these will be answered but the
answers will otherwise be scattered across shell history and memory. A log
converts the RFC's "open questions" section into "resolved, here's how" with no
archaeology — and that converted section is itself part of what makes the
eventual PSC version land as evidence rather than wish.

## Where it stands

Document, figures, and discipline are in place. The build is the next move. The
roadmap exists to keep the build honest — stage order = build order, claims
scoped to proof, generality latent not announced.

## Companion artefacts

- `rfc-mdim-chunk-references.md` — the RFC draft itself.
- `fig1-chunk-extractor-architecture.svg` — the three-sink-mode architecture.
- `fig2-rfc-staged-roadmap.svg` — the four-stage roadmap.
- `figures.md` — both figures with captions, markdown-embeddable.
