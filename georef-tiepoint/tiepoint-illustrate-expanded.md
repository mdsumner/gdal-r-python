---
title: "GeoTIFF Tiepoints & Georeferencing Models: A Visual Guide"
author: "Michael Sumner"
date: "2026-02-05"
output: html_document
---

# GeoTIFF Tiepoints & Georeferencing Models: A Visual Guide

This document provides a visual and code-based introduction to the different ways raster images can be tied to real-world coordinates. It serves as a companion to the technical analysis in [raster-coordinate.md](raster-coordinate.md).

**Target audience:** Practitioners who want to understand *what* these mechanisms do, not just *how* to use them.

---

## 1. The Fundamental Problem

You have a grid of pixels. You want to know where each pixel is in the real world.

```
Pixel Space (I, J)              Model Space (X, Y)
┌───┬───┬───┬───┐              ┌─────────────────────┐
│0,0│1,0│2,0│3,0│              │                     │
├───┼───┼───┼───┤      ?       │   Where is this     │
│0,1│1,1│2,1│3,1│  ────────►   │   on Earth?         │
├───┼───┼───┼───┤              │                     │
│0,2│1,2│2,2│3,2│              │                     │
└───┴───┴───┴───┘              └─────────────────────┘
```

The question mark is where all the complexity lives. There are multiple ways to answer "where is pixel (I, J) in real-world coordinates?"

---

## 2. The GeoTIFF Tiepoint Structure

In GeoTIFF, a **tiepoint** is six numbers:

$$(I, J, K, X, Y, Z)$$

| Component | Meaning | Typical value |
|-----------|---------|---------------|
| I | Pixel column (horizontal) | 0 for origin |
| J | Pixel row (vertical) | 0 for origin |
| K | Pixel depth (3D) | Almost always 0 |
| X | Easting or Longitude | e.g., 500000 (UTM) |
| Y | Northing or Latitude | e.g., 4500000 (UTM) |
| Z | Elevation | Almost always 0 |

**Plain English:** "Pixel (I, J) corresponds to real-world location (X, Y)."

The K and Z components were designed for future 3D support that never really materialized—you can ignore them for most purposes.

---

## 3. The Transformation Gamut

There are fundamentally different ways to get from pixels to coordinates. These are **not** interchangeable—they have different mathematical properties and different use cases.

### 3.1 Variant A: Scale-Offset (The Simple Case)

**GeoTIFF Tags:** `ModelTiepointTag` (33922) + `ModelPixelScaleTag` (33550)

**Plain English:** "Pin one corner of the image to a known location. Every pixel is exactly the same size. The grid is perfectly aligned with north."

**The Math:**
```
X = X_origin + I × ScaleX
Y = Y_origin - J × ScaleY    (note the minus—Y typically decreases as row increases)
```

**What it looks like:**

```
Real World (Model Space)
     N
     ↑
  ┌──┴──┬─────┬─────┬─────┐
  │ 0,0 │ 1,0 │ 2,0 │ 3,0 │  ← All cells same size
  ├─────┼─────┼─────┼─────┤    Perfect grid
  │ 0,1 │ 1,1 │ 2,1 │ 3,1 │    Aligned to axes
  ├─────┼─────┼─────┼─────┤
  │ 0,2 │ 1,2 │ 2,2 │ 3,2 │
  └─────┴─────┴─────┴─────┘
```

**When to use:** Rectified imagery, map products, anything that's been processed to a standard grid. This is the **baseline** GeoTIFF case and covers 99% of real-world usage.

**GDAL's 6-parameter geotransform** is mathematically equivalent to this (for the non-rotated case).

---

### 3.2 Variant B: Affine Matrix (Rotation & Shear)

**GeoTIFF Tag:** `ModelTransformationTag` (34264)

**Plain English:** "Like Scale-Offset, but the grid can be rotated or skewed. Still perfectly regular—every pixel is the same size and shape."

**The Math:**
A 4×4 matrix (though only 6 parameters matter for 2D):

```
┌ X ┐   ┌ a  b  0  c ┐   ┌ I ┐
│ Y │ = │ d  e  0  f │ × │ J │
│ Z │   │ 0  0  1  0 │   │ K │
└ 1 ┘   └ 0  0  0  1 ┘   └ 1 ┘
```

Where:
- `a` = X scale (with rotation component)
- `e` = Y scale (with rotation component)  
- `b`, `d` = rotation/shear terms
- `c`, `f` = translation (origin offset)

**What it looks like:**

