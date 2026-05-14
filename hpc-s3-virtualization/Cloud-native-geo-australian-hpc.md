# Cloud-Native Geospatial on Australian HPC — Pain Points and Patterns

*A living document. Seeded from hands-on debugging of a VirtualiZarr → Zarr
conversion pipeline spanning Pawsey and NCI. Intended to grow as a shared record
of what actually breaks, why, and what to do about it, as Australian HPC data
infrastructure moves from file-server access toward cloud-native storage and
interoperability. Corrections and additions welcome — the value is in the
accumulated specifics, not in any one author being right.*

---

## Why this exists

The direction is not in question. Datasets like BRAN / Bluelink are already past
the size where casually downloading them is sensible — a single BRAN2023
variable-year is on the order of 3 TB across ~184 netCDF files, and that is the
*small* case. Serving array data through THREDDS (a Tomcat web application) was
never designed for the concurrent chunk-range access that analysis at scale
demands. The destination — array data in object storage, addressed as Zarr,
accessed cloud-natively — is correct, and people running these systems (e.g.
Bluelink upgrade work at CSIRO) are already moving that way.

What *is* worth documenting is the path. Specifically: which pains are
**incidental** friction that disappears on arrival, which are **intrinsic** costs
you are signing up to manage permanently, and what consistent tooling and
configuration practice stops you re-solving the same problem on every dataset.

A note on method. Most of what follows was found by walking the moving parts by
hand rather than trusting the abstraction. That is deliberate. The person who has
watched the flush lag and located the concurrency coupling is the person who can
debug it at 2 a.m. later. The abstraction is only safe to lean on once you have
earned the right to distrust it in specific, located ways.

## The stepping-stone: virtualization

The kerchunk / VirtualiZarr layer — a reference store (JSON, or better, Parquet)
recording `{filename, offset, size, codec chain}` per chunk — lets the *source*
stay as netCDF on THREDDS while the *access pattern* becomes Zarr-shaped today.
This is not a hack pending the "real" migration. It is the de-risking instrument
*for* the migration: it lets you prove the cloud-native ergonomics, and run
conformance checks against them, before anyone commits to relaying the bytes.

It also reframes a tempting wrong turn. "Just download all 184 files" is not a
rival architecture — it is one possible *sink* for the same reference recipe,
alongside a materialised Zarr store or a lazy reference store. Download-everything
is a cliff, not a slope: it works perfectly until the dataset is 30 TB instead of
3, or access is one-pass instead of repeated, or scratch purges faster than you
can stage — and then it does not degrade, it stops, and the architecture is rebuilt
from nothing. The reference approach is annoying at a *constant* rate instead:
same shape at 3 TB and 300 TB, only the row count of the recipe changes. The
failure mode to avoid is not *choosing* to download; it is letting "download" mean
hand-rolling a bespoke staging cache every time, so each dataset becomes a fresh
point solution with its own quota negotiation and its own babysitting.

---

## Pain points

### 1. "Done" does not mean "on disk"

**Symptom.** `gdal mdim convert` prints `... 100 - done in 00:00:06`, but `du` on
the output immediately afterward reports only the metadata size (tens of KB).
Repeated `du` calls eventually jump to the real size. `gdalinfo` run in the gap
lists only `.zarray` and reports zero chunk files.

**Cause.** The progress callback reports the *algorithm* finishing — the
chunk-iterating copy loop — not bytes hitting disk. For an array small enough to
fit GDAL's block cache, the chunk payload stays dirty in memory and is only
flushed when the output dataset is closed, which happens *after* the progress line
prints. What `du` caught was the close-time flush in progress.

**What to do.** Never trust the `done in` figure as wall time. For benchmarking,
time the whole process (`time gdal mdim convert ...`); the progress line
systematically undercounts because it stops before the close-flush, and on a
parallel filesystem that flush is a meaningful and variable fraction of true wall
time. Quoting the progress figure flatters the tool by exactly the cost you are
trying to measure.

**Incidental or intrinsic.** Intrinsic. The write/flush/sync lifecycle is a
property of buffered chunked I/O; it follows you onto object storage too.

### 2. Chunk-per-file meets the parallel filesystem

**Symptom.** The close-flush above is slow enough to span several seconds for an
output that is only single-digit MB — far more than throughput alone would
explain.

