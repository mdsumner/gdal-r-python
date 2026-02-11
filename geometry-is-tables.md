# Geometry is tables all the way down

<!-- harvested from r-gris/table-r-book, polyggon, and the spbabel/gris/silicate lineage -->
<!-- target: Ch 8 "Geometry as topology" in the spatial book -->
<!-- status: first draft fragment, needs integration with vertex-pool material -->

## The GIS contract

GIS gives you a table where each row is an object and each object has a geometry. One row, one shape, one set of attributes. I call this the GIS contract, and you can see it enforced everywhere: linked selection in QGIS, the `@data` slot in sp, the sticky geometry column in sf. It's a useful guarantee. It's also a prison.

The moment you want to know something about a *part* of an object — the area of one ring in a multipolygon, the length of one linestring in a multi — you have to break the contract. You explode the object, badge every piece with an ID, track those IDs yourself. The moment you want to store per-vertex attributes (time on a GPS track, depth on a bathymetric contour, temperature on a sensor path) the contract has nothing for you. The geometry column is opaque: coordinates go in, coordinates come out, and there's nowhere to hang additional information on the way through.

Two objections come up whenever you suggest storing geometry in tables:

1. "You have to copy the metadata onto every coordinate row." No. You need two tables, or three, and a join key. This is what databases have done since 1970.

2. "You can't properly store polygons with holes." You can — R's `polypath()` function has supported winding and even-odd fill rules since R 2.12. The problem was that `ggplot2::geom_polygon()` used `grid.polygon()` instead of `grid.path()`, so holes rendered as filled. The geometry model was fine; the renderer was incomplete.

The more interesting problems are subtler. Copying metadata onto coordinates creates the risk that IDs and values get out of sync. The coordinate-order attribute and the grouping can be subsetted in ways that silently corrupt the geometry. And the ring model itself is susceptible to self-intersection — winding and even-odd rules disagree on star-shaped polygons, and neither corresponds to the point-in-polygon test you actually want.

## Five forms of the same data

There's a spectrum of representations for the same spatial objects. Each form has different strengths, and converting between them is where the power lies.

**1. Nested lists.** This is what sp and sf use internally. A multipolygon is a list of lists of matrices. The physical nesting mirrors the logical hierarchy: object → part → ring → coordinates. It's natural for storage but opaque to tabular operations. You can't `filter()` a nested list.

**2. Nested data frames.** Using tidyr's `nest()`, you can store each object's geometry as a data frame inside a list column. One row per object, geometry tucked away as a tibble. This preserves the GIS contract at the top level while making the internals accessible to dplyr verbs. It's a useful intermediate but doesn't allow vertex de-duplication or topology.

**3. Fortify (two tables).** This is what `ggplot2::fortify()` did: one table of coordinates with columns for object ID, part ID, hole flag, and vertex order; one table of object attributes joined by ID. It breaks the GIS contract — objects now span multiple rows — but everything is flat, joinable, and plottable. Two tables, one join.

**4. Branch (three tables).** Separate the parts from the coordinates. An object table, a branch table (one row per ring or linestring, carrying hole status, parent ring, area, length, or whatever per-part attributes you need), and a coordinate table with branch ID and vertex order. Now branches are first-class entities. You can compute on them directly. A GPS track with multiple deployments becomes: objects (animals), branches (deployments), coordinates (fixes with timestamps).

**5. Primitives (four tables).** De-duplicate the vertices. Every unique coordinate gets a vertex ID, stored once. Branches or primitives (line segments, triangles) reference vertices by ID through a link table. Four tables: objects, primitives, links, vertices. This is where topology lives — shared boundaries are explicit, not coincidental. And it's where the link to mesh representations becomes direct.

The progression from form 1 to form 5 is a sequence of normalization steps, in the database sense. Each step makes the structure more explicit and more general, at the cost of needing joins to reassemble the original. The key transition is between forms 3 and 5: you go from "coordinates that happen to be the same" to "vertices that *are* the same, referenced by index."

## The step that changes everything

To get from branches to primitives, you do two things:

1. **De-duplicate vertices** in coordinate space. Every unique (x, y) becomes a single row in the vertex table, with an ID.

2. **Convert paths to a planar straight line graph (PSLG).** Instead of "ring = ordered sequence of coordinates," you have "segment = pair of vertex IDs." The ring's path becomes a chain of segments.

Once you have a PSLG, new structures become available that nested lists can never express: arc-node topology (the TopoJSON model, where shared boundaries are stored once), constrained Delaunay triangulation (every polygon becomes a mesh of triangles sharing a vertex pool), and the doubly-connected edge list (faces, edges, and vertices with full adjacency information — the structure underlying the Atlantis ecosystem model I worked with at AAD via the rbgm package).

