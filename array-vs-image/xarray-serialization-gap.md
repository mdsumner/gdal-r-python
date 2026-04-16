# [Working title: the serialization gap]

*Draft — bookend sections with space reserved for the technical middle. Review notes at the end.*

---

## 1. The asymmetry

Two communities have spent decades solving overlapping problems from opposite ends, and have produced two remarkable, incompatible answers.

On one side, GDAL. Starting from the problem of remote sensing imagery — rasters with georeferencing, a handful of bands, a projection, and an expectation that "opening a file" should mean something sensible across dozens of formats — GDAL built up an abstraction that eventually included VRT, a language-independent XML description of sources, transforms, and output structure. A `.vrt` is consumed by the reader, not written by the producer; every GDAL-linked tool in every language reads the same VRT and gets the same view. GDAL extended this to N dimensions with multidimensional VRT in 2019. The abstraction is real, it's powerful, and two decades of accumulated subtlety have made it genuinely hard for newcomers to understand.

On the other side, xarray and Zarr. Starting from a different problem — the numerical modelling and climate-science world's struggle with NetCDF's limitations at scale — xarray built a labelled, dimension-aware array abstraction that treats coordinates, attributes, and groupby semantics as first-class. Zarr then answered the storage question that NetCDF couldn't: chunked, parallel, cloud-native, schema-evolvable. Together they are not a lightly modified rehash of what geospatial tooling already had. They are a fundamental and novel solution to a class of problems the image-oriented stack never really addressed.

The two grew from the same soup in different directions. The image-processing lineage and the array-computing lineage share more than either community usually admits — both are, underneath, about rectangular numerical data with coordinates and metadata — but the rift between them is real, and the vocabulary, idioms, and assumed file layouts diverge sharply. You can write a long list of binary oppositions (bands vs. variables, affine vs. coordinate arrays, nodata vs. `_FillValue`, subdatasets vs. groups, tiling vs. chunking) and argue about every single one. They don't merge, and the crystals that have formed on each side have their own internal consistency.

Which brings the post to its narrow point. Xarray and Zarr succeeded spectacularly at what they set out to do. But there is one piece the image-processing side has had for a long time — serialization of a *view*, not just a store — and for which the array-computing side has a conspicuous gap. Kerchunk and VirtualiZarr express chunk-level virtualization: byte-range references that make a NetCDF or HDF5 file readable as if it were a Zarr store, without copying. This is a real and important contribution. What it does not do is serialize an xarray Dataset-view. A Dataset that has been selected, renamed, concatenated, or had derived variables computed is a Python object. There is no canonical on-disk form. To share it, you share the Python that produced it.

R now has several routes into the Zarr world. Rarr has been on Bioconductor for some time — emerging from bioinformatics use cases where Zarr has become increasingly standard — and provides a pure-R V2 reader. The `zarr` package arrived on CRAN, preceded by foundational Zarr-engine work inside CopernicusMarine. pizzarr landed on CRAN more recently with V3 support. The multidimensional surface in terra, stars, and gdalraster is smaller and newer than the equivalent for classic raster, but growing fast. [Space for your notes on the people whose work made this happen — Pepjin, David, and others.]

From the vantage of someone arriving new to the multidimensional conversation through the R-side, the xarray gap is visible in a particular way: the ability to *read* a Zarr store is not the same as the ability to *describe* an analysis-ready view. One handles bytes on disk. The other would handle the semantics above them. This post is about that second thing — the format xarray does not yet have, and that GDAL, for its own reasons and in its own neighbourhood, has shipped for a quarter century.

---

## [Technical middle — sections 2, 3, 4 to be written]

*Placeholder. Target ~1000 words across three sections:*

- *§2 — What VRT is, for the uninitiated. A small concrete example (reprojected-and-subset GeoTIFF, ~10 lines of XML). Emphasise: reader-consumed, not writer-produced. Language-independent.*

- *§3 — What mdim VRT adds. A minimal mdim VRT concatenating GeoTIFFs along a named time axis with coordinates (~30 lines of XML). Flag clearly: mdim VRT is a natural extension but is **not** interchangeable with classic VRT — two dialects with overlapping vocabulary. mdim is recent but growing fast.*

- *§4 — The xarray side of the mirror. Kerchunk/VirtualiZarr solve chunk-level virtualization. Walk through a concrete case where you want to hand someone a file that **is** the analysis-ready view, and show what kerchunk can express (byte-refs) and what it can't (view semantics). Keep the example scoped — avoid pulling in per-tile CRS / Sentinel-2 since that belongs in the follow-on post.*

