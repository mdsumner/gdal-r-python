# GDAL Python Binary Wheels: Current State, Technical Challenges, and Practical Solutions

## Overview

The [`GDAL` package on PyPI](#the-osgeo-namespace-and-what-it-contains) ships only source distributions. Installing it requires a pre-installed GDAL C library and development headers matching the exact version, plus a compiler toolchain. This makes `pip install GDAL` one of the most common pain points in the Python geospatial ecosystem.

By contrast, packages like rasterio, fiona, pyogrio, and pyproj ship binary wheels that vendor their own copy of the GDAL shared library and its dependencies. This document examines how they achieve this, why the official GDAL bindings have not followed suit, and what practical options exist today for installing `osgeo.gdal` from binary distributions.

## How rasterio solves binary distribution

The [rasterio-wheels](https://github.com/rasterio/rasterio-wheels) repository is a dedicated project that builds binary wheels uploaded to PyPI. These wheels include a GDAL shared library and supporting dependencies vendored directly into the wheel.

The build process:

1. Compiles GDAL and all C dependencies from source inside the wheel build, using [multibuild](https://github.com/multi-build/multibuild) for Linux/macOS and [vcpkg](https://vcpkg.io/) for Windows.
2. Vendors the shared libraries into the wheel (e.g. `rasterio.libs/` contains `libgdal.so`, `libproj.so`, `libgeos.so`, `libsqlite3.so`, `libcurl.so`, etc.).
3. Uses [auditwheel](https://github.com/pypa/auditwheel) (Linux) and [delocate](https://github.com/matthew-brett/delocate) (macOS) to rewrite RPATHs so vendored libraries are found at runtime.
4. Uses [delvewheel](https://github.com/adang1345/delvewheel) on Windows to bundle DLLs.
5. Ships wheels for manylinux2014_x86_64, manylinux2028_aarch64, macosx_x86_64, macosx_arm64, and win_amd64, covering Python 3.10–3.14 including free-threaded builds.

The result is approximately 25 MB wheels that include a subset of GDAL's format drivers (the commonly used ones, but not every available driver). Pyogrio follows a similar approach using vcpkg and cibuildwheel.

## Why the official GDAL bindings don't ship binary wheels

There are several interconnected technical reasons why the GDAL project has not adopted binary wheel distribution, despite community interest (see [OSGeo/gdal#3060](https://github.com/OSGeo/gdal/issues/3060), [OSGeo/gdal#4352](https://github.com/OSGeo/gdal/issues/4352), and two substantial gdal-dev mailing list threads: ["Python Wheels for gdal"](http://osgeo-org.1560.x6.nabble.com/gdal-dev-Python-Wheels-for-gdal-td5428713.html) (January 2020, 24 messages — Christoph Paulik, Even Rouault, Sean Gillies, Robert Coup, Mateusz Loskot, Christoph Gohlke, and others) and ["GDAL Windows Wheels"](https://www.mail-archive.com/gdal-dev@lists.osgeo.org/msg41562.html) (September–October 2024 — Petr Tsymbarovich, Even Rouault, Idan Miara, Robert Coup)).

### Symbol collision with other geospatial wheels

If `pip install GDAL` delivered a wheel containing its own `libgdal.so`, and the user also ran `pip install rasterio` (which ships its own `libgdal.so`), both copies would be loaded into the same Python process. This results in crashes or state corruption depending on import order. Rasterio can tolerate this architecture because it is a wrapper that only loads its own copy. The GDAL bindings expose the raw C API through multiple extension modules and need a single, shared `libgdal` instance.

Even Rouault addressed this directly in an [October 2024 gdal-dev post](https://www.mail-archive.com/gdal-dev@lists.osgeo.org/msg41674.html) in response to a community member's GDAL wheel-building effort:

> Did you try using both GDAL Python bindings and rasterio/fiona binary wheels within the same Python process on Linux? I would expect that to crash badly, possibly depending on the order you import them.

### Multiple extension modules sharing global state

The GDAL Python bindings consist of several separate extension modules (`osgeo.gdal`, `osgeo.ogr`, `osgeo.osr`, `osgeo.gdal_array`), each compiled as a separate `.so`/`.pyd` file. These all need to link against the same `libgdal` instance and share global state (driver registration, error handlers, configuration options).

If these modules were statically linked, each would get its own copy of GDAL's global state, which would be incorrect. Even Rouault outlined two possible architectural solutions, both requiring substantial effort:

- A single monolithic Python extension module (significant refactoring of the SWIG-generated bindings).
- A dynamically linked `libgdal.so` with all public symbols renamed to avoid collision with other copies — described as "quite an adventure."

### Dependency tree complexity

GDAL links against a large number of optional libraries: PROJ, GEOS, SQLite, curl, OpenSSL, HDF5, NetCDF, OpenJPEG, libpng, libtiff, libgeotiff, libwebp, zstd, lz4, Blosc, PCRE2, libxml2, libspatialite, Arrow/Parquet, and many more. Rasterio's wheels deliberately ship a subset of GDAL's drivers. If the official GDAL package shipped wheels, there would be pressure to include comprehensive driver support, increasing the build matrix complexity and wheel sizes substantially.

### Build time and CI resources

Compiling GDAL and all dependencies from source takes close to an hour per platform/Python combination. The matrix of platforms, Python versions, and architectures easily produces 20+ wheel builds per release.

### Maintenance burden

The GDAL project would need to maintain wheel-building infrastructure indefinitely, respond to platform-specific breakages, and keep up with GDAL's roughly quarterly release cadence. The project does not currently have the resources or personnel for this.

## The osgeo namespace and what it contains

The `GDAL` PyPI package installs the `osgeo` namespace, which contains several SWIG-generated C extension modules: `osgeo.gdal`, `osgeo.ogr`, `osgeo.osr`, `osgeo.gdalconst`, and optionally `osgeo.gdal_array` (when built with NumPy support). These are compiled `.so`/`.pyd` files that bind directly to the underlying C/C++ API.

The `osgeo` package behaves as a namespace package — its `__init__.py` does not eagerly import the submodules. This means that `import osgeo` followed by tab-completion (`osgeo.<tab>`) will not discover the available submodules, because Python's tab-completion relies on `dir()` or `__all__`, neither of which will find unimported C extension modules on disk. Users must import submodules explicitly (e.g. `from osgeo import gdal`), which triggers loading of the C extension and populates its namespace. This is a SWIG artefact rather than a deliberate design choice — hand-written bindings would typically wire up `__all__` or lazy imports so that interactive discovery works naturally.

To discover the available submodules without importing them, use `pkgutil`:

```python
import osgeo
import pkgutil

[name for importer, name, ispkg in pkgutil.iter_modules(osgeo.__path__)]
```

This scans `osgeo.__path__` for importable modules (both `.py` and `.so`/`.pyd` extensions) without triggering any C extension loading, and will return something like `['_gdal', '_gdal_array', '_gdalconst', '_gnm', '_ogr', '_osr', 'gdal', 'gdal_array', 'gdalconst', 'gnm', 'ogr', 'osr']`. The underscore-prefixed names are the raw SWIG C extensions; the others are the Python wrapper modules. Alternatively, `os.listdir(osgeo.__path__[0])` shows exactly what is on disk. The reason `dir(osgeo)` does not work is that it only shows attributes already bound on the module object, and `pkgutil` bypasses this by examining the filesystem directly.

The scope of what `osgeo.gdal` provides goes well beyond raster and vector I/O. It includes the full GDAL C API surface: VRT construction and manipulation, `gdal.Warp` and `gdal.Translate` with their complete option sets, coordinate transformations, raster algebra, the configuration system, and driver management. Notably, GDAL's multidimensional data API (for working with NetCDF, HDF5, Zarr, and other array-oriented formats via `gdal.MultiDimOpen`, `MDArray`, `Dimension`, and `Group` objects) is accessed entirely through `osgeo.gdal`. None of the wrapper packages that ship binary wheels on PyPI (rasterio, fiona, pyogrio) expose GDAL's multidimensional API — it is only available through the GDAL bindings themselves. This is a significant consideration for scientific workflows that depend on multidimensional data access.

## Why R (CRAN) doesn't have this problem

The contrast with R is instructive and highlights how packaging model design choices cascade into ecosystem-wide consequences.

**CRAN builds binaries centrally.** When a package like sf or terra is submitted, CRAN's build machines compile it against a known GDAL installation that CRAN maintains. On Windows this is Rtools/ucrt with bundled libraries; on macOS it is the CRAN recipes infrastructure (maintained by Simon Urbanek). Every R package that needs GDAL links against the same copy. There is no symbol collision because there is one `libgdal`, shared across sf, terra, vapour, gdalraster, and any other package.

In practice, the "same libgdal" guarantee has limits. CRAN binary packages are snapshots: a user may have a previously installed terra binary that was compiled against an older GDAL build than the latest sf binary from CRAN. On Linux, where CRAN does not provide binaries and users install from source, the system GDAL version depends on the distribution and may lag behind considerably. Users who install packages from different repositories, or who mix source-compiled and binary packages, can end up with R packages that were built against different GDAL versions or builds. At runtime they all link against whatever single `libgdal` is present on the system, which usually works due to ABI compatibility across minor GDAL versions, but is not formally guaranteed and can surface as subtle behavioural differences or, in rarer cases, crashes. The important distinction is that these are version-management challenges rather than architectural ones — the packages are not each shipping their own competing copy of the library.

**R's linking model supports shared native dependencies.** R packages use `LinkingTo` and system library discovery (`configure` scripts on Unix, `src/Makevars.win` pointing at Rtools on Windows). Multiple packages share a single dynamic `libgdal`. In Python, each wheel is a self-contained unit — pip has no concept of a shared native library dependency that multiple packages can link against at runtime.

**The Rtools toolchain bundles a curated C/C++ library stack.** Rtools includes pre-compiled versions of GDAL, PROJ, GEOS, HDF5, NetCDF, and dozens of other libraries that any R package can link against. This is functionally equivalent to what conda-forge provides for Python, but it is integrated into CRAN's official distribution workflow. There is no PyPI equivalent.

Python's packaging philosophy of "every wheel ships everything it needs" works well for simple packages but creates duplication and collision problems for the geospatial stack, where multiple packages all require the same complex C library. R's approach of providing a single system-level GDAL that all packages share avoids the problem entirely because CRAN centrally ensures the binary exists and is consistent.

**Social barriers vs technical barriers.** Both ecosystems have a dominant high-level package — sf (and increasingly terra) in R, rasterio in Python — and in both cases the popularity of that package shapes how users think about GDAL access. But the nature of the constraint is fundamentally different. In R, the barrier is social and psychological: when a new package like vapour or gdalraster appears and works directly with the GDAL C API, the immediate community response is "how does this relate to sf?" or "will this be integrated with sf?", as though sf were the gatekeeping layer through which all GDAL work must pass. But this is a perception, not a technical reality. Because every R package links against the same shared `libgdal`, a user can `library(sf)` and `library(gdalraster)` in the same session without conflict. New packages that expose different parts of GDAL (the multidimensional API, lower-level VRT manipulation, warping options sf doesn't surface) can coexist freely. The question "how does this relate to sf?" has a simple answer: it doesn't need to relate to sf at all, and it works alongside it regardless.

In Python, rasterio's dominance has created a *technical* barrier rather than merely a social one. If a user's environment is built around rasterio's PyPI wheels, adding `osgeo.gdal` risks crashes from duplicate `libgdal` symbols. The ecosystem has organised around rasterio's subset of GDAL not only by preference but because the wheel architecture forces a choice: use rasterio's vendored GDAL, or use the GDAL bindings, but combining both via pip requires either conda-forge or one of the co-built wheel sets described below. This means that parts of GDAL not exposed by rasterio (the multidimensional API, advanced VRT operations, the full `gdal.Warp` option space) are not merely overlooked — they are structurally harder to access for users whose environments are built on PyPI wheels. The packaging architecture has, as an emergent consequence, narrowed the effective surface area of GDAL available to the average Python user.

## Practical options for binary GDAL Python installation today

### Option 1: Christoph Gohlke's geospatial-wheels (Windows)

[geospatial-wheels](https://github.com/cgohlke/geospatial-wheels) provides unofficial binary wheels for the Python geospatial stack on Windows. Christoph Gohlke has maintained these builds (previously on his UCI website, now as GitHub releases) for over a decade.

**Coverage:** Python 3.11–3.14 including free-threaded builds, for win32, win-amd64, and win-arm64. The wheels include GDAL plus approximately 60 statically or dynamically linked libraries (PROJ, GEOS, HDF5, NetCDF, OpenSSL, curl, SpatiaLite, PostgreSQL, and many more). This is the most comprehensive GDAL build available as a Python wheel.

**Key advantage:** The release includes co-built wheels for GDAL, rasterio, fiona, pyogrio, pyproj, shapely, Cartopy, netCDF4, and others, all linked against the same library versions. This is the one pip-based environment where `import osgeo.gdal` and `import rasterio` can safely coexist in the same process, because they share the same underlying libraries.

**Installation — manual download:**

```bash
# Download the appropriate .whl from GitHub releases:
# https://github.com/cgohlke/geospatial-wheels/releases
pip install GDAL-3.11.4-cp313-cp313-win_amd64.whl
```

**Installation — via PEP 503 index:**

The [geospatial-wheels-index](https://github.com/nathanjmcdougall/geospatial-wheels-index) project wraps Gohlke's releases into a pip-compatible package index:

```bash
pip install gdal --index-url https://nathanjmcdougall.github.io/geospatial-wheels-index/
```

**Installation — with uv:**

```toml
# pyproject.toml
[tool.uv.sources]
gdal = [
  { index = "geospatial-wheels", marker = "sys_platform == 'win32'" },
]

[[tool.uv.index]]
name = "geospatial-wheels"
url = "https://nathanjmcdougall.github.io/geospatial-wheels-index/"
explicit = true
```

**Limitations:** Windows only. Unofficial — maintained by one individual with no formal SLA. Mixing these wheels with PyPI versions of the same packages (e.g. installing Gohlke's GDAL wheel alongside a PyPI rasterio wheel) risks version mismatches.

### Option 2: Petr Tsymbarovich's gdal-wheels (Linux + Windows)

[gdal-wheels](https://gitlab.com/mentaljam/gdal-wheels) is a newer effort (September 2024) that uses cibuildwheel and vcpkg to build binary wheels for manylinux, musllinux, and Windows. The wheels are published to GitLab's PyPI package registry.

**Installation — pip:**

```bash
pip install gdal --index-url https://gitlab.com/api/v4/projects/61637378/packages/pypi/simple
```

**Installation — cross-platform with uv (combining both wheel sources):**

```toml
# pyproject.toml
[tool.uv.sources]
gdal = [
  { index = "gdal-wheels", marker = "sys_platform == 'linux'" },
  { index = "geospatial-wheels", marker = "sys_platform == 'win32'" },
]

[[tool.uv.index]]
name = "geospatial-wheels"
url = "https://nathanjmcdougall.github.io/geospatial-wheels-index/"
explicit = true

[[tool.uv.index]]
name = "gdal-wheels"
url = "https://gitlab.com/api/v4/projects/61637378/packages/pypi/simple"
explicit = true
```

Then `uv add gdal` resolves per-platform.

**Limitations:** Installing these GDAL wheels alongside rasterio or fiona PyPI wheels in the same environment will result in two copies of `libgdal.so` being loaded, which is expected to cause crashes. Users must choose one or the other. No macOS wheels are currently available. Driver coverage depends on what vcpkg provides, which may be a narrower set than the Gohlke builds or conda-forge. The project is maintained by one individual on GitLab.

### Option 3: conda-forge (all platforms)

conda-forge packages `libgdal` as a shared library that all Python geospatial packages link against. This is the approach that most closely mirrors R/CRAN's model and is the only pip-alternative approach that fully solves the co-installability problem.

**Coverage:** Linux x86_64/aarch64, macOS x86_64/arm64, Windows. Full driver coverage including HDF5, NetCDF, Arrow/Parquet, JPEG2000, KML, SpatiaLite, PostgreSQL, and more. Additional driver packages (e.g. `libgdal-arrow-parquet`) can be added separately.

**Installation — conda/mamba:**

```bash
conda create -n geo python=3.12
conda activate geo
conda install -c conda-forge gdal python-gdal rasterio fiona pyogrio pyproj
```

Or with mamba for faster dependency resolution:

```bash
mamba create -n geo python=3.12
mamba activate geo
mamba install gdal python-gdal rasterio fiona pyogrio
```

**Installation — environment file for reproducibility:**

```yaml
# environment.yml
name: geo
channels:
  - conda-forge
  - nodefaults
dependencies:
  - python=3.12
  - gdal=3.11
  - rasterio
  - fiona
  - pyogrio
  - pyproj
  - numpy
  - xarray
  - netcdf4
```

**Installation — pixi (modern, with lockfile by default):**

```bash
pixi init myproject
pixi add gdal rasterio pyogrio
pixi run python -c "from osgeo import gdal; print(gdal.__version__)"
```

**Key advantage:** No symbol collision. There is one `libgdal` shared library, and rasterio, fiona, pyogrio, the GDAL bindings, and any other geospatial package all link against it. Version pinning is handled by the solver — when GDAL upgrades, all dependent packages are rebuilt against the new version.

**Limitations:** Users are operating in conda's packaging ecosystem rather than pip's. Mixing `pip install` into a conda environment can reintroduce the duplication problem (e.g. `pip install rasterio` in a conda env will bring in its own vendored `libgdal`, breaking the shared library arrangement). Docker and CI workflows require conda/mamba setup, which adds complexity compared to a pure pip workflow. Images like `condaforge/mambaforge` or `ghcr.io/prefix-dev/pixi` mitigate this.

## Comparison matrix

| | geospatial-wheels | gdal-wheels | conda-forge |
|---|---|---|---|
| **Platforms** | Windows | Linux + Windows | All |
| **macOS support** | No | No | Yes |
| **Driver coverage** | Comprehensive (~60 libs) | Moderate (vcpkg set) | Comprehensive |
| **Safe co-install with rasterio** | Yes (co-built) | No (symbol collision) | Yes (shared libgdal) |
| **pip / uv compatible** | Yes (custom index) | Yes (GitLab index) | No (conda/pixi) |
| **Governance** | Individual (Christoph Gohlke) | Individual (Petr Tsymbarovich) | conda-forge community |
| **Release cadence** | Tracks GDAL releases | Tracks GDAL releases | Tracks GDAL releases |
| **Best suited for** | Windows development | Linux CI/Docker | Cross-platform production |

## Related links

- [OSGeo/gdal#3060](https://github.com/OSGeo/gdal/issues/3060) — "Support pip binary wheel manylinux installations" (2020)
- [OSGeo/gdal#4352](https://github.com/OSGeo/gdal/issues/4352) — "Official Windows binary wheels?" (2021)
- gdal-dev thread: ["Python Wheels for gdal"](http://osgeo-org.1560.x6.nabble.com/gdal-dev-Python-Wheels-for-gdal-td5428713.html) — January 2020, 24 messages (threaded view). Participants include Christoph Paulik, Even Rouault, Sean Gillies, Robert Coup, Mateusz Loskot, Christoph Gohlke. Key topics: symbol versioning brainstorming, relationship to rasterio wheel approach, DLL Hell on Windows. Also available via [osgeo pipermail](https://lists.osgeo.org/pipermail/gdal-dev/2020-January/051594.html).
- gdal-dev thread: ["GDAL Windows Wheels"](https://www.mail-archive.com/gdal-dev@lists.osgeo.org/msg41562.html) — September–October 2024. Petr Tsymbarovich announces cibuildwheel/vcpkg-based wheel builds, later [expanded to manylinux/musllinux](https://www.mail-archive.com/gdal-dev@lists.osgeo.org/msg41669.html). [Even Rouault's reply](https://www.mail-archive.com/gdal-dev@lists.osgeo.org/msg41674.html) details the symbol collision problem with co-installing GDAL wheels and rasterio/fiona wheels, and the architectural constraints of multiple extension modules sharing global state.
- [rasterio-wheels](https://github.com/rasterio/rasterio-wheels) — Rasterio's wheel build infrastructure
- [geospatial-wheels](https://github.com/cgohlke/geospatial-wheels) — Christoph Gohlke's Windows wheels
- [geospatial-wheels-index](https://github.com/nathanjmcdougall/geospatial-wheels-index) — PEP 503 index wrapping Gohlke's wheels
- [gdal-wheels](https://gitlab.com/mentaljam/gdal-wheels) — Petr Tsymbarovich's Linux/Windows wheels
- [RFC 78: gdal-utils package](https://gdal.org/en/stable/development/rfc/rfc78_gdal_utils_package.html) — separation of GDAL Python utilities
- [cibuildwheel](https://github.com/pypa/cibuildwheel) — the build tool used by most modern wheel-building workflows
- [vcpkg](https://vcpkg.io/) — C/C++ package manager used for Windows (and increasingly cross-platform) dependency management in wheel builds

---

*Document created February 2026. The binary wheel landscape for GDAL is actively evolving; details may change as new infrastructure and packaging standards emerge.*
