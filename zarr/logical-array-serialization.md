# Logical array serialisation — rationale and tasks

The scientific Python and R ecosystems have mature tools for opening large collections of remote files as logical N-dimensional arrays (xarray `open_mfdataset`, GDAL mdim mosaic, `terra::rast`, `stars::read_stars`), but no standard way to serialise the result of that parsing work. Every reopening repeats the same coordinate inference, dimension alignment, and CF decoding from scratch. Virtualisation — storing byte references rather than copied data — is the right primitive, but current implementations (VirtualiZarr, kerchunk) approach it narrowly as a kerchunk replacement rather than as a general serialisation format for logical array structure. COG is already a Zarr: its IFD table is a chunk manifest, tile offsets are byte references, and the pyramid is a multiscales spec — making this explicit costs nothing and unlocks the entire Zarr/xarray ecosystem for existing COG archives. Aligning GDAL mdim VRT, Zarr consolidated metadata, and xarray's internal representation around a shared serialisation primitive would close a genuine gap across the whole stack.

## Tasks

1. **GDAL mdim mosaic → TIFF input + third-dimension helpers**
   GDAL mdim mosaic currently requires NetCDF/HDF; extend it to accept GeoTIFF (single band or multi-band), and add helper API to nominate/define a third dimension (time, depth) from filename patterns or sidecar metadata, producing a mdim VRT over a file collection.

2. **GDAL mdim VRT → Zarr/xarray translator**
   Write a converter (Python or R) that reads a GDAL mdim VRT and emits either Zarr consolidated metadata or a VirtualiZarr manifest; this makes the mdim VRT a portable serialisation format that xarray can round-trip through without GDAL at read time.

3. **GDAL IFD exposure as output**
   Add a GDAL API path (C and SWIG Python) to emit the IFD tile index of a (possibly remote) GeoTIFF as a structured table (path, ifd, tile_row, tile_col, offset, length, dtype, compression, predictor); this is the missing primitive that makes COG→Zarr virtualisation first-class without requiring async-tiff/Rust.

4. **`gdx` as a GDAL mdim backend for xarray**
   Evaluate whether a thin `gdx` package (GDAL multidim API → xarray backend) could serve as the R and Python bridge that makes GDAL mdim VRT natively openable in xarray, completing the round-trip from file collection → mdim VRT → xarray without a separate translation step.

5. **VirtualiZarr parser landscape from a C++ library perspective**
   Audit the existing VirtualiZarr parsers (TIFF, NetCDF, HDF5, GRIB) and assess what a shared C++ byte-reference extraction library would look like; the parsing logic (IFD walking, HDF5 chunk btree, NetCDF variable layout) belongs in a compiled library callable from R, Python, and GDAL rather than reimplemented per ecosystem — this is the modularisation the Python stack currently lacks.

## Worked examples

Each example targets a different part of the stack and together make the case concrete.

| Dataset | Format | Dimensions | Primary tasks |
|---------|--------|------------|---------------|
| **GEBCO** 2022–2025 | COG (annual) | lat × lon + time via files | 1, 3 — multiscale Zarr from IFD, temporal stack |
| **GHRSST** | NetCDF (daily files) | time × lat × lon | 2 — mdim VRT → Zarr, canonical open_mfdataset serialisation |
| **Bluelink** | NetCDF (ocean model) | time × depth × lat × lon | 1, 2 — third-dimension nomination, multi-variable mdim mosaic |

### GEBCO
Single variable (elevation, Int16), identical grid across years (86400×43200, EPSG:4326), COG with 7-level pyramid (512×512 tiles, DEFLATE+PREDICTOR=2). Working R script using `rustycogs::tiff_refs()` + `arrow` produces a kerchunk parquet store readable by xarray and GDAL. Extend to temporal stack (2022, 2023, 2024, 2025) and full multiscale (ifd 0–6).

### GHRSST
Large time series of daily NetCDF files; the canonical case where `open_mfdataset` does expensive CF parsing on every open. Goal: capture the parsed result as a mdim VRT, translate to Zarr consolidated metadata, and demonstrate that subsequent opens are instantaneous and require no Python parsing overhead.

### Bluelink
Ocean model reanalysis with depth dimension; tests third-dimension nomination in the mdim mosaic TIFF extension and multi-variable handling. Hosted at NCI/Pawsey. Goal: mdim VRT over the full archive, openable in both xarray and R via gdx/ndr.

## Related work

- `intake` — serialises the *recipe* (function call + arguments) but re-runs parsing on every open; complementary not equivalent
- GDAL mdim VRT — closest existing format but GDAL-dialect only, not portable to xarray without a translator
- VirtualiZarr — targets the byte-reference layer but not the logical structure layer; parser implementations are Python-only and not reusable from R or GDAL
- icechunk — versioned living store, v3-native; the right target for mutable/growing datasets once the reference layer is established
