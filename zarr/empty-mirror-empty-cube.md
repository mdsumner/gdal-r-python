# The empty mirror and the empty cube

Notes, 2026-11-11. Captures the "partnership" thread: two partner pieces
that are one idea along two axes, plus the shared core they both cite.
Companion to "How cube can Sentinel-2 go?" (draft v0.3, section 7b) and
the parked promisor README.

## 1. The pair

| | complete from day one | realised on demand | written back as |
| --- | --- | --- | --- |
| empty mirror (promisor) | the index: every chunk ref | the bytes | held chunks (ref -> local) |
| empty cube | the descriptor: instances, grids, time declared | geometry and time (renderings) | rendered tiles as rows |

- Mirror: lazy bytes. Cube: lazy geometry and time.
- Same substrate (byterefs), same ledger, same usage map.
- Prior-art analogues: git partial clone (`--filter=blob:none`, promisor
  remotes: full commit/tree graph, blobs fetched on demand); pull-through
  caches / CDN origin pull. Design rule they share: the index must be
  complete and authoritative while the data is still empty.

## 2. Shared core (cite from both pieces; candidate home: byterefs/spec "why" note)

1. **The table is the truth, not the store.** A store cannot say why a
   position is empty. Only the ledger can.
2. **Tiered resolution.** A request resolves through
   rendering -> held bytes -> remote refs. Each miss is served from the
   tier below and schedules a fill of the missing tier (Slurm byte fills,
   render jobs for tiles).
3. **Use shapes materialisation.** Promotion policy (refs -> held, held
   -> re-layout, re-layout -> rendering) is decided by demand. Per-chunk
   / per-tile hit counts are a usage map, a product in itself.
4. **Parallel fills, serialised commits.** Unless concurrent commits are
   proven safe on the target object store (conditional PUT).

## 3. States: what must stay distinguishable

Mirror (index complete, so Icechunk's existing payloads suffice):

| state | meaning | Icechunk payload |
| --- | --- | --- |
| remote | exists upstream, not fetched | Virtual |
| held | fetched / owned | Native (Ref) or Inline |
| empty | source never wrote this chunk | absent (reads as fill_value) |

Cube (does NOT map cleanly; three states collapse into one):

| state | meaning |
| --- | --- |
| no source | no instance covers this position |
| pending | expected, not yet rendered |
| rendered, all fill | rendered; result is entirely nodata |
| rendered | rendered with data |

The collapse: writers usually skip all-fill chunks
(`write_empty_chunks=False`), so "rendered, all fill" becomes absent,
indistinguishable from "no source" and "pending". Mirror side has the
twin: source-sparse chunk vs not-yet-fetched, which is safe only because
the index is complete.

## 4. Where mirror becomes renderer

- Stage 1, verbatim staging: byte-range copy, no decode. Relocatable,
  hash-checkable against the source range. Still a mirror.
- Stage 2, re-layout of hot patterns (e.g. point time series through
  daily files -> long-in-time chunks): decode and rewrite. Gives up
  verbatim relocatability for decoded-value hash + provenance. This is a
  rendering on the native grid. Derived rows should be a distinct row
  kind, not just a different slot key.

## 5. The question for the Icechunk maintainers

Layering:

- **Zarr** owns reader semantics: absent -> fill_value. No third answer
  in the store API, but a store may *raise* instead of reporting absent,
  so a pending state is implementable as store behaviour with no spec
  change. Agreeing what pending *means* to all readers is a Zarr spec
  conversation: slow, broad, not the place to start.
- **Icechunk** owns the manifest, where the state would live (a fourth
  payload kind, or per-chunk metadata), and its store decides read
  behaviour (raise, fall back to virtual ref, report pending).
- **VirtualiZarr** carries states through (already has virtual / missing
  / inlined); follows if Icechunk adds one.

Draft question:

> Is there, or would you consider, a way to mark a chunk position as
> "known pending" (a tombstone/placeholder distinct from absent), or to
> attach per-chunk metadata? Use case: demand-filled stores, where "not
> yet materialised", "materialised as all-fill" and "nothing here" must
> stay distinguishable without a side table. This needs no Zarr spec
> change; it can be store behaviour.

Either answer is useful: "no" confirms the ledger-plus-published-view
split; "yes" moves a slice of the ledger into the format.

## 6. Same idea at three scales (book thread)

| scale | storage form (a rendering) | fixed slots | where identity/state actually lives |
| --- | --- | --- | --- |
| vertex | simple features coordinate sequence | XYZM | vertex/segment/object tables (silicate) |
| chunk | Zarr / Icechunk store | native, virtual, inline, absent | byte-ref ledger |
| tile | cube / rendered grid | data or fill | instance relation + render ledger |

Tracks are the vertex-scale twilight zone: as points they keep per-fix
attributes but lose order and segments; as linestrings they keep order
but fixes become bare coordinates. Neither holds the segment (duration,
distance, speed, turn angle), which is what the analysis is about (the
trip package). Points and linestrings are two renderings of one relation
of fixes, as native / binned / snapshot are three renderings of one
relation of acquisitions.

Slogan (too strong, but): "Icechunk is the simple features of Zarr."

Fair version: **Icechunk risks being mistaken for a data model, the way
simple features was.**

- Holds: fixed slot set; built for handing off (bytes to a Zarr reader,
  geometry to a renderer) then treated as the model; success makes it
  rigid and pushes state into private side tables.
- Does not hold: Icechunk is young, transactional and versioned, closer
  to a storage engine. SF's limits are in its type system (no identity,
  topology, sharing); Icechunk's are in a state machine, which is far
  cheaper to extend. Hence: ask now, while the format can still say yes.

## 7. Publishing plan

1. **Empty mirror first.** Short, concrete, already measured (wxylib
   design m16: external ranges, merge_insert promotion, mixed read
   hash-matched against GDAL). "Here is the substrate, measured."
2. **Empty cube second.** The S2 argument; needs the section 9 fixture
   run. "Here is what the substrate lets you refuse to decide early."
3. **Shared core** (section 2 and the states tables above) as a short
   note both pieces link to; candidate home byterefs/spec.
4. Each piece carries the pair table (section 1) and points at the other.

Before anything goes public: point to repos, docs and issues rather than
naming people or companies; phrase the counterpoint as "the cube-first
position", not as one talk.

## 8. Open

- Conditional PUT on the target object store (Ceph RGW version) -
  decides single vs many committers.
- Promotion policy home: registry or reader? (Demand answers *when*, not
  *where it lives*.)
- Source drift: ref rows need a source version / etag; mismatch
  invalidates held bytes and downstream renderings.
- Does the render ledger and the byte ledger share one table, or two
  linked by provenance?