```
Real World (Model Space) - Rotated 30°
        N
        ↑
           ◇───◇───◇───◇
          ╱   ╱   ╱   ╱
         ◇───◇───◇───◇      ← Still regular grid
        ╱   ╱   ╱   ╱         but rotated
       ◇───◇───◇───◇
      ╱   ╱   ╱   ╱
     ◇───◇───◇───◇
```

**When to use:** Satellite imagery with off-nadir viewing angle, aerial photography with heading not aligned to north, any regular grid that's been rotated.

**Key point:** This is still fully deterministic. Given the matrix, you can compute *exactly* where any pixel is.

---

### 3.3 Variant C: Multiple Tiepoints / GCPs (The Problematic Case)

**GeoTIFF Tag:** `ModelTiepointTag` (33922) with multiple entries

**Plain English:** "I'll tell you where several specific pixels are located. You figure out where the pixels in between should go."

**The Math:** There isn't one defined math. That's the problem.

Given N tiepoints, you could:
- Fit a 1st-order polynomial (affine—but then why not just use the matrix?)
- Fit a 2nd-order polynomial (quadratic distortion)
- Fit a 3rd-order polynomial (cubic distortion)
- Use Thin Plate Spline interpolation (rubber-sheet)
- Use something else entirely

**What it looks like:**

```
Real World (Model Space) - Warped/Distorted

    •─────────────•          • = Known tiepoints
     \           /           ? = Where do these pixels go?
      \    ?    /              Different methods give
       \       /               different answers!
        \     /
    •────???────•
```

**⚠️ CRITICAL WARNING:** The GeoTIFF specification explicitly states:

> "This is not a Baseline GeoTIFF implementation, and should not be used for interchange."

And:

> "The definition of associated grid interpolation methods is not in the scope of the current GeoTIFF spec."

**What this means:** If you give someone a GeoTIFF with multiple tiepoints, they have no way to know how to interpret the pixels between those points. Different software will give different answers.

**When to use:** Don't, for interchange. If you must work with unrectified imagery, use GCPs as an intermediate step and then warp to a rectified product.

**Empirical reality:** A survey of GDAL's test suite found **zero** test cases for multiple tiepoints in ModelTiepointTag. The rubber-sheeting use case is handled via sidecar files (ArcGIS .aux.xml) or GDAL's GCP metadata domain—not the GeoTIFF tag.

---

### 3.4 Variant D: PixelIsPoint vs PixelIsArea (Orthogonal to A/B/C)

**GeoTIFF Tag:** `GTRasterTypeGeoKey` (1025)

**Plain English:** "When I give you coordinates for a pixel, am I telling you where the center is, or where the top-left corner is?"

**This is NOT a transformation type.** It's a semantic clarification that applies to *any* of the above transformations.

```
PixelIsArea (default)              PixelIsPoint
Coordinates = corner               Coordinates = center

    X,Y                               
     ↓                                  X,Y
     ┌─────────┐                         ↓
     │         │                     ┌───●───┐
     │  pixel  │                     │   │   │
     │         │                     │ pixel │
     └─────────┘                     └───────┘
```

**The half-pixel shift:** If you interpret PixelIsArea coordinates as PixelIsPoint (or vice versa), you'll be off by half a pixel in both X and Y. For a 30-meter Landsat pixel, that's a 15-meter error.

**CF conventions connection:** This maps (imperfectly) to CF's `bounds` convention for cell edges vs. coordinate variables for cell centers.

---

## 4. Beyond Tiepoints: The Full Georeferencing Landscape

GeoTIFF tiepoints are just one approach. The broader landscape includes:

### 4.1 RPCs (Rational Polynomial Coefficients)

**What they are:** A mathematical "black box" provided by satellite vendors that maps (Latitude, Longitude, **Height**) → (Row, Column).

**Key difference:** RPCs are **fundamentally 3D**. You cannot evaluate an RPC model without knowing the ground elevation at each point. This requires a DEM (Digital Elevation Model).

**Plain English:** "The satellite vendor figured out the complex camera geometry and gave you polynomials. Plug in lat/lon/height, get pixel coordinates. But you need elevation data."

**Not covered here:** RPCs are a separate topic requiring their own treatment.

### 4.2 Geolocation Arrays

**What they are:** Explicit coordinate arrays—literally a longitude value and latitude value stored for every pixel (or a subsampled grid).

**Plain English:** "I'm not giving you a formula. I'm giving you a giant table: pixel (0,0) is at lon/lat X₀/Y₀, pixel (0,1) is at lon/lat X₁/Y₁, and so on for every pixel."

**When used:** Satellite swath data (AVHRR, MODIS, Sentinel-3), weather model output on curvilinear grids, ocean model output on rotated-pole grids.

