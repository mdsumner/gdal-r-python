# rrepr and the case for "a VRT for xarray"

Working notes, 2026-07-30. Framing and next steps.

## 1. Purpose

Two related but distinct problems have been circling each other:

1. **Reproducible excerpts.** How do you paste an array dataset into a bug
   report so someone else can evaluate it? R has `dput()`; Python has had
   nothing comparable for xarray.
2. **Compact lazy representation.** How do you describe a large dataset -- its
   dimensions, coordinates, dtypes, attributes, and the location of its bytes --
   in a small string that can be published, shared, and opened, without
   materialising any bulk data?

These are inverses. (1) keeps a little data and discards all provenance.
(2) keeps all provenance and no data. The observation driving this document is
that (2) is the more useful artifact, and that the reference-store formats
already in play (kerchunk, VirtualiZarr, Icechunk, GDAL mdim VRT) are all
instances of it -- they are reprs that happen to be openable.

## 2. xarray-rrepr, as it stands

`https://github.com/MartinSchobben/xarray-rrepr` (MIT, 8 commits as of late
July 2026, no releases).

`rrepr(ds)` emits an evaluable constructor expression -- `xr.Dataset({...},
coords={...})` -- containing a small random subsample of the original object.
`eval(rrepr(ds))` reconstructs a working, tiny xarray object. Stock
`repr(ds)` cannot do this; `eval(repr(ds))` is a SyntaxError.

This is `dput()` for xarray, with the minimisation built in rather than bolted
on, which is the right call: `dput()` on anything real is unusable. The
inclusion of `pyperclip` in the README imports signals attention to the actual
paste-into-an-issue workflow, not just the function.

### 2.1 Limitations observed from the README

These are all additive fixes, not design faults, but they bound what the tool
is currently good for.

**Unseeded randomness.** Each call redraws. In the README,
`print(rrepr(ds))` yields lat `[55.0, 15.0]` while the following
`eval(rrepr(ds))` yields lat `32.5, 30.0`. Fine for generate-once-and-paste;
a hazard if called programmatically. A `seed=` argument would make emitted
excerpts stable and diffable.

**Random indices destroy grid regularity.** The example coordinates go
`[55.0, 15.0]` and `[235.0, 325.0]` -- non-adjacent picks from what was a
regular 2.5 degree grid. The excerpt therefore has irregular spacing where the
original did not. Any bug involving resampling, warping, affine transforms, or
regularity detection becomes unreproducible from such an excerpt. A contiguous
slab (`isel` with slices; corner or centre) preserves spacing. Both modes are
wanted: random points for value-level bugs, slabs for structural ones.

**dtype drift on the round trip.** Source `lat`/`lon` are float32; the emitted
`np.array([55.0, 15.0])` is float64, and the reconstructed object reports
float64. Time goes datetime64[ns] -> `dtype=object` datetime.datetime ->
datetime64[ns]. Given how many xarray and numpy bugs are dtype bugs, silent
promotion defeats the purpose. Explicit `dtype=` on every emitted array would
fix it.

**attrs and encoding are dropped.** The source carries Conventions, title,
description, references; the emitted expression has no `attrs=` at all.
Consequently nothing CF-related -- units, calendar, `scale_factor`,
`_FillValue` -- can be reproduced from an rrepr excerpt, which excludes a large
class of the decoding bugs people most want to report. `encoding` is likewise
absent, so chunking and compressor settings are out of scope, ruling out zarr
and kerchunk issues.

**Unexplained assertion.** The README closes with
`assert_allclose(ds, eval(rrepr(ds)))`, comparing a 2920x25x53 dataset against
a freshly drawn 2x2x2 one, apparently without raising. Worth establishing what
that is actually asserting before relying on it as a correctness check.

### 2.2 Status note

No public GitHub activity on the account since April 2026. Any follow-up should
assume the repo is dormant and plan for a fork or an independent
implementation rather than upstream collaboration.

## 3. The inversion: an xarray-to-mdim-VRT emitter (xvrt)

Instead of subsampling the data and discarding the sources, analyse the
coordinates, keep every source reference, and emit zero bulk bytes.

The appealing property is that the string and the deliverable collapse into one
object. An rrepr excerpt *stands in for* a dataset; an emitted VRT *opens* the
dataset.

