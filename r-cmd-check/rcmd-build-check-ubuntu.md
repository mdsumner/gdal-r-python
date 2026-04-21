# R CMD build / check on Ubuntu: LaTeX, vignettes, and Docker sizing

Notes for `gdal-r-ci`-style images where two GDAL variants (release + HEAD) are
already the dominant cost and TeX/pandoc are the discretionary layers.

## `R CMD build` — what it does

`R CMD build pkg/` produces `pkg_x.y.z.tar.gz`. Flags that matter:

- `--no-build-vignettes` — skips running the vignette engine. Vignette *sources*
  (`vignettes/*.Rmd`) are still included in the tarball; no `.html`/`.pdf` is
  produced and `inst/doc/` is not populated.
- `--no-manual` — skips the PDF reference manual (`pkg.pdf`). This is the
  LaTeX-dependent step.
- `--compact-vignettes="gs+qpdf"` — only relevant if vignettes produce PDFs.
- `--resave-data` — repacks `data/` with better compression.

Key point: vignette sources always ship in the tarball. The flag only controls
whether they get rendered during build.

## `R CMD check` — what it does with those artifacts

Parallel set of flags:

- `--no-vignettes` — skips re-running vignette code during check. Metadata
  (`\VignetteIndexEntry`, engine declarations) is still checked.
- `--no-build-vignettes` — check won't try to build them either.
- `--ignore-vignettes` — stronger than `--no-vignettes`; skips vignette-related
  checks entirely. Pairs cleanly with a tarball built using
  `--no-build-vignettes`, which otherwise triggers complaints about missing
  `inst/doc/` products.
- `--no-manual` — skips LaTeX build of the PDF manual during check. This is the
  flag that matters when LaTeX is absent.
- `--as-cran` — turns on the full CRAN check set. Will try to build the PDF
  manual unless `--no-manual` is also passed.

When check "builds the manual" it runs `R CMD Rd2pdf`, which needs `pdflatex`
plus `inconsolata`, `upquote`, `hyperref`, `titling`, etc. Minimal TeX Live
produces cryptic "LaTeX Error: File `inconsolata.sty' not found" failures.

## When LaTeX matters vs. doesn't

**Doesn't matter:**
- Routine CI running `R CMD check --no-manual --ignore-vignettes`. Catches
  almost all real bugs — code, examples, tests, NAMESPACE, Rd syntax. Rd is
  parsed and syntax-checked without rendering to PDF.
- Reverse-dep checking (`gdalcheck`-style).
- Interactive dev with `devtools::check(manual = FALSE, vignettes = FALSE)`.

**Does matter:**
- CRAN submission — CRAN builds the PDF manual server-side. Rd must be
  LaTeX-valid even though you never render it locally. Worth building the
  manual locally at least once before submission.
- Vignettes that need rendering to catch broken examples, missing figures, or
  knitr engine issues.
- Packages where the PDF manual is a real deliverable (rare for
  hypertidy-style packages).

## HTML vignettes via `\VignetteEngine{knitr::rmarkdown}`

The standard escape hatch from LaTeX for vignettes, and has been for well over
a decade. The `knitr::rmarkdown` engine produces HTML via pandoc, not PDF via
LaTeX. Toolchain requirement drops from `pdflatex + .sty pile` to
`pandoc + rmarkdown + knitr`.

Precise statement: the engine choice decouples **vignettes** from LaTeX but not
the **reference manual**. Two separate artifacts:

- **Vignettes** (`vignettes/*.Rmd` → `inst/doc/*.html`): engine-dependent. With
  `knitr::rmarkdown`, no LaTeX.
- **Reference manual** (`man/*.Rd` → `pkg.pdf`): always LaTeX via
  `R CMD Rd2pdf`. No non-LaTeX path exists for the PDF manual.

So `--no-manual` is still the flag that saves you from LaTeX in routine CI,
even when every vignette is HTML. Rd-to-HTML rendering (`?function`, pkgdown)
is a completely separate path and doesn't touch LaTeX.

**Gotcha:** a vignette with `\VignetteEngine{knitr::rmarkdown}` but
`output: pdf_document` in the YAML *will* pull in LaTeX — the engine is
rmarkdown, but pandoc is asked to produce a PDF, which routes through LaTeX.
The convention is `output: html_document` or, more idiomatically,
`output: html_vignette` (lighter CSS, smaller tarball).

## The build/check matrix

Routine CI (every commit, no LaTeX needed):

```
R CMD build pkg --no-build-vignettes --no-manual
R CMD check pkg_*.tar.gz --no-manual --ignore-vignettes --as-cran
```

Pre-release / tagged-release check (needs LaTeX + pandoc):

```
R CMD build pkg
R CMD check pkg_*.tar.gz --as-cran
```

## `rcmdcheck` translation

Fast CI form:

```r
rcmdcheck::rcmdcheck(
  path       = "pkg",
  build_args = c("--no-build-vignettes", "--no-manual"),
  args       = c("--no-manual", "--ignore-vignettes", "--as-cran"),
  error_on   = "warning"
)
```

Notes:

- `--ignore-vignettes` pairs more cleanly with `--no-build-vignettes` than
  `--no-vignettes` does, because `--no-vignettes` still runs some vignette
  metadata checks and can complain about missing `inst/doc/` products.
- `--as-cran` just goes in `args`, no separate slot.
- `error_on` default is `"never"`, which surprises people. Set to `"warning"`
  or `"error"` in CI to actually fail the job.
- Returns a structured `rcmdcheck` S3 object with `$errors`, `$warnings`,
  `$notes`, `$status`. Makes cross-package aggregation (`gdalcheck`-style)
  straightforward — no text parsing.

## Docker sizing for `gdal-r-ci`

Image size drivers, roughly in order:

1. **GDAL + deps built from source with full driver set** — 500MB–1GB depending
   on what's linked (PROJ, GEOS, SQLite, HDF5, NetCDF, Parquet via Arrow,
   libkea, etc.). Dominant cost, and you pay it twice (release + HEAD).
2. **TeX Live full** — ~4GB. `texlive-latex-recommended` +
   `texlive-fonts-recommended` + `texlive-latex-extra` is ~800MB–1GB. tinytex
   starts tiny and grows with what you install.
3. **Pandoc** — ~150MB, needed for vignettes.
4. R itself + rocker/geospatial base.
5. Compiled R package deps in `/usr/local/lib/R/site-library`.

Clean split for the image chain:

- **`gdal-r`** (routine CI): no TeX, no pandoc. Just what `--no-manual
  --ignore-vignettes` needs. Runs on every commit. Also the right tier for
  `gdalcheck` reverse-dep work — you're testing that rdeps run against new
  GDAL, not that their docs render.
- **`gdal-r-full`**: adds pandoc + tinytex with a pre-installed set of the
  CRAN-required LaTeX packages. Release checks and vignette builds land here.

## tinytex for `gdal-r-full`

Two steps, not one:

```r
tinytex::install_tinytex()                                # TeX Live itself
tinytex::tlmgr_install(c("inconsolata", "upquote", ...))  # specific .sty files
```

`install_tinytex()` gives a minimal working TeX Live — `pdflatex`, kernel, core
packages. Bootable but insufficient for a typical CRAN package's PDF manual
because Rd files reach for `inconsolata`, `upquote`, etc. `tlmgr_install()`
adds those.

**On-demand behaviour:** once `install_tinytex()` is in place, running
`tinytex::latexmk()` or `R CMD Rd2pdf` will intercept "missing .sty" errors
and install what's needed automatically. Fine for an interactive workstation.
**Not what you want in Docker** — CI would do network-dependent TeX installs
on every run. Pre-provision at build time.

Dockerfile shape:

```dockerfile
RUN R -e "tinytex::install_tinytex(bundle = 'TinyTeX-1')" \
 && R -e "tinytex::tlmgr_install(c( \
      'inconsolata', 'upquote', 'courier', 'helvetic', \
      'hyperref', 'titling', 'framed', 'grfext', \
      'metafont', 'mfware', 'tex', 'kpathsea', \
      'pdftex', 'xetex', 'makeindex' \
    ))"
```

Bundle choice: `install_tinytex(bundle = "TinyTeX")` is the default, `"TinyTeX-1"`
and `"TinyTeX-2"` are progressively larger preset bundles Yihui maintains.
`TinyTeX-1` often covers R package documentation without further
`tlmgr_install`. Worth trying as the base.

The exact missing-package list drifts as CRAN's Rd templates evolve. Pragmatic
approach: `install_tinytex()`, then `R CMD check --as-cran` a representative
package (vapour is a good stress test for `\eqn{}` density), `tlmgr_install`
whatever shows up missing, lock the list in the Dockerfile, revisit yearly.

**Size comparison:**

- tinytex with targeted `tlmgr_install`: ~150–300MB for the coverage R packages
  actually need.
- apt `texlive-latex-recommended + texlive-fonts-recommended +
  texlive-latex-extra`: ~800MB–1GB, pulls in a lot of unused material.

tinytex wins on size for equivalent R-package-documentation coverage. That's
the argument for it over apt in a size-sensitive image.

## Hypertidy ecosystem practice

`vapour`, `gdalraster`, `wk`, `vaster`, etc. lean on CI that uses `--no-manual`
for per-commit checks and only does full manual builds on release. Matches the
`r-lib/actions` `check-r-package` defaults, which set `build_args =
'c("--no-manual", "--no-build-vignettes")'` for the fast path.

For `gdalcheck` reverse-dep checks against GDAL HEAD: skip manual and vignettes
always. `gdal-r` (no TeX, no pandoc) does all reverse-dep work.