**Key difference from GCPs:** Geolocation arrays are **complete and unambiguous**. There's no interpolation question—you have the coordinates for every pixel (or can interpolate from a dense subsampled grid).

This is the CF/netCDF world's native approach, where 2D `lat(y,x)` and `lon(y,x)` arrays are the standard way to describe curvilinear grids.

---

## 5. Visual Comparison (Python)

```python
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import Rbf  # For thin-plate spline demo

def generate_comparison_plots():
    """
    Generate a 2x3 figure showing the transformation gamut.
    """
    fig, axs = plt.subplots(2, 3, figsize=(15, 10))
    
    # Create a 5x5 pixel grid
    grid_i, grid_j = np.meshgrid(np.arange(0, 5), np.arange(0, 5))
    
    # =========================================
    # A. Scale-Offset (Standard/Simple)
    # =========================================
    ax = axs[0, 0]
    # Parameters: origin at (100, 100), pixel size 10x10
    origin_x, origin_y = 100, 140
    scale_x, scale_y = 10, 10
    
    m_x = origin_x + grid_i * scale_x
    m_y = origin_y - grid_j * scale_y  # Y decreases with row
    
    ax.scatter(m_x, m_y, c='blue', s=50, zorder=5)
    ax.plot(origin_x, origin_y, 'ro', markersize=12, label='Tiepoint (0,0)', zorder=10)
    
    # Draw grid lines
    for i in range(5):
        ax.plot(m_x[i, :], m_y[i, :], 'b-', alpha=0.3)
        ax.plot(m_x[:, i], m_y[:, i], 'b-', alpha=0.3)
    
    ax.set_title("A: Scale-Offset\n(Single tiepoint + pixel scale)", fontsize=11)
    ax.set_xlabel("Easting (X)")
    ax.set_ylabel("Northing (Y)")
    ax.legend(loc='lower right')
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    
    # =========================================
    # B. Affine Matrix (Rotated)
    # =========================================
    ax = axs[0, 1]
    theta = np.radians(25)  # 25 degree rotation
    
    # Rotation matrix
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    
    # Apply rotation around origin, then translate
    coords_local = np.stack([grid_i.flatten() * scale_x, 
                             -grid_j.flatten() * scale_y])
    rot_matrix = np.array([[cos_t, -sin_t], 
                           [sin_t, cos_t]])
    coords_rot = rot_matrix @ coords_local
    
    m_x_rot = coords_rot[0, :].reshape(grid_i.shape) + origin_x
    m_y_rot = coords_rot[1, :].reshape(grid_i.shape) + origin_y
    
    ax.scatter(m_x_rot, m_y_rot, c='purple', s=50, zorder=5)
    ax.plot(origin_x, origin_y, 'ro', markersize=12, zorder=10)
    
    for i in range(5):
        ax.plot(m_x_rot[i, :], m_y_rot[i, :], 'purple', alpha=0.3)
        ax.plot(m_x_rot[:, i], m_y_rot[:, i], 'purple', alpha=0.3)
    
    ax.set_title("B: Affine Matrix\n(Rotation/shear, still regular)", fontsize=11)
    ax.set_xlabel("Easting (X)")
    ax.set_ylabel("Northing (Y)")
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    
    # =========================================
    # C. Multiple Tiepoints - The Ambiguity Problem
    # =========================================
    ax = axs[0, 2]
    
    # Define 4 corner tiepoints with some distortion
    # These represent "known" control points
    tp_pixel = np.array([[0, 0], [4, 0], [0, 4], [4, 4]])
    tp_model = np.array([
        [100, 140],   # top-left
        [145, 142],   # top-right (slight y offset)
        [98, 98],     # bottom-left (slight x offset)
        [148, 95]     # bottom-right
    ])
    
    # Show tiepoints
    ax.scatter(tp_model[:, 0], tp_model[:, 1], c='red', s=150, 
               marker='*', zorder=10, label='Tiepoints (known)')
    
    # Method 1: 1st order polynomial (affine fit)
    # This would give a simple parallelogram
    from numpy.polynomial import polynomial as P
    # Simplified: just show a polygon connecting the points
    hull_order = [0, 1, 3, 2, 0]
    ax.plot(tp_model[hull_order, 0], tp_model[hull_order, 1], 
            'g--', linewidth=2, label='Affine interp.', alpha=0.7)
    
    # Method 2: Show that interior points are ambiguous
    ax.text(122, 118, '?', fontsize=24, ha='center', va='center', 
            color='orange', fontweight='bold')
    ax.text(122, 108, 'Where do\ninterior pixels\ngo?', 
            fontsize=9, ha='center', va='top', color='gray')
    
    ax.set_title("C: Multiple Tiepoints\n(Ambiguous—interpolation undefined)", fontsize=11)
    ax.set_xlabel("Easting (X)")
    ax.set_ylabel("Northing (Y)")
    ax.legend(loc='lower right', fontsize=9)
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    
    # =========================================
    # D. PixelIsArea vs PixelIsPoint
    # =========================================
    ax = axs[1, 0]
    
    # Show a 3x3 grid with both interpretations
    small_i, small_j = np.meshgrid(np.arange(0, 3), np.arange(0, 3))
    
    # PixelIsArea: coordinates at corners
    area_x = 100 + small_i * 20
    area_y = 140 - small_j * 20
    
    # PixelIsPoint: coordinates at centers (shifted by half pixel)
    point_x = area_x + 10
    point_y = area_y - 10
    
    # Draw pixel boundaries
    for i in range(4):
        ax.axvline(100 + i * 20, color='gray', alpha=0.5, linestyle='-')
        ax.axhline(140 - i * 20, color='gray', alpha=0.5, linestyle='-')
    
    ax.scatter(area_x, area_y, c='blue', s=80, marker='s', 
               label='PixelIsArea (corners)', zorder=5)
    ax.scatter(point_x, point_y, c='red', s=80, marker='o', 
               label='PixelIsPoint (centers)', zorder=5)
    
    # Show the half-pixel offset
    ax.annotate('', xy=(point_x[0,0], point_y[0,0]), 
                xytext=(area_x[0,0], area_y[0,0]),
                arrowprops=dict(arrowstyle='->', color='green', lw=2))
    ax.text(105, 135, '½ pixel\nshift', fontsize=9, color='green')
    
    ax.set_title("D: PixelIsArea vs PixelIsPoint\n(Half-pixel semantic difference)", fontsize=11)
    ax.set_xlabel("Easting (X)")
    ax.set_ylabel("Northing (Y)")
    ax.legend(loc='lower right', fontsize=9)
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    
    # =========================================
    # E. GCP Interpolation Methods Compared
    # =========================================
    ax = axs[1, 1]
    
    # Same 4 tiepoints as C, but now show different interpolation results
    # Use scipy RBF for thin-plate spline approximation
    
    # Create denser grid for visualization
    dense_i, dense_j = np.meshgrid(np.linspace(0, 4, 20), np.linspace(0, 4, 20))
    
    # Thin Plate Spline interpolation
    rbf_x = Rbf(tp_pixel[:, 0], tp_pixel[:, 1], tp_model[:, 0], function='thin_plate')
    rbf_y = Rbf(tp_pixel[:, 0], tp_pixel[:, 1], tp_model[:, 1], function='thin_plate')
    
    tps_x = rbf_x(dense_i, dense_j)
    tps_y = rbf_y(dense_i, dense_j)
    
    ax.scatter(tps_x, tps_y, c='green', s=5, alpha=0.5, label='TPS result')
    ax.scatter(tp_model[:, 0], tp_model[:, 1], c='red', s=150, 
               marker='*', zorder=10, label='Tiepoints')
    
    # Linear interpolation would give different result
    # (simplified—just note the difference)
    ax.text(122, 85, 'TPS vs Polynomial\ngive different\ninterior positions', 
            fontsize=9, ha='center', color='gray', style='italic')
    
    ax.set_title("E: Same GCPs, Different Methods\n(TPS interpolation shown)", fontsize=11)
    ax.set_xlabel("Easting (X)")
    ax.set_ylabel("Northing (Y)")
    ax.legend(loc='upper right', fontsize=9)
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    
    # =========================================
    # F. Geolocation Arrays (Complete Description)
    # =========================================
    ax = axs[1, 2]
    
    # Simulate a curvilinear grid (like satellite swath)
    # Coordinates curve across the image
    curve_i, curve_j = np.meshgrid(np.arange(0, 5), np.arange(0, 5))
    
    # Add curvature
    curve_x = 100 + curve_i * 10 + curve_j * 2  # shear
    curve_y = 140 - curve_j * 10 - 0.5 * (curve_i - 2)**2  # parabolic distortion
    
    ax.scatter(curve_x, curve_y, c='teal', s=50, zorder=5)
    
    # Draw curvilinear grid lines
    for i in range(5):
        ax.plot(curve_x[i, :], curve_y[i, :], 'teal', alpha=0.4)
        ax.plot(curve_x[:, i], curve_y[:, i], 'teal', alpha=0.4)
    
    # Annotate that every point has explicit coordinates
    ax.annotate('Every pixel has\nexplicit (lon,lat)', 
                xy=(curve_x[2,2], curve_y[2,2]), 
                xytext=(135, 130),
                fontsize=9,
                arrowprops=dict(arrowstyle='->', color='gray'))
    
    ax.set_title("F: Geolocation Arrays\n(Explicit coords—no ambiguity)", fontsize=11)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('tiepoint_gamut.png', dpi=150, bbox_inches='tight')
    plt.show()
    
    return fig

# Generate the plots
fig = generate_comparison_plots()
```

