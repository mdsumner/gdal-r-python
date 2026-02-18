# Byte References as a Universal Array Index

*A discussion document on lightweight alternatives to the virtualisation stack*

*Michael Sumner, February 2026*

---

## The problem in one sentence

Large collections of geospatial raster files — Cloud-Optimised GeoTIFFs, NetCDF4, GRIB — contain self-describing metadata about where every compressed data chunk lives within each file. A "virtual dataset" is nothing more than an index that records *where the bytes are* across the whole collection, so that any reader can fetch exactly the chunks it needs without downloading entire files.

This is a small, simple, well-defined problem. The current tooling makes it feel enormous.

## What the information actually is

Consider a concrete example: the GHRSST MUR Sea Surface Temperature archive on Source Cooperative. Each daily file is a Cloud-Optimised GeoTIFF, 17999×36000 pixels, tiled at 512×512, ZSTD-compressed. There are roughly 8,660 files spanning 2002 to present.

For any single file, the entire "virtual" content is two arrays of integers stored in the TIFF IFD (Image File Directory):

- **TileOffsets** (tag 324): byte offset within the file where each compressed tile starts
- **TileByteCounts** (tag 325): number of bytes in each compressed tile

Each file has about 2,556 tiles (36 rows × 71 columns). That's 2,556 offset–length pairs. For 8,660 files, the complete index is roughly 22 million rows of six columns: time index, y-chunk index, x-chunk index, file path, byte offset, byte length.

Alongside the reference table, you need a small fixed block of metadata describing the array structure: image dimensions, tile dimensions, data type, compression codec, predictor, nodata value, coordinate reference system, and geotransform. For a uniform collection this is identical across every file and fits in a few hundred bytes of JSON.

That's it. That's the entirety of what "virtualisation" means for this dataset.

## Where the complexity lives today

The current Python ecosystem approaches this through several interlocking layers, each of which introduces its own weight.

### kerchunk's data model

kerchunk was designed around the mental model that everything should look like a Zarr store. References are keyed by Zarr chunk paths — strings like `"analysed_sst/0.1.2"` — and serialised as nested dictionaries. The output format (JSON or partitioned Parquet with string key columns) must reconstruct these paths on both write and read.

For the GHRSST dataset this means 22 million dictionary entries, each with a string key constructed by formatting three integers into a path. The Parquet serialisation of the full catalogue takes around 6 minutes — longer than reading every file header across the internet. The bottleneck is not I/O or computation; it is the structural overhead of representing integers as formatted strings inside a nested dictionary.

### VirtualiZarr's abstraction tax

VirtualiZarr v2 wraps the reference-extraction step in an xarray-native workflow. Files are opened as "virtual" xarray datasets, concatenated along a time dimension, and then exported to kerchunk format. For a uniform time series of identical grids, the concatenation logic is trivial — it's "stack these in filename order" — but the machinery to support it is not.

VirtualiZarr v2 moved GeoTIFF support into a separate package (virtual-tiff), replaced fsspec with obstore, adopted Zarr v3 as its internal representation, and still supports kerchunk JSON/Parquet as an output format. This means every GeoTIFF virtualisation must traverse a codec translation pipeline: TIFF-native compression descriptors are wrapped in imagecodecs-style v3 codec configurations, which must then be converted back to Zarr v2 numcodecs-style dictionaries for kerchunk output.

In practice, this translation layer is where most of the bugs live.

### Codec conversion as a source of fragility

When virtual-tiff parses a ZSTD-compressed GeoTIFF with horizontal differencing (TIFF predictor=2), the internal v3 codec pipeline contains three codecs: HorizontalDeltaCodec, BytesCodec, and ZstdCodec. The ZSTD configuration arrives triply-nested inside imagecodecs wrapper objects. Only ZSTD has a numcodecs equivalent; the other two must be silently dropped because GDAL-based readers (which will actually perform the decompression) handle them natively.

The v3-to-v2 conversion function does not anticipate codecs without numcodecs equivalents, does not handle recursive imagecodecs nesting, and has dtype-class incompatibilities between zarr-python's v3 type objects and the v2 metadata constructors. Each of these issues required a separate monkeypatch to the VirtualiZarr internals, applied before module import to intercept early binding.

None of these issues have anything to do with the actual problem of recording where bytes live in files.

### The xarray coupling

