# `gdal mdim stack` — Design Notes

## Overview

A new GDAL algorithm subcommand `gdal mdim stack` that takes a list of classic 2D single-band raster files (e.g. GeoTIFFs) and creates a multidimensional dataset by stacking them along a new user-specified dimension (e.g. time, z).

This fills a gap: the VRT multidim driver already fully supports reading `<SourceBand>` + `<SourceTranspose>` + `<DestSlab>` from classic raster sources, but there is no CLI or API pathway to generate such a VRT without manually writing the XML. The existing `gdal mdim mosaic` only merges arrays that are already multidimensional.

A reference implementation exists in R: <https://github.com/mdsumner/vrtstack>

## Scope

### In scope

- Input: list of single-band classic raster files (GeoTIFF, COG, any GDAL-readable raster)
- Create one new dimension with user-specified name
- Dimension values from: start/step, explicit list, or filename regex extraction
- Temporal support: date/time parsing from filenames, origin + unit conversion
- Output: mdim VRT (zero-copy, the natural output format)
- Hard assumption: all inputs share the same CRS, resolution, and extent (validate and error)

### Out of scope (future extensions)

- Multi-band inputs where bands represent a proxy dimension (e.g. `--band-dimension`)
- Spatial mosaicing within slices
- Reprojection/resampling across inputs (see warper API and GTI driver)
- Inputs with different extents 

## VRT XML Structure (Target Output)

The generated VRT uses structures already supported by the VRT multidim reader (`frmts/vrt/vrtmultidim.cpp`):

```xml
<VRTDataset>
  <Group name="/">
    <Dimension name="time" size="3" type="TEMPORAL" direction="FUTURE">
      <IndexingVariable>time</IndexingVariable>
    </Dimension>
    <Dimension name="Y" size="4"/>
    <Dimension name="X" size="3"/>

    <Array name="time">
      <DataType>Float64</DataType>
      <DimensionRef ref="time"/>
      <RegularlySpacedValues start="0" increment="1"/>
    </Array>

    <Array name="data">
      <DataType>Float32</DataType>
      <DimensionRef ref="time"/>
      <DimensionRef ref="Y"/>
      <DimensionRef ref="X"/>
      <Source>
        <SourceFilename>/path/to/file_0.tif</SourceFilename>
        <SourceBand>1</SourceBand>
        <SourceTranspose>-1,0,1</SourceTranspose>
        <DestSlab offset="0,0,0"/>
      </Source>
      <Source>
        <SourceFilename>/path/to/file_1.tif</SourceFilename>
        <SourceBand>1</SourceBand>
        <SourceTranspose>-1,0,1</SourceTranspose>
        <DestSlab offset="1,0,0"/>
      </Source>
      <!-- ... -->
    </Array>

    <!-- SRS and GeoTransform carried from reference file -->
    <Array name="Y">
      <DataType>Float64</DataType>
      <DimensionRef ref="Y"/>
      <RegularlySpacedValues start="..." increment="..."/>
    </Array>
    <Array name="X">
      <DataType>Float64</DataType>
      <DimensionRef ref="X"/>
      <RegularlySpacedValues start="..." increment="..."/>
    </Array>
  </Group>
</VRTDataset>
```

Key VRT elements used:
- `<SourceBand>` — opens classic raster band via `GetRasterBand(n)->AsMDArray()` (vrtmultidim.cpp ~line 2175)
- `<SourceTranspose>` with `-1,0,1` — injects the new dim at axis 0, promoting 2D→3D (~line 1810)
- `<DestSlab>` — places each source at its index along the new dimension
- `<RegularlySpacedValues>` / `<InlineValues>` — for indexing variables

## Files to Create / Modify

| File | Action | Notes |
|---|---|---|
| `apps/gdalalg_mdim_stack.h` | **Create** | Class declaration |
| `apps/gdalalg_mdim_stack.cpp` | **Create** | Implementation |
| `apps/gdalalg_mdim.cpp` | **Modify** | Register `GDALMdimStackAlgorithm` as sub-algorithm |
| `apps/CMakeLists.txt` | **Modify** | Add `gdalalg_mdim_stack.cpp` to sources |
| `doc/source/programs/gdal_mdim_stack.rst` | **Create** | User documentation |
| `doc/source/programs/index.rst` | **Modify** | Add to program index |
| `autotest/utilities/test_gdalalg_mdim_stack.py` | **Create** | Test suite |

### SWIG / API Changes

**None required.** The algorithm is discovered via the generic `GDALAlgorithmRegistry` mechanism. Once registered in C++, it is automatically available via:
- CLI: `gdal mdim stack ...`
- Python: `gdal.Run("mdim", "stack", ...)`
- R: via the gdal bindings

The auto-generated docs file `swig/include/python/docs/gdal_algorithm_docs.i` lists subcommands but appears to be generated from `--help-doc` output.

## Algorithm Parameters

```
--input, -i          Input raster files (glob, @filelist, or explicit paths/URIs)
--output, -o         Output file (.vrt or materialized)
--output-format, -f  Output format (default: VRT)
--dim-name           Name for the new dimension (required)
--dim-values         Explicit numeric values (comma-separated)
--dim-start          Start value for regular axis
--dim-increment      Increment for regular axis
--dim-type           TEMPORAL, VERTICAL, PARAMETRIC, etc.
--dim-direction      NORTH, SOUTH, UP, DOWN, FUTURE, PAST, etc.
--dim-unit           Unit string (e.g. "days since 1970-01-01", "meters")
--dim-pattern        Regex with one capture group to extract value from filename
--parse-format       strptime format string for date/time parsing (implies temporal)
--time-origin        Origin date for temporal offset computation
--time-unit          seconds | minutes | hours | days (default: days)
--array              Name for the output array (default: "data")
--band               Source band number (default: 1)
```