**Cause.** Zarr v2's chunk-per-file model. A modest array at a 256×256 block size
is ~90 separate files, each a create + write + close, and on Lustre every
create/close is a round-trip to the metadata server (MDS). Many-small-files is
precisely Lustre's weak spot — it is built for large striped sequential I/O, and a
burst of tiny-file metadata operations serialised through the MDS is close to the
pathological case.

**What to do.** Two mitigations. (a) **Sharding** — Zarr v3 sharding packs many
chunks into one file and collapses the metadata storm; supported on write in GDAL
3.13 via the `SHARD_CHUNK_SHAPE` array creation option. If you adopt it, confirm
your readers handle the sharded path — it is a different code path from flat v2
chunks. (b) **Concurrent creates** — a hand-rolled parallel copy that issues file
creates concurrently lets the MDS round-trips overlap instead of serialising; that
is latency *hiding*, distinct from the throughput question.

**Incidental or intrinsic.** Intrinsic, but mitigable. Chunked array storage on a
shared parallel filesystem will always have a metadata dimension; sharding makes it
manageable rather than making it disappear.

### 3. One knob, two pools — concurrency coupling

**Symptom.** `GDAL_NUM_THREADS=ALL_CPUS` against a remote store produces a wall of
`CURL error: Recv failure: Connection reset by peer`. Pinning
`GDAL_NUM_THREADS` to a smaller number (e.g. 8) makes the errors stop.

**Cause.** "Connection reset by peer" is the *server* closing connections —
load-shedding or a concurrency cap at the data endpoint, not the scheduler
constraining your job. The underlying problem is that `GDAL_NUM_THREADS` drives
*both* the compute pool (decode / filter / re-encode / write) *and* the fetch pool
(concurrent `/vsicurl/` range requests) with a single integer. The number that is
correct for local CPU-bound work is the wrong number for the remote endpoint, and
you are forced to pin to the *minimum* of the two — leaving compute cores idle for
the whole job.

Note the platform subtlety: `ALL_CPUS` detection behaves differently across
facilities. On a node where it resolves to the *node's* physical core count rather
than your allocation, you also get a misdetection problem on top. But even where
`ALL_CPUS` resolves to your allocation *correctly*, the coupling bug remains — the
correct local number is still wrong for the remote server.

**What to do.** In any scheduled job, pin `GDAL_NUM_THREADS` explicitly rather
than using `ALL_CPUS`. Set `GDAL_HTTP_MAX_RETRY` / `GDAL_HTTP_RETRY_DELAY` so a
transient reset retries rather than aborting a long job — but note that for
*benchmarking*, a silent retry inflates wall time and corrupts the measurement, so
pick a concurrency low enough that retries essentially never fire and quote that.
`GDAL_HTTP_MULTIPLEX` (HTTP/2) can let effective connection count sit below thread
count via connection reuse and range merging, which is why a slightly higher thread
count sometimes squeaks by — useful in production with retries as a safety net,
unreliable as a benchmark assumption.

**Incidental or intrinsic.** The *connection-reset storm against THREDDS* is
incidental — it is an artefact of serving array chunks through a web application
and largely disappears once the bytes are in object storage. The *coupling of
fetch and compute concurrency under one knob* is intrinsic to the current GDAL
mdim-convert path and is the deeper lesson (see pattern below).

### 4. Recipe and payload live apart

**Symptom.** A single logical operation — "convert this array" — is physically
distributed: compute on Pawsey, the kerchunk reference store on Pawsey object
storage, the referenced bytes on NCI THREDDS. The three have completely different
performance and reliability profiles, and a uniform parallel-for over the whole
operation hits the weakest one.

**Cause / pattern.** The reference ("recipe") and the payload are different things
with different access characteristics. Reading the recipe — offsets and codec
metadata — is cheap, local, and parallelises freely. Fetching the payload hits the
origin server and must be throttled to what that origin tolerates. Treating the
copy as one undifferentiated parallel job is what produces the connection-reset
storm.

**What to do.** Size the two pools independently. A bounded fetch pool matched to
what the origin tolerates, feeding a wider decode/encode/write pool matched to your
compute allocation. In R with `mirai` that is two daemon pools, or a fetch
semaphore throttling dispatch into a wider compute pool; in Rust, a bounded async
fetch stage handing off to a wider CPU pool. The principle: the bytes' origin sets
one limit, your compute allocation sets another, and nothing should couple them.

