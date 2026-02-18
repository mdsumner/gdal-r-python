# GDAL Chunk Index Driver — Design Brainstorm

*Working document for early-stage design of a GDAL meta-driver that reads and writes chunk-level byte-range reference indexes.*

*Michael Sumner, February 2026*

---

## Motivation

GDAL already has several meta-drivers that composite multiple data sources into a single virtual dataset: VRT (XML-based virtual raster), GTI (tile index from a vector layer of file references), and the ZARR driver's ability to read kerchunk Parquet references via `GetRawBlockInfo`. Each handles a piece of the problem but none offers a general, chunk-level, spatially-indexed, multi-dimensional reference store.

The proposed driver — working name **CRI** (Chunk Reference Index) — would:

- Present an N-dimensional array backed by byte-range references into existing files
- Support spatial indexing of chunk footprints for efficient subsetting
- Work with any GDAL-readable source format (COG, NetCDF4, GRIB, Zarr, HDF5)
- Be buildable and testable incrementally against real datasets

## Target datasets for initial development

**GHRSST MUR SST** — the simplest case. Single variable, 3D (time × y × x), uniform grid, Cloud-Optimised GeoTIFF, ZSTD compression. ~8,660 files, each 17999×36000 at 512×512 tiles. Regular affine geotransform, EPSG:4326.

**BLUElink (BRAN2020)** — 4D (time × depth × y × x). Multiple variables (temperature, salinity, velocity components). NetCDF4 with internal chunking. Curvilinear-ish grid (regular in lon/lat but with depth as a non-uniform coordinate). Forces the design to handle a fourth dimension and multiple variables from the start.

**Sentinel-2 L2A COGs** — multi-variable (one COG per band), tiled, with overview levels. Tests multi-scale indexing and the case where variables come from different files at the same time step.

## What the index looks like on disk

### Core schema

The index is a table. Each row is one chunk. The minimal columns are:

| Column | Type | Description |
|---|---|---|
| `variable` | string | Variable/array name (e.g. "analysed_sst", "temperature") |
| `level` | uint8 | Overview level (0 = full resolution) |
| `d0` | uint32 | Chunk index along dimension 0 (outermost, typically time) |
| `d1` | uint32 | Chunk index along dimension 1 |
| `d2` | uint32 | Chunk index along dimension 2 |
| `d3` | uint32 | Chunk index along dimension 3 (if 4D+) |
| `path` | string | URI or /vsicurl/ path to source file |
| `offset` | uint64 | Byte offset within source file |
| `length` | uint32 | Byte length of compressed chunk |
| `geometry` | geometry | Spatial footprint of chunk (optional) |

The `d0`–`d3` naming is deliberately generic. Which dimension is time, depth, y, x is declared in the metadata, not encoded in the column names. This avoids a hard limit on dimensionality — a 5D dataset just adds `d4`.

**Open question: generic d0/d1/d2/d3 vs named dim columns?**

Generic is more flexible and avoids schema changes per dataset. But named columns (`time_idx`, `depth_idx`, `y_chunk`, `x_chunk`) are more readable and self-documenting. A compromise: the driver accepts either convention and maps between them via metadata. Or: use named columns matching the dimension names declared in the metadata.

### Path normalisation

File paths are stored once per unique source file. Two options:

**Option A: Inline paths.** The `path` column contains the full URI on every row. Simple, self-contained, but repeats long strings. For GHRSST: 2,556 rows per file × 8,660 files = 22 million rows, each carrying a ~150-char path. Dictionary encoding in Parquet handles this efficiently (the column compresses to a few MB) but in GeoPackage or DuckDB this wastes space.

**Option B: Separate file table.** A companion `files` table with one row per source file:

```
files:
  file_id  (uint16 PK)
  path     (string)
  datetime (timestamp, optional)
  ...per-file attributes...
```

The chunks table carries `file_id` as a foreign key instead of `path`. For GHRSST this replaces 22 million 150-char strings with 22 million uint16 values plus 8,660 strings. Much more compact, supports per-file attributes (timestamp, processing version, quality flags), and enables joins.

