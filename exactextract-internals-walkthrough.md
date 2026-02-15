# How exactextract computes exact coverage fractions

Generated as a prompt to a potential refactor of gridburn to be folded in to a final package (fasterize, controlledburn, gridburn ->> denseburn): https://github.com/mdsumner/gridburn/blob/main/inst/docs-design/denseburn-refactor-design.md


This document traces the path a polygon takes through the exactextract algorithm as vendored in the [gridburn](https://github.com/hypertidy/gridburn) R package. The goal is to understand exactly where dense matrices are allocated, how polygon rings (including holes) are processed, and why memory usage is proportional to the polygon's bounding box rather than the full raster grid.

The exactextract algorithm was written by Daniel Baston and is part of the [exactextract](https://github.com/isciences/exactextract) project. gridburn vendors 27 C++ source files from exactextract's core, patching only the GEOS header includes to use [libgeos](https://github.com/paleolimbot/libgeos) instead of a system GEOS installation.

## Overview

The algorithm takes a raster grid definition and a polygon geometry, and returns a matrix of coverage fractions — one float per cell in the polygon's bounding box subgrid. Each value is the fraction of that cell's area covered by the polygon, ranging from 0.0 (fully outside) to 1.0 (fully inside), with exact partial coverage on boundary cells.

The core steps are:

1. Compute the polygon's bounding box subgrid within the raster
2. Allocate a dense float matrix for the subgrid
3. Walk each polygon ring's vertices through the grid, cell by cell
4. Compute exact coverage fractions where edges cross cells
5. Flood fill the interior to mark fully-covered cells
6. For polygons with holes, subtract hole ring coverage from exterior ring coverage

gridburn then compresses this dense matrix to a sparse two-table format (interior runs + boundary edges) and discards the dense buffer.

## Step 1: The Grid class — metadata, not data

The `Grid<extent_tag>` template (`grid.h`) stores five values:

```cpp
Box m_extent;    // xmin, ymin, xmax, ymax
double m_dx;     // cell width
double m_dy;     // cell height
size_t m_num_rows;
size_t m_num_cols;
```

No data array. It's a coordinate system description that can answer "which row/col contains this (x, y)?" via `get_row()` and `get_column()`.

There are two extent tag types:

- `bounded_extent` (padding = 0): used for the user-facing raster grid, dimensions exactly match the cell count
- `infinite_extent` (padding = 1): used internally, adds one phantom cell on each side so that polygon edges that graze the grid boundary can still be tracked

The `shrink_to_fit(Box)` method returns a new `Grid` cropped to the intersection of the grid with a bounding box, snapping to cell boundaries. This is how the polygon's subgrid is computed — same cell size as the full raster, but only covering the polygon's bounding box.

## Step 2: The dense matrix allocation

The `RasterCellIntersection` constructor (`raster_cell_intersection.cpp`) does two things in its initializer list:

```cpp
RasterCellIntersection(raster_grid, context, g)
    : m_geometry_grid{get_geometry_grid(raster_grid, context, g)},
      m_results{std::make_unique<Matrix<float>>(
          m_geometry_grid.rows() - 2,
          m_geometry_grid.cols() - 2)},
```

**`get_geometry_grid()`** calls `geos_get_component_boxes()` to get the bounding box of each geometry component, intersects the union of those boxes with the raster extent, then shrinks the raster grid to fit. The result is an `infinite_extent` grid — the polygon's bbox in grid coordinates, plus 1-cell padding.

**`m_results`** is the first real allocation. `Matrix<float>` (`matrix.h`) stores a flat `unique_ptr<float[]>`:

```cpp
Matrix(size_t rows, size_t cols) {
    m_data = std::unique_ptr<T[]>(new T[m_rows * m_cols]());
    // () means zero-initialized
}
```

The dimensions are the geometry grid minus padding: if the polygon's bbox spans 500 × 300 cells of the raster, `m_results` is 500 × 300 floats = 600 KB. This matrix lives for the entire lifetime of the `RasterCellIntersection` object and accumulates coverage fractions across all rings.

This is the allocation that causes `bad_alloc` for large polygons on fine grids. A polygon whose bbox fills a 256,000 × 128,000 grid would require `256000 × 128000 × 4 bytes ≈ 122 GB`.

## Step 3: Processing polygon rings

The `process()` method dispatches by geometry type:

```cpp
void RasterCellIntersection::process(context, g) {
    // GEOS_POLYGON:
    process_line(context, GEOSGetExteriorRing_r(context, g), true);
    for (int i = 0; i < GEOSGetNumInteriorRings_r(context, g); i++) {
        process_line(context, GEOSGetInteriorRingN_r(context, g, i), false);
    }
    // GEOS_MULTIPOLYGON / GEOS_GEOMETRYCOLLECTION: recurse
}
```

The `exterior_ring` boolean controls the sign: exterior rings add coverage, interior rings (holes) subtract it.

## Step 4: The cell traversal — `process_line()`

This is the heart of the algorithm. For each ring:

### 4a. Compute ring subgrid

```cpp
Grid<infinite_extent> ring_grid = get_ring_grid(context, ls, m_geometry_grid);
```

The ring's bounding box is intersected with the geometry grid and snapped to cell boundaries. For the exterior ring this is usually the same as the geometry grid. For holes it can be much smaller.

### 4b. Fast path for rectangles

```cpp
if (m_areal && coords.size() == 5) {
    if (area(coords) == geom_box.area()) {
        process_rectangular_ring(geom_box, exterior_ring);
        return;
    }
}
```

Axis-aligned rectangles skip the cell traversal entirely and compute corner, edge, and interior fractions analytically.

### 4c. Allocate the Cell pointer matrix

```cpp
Matrix<std::unique_ptr<Cell>> cells(rows, cols);
```

One pointer per cell in the ring's subgrid. Initially all null — `Cell` objects are lazily allocated only for cells the polygon edge actually passes through:

```cpp
static Cell* get_cell(Matrix<std::unique_ptr<Cell>>& cells,
                      const Grid<infinite_extent>& ex,
                      size_t row, size_t col) {
    if (cells(row, col) == nullptr) {
        cells(row, col) = std::make_unique<Cell>(grid_cell(ex, row, col));
    }
    return cells(row, col).get();
}
```

Memory for the pointer matrix is `rows × cols × 8 bytes` (one pointer per cell), but actual `Cell` objects are proportional to the polygon's perimeter, not area.

### 4d. Walk the coordinate sequence

```cpp
while (pos < coords.size()) {
    Cell& cell = *get_cell(cells, ring_grid, row, col);

    while (pos < coords.size()) {
        cell.take(*next_coord, prev_coord);

        if (cell.last_traversal().exited()) {
            // follow exit to adjacent cell
            break;
        } else {
            pos++;
        }
    }

    cell.force_exit();
    // move to adjacent cell based on exit side (TOP/BOTTOM/LEFT/RIGHT)
}
```

Starting from the first vertex, the algorithm feeds coordinates to the current `Cell`. Each `Cell` tracks line segments entering and leaving it and computes the exact area of the polygon slice within that cell. When a segment crosses a cell boundary, the traversal "exits" to the adjacent cell and continues.

If the ring's winding order is clockwise (not CCW), the coordinates are reversed before traversal — the algorithm requires consistent winding for the signed-area computation.

### 4e. Compute the areas matrix

After the walk, a third matrix is allocated:

```cpp
Matrix<float> areas(rows - 2, cols - 2, fill_values<float>::FILLABLE);
```

Initialized to `-1.0` (FILLABLE), meaning "position relative to polygon unknown, can be determined by flood fill". Then for each cell that was traversed:

```cpp
for (size_t i = 1; i <= areas.rows(); i++) {
    for (size_t j = 1; j <= areas.cols(); j++) {
        if (cells(i, j) != nullptr) {
            auto frac = static_cast<float>(cells(i, j)->covered_fraction());
            if (frac == 0) {
                // Edge passed through but zero coverage — ambiguous,
                // need point-in-polygon test
                areas(i-1, j-1) = ff.cell_is_inside(i-1, j-1)
                    ? INTERIOR : EXTERIOR;
            } else {
                areas(i-1, j-1) = frac;
            }
        }
    }
}
```

At this point, `areas` has exact fractions where edges crossed, `1.0` or `0.0` for ambiguous edge cells resolved by point-in-polygon, and `-1.0` everywhere else.

## Step 5: Flood fill

```cpp
FloodFill ff(context, ls, make_finite(ring_grid));
ff.flood(areas);
```

The flood fill walks the `areas` matrix. When it encounters a FILLABLE cell (`-1.0`), it does one `cell_is_inside()` test using a GEOS `PreparedGeometry` point-in-polygon query, then scanline-fills the entire connected FILLABLE region with either `1.0` (INTERIOR) or `0.0` (EXTERIOR).

This is efficient: one GEOS call per connected region, not per cell. For a simple convex polygon, the exterior is one connected region and the interior is another — two GEOS calls total, regardless of grid resolution.

The scanline fill (`floodfill.h`) uses a BFS queue:

```cpp
void flood_from_pixel(Matrix<T>& arr, size_t i, size_t j, T fill_value) {
    std::queue<std::pair<size_t, size_t>> locations;
    locations.emplace(i, j);

    while (!locations.empty()) {
        // fill along current row, enqueue adjacent FILLABLE cells above/below
    }
}
```

## Step 6: Ring accumulation

After flood fill, the ring's `areas` matrix is merged into the geometry-wide `m_results`:

```cpp
void add_ring_results(size_t i0, size_t j0,
                      const Matrix<float>& areas, bool exterior_ring) {
    int factor = exterior_ring ? 1 : -1;
    for (size_t i = 0; i < areas.rows(); i++) {
        for (size_t j = 0; j < areas.cols(); j++) {
            m_results->increment(i0 + i, j0 + j, factor * areas(i, j));
        }
    }
}
```

Exterior rings contribute `+areas`, interior rings (holes) contribute `-areas`. A donut polygon works because the exterior ring fills the whole bbox to 1.0, then the hole ring subtracts the hole region back to 0.0. The ring's local coordinates are offset by `(i0, j0)` — the ring grid's position within the geometry grid.

## Step 7: Return and compress

The free function wraps the result:

```cpp
Raster<float> raster_cell_intersection(...) {
    RasterCellIntersection rci(raster_grid, context, g);
    return { std::move(rci.results()), make_finite(rci.m_geometry_grid) };
}
```

The `Matrix<float>` is move-constructed into a `Raster<float>` (a matrix paired with its grid). Back in `gridburn.cpp`, we compute the subgrid's offset within the full raster, copy the values to a contiguous buffer, and pass it to `dense_to_sparse()`.

`dense_to_sparse()` scans row-by-row, left to right:

- `w >= 1.0 - tol` → extend or start an interior **run** (RLE)
- `0 < w < 1` → emit a boundary **edge** with its weight
- `w <= 0` → skip (outside)

The result is two tables: runs (row, col_start, col_end, id) and edges (row, col, weight, id). These are the gridburn output — the dense matrix is discarded.

## Memory summary

For one polygon on one tile, the peak allocations are:

| Allocation | Type | Size | Lifetime |
|---|---|---|---|
| `m_results` | `Matrix<float>` | subgrid_rows × subgrid_cols × 4B | Entire RCI object |
| `cells` | `Matrix<unique_ptr<Cell>>` | ring_rows × ring_cols × 8B (pointers) | Per ring |
| `areas` | `Matrix<float>` | ring_rows × ring_cols × 4B | Per ring |
| `buf` | `vector<float>` | subgrid_rows × subgrid_cols × 4B | gridburn copy step |

The dominant cost is `m_results`, sized to the polygon's bounding box intersection with the raster grid. For gridburn's default tile size of 4096, the worst case is `4096 × 4096 × 4 = 64 MB`.

## The controlledburn aspiration

The exactextract algorithm is elegant and exact, but fundamentally allocates a dense matrix proportional to the polygon's bounding box area. The `cells` pointer matrix and `Cell` objects are proportional to perimeter, but `m_results` and the flood fill operate on the full bbox.

A true sparse algorithm would walk the polygon edges analytically and emit runs and edges directly — never materializing a dense buffer at all. The coverage fraction computation (the `Cell` traversal) already works per-cell and is inherently sparse. The challenge is replacing the flood fill with an analytical interior detection that doesn't require a dense matrix. This is the aspiration behind the name **controlledburn** — a future algorithm that burns only where it needs to, cell by cell.