---

## 6. Visual Comparison (R)

```r
library(ggplot2)
library(dplyr)
library(tidyr)
library(patchwork)  
library(fields)     # For thin plate spline (Tps)

generate_comparison_plots_r <- function() {
  
  # Create base 5x5 pixel grid
  grid <- expand.grid(i = 0:4, j = 0:4)
  
  # Parameters
  origin_x <- 100
  origin_y <- 140
  scale_xy <- 10
  
  # =========================================
  # A. Scale-Offset (Standard/Simple)
  # =========================================
  df_a <- grid %>%
    mutate(
      x = origin_x + i * scale_xy,
      y = origin_y - j * scale_xy,
      type = "grid"
    )
  
  tiepoint_a <- data.frame(x = origin_x, y = origin_y, type = "tiepoint")
  
  p_a <- ggplot() +
    # Grid lines
    geom_path(data = df_a, aes(x = x, y = y, group = i), alpha = 0.3, color = "blue") +
    geom_path(data = df_a, aes(x = x, y = y, group = j), alpha = 0.3, color = "blue") +
    # Points
    geom_point(data = df_a, aes(x = x, y = y), color = "blue", size = 2) +
    geom_point(data = tiepoint_a, aes(x = x, y = y), color = "red", size = 4) +
    coord_fixed() +
    labs(title = "A: Scale-Offset",
         subtitle = "Single tiepoint + pixel scale",
         x = "Easting (X)", y = "Northing (Y)") +
    theme_minimal() +
    theme(plot.title = element_text(size = 11, face = "bold"))
  
  # =========================================
  # B. Affine Matrix (Rotated)
  # =========================================
  theta <- 25 * pi / 180  # 25 degrees
  
  df_b <- grid %>%
    mutate(
      # Local coordinates
      local_x = i * scale_xy,
      local_y = -j * scale_xy,
      # Apply rotation
      x = cos(theta) * local_x - sin(theta) * local_y + origin_x,
      y = sin(theta) * local_x + cos(theta) * local_y + origin_y
    )
  
  p_b <- ggplot() +
    geom_path(data = df_b, aes(x = x, y = y, group = i), alpha = 0.3, color = "purple") +
    geom_path(data = df_b, aes(x = x, y = y, group = j), alpha = 0.3, color = "purple") +
    geom_point(data = df_b, aes(x = x, y = y), color = "purple", size = 2) +
    geom_point(aes(x = origin_x, y = origin_y), color = "red", size = 4) +
    coord_fixed() +
    labs(title = "B: Affine Matrix",
         subtitle = "Rotation/shear, still regular",
         x = "Easting (X)", y = "Northing (Y)") +
    theme_minimal() +
    theme(plot.title = element_text(size = 11, face = "bold"))
  
  # =========================================
  # C. Multiple Tiepoints - Ambiguity
  # =========================================
  tiepoints_c <- data.frame(
    i = c(0, 4, 0, 4),
    j = c(0, 0, 4, 4),
    x = c(100, 145, 98, 148),
    y = c(140, 142, 98, 95)
  )
  
  # Polygon outline
  hull_order <- c(1, 2, 4, 3, 1)
  polygon_c <- tiepoints_c[hull_order, ]
  
  p_c <- ggplot() +
    geom_polygon(data = polygon_c, aes(x = x, y = y), 
                 fill = NA, color = "darkgreen", linetype = "dashed", linewidth = 1) +
    geom_point(data = tiepoints_c, aes(x = x, y = y), 
               color = "red", size = 5, shape = 8) +
    annotate("text", x = 122, y = 118, label = "?", 
             size = 10, color = "orange", fontface = "bold") +
    annotate("text", x = 122, y = 105, label = "Where do\ninterior pixels go?", 
             size = 3, color = "gray40") +
    coord_fixed() +
    labs(title = "C: Multiple Tiepoints",
         subtitle = "Ambiguous—interpolation undefined",
         x = "Easting (X)", y = "Northing (Y)") +
    theme_minimal() +
    theme(plot.title = element_text(size = 11, face = "bold"))
  
  # =========================================
  # D. PixelIsArea vs PixelIsPoint
  # =========================================
  small_grid <- expand.grid(i = 0:2, j = 0:2)
  
  df_area <- small_grid %>%
    mutate(
      x = 100 + i * 20,
      y = 140 - j * 20,
      type = "PixelIsArea (corners)"
    )
  
  df_point <- small_grid %>%
    mutate(
      x = 100 + i * 20 + 10,  # shifted by half pixel
      y = 140 - j * 20 - 10,
      type = "PixelIsPoint (centers)"
    )
  
  df_d <- bind_rows(df_area, df_point)
  
  # Grid lines for pixel boundaries
  grid_lines_v <- data.frame(x = seq(100, 160, by = 20))
  grid_lines_h <- data.frame(y = seq(80, 140, by = 20))
  
  p_d <- ggplot() +
    geom_vline(data = grid_lines_v, aes(xintercept = x), color = "gray70", alpha = 0.5) +
    geom_hline(data = grid_lines_h, aes(yintercept = y), color = "gray70", alpha = 0.5) +
    geom_point(data = df_d, aes(x = x, y = y, color = type, shape = type), size = 3) +
    geom_segment(aes(x = 100, y = 140, xend = 110, yend = 130),
                 arrow = arrow(length = unit(0.2, "cm")), color = "forestgreen") +
    annotate("text", x = 108, y = 138, label = "½ pixel\nshift", 
             size = 3, color = "forestgreen") +
    scale_color_manual(values = c("PixelIsArea (corners)" = "blue", 
                                   "PixelIsPoint (centers)" = "red")) +
    scale_shape_manual(values = c("PixelIsArea (corners)" = 15, 
                                   "PixelIsPoint (centers)" = 16)) +
    coord_fixed() +
    labs(title = "D: PixelIsArea vs PixelIsPoint",
         subtitle = "Half-pixel semantic difference",
         x = "Easting (X)", y = "Northing (Y)",
         color = NULL, shape = NULL) +
    theme_minimal() +
    theme(plot.title = element_text(size = 11, face = "bold"),
          legend.position = "bottom")
  
  # =========================================
  # E. GCP Interpolation Methods Compared (TPS)
  # =========================================
  # Use fields::Tps for thin plate spline
  tp_pixel <- cbind(c(0, 4, 0, 4), c(0, 0, 4, 4))
  tp_model_x <- c(100, 145, 98, 148)
  tp_model_y <- c(140, 142, 98, 95)
  
  # Fit TPS
  tps_x <- Tps(tp_pixel, tp_model_x)
  tps_y <- Tps(tp_pixel, tp_model_y)
  
  # Predict on dense grid
  dense_grid <- expand.grid(i = seq(0, 4, length.out = 20),
                            j = seq(0, 4, length.out = 20))
  dense_matrix <- as.matrix(dense_grid)
  
  df_tps <- data.frame(
    x = predict(tps_x, dense_matrix),
    y = predict(tps_y, dense_matrix)
  )
  
  p_e <- ggplot() +
    geom_point(data = df_tps, aes(x = x, y = y), color = "forestgreen", 
               size = 0.5, alpha = 0.5) +
    geom_point(data = tiepoints_c, aes(x = x, y = y), 
               color = "red", size = 5, shape = 8) +
    annotate("text", x = 122, y = 85, 
             label = "TPS vs Polynomial\ngive different results", 
             size = 3, color = "gray40", fontface = "italic") +
    coord_fixed() +
    labs(title = "E: Same GCPs, Different Methods",
         subtitle = "TPS interpolation shown",
         x = "Easting (X)", y = "Northing (Y)") +
    theme_minimal() +
    theme(plot.title = element_text(size = 11, face = "bold"))
  
  # =========================================
  # F. Geolocation Arrays (Curvilinear)
  # =========================================
  df_f <- grid %>%
    mutate(
      # Add curvature to simulate swath data
      x = 100 + i * 10 + j * 2,  # shear
      y = 140 - j * 10 - 0.5 * (i - 2)^2  # parabolic
    )
  
  p_f <- ggplot() +
    geom_path(data = df_f, aes(x = x, y = y, group = i), alpha = 0.4, color = "teal") +
    geom_path(data = df_f, aes(x = x, y = y, group = j), alpha = 0.4, color = "teal") +
    geom_point(data = df_f, aes(x = x, y = y), color = "teal", size = 2) +
    annotate("text", x = 135, y = 130, 
             label = "Every pixel has\nexplicit (lon,lat)", 
             size = 3, color = "gray40") +
    geom_segment(aes(x = 130, y = 128, xend = 118, yend = 120),
                 arrow = arrow(length = unit(0.15, "cm")), color = "gray50") +
    coord_fixed() +
    labs(title = "F: Geolocation Arrays",
         subtitle = "Explicit coords—no ambiguity",
         x = "Longitude", y = "Latitude") +
    theme_minimal() +
    theme(plot.title = element_text(size = 11, face = "bold"))
  
  # Combine all plots
  combined <- (p_a | p_b | p_c) / (p_d | p_e | p_f)
  
  ggsave("tiepoint_gamut_r.png", combined, width = 15, height = 10, dpi = 150)
  
  return(combined)
}

# Generate the plots
fig <- generate_comparison_plots_r()
print(fig)
```