**Recommendation**: Option B for production use, Option A acceptable for simple cases. The driver should handle both — if it sees a `path` column, use it directly; if it sees a `file_id` column and a `files` table, join them.

### Array metadata

Stored as table-level or layer-level metadata (Parquet schema metadata, GeoPackage `gpkg_metadata`, DuckDB table comments, or a dedicated `arrays` table):

```json
{
  "analysed_sst": {
    "dims": ["time", "y", "x"],
    "shape": [8660, 17999, 36000],
    "chunks": [1, 512, 512],
    "dtype": "int16",
    "fill_value": -32768,
    "compression": {"id": "zstd", "level": 9},
    "predictor": "horizontal_differencing",
    "endianness": "little",
    "scale_factor": 0.001,
    "add_offset": 298.15,
    "units": "kelvin",
    "long_name": "analysed sea surface temperature"
  }
}
```

For multi-variable datasets (BLUElink), each variable has its own entry. Variables can have different shapes, chunk sizes, and dtypes.

### Grid / CRS metadata

```json
{
  "crs": "EPSG:4326",
  "transform": [0.01, 0.0, -179.995, 0.0, -0.01, 89.995],
  "dimensions": {
    "time": {
      "values": ["2002-06-01T09:00:00Z", "2002-06-02T09:00:00Z", "..."],
      "units": "days since 1981-01-01",
      "calendar": "standard"
    },
    "depth": {
      "values": [2.5, 10.0, 25.0, 50.0, "..."],
      "units": "metres",
      "positive": "down"
    }
  }
}
```

For the dimension coordinate arrays (time values, depth levels), these could alternatively live in companion 1D arrays in the same index — which is what GDAL's multidim Zarr driver already does (note `GetRawBlockInfo` example showed `/time` alongside `/analysed_sst`).

**Open question: embed coordinate arrays in metadata JSON, or store as additional 1D chunk references in the same table?**

Storing them as additional rows in the chunks table is more self-consistent (everything is a chunk reference) but makes the table heterogeneous — 1D time chunks alongside 3D data chunks. A separate `coordinates` table or embedded JSON array is simpler.

### Spatial footprints

For regular affine grids, footprint geometry is computable on-the-fly from the geotransform and chunk indices — the driver doesn't strictly need the geometry column stored. But storing it enables:

- Spatial indexing for non-affine grids (swath, curvilinear, GCP-based)
- Fast spatial queries without recomputing footprints
- Mixed grids in a single index (e.g. different Sentinel-2 UTM zones)

**Recommendation**: The geometry column is optional. For regular grids, the driver can compute footprints from metadata at query time. For irregular grids, the geometry column is required and spatially indexed.

## Storage backends

The driver should be agnostic to the specific vector/table format. Candidates:

### Parquet (+ optional GeoParquet)

Pros: Columnar, compressed, fast analytical queries via DuckDB, universal language support, Parquet schema metadata for array metadata, GeoParquet spec for geometry. Efficient dictionary encoding handles path repetition.

Cons: No built-in spatial index (requires external or DuckDB). Append is not native (must rewrite). Not a database.

**Good for**: Distribution, archival, interchange. Write once, read many.

### GeoPackage

Pros: SQLite-based, R-tree spatial index built in, widely supported in GDAL/OGR, single file, read-write. Familiar to the GIS community.

Cons: Row-oriented (slower for columnar scans over millions of chunks), write contention, SQLite overhead for very large tables. Not as natural for non-spatial analytical queries.

**Good for**: Desktop use, moderate-scale datasets, integration with QGIS.

### DuckDB (+ spatial extension)

Pros: Columnar analytical engine, handles millions of rows trivially, spatial extension for geometry queries, reads/writes Parquet natively, in-process (no server), single file. SQL interface.

Cons: Newer, smaller ecosystem. Spatial extension still maturing. File format is not a long-term archival standard (Parquet is).

**Good for**: Interactive exploration, large-scale datasets, complex queries combining spatial and attribute filters. Could be the canonical working format with Parquet as interchange.

### FlatGeoBuf