### Dimension value resolution order

1. `--dim-start` + `--dim-increment` → regular axis, values computed as `start + i * increment`
2. `--dim-values` → explicit values, check if regularly spaced → regular or irregular axis
3. `--dim-pattern` (+ optional `--parse-format` / `--time-origin` / `--time-unit`) → extract from filenames, then as per (2)
4. None of the above → error, or default to 0-based integer index?

## Core Algorithm (RunImpl)

```
1. Resolve input file list
   - Reuse GetInputDatasetNames() pattern from gdal mdim mosaic
   - Support glob patterns, @filelist, explicit paths

2. Resolve dimension values
   - Apply resolution order above
   - For regex extraction: apply pattern to each filename (basename or full path?)
   - For temporal: parse dates, convert to numeric offset from origin
   - Sort files by dimension value (stable sort)
   - Validate: count of values == count of files

3. Read reference info from first file (opened as classic raster)
   - Validate it's single-band (or use --band)
   - Read: SRS, GeoTransform, XSize, YSize, DataType, NoData
   - Optionally validate all inputs match (extent, CRS, size)

4. Build VRT using C++ multidim API:
   a. Create root group
   b. CreateDimension() for new dim, Y, X
   c. Create indexing variables:
      - New dim: RegularlySpacedValues or InlineValues
      - Y: RegularlySpacedValues from GeoTransform
      - X: RegularlySpacedValues from GeoTransform
   d. Create data array with dimensions [new_dim, Y, X]
   e. For each input file i:
      - Add VRTMDArraySource with SourceBand, SourceTranspose(-1,0,1), DestSlab(i,0,0)
   f. Attach SRS metadata

5. Output
   - If VRT: serialize XML to output path
   - If materialized: CreateCopy to target driver (netCDF, Zarr, etc.)
```

## Test Strategy

### Approach

Create temporary GeoTIFFs in-test using `gdal.GetDriverByName("GTiff")`. Small rasters (e.g. 4×3 pixels) are sufficient and fast. This follows the pattern used by `test_gdalalg_mdim_mosaic.py` (which creates netCDF sources from scratch).

### Test cases

1. **Basic stack with `--dim-values`** — 3 GeoTIFFs, explicit values `[10, 20, 30]`, verify dimension values and data readback
2. **Regular axis with `--dim-start` / `--dim-increment`** — verify `RegularlySpacedValues` in output VRT
3. **Auto-detect regular spacing** — explicit values `[0, 1, 2]` detected as regular
4. **Filename regex extraction** — files named `data_001.tif`, `data_002.tif`, `data_003.tif` with `--dim-pattern "data_(\d+)"`
5. **Temporal with date parsing** — files named `2020-01-01.tif`, `2020-01-02.tif`, with `--parse-format "%Y-%m-%d"` and `--time-origin "1970-01-01"`
6. **Dimension metadata** — verify type, direction, unit attributes propagate correctly
7. **Data readback** — open output VRT as multidim, read the array, verify pixel values match input GeoTIFFs
8. **Materialized output** — stack to netCDF or Zarr, verify data integrity
9. **Glob input** — `*.tif` pattern
10. **@filelist input** — text file with paths
11. **Error: mismatched extents** — two files with different sizes → clear error
12. **Error: mismatched CRS** — two files with different SRS → clear error
13. **Error: no matching captures** — regex that fails to match → clear error
14. **Error: value count mismatch** — 3 files but 2 dim values → clear error
15. **Error: no files found** — empty glob → clear error
16. **Non-default band** — `--band 2` on multi-band input

### Test fixtures

No external test fixtures needed. All rasters created in `tmp_path` (pytest fixture). Consider a shared helper function:

```python
def create_test_geotiff(path, xsize=3, ysize=4, value=0, srs_epsg=4326,
                        geotransform=(0, 1, 0, 0, 0, -1)):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(str(path), xsize, ysize, 1, gdal.GDT_Float32)
    ds.SetGeoTransform(geotransform)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(srs_epsg)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    import numpy as np
    band.WriteArray(np.full((ysize, xsize), value, dtype=np.float32))
    ds = None
```

## Reference: vrtstack R Package

The R package (`~/Git/vrtstack/`) generates the same VRT XML structure described above. Key functions:

- `vrt_stack(file_paths, dimension, values, ...)` — main entry point
- `vrt_from_tempfiles(...)` — convenience for temporal stacking with filename date extraction
- Uses `xml2` to build the VRT XML document
- Handles regular vs irregular axis detection
- Supports dimension type/direction/unit metadata
- Date parsing via regex + strptime → numeric days-since-origin

## Notes

- The `<SourceTranspose>-1,0,1</SourceTranspose>` idiom is key: `-1` means "insert a new dimension of size 1 here", then `DestSlab` places each source at its index along that dimension. The VRT reader already handles this correctly.
- Validation of consistent inputs (CRS, extent, resolution) should be on by default but could have a `--no-validate` escape hatch for performance with many files.
- For very large file lists (thousands of COGs), VRT generation should be fast since we only need to read metadata from each file, not pixel data.
- Consider whether the new dimension should be first (axis 0) or last. Convention in netCDF/Zarr is typically `[time, y, x]` — axis 0 is correct.
- The `gdal mdim mosaic` gap for 2D tile mosaicing of classic rasters is a separate issue worth tracking but not addressed here.