---

## 7. GDAL Implementation: GCPs to Warped Output

### Python (osgeo.gdal)

```python
from osgeo import gdal, osr

def warp_with_gcps(input_path, output_path, gcps, epsg_code, method='polynomial'):
    """
    Apply GCPs to an image and warp to a georeferenced output.
    
    Parameters:
    -----------
    input_path : str
        Path to unrectified input image
    output_path : str  
        Path for output (GeoTIFF or VRT)
    gcps : list of tuples
        Each tuple: (model_x, model_y, model_z, pixel_col, pixel_row)
    epsg_code : int
        Target coordinate system EPSG code
    method : str
        'polynomial' (order 1-3) or 'tps' (thin plate spline)
    """
    
    # 1. Open source
    src_ds = gdal.Open(input_path, gdal.GA_ReadOnly)
    if src_ds is None:
        raise ValueError(f"Cannot open {input_path}")
    
    # 2. Create GCP objects
    gcp_list = []
    for model_x, model_y, model_z, pixel_col, pixel_row in gcps:
        gcp = gdal.GCP(model_x, model_y, model_z, pixel_col, pixel_row)
        gcp_list.append(gcp)
    
    # 3. Define target CRS
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg_code)
    wkt = srs.ExportToWkt()
    
    # 4. Apply GCPs to source dataset
    src_ds.SetGCPs(gcp_list, wkt)
    
    # 5. Warp to output
    if method == 'tps':
        warp_options = gdal.WarpOptions(
            format='GTiff',
            tps=True,
            dstSRS=wkt,
            resampleAlg=gdal.GRA_Bilinear
        )
    else:
        # Default polynomial (order determined by number of GCPs)
        warp_options = gdal.WarpOptions(
            format='GTiff',
            polynomialOrder=1,  # Use 2 or 3 for higher-order fits
            dstSRS=wkt,
            resampleAlg=gdal.GRA_Bilinear
        )
    
    gdal.Warp(output_path, src_ds, options=warp_options)
    
    print(f"Warped output written to {output_path}")


# Example usage
if __name__ == "__main__":
    # Define 4 corner GCPs for a 1000x1000 pixel image
    # Format: (easting, northing, elevation, pixel_column, pixel_row)
    example_gcps = [
        (440750, 3750950, 0, 0, 0),        # top-left
        (441150, 3750950, 0, 1000, 0),     # top-right  
        (440750, 3750550, 0, 0, 1000),     # bottom-left
        (441150, 3750550, 0, 1000, 1000),  # bottom-right
    ]
    
    # Note: 4 corner GCPs = mathematically equivalent to affine transform
    # For true rubber-sheeting, you'd use 8+ scattered GCPs
    
    warp_with_gcps(
        input_path="unrectified.tif",
        output_path="rectified.tif",
        gcps=example_gcps,
        epsg_code=32611,  # UTM zone 11N
        method='polynomial'
    )
```