This is no longer hypothetical: **xvrt**
(`https://github.com/rgdal-dev/xvrt`, pure Python) writes GDAL
multidimensional VRT from lazy xarray Datasets, and its v0 already covers most
of the design space sketched below. Shipped in v0:

- Compositions: **stack** (new dim, one element per source) and **concat**
  (existing dim, N elements per source).
- Coordinate carriers: **regular-spacing detection**, **inline values** below a
  size threshold, **source-ref** above it, with per-coordinate override via
  `coord_mode={"lat": "source", ...}` -- i.e. the case table in 3.1 is
  implemented, threshold-inlining included.
- CRS: explicit WKT, or detected from `da.attrs["crs_wkt"]`, `da.rio.crs`, or
  a CF `grid_mapping` attribute.
- Design rules R1..R7 enforced with explicit errors; roundtrip validation
  against GDAL 3.12 and XSD conformance checking in `scripts/`.

The README's motto is the thesis of this document stated more compactly:
"VRT is a text spec, not a GDAL artefact." The worked example is BRAN --
monthly NetCDFs from a THREDDS `fileServer` prefix, URL lists built by
templating `{var}_{year}_{month}.nc`, opened with `open_mfdataset`, emitted as
a concat VRT over Time. Note that the URL construction in that example *is*
the generative-manifest idea of section 4, currently living as ad-hoc
f-strings on the user's side of the API; section 4 is about pulling it inside.

xvrt positions itself explicitly in the surrounding tooling: `vrtstack` (R) is
the parallel writer for classic 2D raster sources (`<SourceBand>` +
`<SourceTranspose>`), xvrt covers mdim sources (`<SourceArray>`, no
transpose); the two target the same XSD, cover disjoint source kinds, and
intersect via VRT-of-VRT composition. `gdx` is the reading half
(VRT -> xarray). VirtualiZarr is named as the architectural precedent: emit
descriptive artefacts from a lazy Dataset without invoking source-file
libraries.

Declared v1 scope: mosaic composition with explicit overlap policy; source-ref
coordinates from a dedicated sidecar source rather than the first data source;
Zarr and nested-VRT source demos; byte-convergence testing against
`vrtstack::write_mdim_vrt()` in R.

The subsections below stand as the design rationale behind what v0 implements,
plus the open edges (time encoding, precision policy) that remain live.

### 3.1 Coordinate analysis: four cases

| Case | Treatment | Compression |
|---|---|---|
| Regular 1D | parameterise as `start`, `step`, `n` | total; no bytes |
| Irregular rectilinear | emit a source reference to the 1D variable | high; one pointer |
| Time | see below | depends on calendar |
| Curvilinear 2D | source references to the lat/lon arrays | none; detect and say so |

**Regular 1D** is the happy path and the whole point. Fully parameterised, no
bytes at all.

**Irregular but rectilinear** cannot be parameterised but does not need
inlining either -- a source reference to the original 1D coordinate variable
keeps it lazy at the cost of one more pointer. Monthly time axes and ocean
depth levels land here, so BRAN2023 hits this case immediately. Note the option
of *inlining* short coordinate arrays directly (a few hundred float64 values is
a few KB) where a source reference would be more fragile than the data is
large. xvrt implements exactly this: a size threshold with per-coordinate
override, rather than a rule.

**Time** is the awkward case, because regularity in encoded CF numeric space and
regularity in decoded datetime64 space are different questions. Daily OISST is
clean under a standard calendar; monthly data is not regular at all; and
non-standard calendars mean a step detected on decoded values is misleading.
Detection should probably run on the encoded values plus the units string, not
on what xarray hands back post-decode.

**Curvilinear 2D** is not rectilinear, degenerates to source references, and
yields no compression. Detect it and refuse to pretend otherwise.

### 3.2 Regularity detection and float precision

The main trap. Testing `np.diff()` for exact constancy on float32 coordinates
fails spuriously; testing loosely is worse, because a slightly wrong step
accumulates over large n (43200 for a global 1/120 degree axis) and visibly
misplaces the far end of the axis.

Suggested approach:

- fit `step = (last - first) / (n - 1)`
- reconstruct the full axis from `start` and `step`
- compare via maximum absolute deviation against a tolerance derived from the
  coordinate's own resolution, not from machine epsilon
