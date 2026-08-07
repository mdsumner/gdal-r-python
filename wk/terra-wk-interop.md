# Parked: terra <-> wk interop consolidation

Side detour from wkpool (2026-08-07). Two actions to take when picked up
again: an issue on paleolimbot/wk (draft text below), and a small issue
on paleolimbot/geos. Everything here was verified against source:
geos @ HEAD, wk 0.9.4.9000, terra (apt binary), during the wkpool
crs-round-trip / wk_handle session.

## The lay of the land today

terra knows nothing about wk or geos. All interop lives in geos,
contributed by a third party, in both directions:

- Input (terra -> geos): `as_geos_geometry.SpatVector` in
  R/geos-geometry.R (~line 59), delegating via
  `terra::geom(x, wkb = TRUE)` plus a `get_terra_crs()` helper
  (~line 254) that does the crs translation: empty-string crs -> NULL,
  otherwise a list of `terra::crs(x, describe = TRUE)$name` + wkt.
- Output (geos -> terra): `vect_geos_geometry` in R/compat-terra.R,
  registered dynamically because `vect()` is an S4 generic owned by
  terra. The machinery is in R/zzz.R `.onLoad`:
  `methods::setOldClass("geos_geometry")` so the S3 class can enter S4
  dispatch, then `register_terra_methods()` doing
  `methods::setMethod(terra::vect, signature(x = "geos_geometry"),
  vect_geos_geometry)`, guarded by requireNamespace, wrapped in silent
  tryCatch, AND re-fired via
  `setHook(packageEvent("terra", "onLoad"), ...)` so registration
  works in either load order. That hook is the fiddly part that makes
  cross-package S4 injection reliable; any relocation must replicate
  it.
- `vect_geos_geometry` converts via `geos_write_wkt(x)` - a text
  round trip (see issue 2 below).

Method-ownership rule that shapes where things can move: you register
a method if you own the generic or the class. terra owns `vect()`;
geos owns `geos_geometry`; wk owns `wk_handle()` and wk_wkb/wk_wkt/xy.

## Issue 1: wk gains SpatVector handleability (input direction)

Draft issue text for paleolimbot/wk, written to test Dewey's read:

---

Title: SpatVector as a handleable: hoist terra input support from
per-consumer methods into wk?

Proposal: a `pkg-terra.R` in wk, mirroring the existing sf support, so
that terra's SpatVector is wk-handleable:

    wk_handle.SpatVector <- function(handleable, handler, ...) {
      wk_handle(
        wkb(terra::geom(handleable, wkb = TRUE), crs = wk_crs(handleable)),
        handler, ...
      )
    }

    wk_crs.SpatVector <- function(x) { ... }        # terra::crs(), with the
                                                    # empty-string -> NULL dance
    wk_set_crs.SpatVector <- function(x, crs) { ... }
    wk_is_geodesic.SpatVector <- function(x) FALSE

terra in Suggests, tests skipped when absent - the same shape as
pkg-sf.R.

The motivation is that this interop already exists in the ecosystem,
but per-consumer: geos carries `as_geos_geometry.SpatVector` plus a
`get_terra_crs()` helper doing exactly the crs translation above. Any
other handler consumer (s2, geoarrow, my wkpool/bigcurve pipeline) has
to reimplement the same delegation and the same crs edge cases to
accept terra input. With SpatVector handleable in wk, every consumer's
`.default` method gets terra input for free through `wk_translate()`
-> `wk_handle()`, and the crs mapping lives once in the shared layer.

Dispatch-wise this is zero-coordination: geos's specific method keeps
winning `as_geos_geometry()` dispatch, so nothing changes there until
or unless geos chooses to drop its copy. No breakage window, no
ordering constraint between releases.

Non-goals: the reverse direction (handleable -> SpatVector) is out of
scope - `vect()` is S4 and belongs to terra's side of the fence (terra
already special-cases sf/sfc in `vect()`, so there is a natural home
for wk_wkb there someday, but that is a terra conversation). No hard
dependency in either direction; terra stays in Suggests.

Happy to PR this if the shape looks right - it is small and
self-contained, and I have a downstream pipeline (terra -> wkpool ->
bigcurve densify -> geos -> terra) that exercises it end to end.

---

Notes on the draft: it leads with "hoist what geos already proves"
(consolidation, not scope expansion - the argument most likely to land
given how deliberately small wk is). `wk_is_geodesic.SpatVector`
returning flat FALSE matches sf's treatment (planar edges regardless
of lonlat crs) but is the one line Dewey may push on - deliberate bait
to test his read. The vect/WKT observation is kept out to protect
scope.

## The output direction (the "oh wowsers" extension)

The vect handling could also move into wk, with one nuance. wk cannot
take over `vect(geos_geometry)` itself - geos owns that class, so that
method stays in geos (or dies there if superseded). What wk CAN do is
register `vect()` methods for its own classes (wk_wkb, wk_wkt, xy)
using exactly geos's zzz.R machinery (setOldClass + setMethod +
load-order hook, terra in Suggests). Then any handleable reaches terra
generically:

    terra::vect(wk::as_wkb(anything_handleable))

and geos's own path could become `vect(as_wkb(x))` internally, or
geos's method just delegates. Combined with issue 1, wk would then own
both directions of the terra bridge for the whole handler ecosystem,
and per-consumer terra code (geos's two files of it) becomes optional.

Ranking of homes for the output direction:

1. terra natively accepts wk_wkb in `vect()` (it already special-cases
   sf/sfc; cleanest, no dynamic registration, but needs Robert).
2. wk registers `vect()` methods for wk's own classes (self-contained,
   proven machinery, but wk takes on the S4 hook fiddliness Dewey may
   not want; propose only after/if issue 1 lands well).
3. Status quo: per-consumer registration a la geos.

Suggested play: park option 2 until issue 1 gets a response - the
response tells you whether Dewey wants wk touching terra at all.

## Issue 2: geos vect_geos_geometry should write WKB, not WKT

Small standalone issue for paleolimbot/geos, independent of everything
above:

`vect_geos_geometry` (R/compat-terra.R) converts via
`geos_write_wkt(x)`, so every geos -> terra trip through `vect()` is a
text round trip: coordinates are formatted to decimal digits and
reparsed, which perturbs doubles at the last-ulp level and costs time
on big vectors. `geos_write_wkb(x)` + terra's WKB ingestion would be
lossless and faster. Caveat to check before filing: which terra
versions accept WKB in `vect()` / `svc()` - older terra (e.g. the apt
binary current in Ubuntu noble) predates `geom(wkb = TRUE)` and may
predate WKB vect input too, so the fix may need a version gate with
WKT fallback.

(Same disease, same cure as wkpool's paste-WKT emitters, fixed in the
wkpool crs-round-trip/wk_handle work this session.)

## Context links

- geos sources inspected: R/geos-geometry.R (as_geos_geometry.SpatVector,
  get_terra_crs), R/compat-terra.R (vect_geos_geometry), R/zzz.R
  (.onLoad, s3_register, register_terra_methods).
- wk precedent files: R/pkg-sf.R, R/handle-sfc.R (sf in Suggests).
- Dispatch chain that makes issue 1 pay off:
  consumer `.default` -> wk_translate.default -> wk_handle(handleable)
  -> S3 dispatch on SpatVector.
- Related wkpool work this session: wkpool carries crs/geodesic and is
  itself handleable (wk_handle.wkpool), so with issue 1 the pipeline
  terra -> wkpool -> bigcurve -> geos -> terra needs no manual
  conversion steps at all.