### R (using gdalraster)
        
```r
library(gdalraster)

warp_with_gcps_r <- function(input_path, output_path, gcps_df, epsg_code, 
                              method = "polynomial", order = 1) {
  #' Apply GCPs to an image and warp to georeferenced output

#'
#' @param input_path Path to unrectified input image
#' @param output_path Path for output GeoTIFF
#' @param gcps_df Data frame with columns: pixel_col, pixel_row, model_x, model_y, model_z
#' @param epsg_code Target EPSG code
#' @param method "polynomial" or "tps"
#' @param order Polynomial order (1, 2, or 3) if method = "polynomial"
  
  # Build GCP string for gdal_translate
  # Format: -gcp pixel_col pixel_row model_x model_y [model_z]
  gcp_args <- character(nrow(gcps_df))
  for (i in seq_len(nrow(gcps_df))) {
    gcp_args[i] <- sprintf("-gcp %f %f %f %f %f",
                           gcps_df$pixel_col[i],
                           gcps_df$pixel_row[i],
                           gcps_df$model_x[i],
                           gcps_df$model_y[i],
                           gcps_df$model_z[i])
  }
  
  # Create intermediate VRT with GCPs attached
  temp_vrt <- tempfile(fileext = ".vrt")
  
  translate_args <- c(
    "-of", "VRT",
    "-a_srs", sprintf("EPSG:%d", epsg_code),
    gcp_args,
    input_path,
    temp_vrt
  )
  
  # Use gdalraster's translate function or system call
  # gdalraster approach:
  translate(input_path, temp_vrt, cl_arg = c(
    "-of", "VRT",
    "-a_srs", sprintf("EPSG:%d", epsg_code),
    unlist(strsplit(paste(gcp_args, collapse = " "), " "))
  ))
  
  # Now warp the VRT to final output
  if (method == "tps") {
    warp_args <- c("-tps")
  } else {
    warp_args <- c(sprintf("-order %d", order))
  }
  
  warp(
    src_files = temp_vrt,
    dst_filename = output_path,
    t_srs = sprintf("EPSG:%d", epsg_code),
    cl_arg = c(warp_args, "-r", "bilinear")
  )
  
  # Clean up
  unlink(temp_vrt)
  
  message("Warped output written to ", output_path)
}


# Example usage
example_gcps <- data.frame(
  pixel_col = c(0, 1000, 0, 1000),
  pixel_row = c(0, 0, 1000, 1000),
  model_x = c(440750, 441150, 440750, 441150),
  model_y = c(3750950, 3750950, 3750550, 3750550),
  model_z = c(0, 0, 0, 0)
)

# Note: This is a simplified example. 
# In practice, you may need to handle the GCP attachment and warping
# through separate gdal_translate and gdalwarp calls, or use vapour/sf.

# Alternative using system calls (more direct):
warp_with_gcps_system <- function(input_path, output_path, gcps_df, epsg_code,
                                   method = "polynomial", order = 1) {
  
  # Build GCP arguments
  gcp_args <- vapply(seq_len(nrow(gcps_df)), function(i) {
    sprintf("-gcp %f %f %f %f", 
            gcps_df$pixel_col[i], gcps_df$pixel_row[i],
            gcps_df$model_x[i], gcps_df$model_y[i])
  }, character(1))
  
  # Step 1: Attach GCPs via gdal_translate to VRT
  temp_vrt <- tempfile(fileext = ".vrt")
  translate_cmd <- sprintf(
    "gdal_translate -of VRT -a_srs EPSG:%d %s %s %s",
    epsg_code,
    paste(gcp_args, collapse = " "),
    input_path,
    temp_vrt
  )
  system(translate_cmd)
  
# Step 2: Warp
  method_arg <- if (method == "tps") "-tps" else sprintf("-order %d", order)
  warp_cmd <- sprintf(
    "gdalwarp %s -t_srs EPSG:%d -r bilinear %s %s",
    method_arg,
    epsg_code,
    temp_vrt,
    output_path
  )
  system(warp_cmd)
  
  unlink(temp_vrt)
  message("Done: ", output_path)
}
```

