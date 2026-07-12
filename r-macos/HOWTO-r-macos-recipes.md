# HOWTO: Contribute changes/additions to R-macos/recipes

A concrete example: adding a `curl` recipe and updating `gdal` from 3.8.5 to
3.12.4, tested solo in a fork before proposing upstream.

## Background

- https://github.com/R-macos/recipes is the build system for the static
  libraries behind CRAN's macOS package binaries (published at
  https://mac.r-project.org/bin/). Recipes are one-paragraph DCF files in
  `recipes/`, optionally with a `<name>.patch` applied to upstream sources.
- Why GDAL was stuck: GDAL >= 3.9 requires curl >= 7.68 (it calls
  `curl_multi_poll()` unconditionally) and there was no curl recipe, so GDAL
  linked the SDK/system curl. On the Big Sur (and older) targets that curl is
  too old, so a 3.10.1 update was reverted with "3.9.0 broke CURL support"
  (commit e501d42). The fix: add a static curl recipe so the SDK curl version
  no longer matters on any target.

## The change set (4 files)

1. `recipes/curl` (new): curl 8.20.0, static, `--with-openssl` (curl 8.15+
   removed Secure Transport), CA bundle `/etc/ssl/cert.pem`,
   psl/brotli/zstd/nghttp2/idn2/ldap disabled.
   SHA256 fc5819cad3f9f5482669adcdc49a782c15f36d2a0715b395b06d9173593d2dc0
2. `recipes/gdal`: Version 3.12.4, `curl` added to Depends, `libcurl` added to
   the pkg-config flag workarounds, and
   `-DCURL_INCLUDE_DIR=/${prefix}/include -DCURL_LIBRARY=/${prefix}/lib/libcurl.a`
   so CMake can never silently fall back to the SDK curl.
   SHA256 68844ae29557b7efae4292c3b4cb3a3b8a79d14b765b89c5a7b17cbae7fa715a
3. `recipes/gdal.patch`: regenerated against 3.12.4. Three hunks: gdal-config
   becomes a pkg-config proxy; `Requires.private: libpq libtiff-4 netcdf libcurl`
   appended to gdal.pc.in; guard around the HDF5 link line.
