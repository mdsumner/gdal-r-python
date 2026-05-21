# Submitting an R package to conda-forge: a maintainer's guide

A pragmatic walkthrough for R package authors who already publish to CRAN and now want their package on conda-forge as well. Assumes you maintain the package, have a working CRAN release, and are comfortable with git and `meta.yaml` at a "willing to read examples" level.

## Why conda-forge after CRAN?

CRAN handles the R-level packaging; conda-forge handles the system-library packaging. The failure modes are inverted:

- **CRAN** bounces on R-level issues (URLs, broken examples, vignette build failures) and lets you treat system dependencies as a black box that's someone else's problem.
- **conda-forge** accepts the R-level package wholesale (CRAN already vetted it) but is strict about system-library pinning, ABI compatibility, and cross-platform builds.

If your package wraps GDAL/PROJ/GEOS/sqlite/etc., conda-forge is where dual-ABI hazards surface. That's a feature, not a bug — most of your downstream conda users will thank you for finding these before they do.

## The mental model

Two distinct repos do two distinct jobs:

1. **`conda-forge/staged-recipes`** — the *entry point*. You PR your recipe directory here exactly once per package. Once merged, the package leaves this repo forever.
2. **`conda-forge/<package>-feedstock`** — auto-created on merge. This is where the package lives permanently. Future version bumps PR against the feedstock, mostly via the autotick-bot.

So the workflow is: generate recipe → PR to staged-recipes → it merges → feedstock appears with you as maintainer → done. Subsequent CRAN releases mostly handle themselves.

## Step 1: Generate the recipe

R recipe generation is less stable than other languages, so there's no one-liner from the staged-recipes root. The current recommended path uses `bgruening/conda_r_skeleton_helper` driven via pixi:

```bash
pixi init conda-forge-cran-generate && cd conda-forge-cran-generate
pixi add git conda 'conda-build 3.27.0' python
pixi run git clone https://github.com/bgruening/conda_r_skeleton_helper.git
cd conda_r_skeleton_helper

echo "r-yourpackage" > packages.txt
echo "    - your-github-handle" >> extra.yaml

pixi run python run.py
```

This produces `r-yourpackage/` containing `meta.yaml`, `bld.bat`, and `build.sh`. That directory is the *only* thing you'll move into staged-recipes.

The pixi workspace and the `conda_r_skeleton_helper` clone are throwaway scaffolding. Don't try to combine them with your fork of staged-recipes.

## Step 2: Spot-check the generated recipe

The skeleton produces ~90% of what you need; the remaining 10% is where first submissions usually fail. Check each of these before pushing.

### License declaration

CRAN's `License: GPL-3` (or similar) in DESCRIPTION is enough for the skeleton to read, but conda-forge wants:

- **SPDX-form expression** in `meta.yaml`. `GPL-3` will be linted; `GPL-3.0-or-later` won't.
- **`license_file:` pointing to a real file.** For GPL-licensed R packages, the convention is to point at the GPL text shipped with R itself:

  ```yaml
  about:
    license: GPL-3.0-or-later
    license_family: GPL3
    license_file:
      - '{{ environ["PREFIX"] }}/lib/R/share/licenses/GPL-3'
  ```

- **Bundled third-party code needs its own attribution.** If your package vendors anything (C/C++ in `src/`, datasets in `inst/`, examples copied from a textbook), grep for `copyright` and add license files for each. This matters for packages that wrap or extend other libraries.

### Tests

CRAN's testthat suite **does not run** on conda-forge. Your test files aren't installed by default and running them would balloon CI time.

What conda-forge means by "test" is a smoke test in `meta.yaml`:

```yaml
test:
  commands:
    - $R -e "library('yourpackage')"           # [not win]
    - "\"%R%\" -e \"library('yourpackage')\""  # [win]
```

For packages that link system libraries, add one line that exercises the linkage. A bare `library()` call can succeed even when GDAL/PROJ/etc. are mis-linked; the failure only shows up on actual use. For example:

```yaml
test:
  commands:
    - $R -e "library('yourpackage'); stopifnot(length(yourpackage::list_drivers()) > 0)"  # [not win]
```

Pick a function that's stable across your release history and that actually touches the system library. This single line catches "package loaded but underlying library didn't link properly" — the most common real-world breakage on a fresh build.

### Requirements

The generator may not pick up all your system dependencies. Cross-check against:

- Your `DESCRIPTION`'s `SystemRequirements:` field
- Your `configure` / `configure.win` scripts
- Existing feedstocks of similar packages (`r-sf-feedstock`, `r-terra-feedstock`, `r-rgdal-feedstock` if it still exists) for reference

