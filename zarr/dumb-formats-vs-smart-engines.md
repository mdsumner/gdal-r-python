# The dividing line: dumb universal formats vs. smart engines

*A discursive explainer, distilled from a conversation while building `vrefs`
(GDAL-composed, rhdf5-scanned virtual Zarr reference stores). The thread started
at a concrete problem — committing 16M+ chunk references to Icechunk past a
32-bit serialization limit — and zoomed out to the structural tension running
through STAC, Zarr, Icechunk, kerchunk-parquet, async-tiff, COG, Arrow, and the
database/lakehouse world.*

## The line

There are two coherent philosophies for "make a large dataset addressable," and
most of the cloud-geo and cloud-array stack is a negotiation between them:

1. **Dumb, universal, serverless formats + many independent engines.** Express
   the minimum needed, make the index resolvable by arithmetic or by fetching
   small named files, and let any number of readers implement access
   independently. COG, kerchunk-parquet, async-tiff, STAC-as-flat-JSON. Maximum
   *reach*, minimal query power.
2. **One smart engine + internalized storage.** Put the data (or at least the
   index) inside a real query/transaction engine — a database — and get
   indexing, transactions, versioning, range queries "for free." Maximum
   *capability*, but the store needs *the* engine to be read, and inherits its
   dependencies and ceilings.

Zarr, Icechunk, and kerchunk sit on the first side. DuckDB/SQLite-style
internalization is the second. The interesting thing is that both sides are
right, and the line between them is being raced over from several directions at
once.

## "Should Zarr/Icechunk just have used a database?"

The instinct is sound — these systems *are* reinventing things databases solved
decades ago. Icechunk's snapshots are copy-on-write MVCC. Manifest splitting is
partitioning. The flatbuffers 2 GiB manifest ceiling is exactly the kind of
homegrown-serialization wall a mature on-disk B-tree simply doesn't have. So
"why not put the chunk index in a real engine" is a legitimate, live question,
not settled folklore. But there are four reasons it mostly hasn't gone that way,
and only the first is fundamental:

**The chunk *data* can't go in the database — only the index can.** A 17 GB
array chunk isn't going into DuckDB; the whole point of virtualization is that
the compressed bytes stay put in HDF5/object storage. So a "database-backed
Zarr" is really "engine holds the manifest table, object storage holds the
chunks" — which is *exactly what a kerchunk-parquet reference store already is*,
just with parquet + positional addressing instead of an embedded DB as the index
engine. The reference table (`path/offset/size` per chunk) is already a database
table. They didn't avoid the database; they built a deliberately minimal one,
tuned for a single query.

**That one query is degenerate.** The only access pattern is "resolve a chunk
coordinate to a byte range." No joins, no aggregations, no ad-hoc predicates.
For that, a positional index — compute a key, open one file, read one row — is
*faster* than SQL, because the coordinate *is* the address: zero query planning,
zero metadata read. A full engine would be 98% unused and would charge planning
and footer-reads on every chunk fetch. The "reinvention" is specialization: they
reimplemented the ~5% of database technology this workload needs, in a stateless
form.

**Serverless / object-store-native is the binding constraint.** Zarr's killer
property is that a store on S3 needs *no server*: a reader does ranged GETs
against static files. A million different readers in a million environments —
xarray, GDAL multidim, a browser, `zaro`, a Rust tile server, terra, stars — can
all do "compute key, fetch bytes." Far fewer can embed and run a query engine
next to the data. The lowest-common-denominator addressing is precisely *why* a
single reference store can be opened by five readers without coordination. A
DuckDB-internalized store would read in fewer places — the opposite of the
property that makes such a store worth publishing.

**The ceiling is the cost of the minimalism, and it's fixable.** The flatbuffers
limit exists *because* they hand-rolled a serialization format rather than lean
on a database's storage layer. That's a fair criticism. The counter is that the
ceiling is removable (manifest splitting) without taking on an engine
dependency — you keep the reach and patch the wall.

So the steelman of the status quo: they took the *table* idea (the manifest is a
relation) and the *versioning* idea (Icechunk = MVCC), but deliberately **not**
the *query engine*, because the access pattern doesn't need one and an engine
dependency would forfeit serverless reach.

## The GDAL meta-driver corollary

GDAL is already partway across this line, which sharpens the distinction. Its
multidim model is a meta-driver over heterogeneous chunk sources; `gdal mdim
mosaic` composing a VRT *is* a query planner producing a logical view over
physical files (the per-source `DestSlab` placement is the plan). A future GDAL
Icechunk driver makes GDAL a *reader* of the database-ish store. So GDAL is
converging on "engine that resolves logical coordinates to physical byte ranges
across many backends" — the query-planning half of a database, minus storage and
transactions.