Pros: Spatial index at front of file, supports HTTP range-read spatial queries (perfect for cloud-hosted index), compact binary format, simple specification.

Cons: No columnar layout, limited metadata capabilities, less widely supported for analytical queries.

**Good for**: Cloud-hosted index files where remote spatial query is the primary access pattern.

### Recommendation

Support multiple backends via OGR. The CRI driver reads the index through OGR's vector API, which already handles GeoPackage, Parquet/GeoParquet, FlatGeoBuf, and (via extension) DuckDB. The driver doesn't need to know which backend is in use — it issues spatial and attribute queries through the OGR SQL or filter interface and gets rows back.

This means: **the index is any OGR-readable vector layer that conforms to the CRI schema.** The driver validates the schema on open and reads through OGR. Backend choice is the user's decision based on their use case.

## Connection string / open syntax

Following GDAL conventions for multi-driver open strings:

```
CRI:path/to/index.gpkg
CRI:path/to/index.parquet
CRI:/vsicurl/https://bucket.s3.amazonaws.com/index.fgb
CRI:path/to/index.duckdb:chunks_table
```

Or with open options:

```python
ds = gdal.OpenEx("CRI:index.gpkg", open_options=[
    "VARIABLE=analysed_sst",
    "TIME=2021-01-15",
    "LEVEL=0"
])
```

For the multidimensional API:

```python
ds = gdal.OpenEx("CRI:index.gpkg", gdal.OF_MULTIDIM_RASTER)
rg = ds.GetRootGroup()
rg.GetMDArrayFullNamesRecursive()
# ['/analysed_sst', '/time']
md = rg.OpenMDArray("analysed_sst")
info = md.GetRawBlockInfo([0, 0, 0])
# → filename, offset, size, codec info
```

This mirrors exactly how the ZARR driver works with kerchunk Parquet today, but with a cleaner index schema.

## Read path

### Classic raster API (2D/3D slicing)

1. User opens `CRI:index.gpkg` with a variable, time selection, and bounding box
2. Driver reads array metadata → determines dtype, chunk layout, CRS
3. Driver selects overview level based on requested resolution vs available levels
4. Driver constructs a spatial filter (from requested bbox) and attribute filter (from time/level selection)
5. OGR query returns matching chunk rows → list of (path, offset, length) tuples
6. Driver groups requests by source file for fetch batching
7. Fetch compressed bytes via `/vsicurl/` range reads (or local file seeks)
8. Decompress using codec specified in array metadata
9. Assemble tiles into output buffer at correct spatial positions
10. Return raster band(s)

### Multidimensional API

1. User opens via `OF_MULTIDIM_RASTER`
2. Driver enumerates variables from array metadata → root group with one MDArray per variable
3. Dimension objects constructed from coordinate metadata
4. `Read()` or `GetRawBlockInfo()` calls translate to index queries as above

### Key optimisation: request merging

When multiple adjacent chunks map to the same source file at nearby byte offsets, the driver should merge them into a single range request. This is what GDAL's `/vsicurl/` already does with `CPL_VSIL_CURL_MERGE_CONSECUTIVE_RANGES`, but the driver can be smarter — it knows the full set of needed chunks upfront and can plan an optimal fetch strategy.

## Write path

### Index creation from existing files

```
gdal_cri_create --input-list files.txt \
                --output index.gpkg \
                --variable analysed_sst \
                --time-from-filename '%Y%m%d' \
                --geometry   # compute and store footprints
```

Or programmatically:

```python
from osgeo import gdal

# open source, extract block info, write to index
src = gdal.OpenEx("input.tif", gdal.OF_MULTIDIM_RASTER)
# ... iterate blocks, write to OGR layer
```

The write path needs to handle:

1. **Format detection**: Determine source block structure (TIFF tiles, NetCDF4 chunks, GRIB messages)
2. **Metadata extraction**: dtype, compression, nodata, CRS, geotransform from the source
3. **Block enumeration**: For each block/tile/chunk, record offset and length
4. **Footprint computation**: From geotransform (regular) or geolocation arrays (irregular)
5. **Coordinate extraction**: Time values from filenames, CF attributes, or explicit input; depth/level values from source metadata
6. **Index writing**: Append rows to the OGR layer

