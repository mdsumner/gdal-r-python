# R CMD check, Suggests, and the limits of ordering

## TL;DR

Whatever options you usually use, occasionally run these two checks
separately:

```sh
R CMD check --no-examples pkg.tar.gz   # tests run first, examples skipped
R CMD check --no-tests    pkg.tar.gz   # examples run, tests skipped
```

`R CMD check` runs examples before tests in its default sequence. When the
first phase fails, the second never runs — so any bug living in the second
phase stays hidden until the first is fixed. Splitting the check into two
runs forces both phases to be exercised independently. Pair this with
`_R_CHECK_FORCE_SUGGESTS_=false` to surface a related class of bugs hidden
by the assumption that all Suggests are installed.

This isn't a substitute for the full check; it's an occasional sweep that
finds bugs the default cadence misses.

## What we found that motivated this

A deliberately-Suggests-minimal CI image (`gdal-r-full` on
[gdal-r-ci](https://github.com/hypertidy/gdal-r-ci)) checking sf surfaced
two real bugs in fast succession:

1. `tests/dplyr.R` calls `library(tidyr)` unconditionally inside an
   `if (require(dplyr, quietly = TRUE))` block. tidyr is in `Suggests:`,
   so the test fails when tidyr isn't installed even though dplyr is.

2. The `\examples{}` block in `man/st_perimeter.Rd` runs code that
   internally requires lwgeom (also `Suggests:`). The example errors
   when lwgeom isn't installed, breaking `R CMD check` examples.

The second bug was masked by the first on the canary's daily run because
`R CMD check` errored out at examples (which run first) before reaching
tests. Splitting into `--no-examples` and `--no-tests` runs would have
surfaced both at once.

These aren't sf-specific. The pattern — Suggests packages loaded
unconditionally in tests, examples, or vignettes — is widespread in the
spatial R ecosystem and probably in R packages generally. The bugs are
invisible in standard development environments because almost everyone
has the relevant Suggests installed.

## Why CRAN doesn't catch this

The intuitive assumption is that CRAN does some kind of leave-one-out
testing across Suggests. It doesn't. CRAN's incoming-check farm runs with
the full Suggests universe available — every package's Suggests are
installed before its check runs. Two reasons this won't change:

**Cost.** Running each package's check twice (once with Suggests, once
without) doubles the farm's compute. Across ~20,000 packages on regular
re-checks, that's prohibitive on volunteer infrastructure.

**Definitional ambiguity.** R's Suggests field conflates several things:
"used in one rare codepath," "needed for a major feature but optional,"
"linked-to in docs only." Each has different testing implications and
none can be detected automatically from `DESCRIPTION` alone. A package
listing lwgeom in Suggests might be using it for one example or for
half its functionality; CRAN can't tell.

The result: CRAN's contract is approximately "package builds, examples
run, tests pass *in an environment where everything it might need is
available*." That's most of what users want from a quality gate, but
it doesn't catch the unguarded-`library()` class of bug.

## Two distinct testing operations

It's worth being precise about a distinction that's easy to elide:

**Testing with Suggests forced absent (`_R_CHECK_FORCE_SUGGESTS_=false`)**
asks: "does the package still work when Suggests are absent en masse?"
Cheap. Catches the gross failures — unguarded `library(foo)` calls,
`@importFrom` of packages not in Imports, examples that depend on
optional packages without `\dontrun{}`. This is what the canary above
exercises.

**Leave-one-out testing** asks: "for each Suggests S, does the package
still work without S specifically?" Costs N check runs for N Suggests.
Catches a different class — code paths that work when *some* Suggests
are absent but fail when a *specific* one is missing. r-hub has a
`nosuggests` platform that's the closest thing to this in the
ecosystem, but it's opt-in and most maintainers never run it.

**Truly thorough leave-N-out testing** asks: "for every subset of
Suggests being present or absent, does the package work?" For a package
with N Suggests, that's 2^N environments. For sf with ~30 Suggests,
combinatorially impossible. Nobody does this.

The thoroughness/cost curve goes vertical fast:

| Test                   | Cost   | Catches                          | Run by                    |
|------------------------|--------|----------------------------------|---------------------------|
| Force-Suggests-present | O(1)   | Install failures                 | CRAN routinely            |
| Force-all-absent       | O(1)   | Mass-absence breaks              | r-hub nosuggests, canaries|
| Leave-one-out          | O(N)   | Single-package breaks            | Almost nobody             |
| Leave-N-out (subsets)  | O(2^N) | Truly thorough                   | Nobody, ever              |

## The deeper pattern: ordering as a sampling strategy

The check-phase ordering issue (`--no-examples` advisory) and the
Suggests issue look unrelated at first but share a deeper structure.

`R CMD check` runs phases in a fixed order:

1. Build, install, package metadata
2. Documentation checks (Rd parsing, xrefs)
3. **Examples** (`runExamples`)
4. **Tests** (`runTests`)
5. Vignettes

Any single ordering hides bugs in phases that come after a failure.
Examples-before-tests is a deliberate prioritisation of "don't ship
broken docs" — not arbitrary, but it does mean test-only bugs only
surface once the example-failure history is cleared.

This is a special case of a general principle: **deterministic ordering
of independent checks is a sampling strategy.** Each ordering samples one
path through "things that might fail." Different orderings sample
different paths. Running only one ordering systematically misses what
the others would have found.

The same framing applies to Suggests-handling. Default check assumes all
Suggests are present, which samples one configuration of the Suggests
hypercube. Forcing all Suggests absent samples the opposite extreme.
Leave-one-out samples each axis. None of these covers the whole space,
but mixing them at least sees more.

## Practical advisory

For most maintainers, most of the time, the default `R CMD check` is
fine. CRAN's gate is good enough for shipping. The bugs we're discussing
are genuinely minor — they don't crash users, they don't corrupt data,
they only annoy people in unusual environments.

But occasionally — once a quarter, once a release, when you have CI time
to spare — run a more thorough sweep:

```sh
# Surfaces bugs in test phase (which examples mask)
R CMD check --no-examples pkg.tar.gz

# Surfaces bugs in example phase (which test failures could mask in
# the other direction, less commonly)
R CMD check --no-tests pkg.tar.gz

# Combine with Suggests-absent to find unguarded library() calls
_R_CHECK_FORCE_SUGGESTS_=false R CMD check --no-examples pkg.tar.gz
_R_CHECK_FORCE_SUGGESTS_=false R CMD check --no-tests    pkg.tar.gz

# Or run inside a deliberately-minimal image like:
docker run --rm -ti ghcr.io/hypertidy/gdal-r-full:latest bash
```

The advisory in one line:

> **Default check ordering plus default Suggests-handling masks bugs
> that surface only in second-phase code, or only when Suggests are
> absent. Both maskings are cheap to break out of; neither is what
> happens by default.**

## What this taught us about the canary

The canary built into `gdal-r-ci` was designed to detect dual-PROJ /
dual-GDAL problems and to track GDAL HEAD against bleeding-edge
reverse-deps (sf, terra, gdalraster, vapour, gdalcubes). It happens
to *also* be a Suggests-minimal environment, because we made deliberate
decisions about what to install — only what's needed to compile and
load the GDAL-linking packages, not their full Suggests trees.

That secondary property turned out to find bugs that the primary
property never would have. The full Suggests tree of sf would pull
Rust toolchains (av, gifski), heavy plotting (rasterVis, mapview),
and a lot of unrelated machinery. We chose not to bake those into the
canary because they had nothing to do with GDAL's behaviour. That
choice incidentally created a Suggests-minimal environment that
exposed bugs nobody else's environment would have seen.

This is a recurring pattern: infrastructure designed for one purpose
producing value via the constraints it imposes for unrelated reasons.
The lesson generalises beyond CI design: when building a constrained
environment, the constraints themselves become a probe of code
written under different assumptions. Sometimes the constraint is the
contribution.

## Open questions

- **Is there a tool for cheap leave-one-out testing?** Not as far as we
  know. r-hub's nosuggests is the all-absent flavour. A `revdepcheck`-style
  tool that runs N checks per package, each missing one Suggests, would
  catch a real class of bugs. Compute cost is feasible at the per-package
  level (~30 runs for sf). Nobody's built it.

- **Should `R CMD check` randomise phase order?** Probably not — log
  reproducibility matters and the cost outweighs the benefit at CRAN
  scale. But targeted infrastructure (canaries, pre-submission tools)
  could expose both orderings with little extra effort.

- **Should unguarded `library(suggests_pkg)` be a check warning?** R-core
  has discussed variants of this and not landed on action. Doing so
  would surface ~thousands of latent bugs at once and break the current
  ecosystem equilibrium. The cost-of-disruption probably exceeds the
  value-of-correctness for now.

## References

- [r-package-devel](https://stat.ethz.ch/mailman/listinfo/r-package-devel)
  archives — recurring discussion of Suggests semantics
- [r-hub builders](https://builder.r-hub.io/) — `nosuggests` platform
- [sf](https://r-spatial.github.io/sf/) — examples used to develop this
  write-up
- [gdal-r-ci](https://github.com/hypertidy/gdal-r-ci) — the canary that
  surfaced the bugs