VirtualiZarr's `open_virtual_mfdataset` defaults to `combine_by_coords`, which requires dimension coordinates on every virtual dataset. Virtual-tiff produces datasets with bare integer dimensions and no geotransform metadata. The workaround is a manual loop: open each file, rename variables, expand dimensions with hand-constructed time coordinates, and concatenate explicitly.

For a researcher who simply wants to know "where are the tiles in these 8,660 files," the requirement to construct valid xarray datasets with CF-compliant coordinates is a detour through an abstraction layer that adds no information to the output.

## The case for something simpler

### Observation 1: The information is inherently tabular

A chunk reference is a tuple: (chunk-position, file-path, byte-offset, byte-length). The chunk position decomposes into integer indices along each array dimension. This is a table. It has always been a table. Representing it as a Zarr store path string is a lossy encoding of structured data into an unstructured format.

### Observation 2: The metadata is small and static

For any uniform collection, the array metadata — dtype, chunk shape, compression, nodata, CRS, geotransform — is fixed across all files. It is a single JSON document, or a single row in a metadata table, or a key-value block in a Parquet file footer. It does not need to be replicated or transformed per-chunk.

### Observation 3: The extraction is trivial

Reading tile offsets and byte counts from a TIFF IFD is a single range-read of a few kilobytes from each file header. Libraries like async-tiff (Rust, with Python bindings) expose `ifd.tile_offsets` and `ifd.tile_byte_counts` as plain integer lists. No Zarr layer, no codec translation, no xarray dataset construction. For 8,660 files with async I/O, this completes in under 7 minutes including network latency.

### Observation 4: The consumers are diverse

The value of a reference index depends on how many tools can use it. Zarr-specific formats (kerchunk JSON, Icechunk) lock the index to the Python/xarray ecosystem. A Parquet table of integers is readable from R (arrow), Python (pyarrow/polars), Julia, Rust, DuckDB, Spark, and anything else with a Parquet binding. The most universal format wins.

## Possible pathways

### Pathway 1: Flat Parquet reference table

The minimal viable format: a single Parquet file containing one row per chunk.

| Column | Type | Description |
|---|---|---|
| time_idx | uint16 | Index along time dimension |
| y_chunk | uint16 | Tile row index |
| x_chunk | uint16 | Tile column index |
| path | string | Relative path or URI to source file |
| offset | uint64 | Byte offset of compressed chunk |
| length | uint32 | Byte length of compressed chunk |

Array metadata is stored in the Parquet file's key-value metadata (schema-level), eliminating the need for a sidecar file:

```json
{
  "dtype": "int16",
  "chunks": [1, 512, 512],
  "shape": [8660, 17999, 36000],
  "dims": ["time", "y", "x"],
  "compression": "zstd",
  "predictor": "horizontal_differencing",
  "nodata": -32768,
  "crs": "EPSG:4326",
  "transform": [0.01, 0.0, -179.995, 0.0, -0.01, 89.995]
}
```

This format writes in seconds via Arrow (compared to minutes for kerchunk Parquet). It supports predicate pushdown for efficient spatial and temporal subsetting. It is readable from every language that matters.

The path column could optionally be normalised out — store an integer file index and a separate path-lookup array — to avoid repeating long URI strings across thousands of rows per file.

### Pathway 2: DuckDB as a self-contained array database

A DuckDB file extends the flat-table concept into a relational model:

**chunks** — the core reference table, with a foreign key to a file table rather than repeating path strings. For the GHRSST dataset, this replaces 22 million copies of ~150-character paths with 22 million uint16 foreign keys and 8,660 rows in a file table.

**files** — one row per source file, with the path/URI and any per-file metadata (timestamp, processing version, quality flags).

**arrays** — one row per variable, with dtype, chunk shape, compression, nodata, and any variable-level attributes.

**grid** — spatial metadata: CRS, geotransform, full array shape, dimension names and coordinate arrays.

This model naturally supports multiple variables (e.g. SST, sea ice, wind), multiple collections in a single file, and relational queries that combine spatial, temporal, and attribute filters:

```sql
SELECT c.offset, c.length, f.path
FROM chunks c
JOIN files f ON c.file_id = f.id
WHERE f.datetime BETWEEN '2021-01-01' AND '2021-01-31'
  AND c.y_chunk BETWEEN 25 AND 35
```

