# Side discussion: "reader-consumed, not writer-produced"

*A working-through of a phrase that appeared in the §2 sketch of the main draft. Kept as its own file because the reasoning is worth preserving even though the conclusion is to retire the phrase. May feed back into §2 when the technical middle is written.*

---

## What the phrase is trying to say

A `.vrt` file is a description that a reader uses to assemble a view. When you `gdal_translate foo.vrt output.tif` or `terra::rast("foo.vrt")`, the consumer is the one interpreting the XML, resolving the source paths, applying the transforms, and delivering pixels. The VRT itself is inert text. Nothing "produced" the VRT in the sense of running a computation whose output is the VRT — most VRTs are either hand-edited, written by `gdalbuildvrt`, or emitted as a side-effect of `gdal_translate -of VRT`. In all cases the VRT describes *what a reader should do*, not *what a writer did*.

The contrast being drawn is with something like a Zarr store or a Parquet file: those are produced by a writer that materializes bytes, and a reader consumes those bytes directly. The writer-produced artefact *is* the data. The reader doesn't re-assemble it; it just decodes it.

So the distinction is roughly:

- **Writer-produced, reader-decoded**: Zarr, Parquet, NetCDF, GeoTIFF — the file is a materialization of the data, and reading is decoding.
- **Writer-described, reader-assembled**: VRT, kerchunk reference JSON, STAC — the file is a recipe, and reading is execution of that recipe against other data.

The second category is where view-description lives, and it's the category xarray doesn't have a native entry in at the view level.

## Where the phrase gets shaky

Two problems with "reader-consumed, not writer-produced" as stated:

**First, "writer-produced" is literally true of both.** `gdalbuildvrt` is a writer; it produces a VRT. The VRT file exists on disk because a program wrote it. So the phrase, taken literally, is wrong. What was meant was something closer to "the semantics live on the reader side" or "the file is a set of instructions rather than a payload."

**Second, kerchunk is *also* reader-assembled in this sense** — it's a JSON recipe saying "for chunk X, go to file Y at byte offset Z". It's describing what the reader should do to assemble a Zarr-shaped view over other files. So if VRT is "reader-consumed," so is kerchunk. Which is fine — that's the *right* observation, and it strengthens the post's argument. But it means the phrase doesn't distinguish VRT from kerchunk the way a casual reader might assume.

The actual distinction between VRT and kerchunk isn't consumer-vs-producer, it's **scope**. Kerchunk describes how to find chunks (bytes). VRT describes how to compose a view (semantics). Both are reader-assembled. The gap the post identifies is that xarray has the *byte-level* reader-assembled format (kerchunk) but not the *view-level* one.

## A more precise phrasing

Something like:

> A VRT is a set of *instructions* rather than a *payload*. The file on disk contains no pixels; it contains a description of how to obtain pixels from somewhere else, which any GDAL-linked reader can execute. This places VRT in the same family as kerchunk and STAC — artefacts that describe how to assemble a view from underlying data, rather than being the data themselves. The difference, within that family, is what each of them describes: kerchunk describes bytes, STAC describes scenes, VRT describes pixel-views, and mdim VRT describes multidimensional array-views.

Wordier but more defensible, and it sets up the rest of the post better because it names the family explicitly. The gap then becomes: xarray has a byte-level recipe format (kerchunk) but no view-level one, in a family where other ecosystems have view-level ones.

## The underlying conceptual point, phrased most tightly

The useful distinction is between **materialized artefacts** and **descriptive artefacts**.

- Materialized artefacts carry the data.
- Descriptive artefacts carry instructions for assembling data.

Both kinds live on disk; both kinds are read by programs. But they answer different questions:

- "What is the data?" → materialized artefacts
- "What is the view?" → descriptive artefacts

Most storage formats (Zarr, Parquet, GeoTIFF, NetCDF) answer the first question. VRT, kerchunk, STAC, NcML answer the second. The second category is less developed in every ecosystem, because it's conceptually harder (what's the right set of primitives?) and because most users need the first category most of the time.

The xarray ecosystem, specifically, has a descriptive format at the byte level (kerchunk) and not at the view level. That's the gap the main post is about. And the reason it matters is that view-level descriptive artefacts are what make analysis-ready data *shareable across languages and tools* — because the view description is the contract, and anyone who can execute the contract gets the same result.

## For the main post

Retire "reader-consumed, not writer-produced" in favour of something like:

- "VRT is a *recipe*, not a *payload*."
- "VRT is a set of instructions the reader executes, not a container of data the reader decodes."
- "VRT describes a view; it does not carry it."

Any of those makes the point cleanly. The recipe/payload framing is probably the most accessible and sets up the "xarray has a recipe format for bytes but not for views" observation well.

This is really a §2 concern more than a §1 concern, which is probably why it felt thin when it first appeared up top.

## A taxonomy worth keeping

If the main post wants it, the family looks something like this:

| Format     | Describes                         | Consumed by        |
|------------|-----------------------------------|--------------------|
| Kerchunk   | Byte offsets of chunks            | Zarr-speaking reader |
| STAC       | Scenes and their assets           | Catalog-aware reader |
| NcML       | A view over NetCDF                | NetCDF-Java, THREDDS |
| VRT        | Pixel-views over rasters          | GDAL               |
| mdim VRT   | Multidimensional array-views      | GDAL mdim API      |

What's missing from this table, for the xarray ecosystem, is a row whose "Describes" column reads "xarray Dataset-views" and whose "Consumed by" column reads "xarray". Kerchunk fills a different row. STAC fills a different row. No row currently exists for xarray-view-serialization.