**Incidental or intrinsic.** Intrinsic — and arguably the central design pattern
in this whole document. The recipe/payload split is not just conceptually tidy; it
is operationally necessary, because the two halves genuinely want different
concurrency. This is a general truth about virtualized cloud-native access, not a
quirk of any one tool.

### 5. Terminology drift between formats

**Symptom.** Looking for a `PREDICTOR` creation option on the Zarr driver and not
finding one. (The horizontal-differencing predictor *is* available — it is spelled
`FILTER=DELTA`, and it pairs with `COMPRESS=GZIP`.)

**Cause.** `PREDICTOR` is TIFF vocabulary — literally a TIFF tag, a property the
TIFF model attaches to the codec, exposed by GDAL's GTiff driver under that name.
Zarr models the same idea as a *filter* in a composable codec chain (numcodecs
`delta`), so GDAL surfaces it as `FILTER` to match Zarr's own vocabulary. Same
operation, different container's terminology. GTiff's single `PREDICTOR` option
actually spans two distinct transforms that Zarr separates: horizontal differencing
(→ `delta`) and the floating-point predictor (→ byte `shuffle` / bitshuffle,
usually via Blosc). It is worth noting GDAL's Zarr driver ships only `delta` as a
builtin filter, with a C API to register others — so the constraint is "few builtin
filters", not the n-dimensionality of Zarr.

**What to do.** Maintain a small Rosetta table as part of this resource (started
below). The smash between legacy raster terminology and cloud-native vocabulary is
a recurring, avoidable source of confusion, and a shared glossary is cheap.

**Incidental or intrinsic.** Incidental — pure documentation friction — but it
recurs constantly and is worth defending against deliberately.

#### Terminology Rosetta (seed — extend this)

| Legacy / TIFF term | Cloud-native / Zarr equivalent | Notes |
|---|---|---|
| `PREDICTOR=2` (horizontal differencing) | `FILTER=DELTA` (numcodecs `delta`) | Pairs with `COMPRESS=GZIP`; ~10× over GZIP-alone on smooth Int16 fields |
| `PREDICTOR=3` (floating-point predictor) | byte `shuffle` / bitshuffle | Usually via Blosc; a different mechanism from `delta` |
| `COMPRESS=DEFLATE` | `COMPRESS=GZIP` / zlib | numcodecs naming differs from GTiff naming |
| TIFF tile / block | Zarr chunk | One-file-per-chunk in v2; sharding changes this in v3 |
| Overviews / pyramids | multiscales convention | GDAL 3.13 reads Zarr V3 multiscales as overviews |

---

## Incidental vs intrinsic — the summary distinction

This is the most useful single thing to hand to anyone planning a migration. A
pitch that lists only the wins gets ambushed later by the intrinsic costs nobody
flagged.

**Incidental — disappears (or largely so) on arrival at cloud-native storage:**

- Connection-reset storms from serving array chunks through a web application
  (THREDDS / Tomcat). Object storage does not reset you at ~8 concurrent
  connections; the whole connection-storm class of bug is an artefact of the
  current serving layer.
- `ALL_CPUS` misdetection interacting with a node-vs-allocation mismatch — a
  configuration hygiene problem, not a fundamental one.

**Intrinsic — you are signing up to manage these permanently:**

- The write / flush / sync lifecycle — "done" is not "on disk".
- The metadata dimension of chunked storage — mitigable with sharding, not
  eliminable.
- The fetch-vs-compute concurrency split — the recipe and the payload genuinely
  want different concurrency, wherever they live.
- Terminology drift across format ecosystems — perpetual, cheap to defend against.

The honest migration statement: *this* class of problem (incidental) is *why* we
are moving; *that* class (intrinsic) is *what we are signing up to manage*
afterward.

---

## Tooling and configuration notes

**The GDAL-backed R packages share GDAL's plumbing.** `vapour` and `gdalraster`
both sit on GDAL's `/vsicurl/` layer, so they read the same configuration —
`GDAL_NUM_THREADS`, the `GDAL_HTTP_*` family — and throttle identically. None of
them can separate fetch concurrency from compute concurrency, because GDAL itself
does not (pattern 3 above). For GDAL-backed work, configuration is environment
variables / GDAL config options, and it is uniform across the packages.