DuckDB runs this in milliseconds, in-process, with no server. It reads and writes Parquet natively. It works from R, Python, and the command line. And because DuckDB supports HTTP range reads on remote files, the index itself could be hosted on object storage and queried without downloading.

### Pathway 3: Convention layer atop existing tools

Rather than building new infrastructure, define a lightweight convention — a specification — for how to store array references in Parquet. This would be:

- A column naming convention (the table schema above, or something close to it)
- A metadata key convention for the Parquet schema metadata (array structure, CRS, etc.)
- A small reference reader library in each target language that takes a query result (list of path/offset/length tuples) and performs range-GET fetches and decompression

The decompression step already exists in multiple forms: GDAL (accessible from R via gdalraster/terra/vapour and from Python via rasterio), async-tiff (Rust/Python), and imagecodecs (Python). A convention-based approach lets existing tools do the heavy lifting while providing a common interchange format.

### Pathway 4: GDAL as the virtualisation engine

GDAL already understands every relevant file format, every compression codec, every CRS, and every geotransform. It reads TIFF IFDs, NetCDF chunk indices, and GRIB message offsets. Its VRT format is already a virtualisation layer for raster data.

A `gdal_virtualize` command-line tool that reads file headers and outputs a flat Parquet reference table (with metadata in the schema) would be immediately useful from any GDAL binding — R, Python, Julia, or direct CLI. No Python-specific dependency chain, no Zarr layer, no xarray. Just GDAL doing what GDAL already does, with a universal output format.

This could be implemented as a GDAL utility or as a thin wrapper around the existing GDAL C API. The core operation — open dataset, read tile offsets from the internal block table, write to Parquet — is a few hundred lines of code.

## What a reader looks like

Regardless of which pathway produces the reference table, the reader side is the same pattern:

1. **Query** the reference table for the chunks you need (spatiotemporal bounding box → list of path/offset/length tuples)
2. **Fetch** compressed bytes via HTTP range requests (or local file seeks)
3. **Decompress** using the codec specified in the metadata
4. **Assemble** into an in-memory array

Step 1 is a table query — DuckDB, Arrow filter, or SQL WHERE clause. Step 2 is HTTP client code that every language already has. Step 3 is one function call to GDAL, imagecodecs, or a native codec library. Step 4 is memory layout — writing decompressed tiles into the right positions of a pre-allocated array.

Each step is independent. None requires Zarr, xarray, or any specific Python package. The reference table is the interchange format; everything else is a local implementation detail.

## What we're not trying to replace

This discussion is about the *index* layer — the recording of where bytes live. It is not about replacing Zarr as a storage format for new data, or replacing xarray as an analysis framework, or replacing STAC as a catalogue standard.

A Parquet reference table complements all of these. It could be generated from a STAC catalogue. It could feed into xarray via a lightweight accessor. It could coexist with Zarr stores for datasets that have been physically rechunked. The argument is simply that the index itself should be stored in the simplest, most universal, most efficiently queryable format possible — and that format is a table of integers, not a dictionary of formatted path strings.

## A GDAL meta-driver for reference stores

The pathways above describe formats for storing references. The natural next step is a GDAL driver — analogous to GTI (GDAL Tile Index) — that reads and writes reference stores and presents them as standard GDAL raster datasets.

GTI works at the file level: each feature in a vector layer points to a raster file and has a footprint geometry. The driver proposed here works at the *chunk* level with byte-range resolution: each row in the index points to a specific byte range within a file, and has a footprint geometry computed from the chunk's spatial extent.

### The reference store as a vector layer

By storing chunk references in a spatial vector format — GeoPackage, FlatGeoBuf, or DuckDB with a spatial extension — the index gains a geometry column. For regular grids, each chunk's geometry is a rectangle trivially computed from the geotransform and the chunk's row/column indices:

```
┌──────────┬─────────┬─────────┬──────────────┬────────────┬────────┬───┬──────────────────┐
│ time_idx │ y_chunk │ x_chunk │ path         │ offset     │ length │...│ geometry         │
├──────────┼─────────┼─────────┼──────────────┼────────────┼────────┼───┼──────────────────┤
│ 0        │ 25      │ 30      │ /vsis3/...   │ 113809065  │ 63     │   │ POLYGON((...))   │
│ 0        │ 25      │ 31      │ /vsis3/...   │ 113809128  │ 14823  │   │ POLYGON((...))   │
└──────────┴─────────┴─────────┴──────────────┴────────────┴────────┴───┴──────────────────┘
```