---

## 5. What the shape of the missing thing looks like

*This section still reflects the earlier tone — needs a light pass to match the "two crystals" framing established in §1. Flagged in review notes below.*

The gap is not "xarray needs a storage format." Zarr is a storage format and a good one. The gap is one level up: a format for describing views *over* storage, declaratively, in a form that doesn't require running the code that produced the view.

Most of the machinery for such a format already exists. Multidimensional VRT handles concatenation along named dimensions, coordinate attachment, source transformations, and derived variables through expressions. What an xarray-native version would add is a thin layer of xarray-specific semantics: indexes, selection by label, the conventions around dimension naming and coordinate variables. The hard parts — the source-and-transform algebra, the N-dimensional structure, the language-independent XML (or JSON, or whatever) — are solved problems in a sibling ecosystem.

There are real caveats. Classic GDAL VRT and multidimensional VRT are not interchangeable; they are two dialects with overlapping vocabulary, and the mdim dialect is recent enough that its adoption curve is still climbing. Any xarray-serialization format modelled on it would inherit that youth. Not every xarray operation is serializable — lazy computations involving arbitrary Python callables cannot be written down, and the format would have to draw a line between operations it can capture and operations that force materialization. GDAL VRT draws this line in its own place, and an xarray version would draw it differently.

But the observation that lands, for someone arriving from the R-spatial world with VRT in muscle memory, is that the xarray community has been solving serialization *downward* — toward bytes, through kerchunk and VirtualiZarr — while the serialization problem *upward* — toward views, through something like VRT — has received less attention. This may be because the Python ecosystem's single-language cohesion makes "just share the notebook" feel sufficient, and because xarray's richness makes a clean declarative subset feel like a compromise. These are reasonable reasons. They are also the reasons the gap persists.

The cross-language view-description format that xarray is missing may already exist, in a different neighbourhood, wearing different clothes. Whether the right answer is to adopt it, adapt it, or build something fresh that rhymes with it is a question for the xarray community. But it is a question worth asking, and one that is easier to see from outside the tent.

---

# Review notes

## Names and credits to confirm/flesh out

- People behind Rarr, `zarr`, pizzarr, and the CopernicusMarine Zarr-engine work. "Pepjin" and "David" are placeholders — confirm full names, correct spellings, and add anyone else whose contribution belongs in that paragraph.
- Double-check Rarr's Bioconductor vs. CRAN status and the rough timeline of each package's arrival. The post will be dated by these details so worth verifying once more before publishing.

## Claims to sanity-check

- GDAL VRT origin date (said 2001) and multidimensional VRT arrival (said 2019). Both from memory — verify against GDAL changelog / release notes.
- "Zarr answered the storage question that NetCDF couldn't" is a fair shorthand. If you want to be more precise (parallel writes, cloud-native access, chunk-level random access), swap in the specifics that matter most to you.

## Tonal judgment calls to revisit

- "Two decades of accumulated subtlety have made [GDAL] genuinely hard for newcomers to understand" — honest and self-balancing, but airs criticism of the GDAL side. Keep / soften / remove.
- "Fundamental and novel solution to a class of problems the image-oriented stack never really addressed" — strong claim about xarray/Zarr. Intentional, but check it reads as respect rather than overstatement.
- "Conspicuous" as the descriptor for the gap — kept once, with surrounding context. Swap for "visible" or "apparent" if still reads sharp.
- "Two crystals from the same soup" metaphor — used in prose form. Could lean into it more (section heading, through-line) if desired.
- Binary-oppositions list (bands vs. variables, affine vs. coordinate arrays, etc.) — gave a handful inline. May want more, fewer, or different ones depending on audience recognition.

## Section 5 needs a light revision

Section 5 was drafted before the "two crystals" framing. It'll need a pass to match the same register — less "the gap persists for reasons" and more "the rift is real, and this is one place where a bridge is shorter than it looks." Ping for the rewrite when the technical middle is in place.

## Deliberately absent

- No mention of your own projects, per your direction. If a reviewer asks "okay, what would this look like concretely?" — that's post (b) territory, not this one.

## Structural question

- Current ordering: xarray/kerchunk description → R-reader landscape → vantage observation. If you'd rather lead with R readers and then contrast with what xarray has/hasn't, the restructure is easy.