- emit `start` and `step` at full float64 round-trip precision regardless of
  input dtype, since this is the one place lossiness is unaffordable
- record the *original* dtype separately as metadata, so a reader can choose to
  reproduce float32 coordinates faithfully

The schema question -- what mdim VRT offers for expressing regularly-spaced
dimension values directly, versus requiring an indexing variable -- is now
answerable by reading xvrt's emitter and its `schema/` directory rather than
by spelunking the GDAL source. The BRAN example's `decode_times=False` comment
("VRT coords stay numeric") also confirms the section 3.1 position on time:
the emitted artifact carries encoded values, and detection belongs on the
encoded side.

### 3.3 Serialisation targets

Three, for three different readers, and they are not competing:

- **XML** -- because GDAL already speaks it, and this is the form that *opens*.
- **JSON** -- portable interchange; the better universal string repr for most
  purposes.
- **Code form** -- purely for pasting into issues, where a human has to read it
  and `rast(...)` or `xr.Dataset(...)` beats any amount of well-formed markup.
  This is the only one occupying the rrepr niche.

## 4. Beyond JSON and XML: generative manifest reprs

The most interesting unexplored direction. Consider a VirtualiZarr chunk
manifest serialised to repr form rather than enumerated.

A manifest is, per chunk key, a triple of (path, offset, length) -- in
VirtualiZarr, three parallel arrays. Enumerated, this scales linearly with chunk
count and gets large fast. But the contents are typically highly structured:

- **Offsets are frequently arithmetic.** Chunks laid out sequentially within an
  HDF5 or netCDF file give `offset = base + stride * i`. This is exactly the
  regularity-detection problem from section 3.2, applied to byte offsets
  instead of coordinate values.
- **Lengths are frequently constant**, or take a handful of distinct values
  (uncompressed fixed-size chunks; compressed chunks vary but often within a
  narrow band).
- **Paths are frequently templatable.** A daily OISST collection differs only
  by a date component. `path = template.format(date)` over a date range
  replaces thousands of enumerated strings.

So the compact form is not a smaller serialisation of the manifest but a small
declarative expression that *generates* it. The unifying insight: **coordinate
regularity detection, chunk-offset regularity detection, and filename
templating are the same operation applied at three levels.** One analysis
engine, three consumers.

Tradeoffs to keep in view:

- Generative forms are fragile. One missing file, one reprocessed granule with
  a different internal layout, and the pattern breaks. This argues for a hybrid
  -- pattern plus an explicit exception list -- rather than pure generation.
- Verification becomes essential and cheap: expand the generative form, compare
  against the enumerated manifest, assert equality. Worth doing as a build step
  rather than trusting the pattern.
- The efficient *binary* option already exists (kerchunk-parquet, columnar,
  scales fine). Generative text is not competing with it; it serves the
  human-readable and pasteable case, where parquet is opaque and JSON is
  merely verbose.

### 4.1 Arrow as the in-memory pivot

If the in-memory representation of a manifest is Arrow -- which
kerchunk-parquet already implies, since a parquet reference store *is* an
Arrow table at rest -- then the on-disk forms become consequences rather than
commitments. A manifest-as-Arrow-table (columns: key, path, offset, length, or
the generative parameters that expand to them) can be rendered to
kerchunk-parquet, to VirtualiZarr's parallel arrays, to VRT XML, to JSON, or
to a code-form repr, each as a serialization of the same object. The
consumer-side details -- dask graphs in Python, GDAL's internal source lists,
data frames in R -- likewise stop mattering as distinct formats, because each
runtime materialises its native structure from the same table. This is the
same move Arrow made for tabular interchange generally, applied to byte
manifests: one canonical in-memory form, N renderings, zero-copy where the
runtime allows it. It also gives the generative form a natural home -- the
compact expression is just a *lazier column* (parameters instead of
enumerated values), expanded on demand, verified by comparing the expansion
against an enumerated sibling.

## 5. Relation to the three-sibling publication model

Historically VRT was privileged as the origin: `vrtstack` builds it,
`blocklist` derives kerchunk-parquet from it. xvrt breaks that ordering: any
sibling can now be the source of truth and the others fall out. A dataset held
only as an Icechunk store or a VirtualiZarr result can emit its VRT rather
than requiring one to have pre-existed. With `gdx` as the reading half, the
VRT sibling is bidirectional, and with an Arrow pivot (4.1) the whole sibling
set becomes renderings of one manifest object.