For the initial implementation targeting COGs, steps 1–3 could use GDAL's internal block information or `GetRawBlockInfo` on an existing Zarr/kerchunk reference. For NetCDF4, the chunk index is accessible via the HDF5 layer.

**Open question: should the write path use GDAL's own dataset reading, or allow pluggable extractors (e.g. async-tiff for high-throughput COG header reading)?**

For a GDAL driver, using GDAL's own APIs is cleaner and more maintainable. For a standalone index-building tool, async-tiff or tifffile would be faster for bulk COG processing. Both could produce the same index format.

## Handling specific dataset patterns

### GHRSST MUR (3D, regular, one variable per file)

```
dimensions: time(8660) × y(17999) × x(36000)
chunks:     1 × 512 × 512
files:      one COG per time step
```

Index building: iterate files, extract tile offsets from each IFD, assign time_idx from filename. No geometry column needed (regular EPSG:4326 grid). Single variable. Simplest possible case.

Chunk count: ~22 million rows.

### BLUElink BRAN2020 (4D, regular, multi-variable)

```
dimensions: time(~14000) × depth(51) × y(1500) × x(3600)
chunks:     1 × 1 × ~300 × ~300   (varies by variable)
variables:  temp, salt, u, v, eta
files:      daily NetCDF4, one file per variable per day (or combined)
```