**The pure-R reader is a separate world — sometimes a limitation, sometimes an
advantage.** `zaro` does its own HTTP fetching in pure R, entirely outside GDAL's
connection layer, so none of the `GDAL_*` configuration touches it; it needs its
own concurrency controls (whatever is wired through `curl` / `httr2` / `mirai`).
The advantage: because it is not bound to GDAL's single-knob model, `zaro` *can*
in principle run a narrow fetch pool and a wide decode pool as separate things —
exactly what the cross-facility pattern needs. Whether it currently does, or
whether its parallel reads use one pool for both, is worth checking; if the latter,
splitting them is a real, well-motivated improvement. The caveat for
**benchmarking**: if a `zaro` fetch path and a `gdalraster` fetch path are both in
a comparison, they do not respond to the same controls — document the per-path
throttle settings explicitly or the comparison is not apples-to-apples.

**Codec coverage is the round-trip risk.** GDAL-backed packages get any
GDAL-supported codec for free. A pure-R reader reimplements codecs, so coverage is
finite — confirm the specific codec chain you write is one the reader implements
before relying on a write→read round-trip as a correctness check. (`delta` in
particular is worth verifying.)

### Config knobs worth knowing

- `GDAL_NUM_THREADS` — pin explicitly in scheduled jobs; do not use `ALL_CPUS`.
- `GDAL_HTTP_MAX_RETRY`, `GDAL_HTTP_RETRY_DELAY` — robustness for long jobs; but
  retries silently inflate benchmark wall time.
- `GDAL_HTTP_MULTIPLEX` — HTTP/2 connection reuse; can decouple connection count
  from thread count somewhat, but load-dependent.
- `SHARD_CHUNK_SHAPE` (Zarr V3, GDAL ≥ 3.13) — sharding to collapse the
  many-small-files metadata storm.
- `CACHE_KERCHUNK_JSON` open option / `CONVERT_TO_KERCHUNK_PARQUET_REFERENCE`
  creation option — JSON reference stores are slow to parse; prefer Parquet.

---

## Related: the chunk-reference primitive *(side note)*

A brief pointer, not the focus of this document. GDAL's classic API has a limited
`ReadCompressedData` (only some drivers; designed for self-describing bytestreams
like JPEG-in-TIFF). The multidimensional API exposes `GDALMDArray::GetRawBlockInfo`,
which returns exactly the per-chunk reference — filename, offset, size, inline data,
and the codec info (`COMPRESSION=DEFLATE`, `FILTER=SHUFFLE`, `ENDIANNESS=LITTLE`).
`GDALMDArray::CopyFrom` (which backs `gdal mdim convert`) is a *decoded-value* copy
loop — it reads through the typed view and re-encodes on write — so even a
nominally identical codec chain and chunk geometry currently go through a full
decode / re-encode round trip, and the output is not byte-identical (zlib level is
not pinned).

The relevance to this document: the per-chunk reference — `{filename, offset, size,
codec chain}` — is the **invariant** primitive. The same recipe can be written to a
Parquet reference store (lazy access), used to drive a byte-copy materialisation
(no codec round trip), or, if GDAL grew a passthrough mode, handled by the C++ side
directly. The recipe is what stays constant across problem *size* and problem
*shape*; the payload is the thing that has the size problem. That is the
architectural through-line behind every pain point above — the migration is, in
essence, about making the recipe a first-class artefact rather than something each
workflow reconstructs by hand.

---

## Open questions / to verify

- Does GDAL's `FILTER` creation option for Zarr accept a *list* (e.g. `delta` then
  `shuffle`), or only a single value?
- Does `zaro` implement the `delta` codec? Does its parallel-read path use one pool
  or separable fetch/decode pools?
- Confirmed `ALL_CPUS` resolution behaviour per facility (Pawsey vs NCI vs NeCTAR) —
  does it see the allocation or the node?
- Sharded Zarr v3 round-trip: does each reader in the stack (GDAL, `zaro`,
  zarr-python) handle the sharded path correctly?
- Is there any per-facility guidance on tolerated concurrent connection counts for
  THREDDS endpoints, or is it purely empirical?

---

*Contributions: add pain points in the same shape — symptom, cause, what to do,
incidental-or-intrinsic. The incidental/intrinsic call is the part that earns its
keep; resist the urge to skip it.*