---
        
## 8. Summary: The Decision Tree

When encountering georeferenced raster data, ask:

```
Is there a geotransform / affine matrix?
├── YES → Simple case. Math is deterministic. You're done.
│
└── NO → What georeferencing info exists?
         │
         ├── Single tiepoint + scale → Equivalent to affine. You're done.
         │
         ├── Multiple tiepoints / GCPs
         │   └── ⚠️ AMBIGUOUS. You need to choose an interpolation method.
         │       Ask: What method was intended? Is there documentation?
         │       For interchange: WARP FIRST, then share the rectified product.
         │
         ├── RPCs
         │   └── You need a DEM. Without elevation, you cannot resolve coordinates.
         │
         └── Geolocation arrays (2D lat/lon)
             └── Complete description. Coordinates are explicit for every pixel.
                 Common in satellite swath data and model output.
```

---

## 9. References

- [Raster-to-Model Coordinate Transformations: A Historical and Technical Analysis](raster-coordinate.md) — The companion technical document
- GeoTIFF Specification 1.0 (1995): http://geotiff.maptools.org/spec/geotiffhome.html
- OGC GeoTIFF Standard 1.1: https://docs.ogc.org/is/19-008r4/19-008r4.html
- GDAL Geotransform Tutorial: https://gdal.org/tutorials/geotransforms_tut.html
- GDAL RFC 4 (Geolocation Arrays): https://gdal.org/development/rfc/rfc4_geolocate.html
- GDAL RFC 22 (RPC): https://gdal.org/development/rfc/rfc22_rpc.html
