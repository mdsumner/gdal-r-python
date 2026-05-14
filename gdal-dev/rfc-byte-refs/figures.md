# Figures — mdim chunk-reference extraction

Two diagrams for the RFC and related writeups. Each is a standalone `.svg`
alongside this file; the image links below render wherever markdown does
(GDAL RFC wiki, GitHub, most static-site blog pipelines).

---

## Figure 1 — Extractor architecture

![Architecture of an mdim-to-vector chunk reference extractor: a multidimensional source feeds a per-chunk GetRawBlockInfo enumeration loop producing one feature per chunk, which writes to an OGR layer sink in one of three modes — attribute-only, index-space geometry, or projected geometry.](fig1-chunk-extractor-architecture.svg)

**Figure 1.** The extractor is mdim-in, vector-out. A multidimensional array
(netCDF, HDF5, Zarr, VRT) is walked chunk by chunk; each chunk's raw storage
reference becomes one feature. Because the sink is an ordinary `OGRLayer`, the
same feature stream serves three modes: an attribute-only table, a table whose
geometry is the chunk bounding box in array index space, or one whose geometry
is the chunk bounding box in the array's CRS. The three modes differ only in the
geometry choice — the reference attributes are identical across all of them.

---

## Figure 2 — Staged roadmap

![Four-stage roadmap: Stage 1 a minimum viable extractor writing an attribute-only Parquet table for one array; Stage 2 adds index-space bounding-box geometry; Stage 3 adds projected geometry conditional on a valid geotransform; Stage 4 generalises to a read-side metadriver where a reference table plus metadata is equivalent to a Zarr store. Each stage is independently shippable.](fig2-rfc-staged-roadmap.svg)

**Figure 2.** The proposal is deliberately staged, and each stage is
independently shippable. Stage 1 is a self-contained extractor — one array, all
chunks, attribute-only Parquet — useful even though it does not yet carry inline
chunk data or geometry. Stage 2 adds index-space geometry, which needs no CRS
and works for every array including curvilinear grids. Stage 3 adds projected
geometry, conditional on a well-defined geotransform and with an explicit
fallback when one does not exist. Stage 4 is the speculative end state: a
read-side metadriver in which the reference table plus array metadata is
consumable as a virtual store — the kerchunk / VirtualiZarr round trip expressed
in GDAL's own primitives.

---

### Editing notes

Both files are hand-written SVG with an internal `<style>` block — colours and
type sizes are defined once at the top of each file and easy to adjust. They use
a fixed 680-unit viewBox width. Text is dark-on-light; if either figure needs to
sit on a dark background, the `*-t` / `*-s` / `gray-t` fills in the `<style>`
block are the things to change. No external fonts or assets are referenced.
