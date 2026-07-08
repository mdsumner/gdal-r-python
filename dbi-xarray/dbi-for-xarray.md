# DBI for xarray (working title)

A design note, parked. Distilled from the rema-icechunk work, July 2026.

## Thesis

An array store is already a database. A Zarr store is a key-value relation
(key "array/c/i/j", value blob); Icechunk is that relation under transactional
version control, with manifests as index tables. Kerchunk-parquet, Icechunk
manifests, rustycogs/blocklist refs output, and a SQLite byte-cache are four
serializations of the same relation. Therefore the right access layer for R
(and anything else) is not a port of xarray but a DBI-style connection over
that relation, with grid math done client-side and only integers crossing any
boundary.

The array is a rendering format. Normal form is the chunk relation plus a
codec recipe plus six numbers. A materialized array is a view.
(Same move as silicate: simple features are a rendering format over
vertex/edge tables. Same payoffs: the normal form is diffable, queryable,
joinable; renderings are cheap and disposable.)

## The relation

```
datasets: dataset_id, title, crs, source, engine_hint
arrays:   dataset_id, array_id, shape, chunk_shape, dtype,
          codec_chain, fill_value, affine (6 numbers), level_of, group
chunks:   array_id, i, j, ..., location, offset, length, etag
blocks:   array_id, i, j, ..., blob            -- latent; collect() = range-GET
```

- `chunks` is the index: tiny, permanent, shippable (a parquet file).
- `blocks` is lazy in the dbplyr sense; collect costs network GETs.
- `codec_chain` is first-class schema, not opaque metadata. A missing codec
  column (predictor=3) is silent garbage; this is a schema problem and the
  schema must own it.
- `etag` makes caching a materialized view of `blocks` with principled
  invalidation, and makes source drift detectable.

## The mappings that make it DBI

- dbConnect(dsn)        -> run the recipe (registry row), get a handle
- dbListTables          -> arrays / groups
- predicate pushdown    -> bbox compiles via affine to integer ranges on (i,j);
                           the query planner is grid math, client-side, in R
- lazy tbl + collect    -> blocks table; lazysf already built this idiom
- transactions / AS OF  -> icechunk commits and snapshot sessions, literally
- scalar UDF over blobs -> decode(blob, codec_chain, chunk_shape)

## The verb set (small, closed)

```
con  <- dsn_connect(sds::dsn("rema_v2_icechunk"))
g    <- grid(con)                       # affines, dims, levels: pure data
x    <- read(con, bbox, level)          # THE contract, see below
y    <- regrid(x, target_grid, kernel)  # the warper, invoked by name
tbl(con, "chunks")                      # drop to the relation any time
```

## The read contract (non-negotiable)

read(bbox, level) returns (array, affine) where the window is bbox snapped
outward to the level's pixel edges and the affine is the level affine offset
by the window. Shape is an output, not an input. Every returned value is a
source sample. Anything that changes the grid -- shape, resolution, rotation,
CRS -- is regrid(), a separate named operation with a full target-grid
specification. Kernel-in-read (GDAL RasterIO with -r != near) is thereby
unexpressible, not prohibited. In a pyramid-complete store this costs
nothing: the pyramid is the precomputed anti-aliased decimation.

Consequences: reads compose bit-for-bit; every read is assertable against
source; contract-fixture testing extends into the access layer.

## Layers (each one a table or a function over tables)

```
registry row (sds)  ->  connection  ->  grid()/read() verbs
     ->  chunks relation (parquet / manifest / SQLite)  ->  blobs (range-GETs)
```

Veneers on top: R wrapper, Python/xarray snippet, JS client, a website's
copy-paste box -- all renderable by template from the registry row plus the
relation. Publishing data about data, not an API.

## What already exists

- harvest: rustycogs tiff_refs / tiff_ifd_info (minutes for whole archives)
- relation serializations: refs parquet, kerchunk-parquet (blocklist),
  icechunk (build_rema_icechunk.py pattern)
- grid math: vaster (affines), grout/tilemath (partitions)
- transport: bowerbird curl multi pool; paws.storage
- query prototype: DuckDB over refs.parquet, today, zero new code
- proof of the whole decode chain: LZW + floatpred + bytes round-trips
  bit-identically (rema-icechunk fixtures)

## The one unproven primitive

R-native chunk decode: TIFF-flavoured LZW + floating-point predictor undo,
natural home rustycogs. Spike: DuckDB query -> range-GET -> decode -> 512x512
float matrix == vapour read of the same window. An afternoon. If it passes,
nothing in this note requires GDAL or Python on the read path.

## Non-goals

- Not SQL for hyperslabs: read() is a method on the connection, made
  inspectable by the relation underneath, not replaced by it.
- Not sub-chunk relational access: the blob is the atom; sub-chunk is
  decode-then-slice.
- Not a rewrite of xarray, GDAL, or anything: those become veneers and
  oracles over the same relation.

## Naming

Undecided. Candidates live near blocklist (which already owns the chunk
relation) and dsn/sds (which own the connection). Possibly this is not a new
package at all but the stated architecture that blocklist + dsn + vaster +
rustycogs already form.
