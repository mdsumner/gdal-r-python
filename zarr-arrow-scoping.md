# A Low-Level Zarr Interface via Arrow in R

## The premise

Zarr is a key-value store for chunked, compressed N-dimensional arrays. The entire metadata layer is JSON text. The data layer is compressed binary blobs addressed by deterministic keys. Arrow in R provides JSON parsing (via jsonlite or native), efficient memory buffers, compression codecs, filesystem abstraction over S3/GCS/local, and zero-copy data interchange. The question is whether Arrow's existing R infrastructure can serve as the plumbing for a lean Zarr reader (and eventually writer) — one that sidesteps GDAL's Multidim API and the existing pure-R Zarr packages in favour of a composable, low-level toolkit.

## What Zarr actually requires

A Zarr store (V2 or V3) boils down to:

**Metadata resolution** — reading `.zarray`/`.zattrs`/`.zgroup` (V2) or `zarr.json` (V3) from known key paths, parsing JSON, and building an in-memory representation of shape, chunks, dtype, codecs, fill_value, dimension_names, and attributes.

**Chunk addressing** — computing the key for a given chunk index. In V2 this is `{path}/{i.j.k}` (with `.` or `/` separator). In V3 it's `{path}/c/{i/j/k}` by default.

**Chunk retrieval** — fetching the raw bytes for a given key from whatever store backend is in play (local filesystem, S3, HTTP, GCS).

**Decompression** — running the codec pipeline in reverse. V2 typically has a single compressor (blosc, zlib, zstd, etc.) plus optional filters. V3 has a formal codec chain (bytes → array-to-bytes → bytes-to-bytes).

**Type coercion** — interpreting the decompressed byte buffer as a typed array given the dtype, endianness, and chunk shape.

**Consolidated metadata** — optionally reading `.zmetadata` (V2) or the root `zarr.json` consolidated_metadata field (V3) to avoid per-array metadata lookups over the network.

That's it. There's no complex query engine, no indexing structure, no relational model. It's a thin coordination layer over key-value retrieval, JSON parsing, decompression, and buffer reinterpretation.

## What Arrow in R provides

### Filesystem abstraction

Arrow's `FileSystem` class hierarchy gives you `LocalFileSystem`, `S3FileSystem`, and `GcsFileSystem` with a uniform interface: `$GetFileInfo()`, `$OpenInputStream()`, `$OpenInputFile()` for random access. This maps directly onto Zarr's store concept. You can read a chunk from S3 the same way you'd read it from disk, which is the core value proposition for cloud-native Zarr.