Index building: iterate NetCDF4 files, read HDF5 chunk index (B-tree lookup for each chunk's offset/length within the file), assign time_idx and depth_idx from coordinate variables.

This tests:
- 4D chunk indices (d0=time, d1=depth, d2=y, d3=x)
- Multiple variables with potentially different chunk shapes
- NetCDF4/HDF5 as source format (not just COGs)
- Non-uniform depth coordinate

Chunk count per variable: ~14000 × 51 × 5 × 12 ≈ 43 million (rough estimate). Total across variables: ~200 million. Still very manageable in Parquet/DuckDB.

### Sentinel-2 L2A (3D, multi-file per timestep, with overviews)

```
dimensions: time × y(10980) × x(10980)  [per UTM zone tile]
chunks:     1 × 512 × 512
files:      one COG per band per time step
overviews:  3–4 levels per COG
```

Index building: each band is a separate variable; overview levels indexed with `level` column. Different bands have different native resolutions (10m, 20m, 60m) so chunk shapes differ by variable.

This tests:
- Multi-variable from separate files at same time step
- Overview levels
- Mixed native resolutions
- Mixed UTM zones (if building a multi-zone index, geometries are essential)

## Edge cases and open questions

### Partial chunks at array edges

When the array dimensions are not exact multiples of the chunk size (e.g. 17999 pixels / 512 = 35.15 → the last row of tiles is partial), the tile in the file still occupies a full 512×512 block, padded with nodata. The reference table records the full compressed tile as-is. The driver handles the edge trimming when assembling the output buffer. This is exactly how GDAL handles partial tiles in COGs today — no change needed.

### Sparse arrays / missing chunks

If a chunk doesn't exist (sparse array), there is simply no row for that chunk index in the table. The driver fills the output buffer with the fill_value for missing chunks. This is cleaner than Zarr's approach of checking for the existence of an object/file — absence of a row is an efficient negative signal.

### Multiple CRS / mixed grids

A single index could contain chunks from different CRS zones (e.g. Sentinel-2 tiles across UTM zones). The geometry column (in a common CRS like EPSG:4326) enables spatial queries that span zones. The array metadata would need per-variable or per-file CRS information. This is an advanced use case — initial implementation can require a uniform CRS.

### Compression heterogeneity

Different source files might use different compression (e.g. a collection that migrated from deflate to ZSTD partway through). The array metadata assumes uniform compression. If heterogeneity is needed, a `compressor` column could be added per-chunk or per-file, but this significantly complicates the read path. Initial implementation should require uniform compression within a variable.

### Update and append

Adding new time steps to the index should be an append operation, not a full rewrite. GeoPackage and DuckDB support this natively. Parquet does not (must rewrite or use a partitioned dataset with new partition files). This favours GeoPackage or DuckDB as the working format, with Parquet as an export/interchange format.

### Relationship to STAC

A STAC catalogue describes items (granules/scenes) with spatial footprints, temporal extents, and asset links. A CRI index describes chunks within those items. They're complementary: STAC tells you which files exist; CRI tells you which bytes within those files you need. A CRI index could be generated from a STAC catalogue by opening each asset and extracting its block structure.

A STAC extension could link to a CRI index file, enabling a workflow where STAC discovery leads directly to chunk-level access without opening any source files.

## Minimal viable scope

For an initial implementation / proof of concept:

1. **Read-only driver** consuming a GeoPackage (or Parquet) index
2. **Multidimensional API** only (not classic raster API initially)
3. **COG sources only** (TIFF tile offsets/byte counts)
4. **Uniform grid** (affine geotransform, single CRS)
5. **Single compression** per variable
6. **No geometry column** (compute footprints from metadata at query time)
7. **`GetRawBlockInfo`** support (the primitive that makes this interoperable)
8. **Standalone index builder** as a Python/CLI tool using async-tiff or GDAL

This is enough to demonstrate the concept with GHRSST data and to get the read path working end-to-end. The BLUElink 4D case can follow immediately as it only adds a dimension — no architectural change.

The geometry column, irregular grids, overview levels, write-side GDAL integration, and classic raster API can be added incrementally, each as a well-scoped extension.

## Relationship to existing GDAL components

| Component | Relationship |
|---|---|
| **VRT** | VRT is XML-based, file-level references, no spatial index, 2D-oriented. CRI is complementary — it could generate VRTs, or replace them for chunk-level access. |
| **GTI** | GTI is file-level tile index with spatial footprints. CRI is chunk-level with byte-range references. GTI's vector-layer-as-index pattern is the direct precedent. |
| **ZARR driver** | Already reads kerchunk Parquet, exposes `GetRawBlockInfo`. CRI could be an alternative input format for the same machinery, or a separate driver that shares decompression code. |
| **`/vsicurl/`** | CRI relies on `/vsicurl/` (or `/vsis3/` etc.) for range-read access to source files. No changes needed to the virtual filesystem layer. |
| **Multidim API** | CRI is a natural fit for `GDALGroup` / `GDALMDArray` / `GDALDimension`. Each variable becomes an MDArray; dimensions come from coordinate metadata. |

## What the driver does NOT do

- **Does not store pixel data.** It is purely an index.
- **Does not reproject or resample.** Source chunks are returned as-is; higher-level tools handle transformation.
- **Does not manage source files.** It references them; it doesn't move, copy, or validate them.
- **Does not replace Zarr.** For data that's already in Zarr format, use Zarr. CRI indexes data that lives in other formats.
- **Does not replace STAC.** STAC is discovery and catalogue; CRI is chunk-level access. They're complementary.

## Naming

Working name "CRI" (Chunk Reference Index) is functional but uninspiring. Alternatives:

- **ARI** — Array Reference Index
- **BRI** — Byte Range Index (pleasingly short)
- **GRID** — Generalised Reference Index for Data (tortured acronym, good name)
- Just extend GTI — "GTI2" or "GTI with byte ranges"

Or let the name emerge from the design. What matters is the format and the driver, not the label.

## Next steps

1. **Build a GHRSST index** using async-tiff → GeoPackage with the schema above. Validate that it contains everything needed.
2. **Build a BLUElink index** using GDAL Python (or nctoolkit/netCDF4) → same schema with 4D. Validate the multi-variable, multi-dimension case.
3. **Write a minimal read-only GDAL driver** (C++) that opens the GeoPackage via OGR, exposes the multidim API, and implements `GetRawBlockInfo`. Test against the GHRSST index.
4. **Compare performance** with the existing ZARR driver reading kerchunk Parquet for the same data.
5. **Write up the design** as an RFC for the GDAL community, with working code and benchmarks.
6. **Iterate on the schema** based on what breaks when adding BLUElink, Sentinel-2, and eventually irregular grids.
