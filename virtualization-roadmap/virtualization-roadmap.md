# Virtualisation Without the Python Stack — What We Have and Where We're Going

*Working roadmap, February 2026*

---

## What exists right now

### async-tiff: fast COG reference extraction

A Rust library with Python bindings (developmentseed) that reads TIFF IFDs asynchronously over HTTP. For virtualisation purposes, the key primitives are:

```python
tiff = await TIFF.open(path, store=store)
ifd = tiff.ifds[0]
offsets = ifd.tile_offsets       # list[int] — byte offset per tile
counts  = ifd.tile_byte_counts  # list[int] — byte length per tile
```

Also exposes: `image_width`, `image_height`, `tile_width`, `tile_height`, `bits_per_sample`, `sample_format`, `compression`, `predictor`, `model_pixel_scale`, `model_tiepoint`, `geo_key_directory`, `gdal_nodata`. Everything needed to both build a reference table and write array metadata. Native async I/O means thousands of COG headers can be read concurrently without threading workarounds.

**Status**: Installed, tested against GHRSST MUR COGs on Source Cooperative. Works.

### gdalraster GDALMultiDimRaster: R-native multidimensional array access

The `gdalmultidim-api` branch of gdalraster exposes GDAL's multidimensional API to R. This includes `getRawBlockInfo()` which returns the filename, byte offset, byte length, and codec info for any chunk in any GDAL-readable multidimensional dataset.

Already demonstrated working against:

- **Kerchunk Parquet** via the ZARR driver — a parquet file of virtualised references is opened as a multidimensional array and chunk references are resolved:

```r
dsn <- 'ZARR:"/vsicurl/https://.../ocean_temp_2023.parq"'
ds <- new(GDALMultiDimRaster, dsn, TRUE, character(), FALSE)
arr <- ds$openArray("temp", "character")
arr$getBlockSize()          # [1, 1, 300, 300]
arr$getDimensionCount()     # 4  (time, depth, y, x)
info <- arr$getRawBlockInfo(c(0L, 0L, 0L, 0L))
# $filename: /vsicurl/https://thredds.nci.org.au/.../ocean_temp_2010_01.nc
# $offset:   46353
# $size:     74186
# $info:     shuffle + zlib codec chain
```

- **BLUElink BRAN2023** — 4D ocean reanalysis (time × depth × y × x), 5,479 time steps, 51 depth levels, 1500×3600 grid, chunked at 1×1×300×300, NetCDF4 with shuffle+zlib.

**Status**: Working on development branch. GDAL 3.13.0dev. Reads kerchunk parquet natively.

### GDAL's ZARR driver with kerchunk support

GDAL (since ~3.11) can open kerchunk Parquet references via the `ZARR:` prefix. This is the engine underneath the gdalraster example above. It understands the kerchunk partitioned parquet schema, resolves chunk paths to byte ranges, and exposes `GetRawBlockInfo` for any chunk.

**Status**: In GDAL main. Works with kerchunk parquet produced by VirtualiZarr or any other tool.

### VirtualiZarr + virtual-tiff: COG → kerchunk parquet (with caveats)

We have a working (if fragile) pipeline that virtualises COGs to kerchunk parquet:

- VirtualiZarr v2 + virtual-tiff parse COG headers via obstore
- Monkeypatches fix codec conversion bugs (imagecodecs nesting, non-numcodecs codecs, dtype incompatibilities)
- ThreadPoolExecutor parallelises header reads
- Output: partitioned kerchunk parquet

For the full GHRSST catalogue (8,660 files, 22 million chunk references): ~7 minutes to read all headers, ~6 minutes to write kerchunk parquet. Total ~13 minutes.

**Status**: Works with monkeypatches. Fragile. The 6-minute parquet write is a structural bottleneck of the kerchunk format.

### The virtualised parquet files

We have actual kerchunk parquet files for:

- GHRSST MUR SST (produced via VirtualiZarr)
- BLUElink BRAN2023 ocean_temp (produced independently, hosted on GitHub)