The testing story is also better than rrepr's. The round-trip assertion
becomes: open the emitted VRT; compare dims, coordinate values, dtypes, and
attributes against the original; read zero bytes. Cheap enough to run as a
conformance sweep across the whole raad collection, and it is precisely the
test that catches a coordinate-precision regression before it silently shifts
a grid by half a cell.

## 6. Caveats

**Non-materialised bulk sources.** The emitted artifact is only as valid as the
byte offsets it embeds. Anything that rewrites a source file -- reprocessing,
recompression, a provider silently reissuing a granule -- invalidates the
manifest without changing the logical dataset. Reference stores need a
staleness story: checksums, ETags, size-and-mtime, or at minimum a recorded
harvest timestamp so a consumer knows how much to trust it.

**Path portability.** Embedding paths reintroduces a sharing problem that rrepr
does not have. The same logical dataset lives at different paths per machine;
VSI prefixes and credentials leak deployment detail; a local-filesystem VRT is
useless to anyone else. This needs a templating or relativisation mode so an
emitted artifact is shareable without exposing a bucket layout, and it means
only URL-sourced manifests are truly portable.

**VRT is the larger concept.** Kerchunk and VirtualiZarr references are
strictly a byte index. VRT is additionally a declarative *processing* graph --
warped VRT, derived bands, pixel functions, resampling on read. There is no
warped kerchunk. This is the reason to keep mdim VRT alongside a reference
store rather than treating either as redundant: one says where the bytes are,
the other can say what to do with them.

## 7. R side

The gap is wider here than in Python.

- **terra**: `wrap()` on a file-backed SpatRaster returns an opaque blob, when
  the honest representation is nearly trivial -- a path plus a grid spec, both
  already exposed via `sources()` and the dimensions. `dput()` on a SpatRaster
  gives an external pointer, i.e. nothing. Check whether terra has since grown
  an as-character or similar path before building anything; there may be
  partial support already.
- **stars**: a plain array with a dimensions attribute, so `dput()` technically
  works but emits something unreadable at any realistic size.
- **Opportunity**: a `vaster`-flavoured minimal grid repro -- emit dim, extent,
  and CRS spec plus a small slab, in constructor form. The grid primitives to
  express this compactly already exist.

## 8. Next steps

The emitter exists (xvrt v0), so the steps shift from building to hardening
and extending:

1. Audit xvrt's regularity detection against the precision policy in 3.2 --
   fitted step, reconstruct-and-compare against coordinate-resolution
   tolerance, float64 emission with original dtype recorded. Test against
   OISST (regular daily), BRAN (irregular depth, monthly time), and a
   curvilinear case; the curvilinear detect-and-refuse behaviour in particular.
2. Confirm the time policy end to end: `decode_times=False` at open keeps
   coords numeric, but the emitter should be robust to being handed a decoded
   Dataset -- detect on encoded values plus units, or fail loudly.
3. Work through xvrt's declared v1 list (mosaic with overlap policy, sidecar
   source-ref coords, Zarr and nested-VRT sources, byte-convergence against
   `vrtstack::write_mdim_vrt()`), which aligns with the raad.virtualize needs
   anyway.
4. Pull the BRAN-style URL templating from the user's side of the API into a
   generative-manifest layer (section 4): template + range + exception list,
   expandable and verifiable against an enumerated form.
5. Prototype the manifest-as-Arrow-table pivot (4.1): one table, renderings to
   kerchunk-parquet, VRT via xvrt, and JSON; measure what falls out for free.
6. The code-form repr emitter -- the only remaining rrepr-niche piece -- as a
   rendering of the same intermediate structure.
7. Separately and cheaply: the R-side minimal grid repro (section 7), which
   does not depend on any of the above.

## 9. References

- xvrt (xarray -> mdim VRT writer, pure Python): https://github.com/rgdal-dev/xvrt
  - design rationale and rules: `mdim-vrt-writer-plan.md` in that repo
- xarray-rrepr: https://github.com/MartinSchobben/xarray-rrepr
- Martin Schobben, TU Wien Department of Geodesy and Geoinformation,
  Research Unit of Remote Sensing (E120-01): https://www.tuwien.at/en/mg/geo/staff