Spatial queries become native: an R-tree hit on a bounding box returns exactly the chunk references needed. No coordinate-to-index arithmetic on the client side. No opening source files. Just a spatial filter on a local index.

### Multi-scale indexing

Cloud-Optimised GeoTIFFs contain overview IFDs at progressively lower resolutions, each with its own tile grid. These are indexed into the same table with a `level` column:

```
level=0  →  36×71 tiles at full resolution
level=1  →  18×36 tiles at 2× downsample
level=2  →  9×18 tiles at 4× downsample
```

The driver selects the appropriate level based on the requested resolution, applies a spatial filter, and fetches only the relevant chunks. This is what GDAL already does internally when reading a single COG — the difference is the index spans thousands of files without opening any of them.

### Irregular grids and swath data

The geometry column is where this approach genuinely transcends regular-grid assumptions. For a regular affine grid, the chunk footprint is a trivial rectangle. But the same schema handles every other geolocation model in Earth observation:

**Swath satellite data** (MODIS, Sentinel-3, VIIRS): Geolocation arrays give latitude/longitude per pixel. The chunk footprint is the polygon enclosing the geolocation values for that chunk's index range. Swath granules that overlap, have bowtie gaps, or cross the antimeridian are simply polygons with byte ranges. A spatial query finds the right chunks regardless of orbital geometry.

**Curvilinear ocean models** (ROMS, NEMO, MOM6): The grid is defined by two-dimensional coordinate arrays, not an affine transform. Each chunk's footprint is the convex hull of its coordinate values. Finding which chunks intersect a region of interest requires no understanding of the grid topology — just a spatial filter.

**Ground Control Points**: Datasets where GDAL carries GCPs instead of a proper geotransform. The chunk footprint is bounded from the GCPs within or near that chunk's pixel range.

**Unstructured meshes** (MPAS, FESOM, UGRID): Cells are not on any grid. Each chunk of the mesh has a spatial extent derived from its vertex coordinates. Same table, same spatial query.

The footprint does not need to be geometrically precise — it serves as a spatial index, not a coordinate transform. A bounding polygon slightly larger than the true chunk extent means at worst one unnecessary fetch. The convex hull of corner geolocations for each chunk is sufficient and cheap to compute.

This solves a long-standing practical problem: "which swath granules have data over my study area?" Currently this requires either coarse granule-level STAC spatial queries or opening every file to inspect geolocation arrays. With chunk-level footprints in a spatial index, sub-granule spatial precision comes from a single query against a local file.

### Driver architecture

**Write (index-building) side:**

- Accepts any GDAL-readable raster source: COGs, NetCDF4, GRIB, existing Zarr, HDF5
- Reads the internal block structure (tile offsets/lengths from IFD, chunk table from NetCDF4, message offsets from GRIB)
- Computes chunk footprint geometries: from affine geotransform for regular grids, from geolocation arrays or GCPs for irregular grids
- Writes references to the index store (GeoPackage, FlatGeoBuf, DuckDB, or Parquet with WKB geometry)
- Stores array metadata (dtype, compression, nodata, CRS, coordinate arrays) as layer metadata or in companion tables

**Read (data access) side:**

- Receives a read request (bounding box, target resolution, band/variable selection, time range)
- Selects the appropriate overview level for the requested resolution
- Spatial (and attribute) query on the index → list of (path, offset, length) tuples
- Batch range-GET fetch with request merging for adjacent chunks
- Decompress and assemble into the output buffer

FlatGeoBuf is particularly suited as a remote index format because its spatial index is stored at the front of the file and supports HTTP range-read queries — a FlatGeoBuf reference index on S3 can be spatially queried without downloading the whole file.

### Relationship to existing tools

This driver would be complementary to the existing `ZARR:` driver's ability to read kerchunk Parquet (via `GetRawBlockInfo`), but with a cleaner index schema and native spatial querying. It connects directly to the Rust-based COG reading infrastructure in projects like rustycogs, where the range-reading and decompression machinery already exists — the reference store provides the orchestration layer that tells the reader which ranges to fetch from which files.

## Collapsing the real/virtual distinction

There is a conceptual divide in the current ecosystem between "real" Zarr stores — where each chunk is a separate object on S3 or a separate file on disk — and "virtual" Zarr stores — where chunks are byte ranges inside larger container files like COGs or NetCDF4. This divide drives much of the tooling complexity: kerchunk exists to bridge it, VirtualiZarr exists to make the bridge feel native, Icechunk exists to version it.

