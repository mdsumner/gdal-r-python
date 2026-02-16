# GDAL Multidim Notes — 2026-02-16

## PR #13924: Multidim Read Enhancements (GDAL 3.13.0)

<https://github.com/OSGeo/gdal/pull/13924>

 PR has three commits that improve the bridge between the GDAL multidimensional API (`GDALMDArray`) and the classic 2D raster band interface (`GDALRasterBandFromArray`).

### What it does

1. **Python binding polish** — `Dataset.AdviseRead()` and `Band.AdviseRead()` now accept keyword arguments, and `Dataset.AdviseRead()` defaults to querying all bands. This is a SWIG ergonomic fix — the C++ methods already existed but the Python bindings didn't expose them idiomatically.

2. **AdviseRead() on the multidim→raster bridge** — `GDALRasterBandFromArray` now implements `AdviseRead()`. Previously, pre-fetch hints were silently dropped when reading multidim data through the raster band interface. This means the underlying multidim driver (Zarr, NetCDF, etc.) can now actually receive and act on read-ahead hints.

3. **Block cache for unaligned reads** — `GDALRasterBandFromArray::IRasterIO()` now uses the block cache for requests not aligned on chunk boundaries. Previously, each unaligned read went directly to the driver, causing redundant I/O for random-access patterns over chunked data.

### Why it matters 

Anything reading multidimensional sources (Zarr, NetCDF, HDF5) through the classic raster API — which is the path `gdalraster`, `vapour`, and `raadtools` take — benefits from these improvements. The block cache fix is especially relevant for arbitrary-extent reads against chunked Southern Ocean datasets where the requested window rarely aligns with the underlying chunk grid.

### VRT and the multidim question

Classic VRT could potentially be extended to compose 2D raster sources into a multidim view — e.g., stacking 100 single-band GeoTIFFs into a (T, Y, X) cube. The multidimensional VRT driver already does something like this, but a lighter path through classic VRT would lower the barrier. The fundamental issue is that GeoTIFF doesn't carry multidim metadata (named dimensions, coordinate arrays, CF attributes), so any multidim view of GeoTIFF data requires that metadata to be supplied externally (via VRT, or by a higher-level tool).

The more pragmatic angle: if the R-level abstraction (`gdalraster`, hypertidy stack) can read any source through whichever API is native and present a consistent array interface, the format distinction becomes less important. The improvements in PR #13924 make the multidim→raster bridge faster; `GDALMDArrayFromRasterBand` handles the raster→multidim direction. The missing piece is a unified R-level layer that picks the right path automatically.

---

## Missing SWIG Bindings: `GuessGeoTransform()` and `IsRegularlySpaced()`

### The problem

`GDALMDArray` has two C++ methods that are essential for efficiently working with multidimensional data as raster-like grids, but neither is exposed through the C API or SWIG bindings:

- **`GDALMDArray::GuessGeoTransform()`** — Given dimension indices for X and Y axes and a pixel-is-point flag, returns a 6-element affine geotransform (origin, resolution, skew). This lets you determine the spatial grid of a multidim array without reading the coordinate variables.

- **`GDALMDArray::IsRegularlySpaced()`** — Tests whether a 1D coordinate array has regular spacing and returns the start value and increment. This is the prerequisite for knowing whether a dimension can be described by an affine transform at all.

Without these in the C API, any code using the C or SWIG bindings (Python `osgeo.gdal`, R via `gdalraster`/`vapour`) must read the full coordinate arrays and compute regularity/geotransforms in user code. For large dimensions (e.g., a time axis with millions of steps, or high-resolution lat/lon), this is wasteful — the C++ implementation already has optimised logic that reads only what it needs.

### Why it blocks downstream work

In the hypertidy ecosystem, packages like `gdx` and `rasterix` need to detect whether a multidim source has a regular spatial grid so they can wire up affine indexing efficiently. Currently this requires materialising coordinate arrays in R and checking for regularity — slow, memory-hungry, and duplicating logic that GDAL already has internally.

With these methods exposed, the workflow becomes: open multidim source → call `IsRegularlySpaced()` on each spatial dimension → if regular, call `GuessGeoTransform()` → you have your affine grid without reading data.

### Implementation plan

The GDAL binding architecture requires: **C++ method → C API wrapper → SWIG interface → Python/other bindings**. There is no shortcut; SWIG works from the C API, not C++ directly.

#### Files to modify

1. **`gcore/gdal.h`** — Declare the C API functions:

```c
int CPL_DLL GDALMDArrayGuessGeoTransform(
    GDALMDArrayH hArray,
    size_t nDimX,
    size_t nDimY,
    int bPixelIsPoint,
    double *padfGeoTransform    /* out: array of 6 doubles */
);

int CPL_DLL GDALMDArrayIsRegularlySpaced(
    GDALMDArrayH hArray,
    double *pdfStart,           /* out */
    double *pdfIncrement        /* out */
);
```

2. **`gcore/gdalmultidim.cpp`** — Implement the C wrappers calling the C++ methods. Thin wrappers: cast the handle, call the method, write to output pointers.

3. **`swig/include/MDArray.i`** — Add `%extend GDALMDArrayHS` blocks with Python-friendly wrappers:

```python
# Target Python API:
gt = arr.GuessGeoTransform(nDimX=1, nDimY=0, bPixelIsPoint=False)
# → (x_origin, x_res, 0, y_origin, 0, -y_res) or None

is_regular, start, spacing = arr.IsRegularlySpaced()
# → (True, 0.0, 1.0) or (False, None, None)
```

#### C API design rationale

The signature for `GuessGeoTransform` is modelled on existing GDAL functions that return bool + fill a 6-double array: `GDALInvGeoTransform()` and `GDALGCPsToGeoTransform()`. Same pattern, easy to follow.

### Status

Not yet submitted as a PR. The C API signatures and SWIG approach are scoped out; implementation and tests remain. The `autotest/gcore/multidim.py` test suite is where tests should go.

### References

- Original scoping conversation (Oct 2025): <https://claude.ai/chat/b5673077-59ff-4f53-9534-7872cb3bff3f>
- Extended design with gdx/rasterix context (Dec 2025): <https://claude.ai/chat/60741652-a4dd-4eb4-ac7d-3f2a1d0c8b7c>
- GDAL PR #13924 (related, not the same): <https://github.com/OSGeo/gdal/pull/13924>