This is exactly what rgl has always done. Every rgl rendering primitive — triangles, quads, line segments — is defined as indices into a coordinate array. The coordinates don't have to be unique, but they *can* be, and when they are, you have a true mesh. Rgl includes ear-clipping triangulation to convert polygons into triangle surfaces, and those surfaces can wrap around in ways that GIS polygons never could.

The realization that these are the same idea — the database normalization of spatial geometry and the indexed vertex pool of GPU renderers — was the seed of the silicate package.

## What the tables buy you

Once geometry is decomposed into normalized tables, several things that are hard or impossible with nested structures become straightforward:

**Per-entity attributes at the right level.** On a triangulated terrain surface, elevation belongs on the vertex, not duplicated across every triangle that references it. On a GPS track, timestamps and sensor readings belong on the coordinate-instances, while deployment metadata belongs on the branch. On a multi-level administrative hierarchy, state boundaries and county boundaries can share vertices, and the containment relationship is just a join.

**Database portability.** A set of normalized tables transfers directly to and from any database without specialist spatial types. You can store a full topological spatial dataset in SQLite, PostgreSQL, or DuckDB using nothing but standard tables and integer foreign keys. No geometry columns, no WKB, no spatial extensions required. Reading it back needs no special tools — just joins.

**Round-tripping through different systems.** The same decomposition can reconstruct sp objects, sf objects, ggplot2 aesthetics, rgl meshes, or GeoJSON. A cascading inner join rebuilds the fortify table; a cascading semi-join propagates a subset from the object table down through the hierarchy. The normalized form is the universal intermediate.

**Topology.** Change one vertex and every primitive that references it moves. Shared boundaries between adjacent polygons are stored once — edit once, both sides update. This isn't exotic; it's how every CAD system and game engine works. GIS just never adopted it at the format level, and the nested-list representations in R actively prevent it.

## The dream that partially came true

Back when ggplot2 had just appeared and `fortify()` was being frowned upon by the "spatial is special" crowd, I could see something that seemed both obvious and unrealized. If you have a data frame of coordinates with group IDs, you already have geometry. The grammar of graphics knows this — you assign x, y, group, fill, and it draws polygons. You don't need a geometry column. You need a *convention*.

I imagined an API like this:

```r
track_df |> group_by(tag_id, deployment) |> arrange(timestamp) |> linearize(x, y)

poly_df |> group_by(id, part) |> arrange(order) |> polypatherize(long, lat)
```

Then pipe to a driver at the end — `as_sf()`, `as_sp()`, `as_geojson()`, `as_wkt()` — to get the right format for a given application. The grouped, arranged data frame *is* the geometry; the output format is just a serialization choice.

Pieces of this vision have since appeared. The sfheaders package builds sf objects from flat data frames at high speed. The wk package provides a streaming handler protocol for coordinate sequences. The geos package gives you GEOS operations on coordinate vectors. But the unifying "geometry is just a grouped data frame" idiom — where the same pipe chain can produce a line, a polygon, a mesh, or a trip object depending on what you ask for — never quite crystallized into one package.

The reason, I think, is that it requires solving the vertex-identity problem first. You need to know when two coordinates at the same position are the *same vertex* (shared boundary) versus merely *coincident* (overlapping features). Flat tables don't carry that information. You need the fourth form — the primitives model with a vertex pool — and you need it to be lightweight enough that people use it by default rather than as a special-purpose tool.

That's the direction the next generation of spatial infrastructure is heading. The wk vertex-pool proposal, the sidecar topology model we've been discussing, and the n-dimensional vertex table that breaks free of the XYZM cul-de-sac — these are all attempts to finish what fortify started: geometry as tables, with topology you can opt into.

<!-- 
## Lineage notes (not for publication)

The package progression: spbabel (2015, two-table round-trip sp↔fortify) → 
gris (2016, four-table "o, b, bXv, v joinable chain") → rangl (2016, primitives + 3D) → 
silicate (2017–present, PATH/SC/SC0/TRI/ARC models as formal S3 classes).

polyggon (2016) was the epiphany package: proved that flat-table polygons + 
polypath(rule="evenodd") rendered holes correctly in ggplot2 via geom_holygon(). 
The hole problem was never a data-model problem, it was a renderer problem.
ggplot2 fixed it natively in v3.2.0 (2019) with the subgroup aesthetic.

ggpolypath was the CRAN version of the same idea; now deprecated in favour of 
ggplot2's native support.

The "dozens of trajectory packages" observation (2016) remains true in 2026.
-->