These are the concrete test fixtures that GDAL and gdalraster already read.

---

## What this gives us

The complete working chain today is:

```
COG files on S3
    ↓  (async-tiff or VirtualiZarr+virtual-tiff)
kerchunk parquet
    ↓  (GDAL ZARR driver)
GDALMultiDimRaster in R
    ↓  (getRawBlockInfo)
filename + offset + length + codec info
    ↓  (range-GET + decompress)
pixel data
```

And for NetCDF4 sources, GDAL can both read the source directly *and* read virtualised kerchunk references pointing back into the source files. The BLUElink example proves this works for 4D data.

The critical insight is: **we already have a language-agnostic virtualisation pathway that doesn't depend on xarray, Zarr-python, or any Python-specific runtime.** The parquet file is the interchange format. GDAL is the reader. gdalraster is the R interface. The only Python dependency is in the *index-building* step, and even that can be replaced.

---

## Three axes of improvement

### Axis 1: Better index building

The index-building step currently has two options, both with friction:

**VirtualiZarr** works but requires monkeypatches, has the 6-minute parquet write bottleneck, and couples simple COG header reading to the entire xarray/Zarr codec stack.

**async-tiff** gives raw tile offsets/byte counts in seconds, but currently outputs plain Python lists — there's no tool that takes those and writes kerchunk-format parquet (or any other format GDAL can read).

The gap is a **lightweight index writer** that takes async-tiff output and produces GDAL-readable parquet. This could be:

- A small Python script using pyarrow that formats async-tiff output into kerchunk parquet schema (works today, GDAL reads it)
- The same script writing a simpler flat parquet with the array metadata in the schema metadata (requires GDAL to understand the simpler schema)
- A standalone tool (Rust or Python) that does the full pipeline: list COGs → read IFDs via async-tiff → write index

For NetCDF4/HDF5 sources, GDAL itself can extract chunk info via `GetRawBlockInfo` on the source files. So the index builder for non-COG formats is: open with GDAL multidim API, iterate chunks, record the info. This can be done from R or Python — it's just GDAL calls.

**Near-term action**: Write the async-tiff → kerchunk-parquet bridge script. This replaces the entire VirtualiZarr stack for COG sources and should produce the same output in under a minute instead of 13 minutes.

### Axis 2: Better index format

Kerchunk parquet works — GDAL reads it — but it's not ideal:

- String-keyed chunk paths (`"analysed_sst/0.1.2"`) are parsed on every lookup
- Partitioned parquet with one row-group per variable adds structural complexity
- The format was designed for Python/Zarr consumption, not for general-purpose querying
- Writing 22 million string-keyed rows takes 6 minutes; writing 22 million integer-indexed rows takes seconds

A simpler format — flat parquet with integer chunk indices, array metadata in schema metadata — would be faster to write, faster to query, and easier to understand. But it requires GDAL to understand it.

**Medium-term action**: Propose a simpler reference parquet schema to the GDAL community, either as an extension to the existing ZARR driver's kerchunk support or as a new lightweight driver. The BLUElink and GHRSST test cases provide concrete motivation. The key selling point: identical capability, dramatically simpler format, and the existing kerchunk support proves the read-path machinery already exists in GDAL.

### Axis 3: Spatial indexing and irregular grids

The chunk-level spatial footprint concept opens up swath data, curvilinear grids, and multi-CRS datasets. This is genuinely new capability — neither kerchunk nor VirtualiZarr handle it.

But this is a later step. It requires:
- A vector-capable index format (GeoPackage, GeoParquet, FlatGeoBuf)
- Footprint computation for irregular grids (from geolocation arrays or GCPs)
- A driver that does spatial queries on the index

This is the "CRI meta-driver" concept from the brainstorm document. It's the most ambitious piece and depends on Axes 1 and 2 being solid first.

**Longer-term action**: Prototype with GeoPackage. Build a GHRSST index with geometry column (trivial for a regular grid), test spatial queries, measure performance. Then try with a swath dataset to validate the irregular grid case.

