# Where things live, and how to keep what was learnt

12 October 2026. A map of the browser-mapping repos, what each one is for,
what is duplicated between them, and the mechanism proposed to stop the
duplication from growing. Written to be dropped into a repo (wgis/docs/ or
the core package when it exists) and kept current.

## 1. The repos, by kind

There are four kinds of thing, and most confusion comes from one repo
holding two kinds at once.

| kind | repos | what it is |
|---|---|---|
| **library** | rwarp (Rust + wasm); `core/` + `engine/` inside ortho-cog-viewer, copied into wgis; aatgrid (R); lunamap's `core.js` and `grid.js` | code with no UI that other things build on |
| **shell** | ortho-cog-viewer, wgis, rangefinder, listmap3d, nuyina-proximal, lunamap, aatgrid/explorer | an app that composes libraries into a view and a UI |
| **data pipeline** | seaice.map, tnbc / wildtiles, banksia, thelist-opendata, aatgrid's grid tables | produces the COGs, cubes, indexes, parquet and tile schemes the shells read |
| **narrative / harness** | sidebyside, regular-in-what, trianglewins, hypertidy.org posts, wgis/docs, rangefinder/docs | the argument and the evidence: warp vs cell, grid frames, triangulation, mesh refinement, fidelity |

### The story in one paragraph