Could such a meta-driver "internalize everything" DuckDB-style? Plausibly it
could grow a unified index layer. But the same constraint bites: GDAL would
become *the engine*, and unless the store stays readable by non-GDAL tools you've
built a GDAL-only database and lost the interop. The reason a dumb reference
store works in xarray *and* GDAL *and* `zaro` is that none of them is the engine —
the format is dumb and universal. The moment the index needs a specific engine to
interpret it, you're back in a single-reader world with an implicit server
dependency, which is the thing the dumb-format camp was reacting against.

## The 2015 rhyme: netCDF vs. GeoTIFF

This cycle rhymes precisely with the COG-vs-netCDF argument of ~2015. That fight
was about *container generality vs. profile sufficiency*. netCDF can express
anything — groups, ragged arrays, arbitrary dimensions — and that generality
means a reader must handle the whole model to read anything. GeoTIFF (then COG)
expressed *less* but was *sufficient* for the gridded-raster case, and the
insufficiency was the feature: a constrained format is universally readable
because there is less to implement. "Don't put it in netCDF if GeoTIFF suffices"
was really "don't pay the generality tax when the constrained profile reaches
more readers." COG won the web not by being more capable but by being dumb enough
that a browser doing range requests could read it.

The same sentence, one level up, is the emerging discipline now:
**kerchunk-parquet / COG-style references if sufficient; Icechunk only when you
genuinely need transactions and versioning.** A dual-store strategy is that
sentence made operational — the parquet sibling plays the COG role (dumb,
universal, publish-anywhere); Icechunk plays the netCDF role (powerful,
engine-backed, for the mutable/versioned working set). You don't pick a side; you
*profile*: emit the sufficient thing for reach, emit the capable thing for the
cases that need it.

## What's new this cycle: STAC, the lakehouse, and Arrow

Two things are genuinely new since 2015, and they're why the line is "raced over
from many directions" rather than settled.

**STAC is connective tissue that didn't exist before.** The COG-vs-netCDF fight
was about a single file's format. STAC adds a *catalog* layer indexing across
files and assets — so the "where is everything and how is it addressed"
knowledge now lives in a dumb, universal, queryable JSON layer *on top of* dumb
universal data files, engine optional. A reference store is STAC-adjacent: it's
the asset-to-byte-range index a STAC item can point at.

**The lakehouse is the database world arriving at the same compromise from the
other side.** Iceberg and Delta are exactly "a database metadata layer over dumb
columnar files on object storage." Icechunk is, in effect, *Iceberg for arrays*.
DuckDB querying parquet on S3 is the engine reaching out to dumb files rather than
ingesting them. So the table world and the array world are independently
rediscovering the same shape: **metadata-as-dumb-data, engine-as-optional
accelerator.**

**Arrow is the substrate where the two worlds may actually merge — or collide.**
Arrow is the columnar in-memory format and IPC layer beneath parquet and DuckDB.
As Arrow grows real array support (tensor / fixed-shape-tensor canonical
extension types, nanoarrow, zero-copy device buffers), the tabular substrate
starts spanning into array territory. That is the convergence point itself: a
shared dumb substrate that both the lakehouse (parquet/Iceberg) and array stores
could sit on. When Arrow stores arrays well, the "is this a table or an array"
distinction — which has organized the whole field — gets much blurrier, and the
tension between the two camps lands squarely on Arrow.

## The through-line

Over each cycle the pattern repeats: the sufficient/dumb profile wins the
*reach* battle; the general/engine format retreats to the cases that truly need
it; and a new connective layer appears to index across the dumb things without
forcing an engine. **COG → STAC → reference stores → lakehouse tables / Arrow.**
Every few years someone re-litigates "but a real engine would be so much more
capable," and they are right, and it loses to universality for *publishing* while
winning for the *mutable working set*. The line settles slightly further toward
"dumb + indexed" than before.

## The practical upshot

The dumb sibling is not the *compromise* sibling — it is the *legacy* sibling.
Ten years on, an engine-backed store's internals may have churned (a new
serialization, a different transaction model, a raised ceiling), but a
`path/offset/size` table pointing at public URLs is readable by anything that can
issue a ranged GET, the same way a COG from 2016 still opens today. The dumb
format is the one that survives the engine churn — which is exactly why "GeoTIFF
if sufficient" aged into wisdom while maximalist "netCDF for everything" did not.

So when publishing an archive — making it addressable by anyone, anywhere, with
no permission and no shared code — the durable instinct is: emit the dumb,
universal, indexed thing as the primary artifact, and treat the engine-backed
store as the capability layer for those who need versioning and transactions.
The reference store, pointing at public URLs, is the part that becomes the
legacy.