---

## The R-native virtualisation workflow

Putting the pieces together, the near-term target workflow from R is:

### Building an index (one-time)

For COGs — using async-tiff from Python (or eventually from R via reticulate/mirai):
```r
# pseudocode — the bridge script runs async-tiff and writes parquet
system("python build_index.py --source s3://bucket/ghrsst/ --output ghrsst_refs.parquet")
```

For NetCDF4 — using gdalraster directly:
```r
# open source NetCDF4, iterate chunks, write reference table
ds <- new(GDALMultiDimRaster, "ocean_temp_2010_01.nc", TRUE, character(), FALSE)
arr <- ds$openArray("temp", "character")
# for each chunk index, getRawBlockInfo → collect into arrow table → write parquet
```

### Reading from the index

```r
library(gdalraster)

dsn <- 'ZARR:"ghrsst_refs.parquet"'
ds <- new(GDALMultiDimRaster, dsn, TRUE, character(), FALSE)
arr <- ds$openArray("analysed_sst", "character")

# Discover structure
arr$getDimensionCount()   # 3
arr$getBlockSize()        # [1, 512, 512]

# Get raw chunk reference
info <- arr$getRawBlockInfo(c(100L, 5L, 10L))
# → filename, offset, size, codec info

# Or read actual data (GDAL does the fetch + decompress)
data <- arr$read(...)
```

This is a complete virtualisation workflow with no Python runtime dependency for the read path. The index is a parquet file. The reader is GDAL. The interface is R.

### Building indexes from R without Python

For NetCDF4/HDF5 sources, the index can be built entirely from R using gdalraster's multidim API to extract `getRawBlockInfo` for every chunk, then writing to parquet via arrow. No Python, no reticulate.

For COGs, the missing piece is an R binding to a fast TIFF IFD reader. Options:
- `{vapour}` or `{gdalraster}` reading TIFF metadata via GDAL (works but opens each file synchronously)
- An R wrapper around async-tiff's Rust core via extendr (fast, async, but new dependency)
- Using GDAL's own `GetRawBlockInfo` on COG-backed Zarr references (chicken-and-egg — need the index to use GetRawBlockInfo, but need the IFD info to build the index)

The GDAL path for COGs would be: open each COG as a raster dataset, read its block structure via `GetBlockSize`, `GetGeoTransform`, and the internal tile offset table. This information is accessible through GDAL's C API but not all of it is exposed through gdalraster yet.

---

## Summary of the position

We're not starting from scratch. We have:

| Piece | Status | Role |
|---|---|---|
| async-tiff | Working | Fast COG IFD reading, reference extraction |
| VirtualiZarr | Working (fragile) | COG → kerchunk parquet (current pipeline) |
| GDAL ZARR driver | In GDAL main | Reads kerchunk parquet, exposes GetRawBlockInfo |
| gdalraster multidim | Dev branch | R interface to GDAL multidim API |
| BLUElink kerchunk parquet | Built, tested | 4D test fixture (time × depth × y × x) |
| GHRSST kerchunk parquet | Built, tested | 3D test fixture (time × y × x) |
| rustycogs | In development | Rust COG range-reader infrastructure |

The immediate gap is a lightweight bridge from async-tiff to GDAL-readable parquet — replacing VirtualiZarr for COG sources. This is a small script, not a large project.

The medium-term goal is a cleaner index format that GDAL understands natively — simpler than kerchunk parquet, faster to build, queryable, extensible to spatial footprints.

The longer-term vision is a GDAL meta-driver that treats chunk reference indexes as first-class spatial datasets, handling regular and irregular grids, multiple overview levels, and N-dimensional arrays through a single, query-driven interface. The design documents exist. The test data exists. The GDAL multidim machinery exists.

The distinguishing characteristic of this entire approach: **it's GDAL-native, language-agnostic, and doesn't require any Python-specific runtime for the read path.** The index is a file. The reader is GDAL. Any language that speaks GDAL — R, Python, Julia, C++, QGIS — can use it.