There is one idea underneath all of it: a coordinate system is a frame,
and most of the trouble in geospatial software comes from code that
assumes the frame instead of asking for it. The library kind (rwarp, the
viewer core, aatgrid) is where the frame is handled explicitly and
checked. The shells (viewer, wgis, rangefinder, listmap3d, lunamap,
nuyina) each put that handling in front of a particular person with a
particular job: look at a raster in any projection, draw and edit
vectors, browse a catalogue through time, fly over terrain, read a grid
reference out over a radio, watch a ship. The pipelines (seaice.map,
banksia, thelist-opendata, aatgrid's tables) make data the shells can
read without the frame being lost on the way. And the narrative repos
(sidebyside, regular-in-what, trianglewins) are where the idea is argued
and the evidence is kept, each by putting the same inputs through more
than one stance and judging the results from outside. The drift problem
is only in the first two kinds: library code copied into shells. The
other two kinds are fine as they are.

### Per repo

**rwarp** (hypertidy). The GDAL warp pipeline in Rust: GenImgProj and
approximate transformers, source-window planning, resampling kernels over
any sample type, `CrsTransform` trait with proj / proj4rs / caller-supplied
(JS) backends. `rwarp-wasm` is the browser shim (Warper, withTransforms).
A library, cleanly. Its `www/` demo (Leaflet, LAEA tiles, sources.js,
cog.js, colormap.js) is a shell that predates ortho-cog-viewer and
duplicates what ortho-cog-viewer now does better; it can retire once the
viewer is the demo.

**ortho-cog-viewer** (mdsumner). Two things in one repo:

- `src/core/`: sources (COG, XYZ, WMTS, local files), CRS resolution
  (table, zone families, PROJ-in-wasm, any-string normalisation), two
  executors (proj4js, PROJ), `GeoTransform`, colormaps, wrap detection,
  graticule, warp backends. Worker-safe. **This is the library.**
- `src/engine/` + `src/main.ts`: mesh engine, warp engine, renderers, the
  shell. The engine seam (`LayerEngine`) is library; the shell is the app.

**wgis** (mdsumner). A copy of ortho-cog-viewer's core and engine at
3e00588, plus the things ortho-cog-viewer does not have: vector layers
(read, write, project, densify, reproject, a vector engine), drawing and
editing, the start card (scopes, locate), and mesh refinement at the edge
of the map (`refine.ts`, measured in docs/mesh-refinement.md). The vector
and edit and start code is wgis's own; the copied core is the problem.

**rangefinder** (hypertidy). Plain-JS modules, no build, a standalone
single-file build. Its own CRS chain (`mapcrs.js`), COG reader (`cog.js`),
tile sources (`tiles.js`), compositing and ramps (`render.js`) - all
parallel to ortho-cog-viewer's core, written independently. Its unique
value is everything else: STAC search, starc stores, wildtiles cube, GDAL
VRT mosaics with overviews, Zarr (zarrita), HEALPix and curvilinear grids,
the day list and timeline, clear-day scanning, inspect, export. It is the
catalogue-and-time shell.

**virtual-cog-pilot** (mdsumner). COG IFDs to kerchunk byte references,
a TIFF-predictor codec for zarrita, a Zarr store that reads a COG's
tiles as chunks. Small and precise. It is one `RasterSource` waiting to
be: a Zarr source whose special case is a COG.

**listmap3d** (mdsumner). Two terrain viewers over the Tasmania 2 m DEM:
MapLibre with a `cogdem://` protocol that cuts terrain tiles from the COG,
and a three.js native viewer with a quadtree in the view CRS, 128 x 128
tile meshes with skirts, split by screen-space error. Plus LIST WFS paging
with the lat/lon-order quirk documented. The native viewer is the terrain
prototype the blue-sky conversation asked about: LOD, skirts, error
metric, all already worked out, in a projected CRS, from a COG.

**seaice.map** (mdsumner). R/targets pipeline: NSIDC sea ice to COGs on
Pawsey with a parquet index, and a vessel map. Data pipeline; the viewer
side is a consumer.

**nuyina-proximal** (mdsumner). A single-page vessel tracker over the AAD
WFS, with a design document for voyages and viewpoints. A shell with one
job; its design doc is the useful part.

**sidebyside** (mdsumner). Warp vs cell, page by page, with a named gap
and a bridge each; the article; the thesis history notes. Narrative, and
the place where the conceptual frame lives. Not a viewer, on purpose.

**lunamap** (mdsumner). A phone-first static site, no build: tap or type
a location in Tasmania and get it back in every format a person might
read out or paste (address, lon/lat both orders, MGA94 and MGA2020, grid
reference, street atlas and map book squares, DMS), with LIST layers,
routing over the LIST road network, drawing, offline tiles. Two kinds in
one repo. The shell is the pin panel and the fire-brigade layers; the
library is `core.js` and `grid.js`: forgiving coordinate parsing in a
dozen notations, MGA zone handling, the grid overlay labelled at the
screen edge. wgis already reuses the parsing, which is the tell: that
part wants to be a module.

**banksia** (mdsumner). Every openly licensed Australian cadastre,
harvested by GDAL straight from the custodians' endpoints into
GeoParquet, GDA2020, one partition per state, with a small shared core
schema as a DuckDB view. `sources.csv` is the whole configuration: one
row per source with route, endpoint, layer, filter, page size, sort key,
licence. `docs/sources.md` is the survey of all eight jurisdictions and
every quirk met on the way. A pipeline, built on the thelist-opendata
pattern (read the public source directly, stage nothing by hand). This
is the shape for the "aggregate and cloudify from LIST and other state
sources" work: a sources table, a harvest script that reads it, a
survey document, and a core schema over the output. Each new state
dataset is a row and a paragraph, not a new repo.

**aatgrid** (mdsumner). The AAT two-level tiling: UTM zones 42S to 58S,
43.2 km tiles at 60 m nesting 6 x 6 into 7.2 km tiles at 10 m, 720 px,
Sentinel-2 aligned, ids like `43S_L1_0006_0114`. An R package (the
library: zones, tile ids, children, bounds, MGRS, raster-to-tile
identification), generated tables (a pipeline output), and `explorer/`
(a small shell that draws the grid over a coast). All three kinds in one
small repo, and that is fine here because they are one idea; the thing
to remember is that the explorer is a consumer of the scheme, not its
definition.

**regular-in-what** (hypertidy). A Quarto book consolidating the grids
writing: a grid is regular, rectilinear or curvilinear *in a frame*, and
"what kind of grid?" is two questions (index mapping, and frame) plus a
third thing (what was stored) that conventions read as if it were the
answer. Chapters walk the costumes A to H, the communities, the
software behaviour, a field guide, cases, edges. ASCII only, figures by
script, code chunks not executed. Narrative, and the conceptual twin of
sidebyside: sidebyside is about the warp, this is about the grid the
warp reads from.

**trianglewins** (mdsumner). A comparison harness for constrained
triangulation in R: the same inputs (vertices and segments) through four
backends (CGAL via laridae, CDT via cdtr, spade via trowel, Triangle via
RTriangle), each reduced to a vertex table and a triangle table, judged
by `tw_measure()` which belongs to none of them. Missing backends are
skipped, not failed. It doubles as laridae's acceptance test. Narrative
with teeth: the articles are built on the harness, so the claims are
re-runnable.

## 2. What is duplicated

| concern | copies | which should win |
|---|---|---|
| CRS resolution and executors | ortho-cog-viewer core, wgis (copy), rangefinder `mapcrs.js` | ortho-cog-viewer core: it is checked against PROJ (`check-crs.py`), has the PROJ executor, and hands out one definition string |
| COG reading | ortho-cog-viewer `cogSource.ts`, wgis (copy), rangefinder `cog.js`, rwarp-wasm `www/cog.js`, listmap3d (inline) | core `cogSource.ts`, which today learnt YCbCr and palettes; the others will not |
| tile pyramids and WMTS | core `xyzSource.ts` + `wmts.ts`, wgis (copy), rangefinder `tiles.js`, rwarp-wasm `www/sources.js` | core, for the same reason; rangefinder's time dimension handling is the piece to bring across |
| colour ramps, hillshade, compositing | core `colormap.ts` + the shader, rangefinder `render.js`, rwarp-wasm `www/colormap.js` | the shader does it on the GPU for free; rangefinder's export path still needs a CPU version, which can call the same tables |
| mesh engine, renderers, engine seam | ortho-cog-viewer, wgis (copy) | one copy, and wgis's `refine.ts` belongs in it |
| warp (rwarp in a worker) | ortho-cog-viewer, wgis (copy) | one copy |
| terrain LOD | listmap3d native only | nothing to merge yet; it is the prototype for a TerrainEngine over the core |
| coordinate parsing and MGA zones | lunamap `core.js`, wgis (copy of it) | lunamap's, as a module the core package re-exports or depends on; it is the one with the tests-by-use (a brigade reads these out loud) |
| LIST service access (WFS, WMTS, open data zips) | lunamap `layers.js`/`route.js`, listmap3d, thelist-opendata, banksia TAS row | the source-table pattern (banksia `sources.csv`) for bulk; the core's WMTS source for tiles; the WFS quirks in one ledger entry |
| UTM / MGA zone families | core `crs.ts` zone families, aatgrid `define_utm_zones`, lunamap | the core for CRS strings; aatgrid for tile ids. They should agree on the zone definitions, and a check can say so |

Everything in the "copies" column that is not the core is either older
than it or a copy of it. None of the duplicates is wrong; they are the
same idea written at different times, and they will drift.

## 3. The mechanism: core becomes a package

The one structural change that fixes the drift: `core/` and `engine/`
leave ortho-cog-viewer and become a package that every shell depends on.

- **Repo**: hypertidy/`<name>` (the name is yours; it is the thing that
  knows what a CRS means and how to draw a raster in it).
- **Contents**: `core/` as it stands, `engine/` (mesh, warp, worker,
  renderers, `LayerEngine`), the checks (`check-crs.py`,
  `check-projections.py`, `check-warp.mjs`, `check-worker-core.mjs`), the
  bundling scripts for proj-wasm and rwarp-wasm, and wgis's `refine.ts`.
- **Shells depend on it**: ortho-cog-viewer (the raster viewer and the
  demo), wgis (vector, edit, start), rangefinder (catalogue, time),
  listmap3d (terrain), each keeping only what is its own.
- **How shells get it**: npm publish is the clean answer and is the same
  shape as proj-wasm; a git dependency or a pnpm workspace works until
  then. The test is: a fix made once is in every shell on the next
  install.

Order of migration, least disruptive first:

1. Create the package from ortho-cog-viewer's `core/` + `engine/` (a
   move, with history kept via subtree or filter-repo); ortho-cog-viewer
   becomes its first consumer. Nothing visible changes.