The notable gap: **no HTTP/HTTPS filesystem**. Arrow's C++ library doesn't expose a generic HTTP filesystem (there's been a long-standing request, apache/arrow#18980). For HTTP-served Zarr (e.g. via THREDDS or plain web servers), you'd need to fall back to `curl` / `httr2` for byte-range requests, or route through GDAL's `/vsicurl/` via vapour. S3-compatible stores (MinIO, Ceph) work fine via `S3FileSystem` with `endpoint_override`.

### Compression codecs

`arrow::Codec` supports: gzip, zstd, lz4 (frame), snappy, brotli, bz2, lzo. The critical ones for Zarr are zstd (the V3 default) and gzip (common in V2). Arrow does **not** support blosc directly — this is the biggest gap, since blosc is extremely common in Zarr V2 stores. The `zarr` CRAN package and `pizzarr` both have blosc support through the `blosc` R package, and you'd need to pull that in as a fallback.

Arrow codecs operate on raw vectors and return raw vectors, so they compose cleanly:

```r
codec <- arrow::Codec$create("zstd")
decompressed <- codec$Decompress(raw_chunk, output_buffer_size)
```

### Buffer and memory management

Arrow's `Buffer` class wraps a contiguous memory region with zero-copy slicing. You can construct a `Buffer` from a raw vector, slice it, and convert to typed R vectors. This is relevant because Zarr chunks are fundamentally "a buffer of known length with known type interpretation." Arrow's `buffer()` function and the buffer-to-array pathway (`arrow::Array$create()` from raw + type) could handle the dtype reinterpretation step.

```r
buf <- arrow::buffer(raw_bytes)
# reinterpret as float32 array
arr <- arrow::Array$create(type = arrow::float32(), length = n_elements)
```

However, this pathway isn't as direct as you might want. Arrow arrays are columnar and 1-D; they don't natively carry shape metadata for N-D interpretation. You'd get a flat vector out and then `dim<-()` it in R, which is perfectly fine given your stated position that flat vectors with shape metadata suffice.

### JSON parsing

jsonlite is the idiomatic R choice for Zarr metadata and is what you'd use. Arrow itself doesn't expose JSON parsing in a way that's useful for arbitrary metadata documents (its JSON reader is oriented toward record-batched tabular data). No reason to use Arrow for this part.

### Data types

Arrow has a rich type system: int8/16/32/64, uint8/16/32/64, float16/32/64, and more. This maps well onto Zarr's dtype specification. The mapping isn't always 1:1 with R's native types (R lacks unsigned integers and has only double and integer), but Arrow can hold the full-fidelity typed representation before coercing to R.

## The existing R Zarr landscape

Three packages matter here:

**`zarr`** (CRAN, Dec 2025, by Pepijn de Vries / R-CF) — Native R implementation, Zarr V3 only, extensible store/codec architecture. Local filesystem store only. Uses R6 classes. Early stage, modular design. This is the "proper" community effort.

**`pizzarr`** (GitHub, keller-mark) — Pure R, Zarr V2 focused, with optional blosc via Rarr. HTTP store support. R6-based. More mature but not on CRAN, bioimaging origin.

**GDAL Zarr driver** (via gdalraster/vapour) — C++ implementation supporting both V2 and V3, with Kerchunk reference stores, consolidated metadata, all GDAL virtual filesystems (vsicurl, vsis3, etc.). This is your ndr Multidim backend and it's extremely capable but tied to GDAL's abstractions.

The question is where an Arrow-based approach sits relative to these. It wouldn't replace the GDAL backend for ndr — that's already working and gives you the full Multidim API with lazy evaluation and `collect()`. Instead, an Arrow-backed Zarr layer would be a **standalone low-level interface** that's GDAL-independent, giving you direct control over the store interaction, and potentially serving as a building block for tools that need Zarr access without a GDAL dependency.

## Proposed architecture

The interface would have three layers:

### Layer 1: Store (key-value access)

A thin S7 class wrapping Arrow's filesystem abstraction or falling back to HTTP clients:

```r
zarr_store <- function(path) {
 # dispatch on URI scheme
 if (grepl("^s3://", path)) {
   fs <- arrow::S3FileSystem$create(anonymous = TRUE)
   ZarrStore$new(fs = fs, root = sub("^s3://", "", path))
 } else if (grepl("^gs://", path)) {
   fs <- arrow::GcsFileSystem$create(anonymous = TRUE)
   ZarrStore$new(fs = fs, root = sub("^gs://", "", path))
 } else if (grepl("^https?://", path)) {
   # Arrow has no HTTP FS — use httr2/curl backend
   ZarrStoreHTTP$new(base_url = path)
 } else {
   fs <- arrow::LocalFileSystem$create()
   ZarrStore$new(fs = fs, root = path)
 }
}
```

The store exposes `$get(key)` → raw vector, `$list(prefix)` → character, `$exists(key)` → logical. For the Arrow-backed stores, `$get()` calls `fs$OpenInputFile(full_path)$Read()` or similar. The HTTP fallback uses `httr2::req_perform()` with byte-range support.

### Layer 2: Metadata (JSON → structured representation)

Pure R, no Arrow dependency. Parse `zarr.json` or `.zarray`/`.zattrs`/`.zgroup` into an S7 class holding shape, chunks, dtype, codecs, fill_value, dimension_names, attributes. Handle consolidated metadata by parsing the root document once.

```r
zarr_metadata <- function(store, path = "/") {
  raw <- store$get(file.path(path, "zarr.json"))  # V3
  if (is.null(raw)) {
    raw <- store$get(file.path(path, ".zarray"))   # V2 fallback
  }
  meta <- jsonlite::fromJSON(rawToChar(raw))
  ZarrArrayMeta$new(meta)
}
```

The metadata object knows how to compute chunk keys:

```r
# V3 default encoding
chunk_key <- function(meta, idx) {
  paste0("c/", paste(idx, collapse = "/"))
}
```

### Layer 3: Codec pipeline (decompress + type-cast)

This is where Arrow earns its keep. The codec chain is walked in reverse:

```r
zarr_decode_chunk <- function(raw_bytes, meta) {
  # walk codecs in reverse
  buf <- raw_bytes
  for (codec_spec in rev(meta$codecs)) {
    buf <- decode_one(buf, codec_spec)
  }
  # reinterpret as typed R vector
  readBin(buf, what = r_type(meta$dtype),
          n = prod(meta$chunk_shape),
          size = dtype_size(meta$dtype),
          endian = meta$endian)
}

decode_one <- function(buf, spec) {
  switch(spec$name,
    "zstd" = arrow::Codec$create("zstd")$Decompress(
               buf, spec$configuration$decompressed_size %||% length(buf) * 10),
    "gzip" = arrow::Codec$create("gzip")$Decompress(buf, ...),
    "blosc" = blosc::blosc_decompress(buf),  # external dependency
    "bytes" = buf,  # identity / endian handling
    "transpose" = transpose_chunk(buf, spec),
    stop("unsupported codec: ", spec$name)
  )
}
```

The final step uses base R's `readBin()` for the actual byte reinterpretation. Arrow's type system could be used here instead (constructing an `arrow::Array` from the buffer), but for getting data into R vectors, `readBin()` is perfectly direct and avoids an unnecessary Arrow round-trip.

### Putting it together

```r
# Open a remote Zarr store on S3
store <- zarr_store("s3://mur-sst/zarr-v1")
meta <- zarr_metadata(store, "/analysed_sst")

# Read a single chunk
key <- chunk_key(meta, c(0, 100, 200))
raw <- store$get(paste0("analysed_sst/", key))
values <- zarr_decode_chunk(raw, meta)
dim(values) <- meta$chunk_shape

# Or: read a hyperslab across multiple chunks
slab <- zarr_read(store, "analysed_sst",
                  start = c(1, 1000, 2000),
                  count = c(1, 500, 500))
```

The `zarr_read()` function would handle chunk-to-slab assembly: determining which chunks intersect the requested region, fetching them (potentially in parallel), decoding, and stitching the results into a single R array. This is the main piece of non-trivial logic and it's purely index arithmetic.

## Relationship to hypertidy packages

**tidync** — operates on NetCDF via RNetCDF/ncdf4 with a metadata-first exploration model (activate grids, filter dimensions, then read). An Arrow-backed Zarr store could be another backend for the same UX pattern. The `hyper_filter()` → `hyper_array()` idiom maps directly to "compute chunk indices from dimension filters, fetch and decode chunks, assemble array."

**ndr** — the S7-based xarray-alike. The gdalraster Multidim backend is the heavy-lifter here. An Arrow-backed Zarr store would be an alternative backend, useful when you want to avoid the GDAL dependency or need direct store-level control (e.g. for Kerchunk/VirtualiZarr reference stores that GDAL might not support yet, or for custom codec pipelines).

**vapour** — provides GDAL's virtual filesystem access to R. For HTTP-served Zarr, you might still lean on vapour's `/vsicurl/` wrappers for the store layer rather than reimplementing HTTP byte-range logic, even in an otherwise Arrow-based pipeline.

**sds** — your WMTS catalog system. Zarr stores are increasingly how climate/ocean data is served; having a direct R interface to these stores (without GDAL mediation) could simplify the sds data pipeline for Zarr-native sources.

## Package dependencies

The minimal dependency set for an Arrow-backed Zarr reader:

- **arrow** — filesystem, codecs, optionally buffers/types
- **jsonlite** — metadata parsing
- **S7** — class system (consistent with ndr)

Optional/conditional:

- **blosc** — for Zarr V2 stores using blosc compression
- **httr2** — for HTTP-served stores (Arrow lacks HTTP filesystem)
- **curl** — alternative HTTP backend, byte-range support

This is a deliberately small footprint. The `zarr` CRAN package already handles V3 stores natively in pure R, so the Arrow approach is most compelling when you need: cloud filesystem abstraction (S3/GCS), Arrow's codec implementations (zstd/lz4 performance), or integration into an Arrow-centric data pipeline.

## Trade-offs and gaps

**Blosc** is the elephant in the room. It's the default compressor for most existing Zarr V2 data in the wild, and Arrow doesn't support it. Any practical Zarr reader needs blosc support, which means pulling in the `blosc` R package or implementing the decompress via `.Call()` to libblosc. This somewhat undermines the "Arrow as the one dependency for byte handling" story.

**Sharding** (ZEP 2, now in the V3 spec) means a single store object contains multiple chunks with an index. Reading sharded chunks requires parsing the shard index and doing byte-range reads within a single blob. Arrow's `RandomAccessFile` supports this (seek + read), but you'd need to implement the shard index parsing yourself.

**HTTP access** is a real gap. Many Zarr stores are served over plain HTTPS (THREDDS, Copernicus Marine, various data portals). Without Arrow HTTP filesystem support, you need a separate HTTP client stack. For S3-compatible endpoints this isn't an issue — Arrow handles them — but for vanilla HTTPS it is.

**No multithreaded chunk fetch** out of the box. Arrow's filesystem reads are synchronous from R's perspective. For cloud Zarr stores where latency dominates, you'd want parallel chunk fetching. This could be done via `future`/`mirai` at the R level, dispatching multiple `store$get()` calls concurrently.

**Consolidated metadata** is well supported — it's just JSON parsing, no special handling needed beyond reading the root document and extracting the nested metadata for each array/group.

## Conclusion

Arrow in R provides roughly 70% of what you need for a Zarr reader: filesystem abstraction for S3/GCS/local, compression codecs for the most important algorithms (zstd, gzip, lz4), efficient buffer handling, and a rich type system. The remaining 30% — blosc support, HTTP filesystem, chunk-to-slab assembly logic, and the metadata parsing layer — would need to come from other packages or be implemented directly.

The architecture is clean: a store layer (Arrow filesystem + HTTP fallback), a metadata layer (pure R/jsonlite), and a codec layer (Arrow codecs + blosc fallback). This gives you a GDAL-independent Zarr reader that composes with the ndr/tidync ecosystem and plays naturally with Arrow's broader data tooling.

Whether this is worth building as a standalone package depends on how much you need GDAL-free Zarr access. If the gdalraster Multidim backend covers your use cases, the Arrow approach is an interesting exercise but not urgent. Where it becomes compelling is for Zarr-native cloud data (where you want direct S3 access without GDAL's VFS overhead), for custom codec pipelines, or for environments where GDAL installation is a barrier but Arrow is available.