But the divide is artificial. A real Zarr chunk sitting at `s3://bucket/mydata.zarr/temperature/0.1.2` has a reference: the object path, byte offset zero, and byte length equal to the object size. A virtual chunk inside a COG has a reference: the file path, a non-zero byte offset, and a byte length smaller than the file. These are the same tuple. The only difference is whether the offset is zero and the length equals the whole object.

```
# "real" Zarr chunk — whole object
("s3://bucket/mydata.zarr/temperature/0.1.2",  0,      48219)

# "virtual" chunk inside a COG
("s3://bucket/ghrsst/20210101.tif",             283746, 12841)
```

Same schema. Same reader logic. The reference table doesn't care whether the bytes happen to live in their own object or inside a larger file.

This means the reference table is not a "virtualisation format." It is *the* array index format, and conventional Zarr stores are simply the special case where every chunk occupies an entire object starting at byte zero.

### Consequences

**Retroactive indexing of existing Zarr stores.** Any Zarr store already written to S3 can be indexed into a reference table by listing the objects and recording their sizes. From that point on, the reference table is a complete, queryable index that works identically to one built from COG headers. No more relying on `.zmetadata` accuracy or S3 LIST operations to discover what exists.

**Discoverability without listing.** With a conventional Zarr store, answering "what time steps are available" or "are there missing chunks" requires listing a bucket prefix — an operation that is slow, paginated, and eventually consistent on S3. With a reference table, these are instant table queries. Sparse arrays (where some chunks intentionally don't exist) are represented by the absence of a row, which is trivially queryable.

**Mixed storage is natural.** A single reference table can point to chunks in different storage layouts — some in a Zarr object store, some inside COGs, some in a local NetCDF4 file. The reader doesn't care; it gets a path, an offset, and a length, and fetches the bytes. Migration from one storage layout to another is a table update, not a data copy.

**The filesystem convention becomes optional.** Zarr's current architecture requires chunks to be stored at paths derived from their dimensional indices (`variable/t.y.x`). This couples the logical array structure to the physical storage layout. With a reference table as the primary index, chunks can live anywhere — in any directory structure, in any container format, split across archives — and the table maintains the mapping. The path convention becomes one possible layout, not a requirement.

**Zarr metadata becomes table metadata.** The `.zarray` JSON (dtype, chunks, compressor, fill_value) and `.zattrs` JSON (CF attributes, units, long names) are currently sidecar files in the store. In the reference table model, they're rows in a metadata table or key-value entries in the Parquet schema metadata. Everything about the array — its structure, its attributes, and where every byte lives — is in one queryable object.

### What this reframes

The question is no longer "how do we virtualise non-Zarr data to make it accessible through the Zarr API." The question becomes "how do we index array data, regardless of physical storage, in a way that any reader can efficiently query and fetch." The answer is a table. It has always been a table.

## Summary of the argument

Every chunk of array data — whether it lives in its own Zarr object, inside a Cloud-Optimised GeoTIFF, or packed into a NetCDF4 file — is located by the same three values: a path, a byte offset, and a byte length. A "real" Zarr store is just the special case where every offset is zero and every length equals the object size. A "virtual" dataset is just the case where offsets are non-zero and lengths are less than the file size. The distinction is not fundamental. It's the same table.

This is the core insight: the array index is a table of integers. Parquet is the universal columnar table format. Array metadata is a few hundred bytes of JSON. Everything else — Zarr path formatting, codec configuration objects, xarray dataset construction, v3-to-v2 conversion, the entire real-vs-virtual distinction — is ceremony that the core problem does not require.

The tooling to extract this information already exists (async-tiff, GDAL, tifffile). The tooling to store it efficiently already exists (Arrow, Parquet, DuckDB). The tooling to query it already exists (DuckDB, Arrow compute, SQL). The tooling to act on it already exists (HTTP range requests, GDAL decompression, imagecodecs).

What's missing is the convention that ties them together: a specification for how array chunk references are stored in Parquet, and a small family of readers (one per language ecosystem) that consume that specification. The result would be a universal array index format that is faster to produce, smaller to store, simpler to query, and accessible from every programming language — without requiring any single ecosystem's abstractions.