2. wgis replaces its copy with the dependency; `refine.ts` moves up into
   the package. The two shells now share one core.
3. rangefinder adopts the core's CRS chain in place of `mapcrs.js` (one
   CRS authority everywhere), then its tile source; its own sources
   (STAC, starc, VRT, wildtiles, Zarr) become `RasterSource`
   implementations contributed to the package, bringing the time
   dimension with them.
4. virtual-cog-pilot's store becomes the package's Zarr source.
5. rwarp-wasm's `www/` demo retires in favour of the viewer; rwarp stays a
   library with the viewer as its showcase.
6. listmap3d's native viewer is rewritten, when wanted, as a TerrainEngine
   over the package; until then it stands as the prototype and the notes.
7. lunamap's parsing and grid modules are split out (an npm module, or
   a second package next to the core); wgis drops its copy; lunamap
   itself stays a no-build static site that loads the module by URL,
   which is exactly how the viewer loads proj-wasm.
8. The state-source aggregation work starts as rows in a banksia-style
   `sources.csv` under one repo (banksia itself, or a sibling for
   non-cadastre layers), with the same harvest script and survey doc.
   No new viewer; the viewer reads the parquet when it can read
   GeoParquet, which is wgis's vector engine's job.

## 3a. The comparison pattern

sidebyside, regular-in-what and trianglewins look like three unrelated
writing projects. They are one method, applied three times:

1. State the stances. Warp vs cell. Regular-in-longlat vs
   regular-in-projected vs curvilinear. CGAL vs CDT vs spade vs
   Triangle.
2. Put the same inputs through every stance. Not a description of what
   each would do; the actual run, the actual numbers.
3. Judge from outside. The measure belongs to none of the stances:
   sidebyside's named gap and bridge per case, regular-in-what's two
   questions asked of every file, trianglewins' `tw_measure()`.
4. Keep it re-runnable, so the claim is a check and not a memory.
   trianglewins does this fully (the articles are built on the harness);
   regular-in-what does it for figures (scripts print the numbers quoted
   in the text) and lists what is still "to check before publishing";
   sidebyside does it page by page.

Seen this way, several of the code checks are the same pattern at
smaller scale: `check-warp.mjs` is rwarp vs the reference warp vs
GDAL, judged by a pixel diff that belongs to none of them;
`check-crs.py` is the viewer's CRS chain vs PROJ; `check-projections.py`
is the proj4js ports vs PROJ; wgis's mesh-refinement doc is 64 vs 128
vs refined, judged by undrawn limb. The ledger in section 4 is the
index of these.

The practical consequence: when a new question of the form "which of
these is right?" comes up (an engine, a backend, a grid convention), the
answer is a harness in this shape, not an opinion in a README. The
harness can live in the narrative repo if the question is conceptual
(regular-in-what), in a package's tests if it is an acceptance test
(trianglewins for laridae), or in the core's `tools/` if it guards a
learning (check-warp). What it should not be is a paragraph that says
"we found that".

The name for this kind, if the inventory needs one: harness. A narrative
repo without a harness is an essay; with one it is evidence.

## 4. Keeping what was learnt: the ledger

Knowledge in this family of projects has a pattern: it is a fact about
some library's behaviour that cost an afternoon, and it is kept safe only
where a check would fail if it were forgotten. The checks already exist in
several repos. The missing piece is an index of them.

Proposal: one file, `LEDGER.md`, in the core package, one entry per thing
learnt, each with the claim, where it bit, where the code that encodes it
lives, and the check that guards it. Entries without a check are the
to-do list. Seeded from what is already known:

| learnt | where it lives | check |
|---|---|---|
| proj4js returns NaN for several projections without `+x_0 +y_0` | `transform.ts forProj4js`, centred presets | check-projections |
| proj4js ships its own defs for WGS84 UTM, so a code can be usable without ever being registered; `crsDefinition` must recover the string | `crs.ts crsDefinition` | check-crs handoff test |
| EPSG:9354 (IBCSO) has `lat_ts=-65`, not -71; EPSG:7899 is Vicgrid not GA Lambert | `crs.ts` table | check-crs vs PROJ |
| wintri: PROJ's pipeline defaults a missing `lat_1` to acos(2/pi), its CRS path (GDAL, pyproj) to 0; always write `lat_1` | `projections.ts`, wintri preset | check-projections (explicit lat_1) |
| proj4js lacks ortho, aeqd, eck4, natearth, hammer, wintri, igh, cea needs `lat_ts`; eck4/natearth/hammer/wintri/igh are ported | `projections.ts` | check-projections |
| `+over` shears pseudocylindricals; a sideways repeat is a rigid translation detected by `fwd(lon_0+180) - fwd(lon_0-180)` being parallel at every latitude | `wrap.ts` | (none yet; a node test over the 26 CRSs would do) |
| URLSearchParams turns `+` into space and destroys proj strings | `source.ts parseFragment` | (none; worth one) |
| geotiff.js `getBitsPerSample()` returns a number, not an array | `cogSource.ts` | (none) |
| geotiff.js `readRasters` returns stored samples: YCbCr JPEG comes out purple; use `readRGB`; a ColorMap without photometric=3 must be expanded by hand | `cogSource.ts fetch` | (none yet; a YCbCr fixture would do) |
| Git LFS: the raw URL is a 133-byte pointer and github.com's redirect to the media host has no CORS; address media.githubusercontent.com | README, ibcso preset | (none) |
| mobile pickers treat `accept` as a mode; no `accept` on the file input | index.html | (none) |
| rwarp's approximate transformer drops columns on Mercator to polar stereographic; exact does not | rwarp (to fix) | check-warp note lines |
| rwarp with closures calling proj4rs is byte-identical to the built-in backend | rwarp-wasm tests | cargo test |
| LIST WFS reads a bare bbox as lat, lon and returns GeoJSON in lat, lon with `"null"` strings | listmap3d README | (none) |
| a 64 grid leaves 2-6 percent of a globe's limb undrawn; refining at the edge fixes it for a known cost | wgis `refine.ts`, docs/mesh-refinement.md | `pnpm check-mesh` |
| exact rwarp agrees with the reference warp 100 percent on six CRSs | `warp.ts referenceBackend` | check-warp |
| PROJ-in-wasm loads inside a worker (a worker in a worker) | worker smoke test | check-worker-core |
| a grid is regular in a frame; "what grid" is index mapping plus frame plus what was stored; the stored thing is not the answer | regular-in-what ch 2, 6 | scripts/make_figures.py prints the quoted numbers |
| a regular grid is six numbers (dimension and extent), not four | regular-in-what | (none; a sanity check on CryoSat ratio) |
| the state cadastres can be read by GDAL directly from the custodians' endpoints: WFS keyset paging (VIC), ArcGIS REST (QLD, NSW, ACT), zips per council (TAS); WA, SA, NT are not openly licensed | banksia `sources.csv`, `docs/sources.md` | the harvest itself; counts in README |
| a constrained triangulation backend is judged on vertex and triangle tables, with its own depth when it has one; missing backends skip rather than fail | trianglewins `tw_measure()` | the package tests, bench |
| AAT tiles nest 6 x 6 from 43.2 km at 60 m to 7.2 km at 10 m, 720 px, across UTM zones 42S to 58S; ids `{zone}_{level}_{col}_{row}` | aatgrid R | aatgrid tests |
| a brigade reads coordinates in a dozen notations; MGA94 and MGA2020 both matter and differ by about 1.8 m; lon/lat in either order must be accepted | lunamap `core.js` | (none committed; the help page is the spec) |

The rule that keeps the ledger honest: a new entry is added in the same
commit as the code that encodes it, with a check or an explicit "(none)",
and "(none)" entries are the first thing to pick up on a quiet day.

## 5. What not to consolidate

- regular-in-what, trianglewins, sidebyside: harnesses stay with their
  question. Nothing from them moves into the core except the learnings
  the ledger indexes.
- banksia and the LIST harvests: pipeline, like seaice.map.
- lunamap: the shell stays as it is; only the parsing module moves.
- sidebyside and the article: narrative stays separate from code, by
  design, so it can say things the code does not yet do.
- seaice.map and the other pipelines: they produce data; they do not need
  to know how it is drawn.
- nuyina-proximal: a one-page app with its own design doc; it will consume
  the core when it wants a map, and not before.
- rwarp: already a library with a clean boundary. The only consolidation
  is retiring its demo.