Watch for:
- **Compiler stanza:** `{{ compiler('c') }}` and/or `{{ compiler('cxx') }}` in `requirements.build`.
- **System libraries** in `requirements.host` *and* `requirements.run`. Use conda-forge metapackage names (`gdal`, not `libgdal-dev`).
- **R version constraint** in both host and run, matching your DESCRIPTION's `Depends: R (>= x.y.z)`.

### Maintainers list

Verify that `extra.yaml`'s indentation survived into the final `meta.yaml`:

```yaml
extra:
  recipe-maintainers:
    - your-github-handle
```

The skeleton's combination step occasionally eats indentation. The recipe-maintainers list is what determines who gets added to the feedstock on merge, so don't skip this check.

## Step 3: PR to staged-recipes

1. **Fork** `conda-forge/staged-recipes` on GitHub.
2. **Clone, branch, copy, commit, push:**

   ```bash
   git clone git@github.com:your-handle/staged-recipes.git
   cd staged-recipes
   git checkout -b r-yourpackage
   cp -r /path/to/conda_r_skeleton_helper/r-yourpackage recipes/
   git add recipes/r-yourpackage
   git commit -m "Add r-yourpackage"
   git push -u origin r-yourpackage
   ```

3. **Open the PR** from `your-handle:r-yourpackage` → `conda-forge:main`.

4. **Fill the PR template.** Tick the checkboxes honestly — they're a checklist, not a formality.

5. **CI runs automatically.** Builds happen on linux-64, osx-64, osx-arm64, win-64. Expect at least one platform to fail on a first submission.

## Step 4: Iterate on CI failures

The reliable pattern of failure:

- **linux-64:** usually passes if your linux R package builds cleanly.
- **osx-64 / osx-arm64:** mostly works; occasional darwin-specific compiler flag issues.
- **win-64:** the high-stakes one for C/C++ packages. UCRT toolchain quirks, Windows-specific paths, `bld.bat` issues with system libs.

Read the logs carefully — conda-build output is verbose but the actual error is usually grep-able for `error:` or `ERROR`. Push fixes to the same branch; the PR re-runs CI.

If you're stuck after a couple of iterations, **ask on Zulip** (https://conda-forge.zulipchat.com/) in the R-language stream, or `@`-mention `conda-forge/help-r` on the PR. They're responsive and have seen every R-package failure mode. Don't ask before opening the PR — they'll just say "open one."

## Step 5: After merge

On merge, the autotick-bot creates `conda-forge/r-yourpackage-feedstock` and adds you as a maintainer. From this point:

- **Future CRAN releases trigger autotick-bot PRs** to the feedstock. You review, merge, and the new conda package appears.
- **Recipe changes** (e.g. new system dependency) PR against the feedstock, not staged-recipes.
- **Adding co-maintainers** is a PR to `recipe/meta.yaml`'s `extra.recipe-maintainers` list.

The feedstock has its own CI that re-builds on every PR, plus periodic migrator PRs as conda-forge updates pinnings (R version bumps, ABI migrations, etc.). Most of these you'll just merge as they come in.

## Common pitfalls

- **Mixing the skeleton workspace and staged-recipes.** Two different repos with different purposes. Keep them separate.
- **Forgetting the SPDX license form.** `GPL-3` fails the linter; `GPL-3.0-or-later` passes.
- **Relying on `library()` as the test.** Add at least one call that exercises the system library, especially for packages wrapping GDAL/PROJ/sqlite/etc.
- **Pinning system deps too loosely or too tightly.** Use conda-forge metapackages with version constraints matching upstream policy; check similar feedstocks for the current convention.
- **Treating CI failures as authoritative.** Sometimes CI fails for transient infrastructure reasons. Re-running can help; if a specific job keeps failing, it's real.
- **Ignoring license attribution for bundled code.** If you vendor any C/C++ or data, each source needs its own license file. CRAN doesn't enforce this; conda-forge does.

## A note on philosophy

Most CRAN package authors think of conda-forge submission as a one-off chore. It's better treated as a one-time setup followed by a permanent low-effort relationship: the autotick-bot does ~95% of release maintenance for you once the feedstock exists. The upfront cost is real (a half-day for a system-library-wrapping package) but it's amortised across every future release.

The other side benefit: conda-forge's strict ABI hygiene will surface real bugs in your build system that CRAN's looser checks let through. Treat the win-64 failures less as obstacles and more as a free audit.