4. `.github/workflows/cook.yml`: matrix `macos-13` -> `macos-15-intel`
   (macos-13 runners retired; see upstream PR #91), artifact
   `retention-days: 3` -> `14`.

DCF gotcha: every line in a recipe, including `#Note:` comments, must contain
a colon or `mkmk.pl` rejects the file. Sanity check locally with
`perl scripts/mkmk.pl` and inspect `build/Makefile` for your target.

## Steps

### 1. Investigate before changing

Read the recipe, its patch, and its git history
(`git log -- recipes/gdal`) - the revert commit message named the exact
blocker. Verify claims against upstream sources (here: GDAL's
`CheckDependentLibraries.cmake` version floor and `cpl_http.cpp`).

### 2. Fork and branch

Fork R-macos/recipes, create a topic branch (e.g. `gdal-curl`), apply the
change set, commit, push. Compute Source-SHA256 from the actual downloaded
tarball, and dry-run any recipe patch against the pristine source tree
(`patch -p1 --dry-run`).

Keep scaffolding files (like a saved branch diff) OUT of the repo tree, or
add them to `.git/info/exclude` - `git add -A` will happily commit them.
Before pushing: `git diff upstream/master --stat` should show only the
intended files.

### 3. Enable Actions in the fork

Fork workflows are disabled by default. Settings -> Actions -> General ->
"Allow all actions", or:

    gh api -X PUT repos/<you>/recipes/actions/permissions \
      -F enabled=true -f allowed_actions=all

The "Run workflow" button only appears for workflows on the default branch;
either select your branch in the dropdown or temporarily make the topic
branch the default (flip it back before the PR).

### 4. Run "Cook from Recipes"

Dispatch with `bootstrap=false` (skips freetype/harfbuzz/r-base-dev, saves
hours). Iterate cheaply: first `target=curl` (minutes), then `target=gdal`
(a couple of hours - it builds the whole dependency chain).

Leave `sdkurl` empty: the fork has no SDKURL secret, so each runner targets
its own OS. macos-14 (arm64) -> darwin23, which matches CRAN's R 4.6 arm64
chain. macos-15-intel -> darwin24, which proves x86_64 compile/link but is
NOT a Big Sur-deployable binary - frame it that way.

### 5. Verify the build honestly

In the gdal job log, the CMake summary must show CURL 8.20.0 found at the
prefix `libcurl.a`, not an SDK path. After download, the acceptance test is
`gdalinfo --version` and a `gdalinfo /vsicurl/...` read of a remote COG -
that answers the original revert commit directly.

### 6. Download artifacts

Artifacts hang off the RUN (bottom of the run summary page), not the branch,
and survive branch surgery. One artifact per matrix leg; the outer name may
read `dist-darwin-arm64` (OS_VER unset without sdkurl) but the tarballs
inside are canonically named `pkg-ver-darwin.NN-arch.tar.xz`.

    gh run download <run-id> -R <you>/recipes -D /tmp/cook

Only the gdal run's artifacts are needed: mkdist.pl sweeps every package
built in that run, so they contain curl and the whole dep chain. Tarballs
are per-package, not cumulative - consumers extract the whole set.

### 7. Promote to a release

Artifacts expire and need auth; releases give permanent flat URLs. Both
arches can share one release (arch is in the filename):

    gh release create darwin-gdal3124 -R <you>/recipes \
      --title "gdal 3.12.4 + static curl stack" \
      --notes "from cook run <run-id>" \
      /tmp/cook/dist-darwin-*/*.tar.xz

Optional later: add a `gh release upload` step to cook.yml gated on a
dispatch input, so good runs publish themselves.

### 8. Consume downstream

Installation is untarring at root. arm64 lands in `/opt/R/arm64` (CRAN's
layout, so package configure scripts just work); Intel in `/usr/local`.

    gh release download darwin-gdal3124 -R <you>/recipes -p '*arm64*' -D /tmp/libs
    for f in /tmp/libs/*.tar.xz; do sudo tar xJf "$f" -C /; done
    export PATH=/opt/R/arm64/bin:$PATH
    export PKG_CONFIG_PATH=/opt/R/arm64/lib/pkgconfig

PKG_CONFIG_PATH matters: the patched gdal-config is a pkg-config proxy.
Drop the same steps into a macos-14 CI job in downstream package repos
(vapour, gdalraster, sf, ...) to run R CMD check against the exact stack
CRAN would ship - that's also evidence for the upstream PR.

### 9. Iterate

The cycle for any further change: edit recipe on the branch -> dispatch cook
(bootstrap=false, targeted) -> check logs -> download run artifacts ->
release -> consume. Only re-cook what changed; build.sh rebuilds dependents
as needed.

### 10. Recovering from git mishaps

The expensive thing is the compute, and it lives in run artifacts - secure
those first, then branches are disposable. To rebase reality on upstream:

    git remote add upstream https://github.com/R-macos/recipes.git
    git fetch upstream
    git checkout master && git reset --hard upstream/master && git push -f
    git checkout -b gdal-curl && git apply gdal-curl-branch.diff
    git add -A && git commit -m "..." && git push -f origin gdal-curl

(Flip the fork's default branch back to master in Settings before deleting
a branch that is currently the default.)

### 11. Upstreaming

When satisfied: default branch back to master, topic branch rebased on
upstream/master, diff limited to the recipe files (drop the cook.yml
matrix/retention tweaks or split them out - PR #91 already covers the
runner change), and a PR framed against the revert commit: "adds a static
curl recipe so GDAL >= 3.9 keeps CURL support on all targets", pointing at
green cook runs and downstream R CMD check results in the fork.
R-SIG-Mac is the venue if it needs discussion.

## Two-layer patch mental model

- `recipes/gdal.patch` lives INSIDE the recipes repo and is applied by
  build.sh to the GDAL sources on every cook.
- A branch diff of the recipes repo itself is one-shot scaffolding for
  recreating your fork branch. Same mechanism, different altitude.
