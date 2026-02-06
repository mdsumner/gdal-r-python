---
title: "Untitled"
author: "Michael Sumner"
date: "2026-02-04"
output: html_document
---


# GeoTIFF Tiepoint Model & Transformation Gamut

This document provides a comprehensive technical overview of the GeoTIFF Tiepoint Model, its mathematical variants, and programmatic implementation using `osgeo.gdal`.

---

## 1. The Core Data Structure
In the GeoTIFF specification, a tiepoint is defined by six double-precision values:
$$(I, J, K, X, Y, Z)$$
*   **$(I, J, K)$**: Raster Space (Pixel column, row, and usually $0$ for $K$).
*   **$(X, Y, Z)$**: Model Space (Easting/Longitude, Northing/Latitude, and Elevation).

---

## 2. The Full GeoTIFF Transformation Gamut
There are four primary ways GeoTIFFs handle the mapping from pixels to the real world.

### Variant A: Scale-Offset (Standard)
*   **Tags**: `ModelTiepointTag` (33922) + `ModelPixelScaleTag` (33550).
*   **Logic**: A single point (usually $0,0$) anchors the image; the scale defines the grid size.
*   **Math**: $X = X_{off} + I \cdot Scale_x$

### Variant B: Affine Matrix
*   **Tag**: `ModelTransformationTag` (34264).
*   **Logic**: A $4 \times 4$ matrix handles translation, scale, rotation, and shear.
*   **Math**: Matrix multiplication against a vector of pixel coordinates.

### Variant C: Multi-Tiepoint (GCPs)
*   **Tag**: `ModelTiepointTag` (33922) with multiple entries.
*   **Logic**: A sparse set of points anchors the image. Software interpolates the spaces between.
*   **Use Case**: Non-linear distortions or unrectified aerial imagery.

### Variant D: Raster Space (PixelIsPoint vs PixelIsArea)
*   **Tag**: `GTRasterTypeGeoKey` (1025).
*   **Logic**: Defines if the coordinate refers to the **center** of the pixel (Point) or the **top-left corner** (Area). This creates a half-pixel shift ($0.5$ offset).

---

## 3. Visualising the Gamut (Python Illustration)

```python
import matplotlib.pyplot as plt
import numpy as np

def generate_gamut_plots():
    fig, axs = plt.subplots(2, 2, figsize=(12, 10))
    grid_i, grid_j = np.meshgrid(np.arange(0, 5), np.arange(0, 5))
    
    # A. Scale-Offset (Rigid)
    ax = axs[0, 0]
    m_x, m_y = 100 + grid_i * 10, 100 - grid_j * 10
    ax.scatter(m_x, m_y, c='blue')
    ax.plot(100, 100, 'ro', label='Tiepoint (0,0)')
    ax.set_title("A: Scale-Offset (Standard)")
    ax.legend()

    # B. Affine Matrix (Rotation/Shear)
    ax = axs[0, 1]
    theta = np.radians(30)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    coords = np.stack([grid_i.flatten() * 10, -grid_j.flatten() * 10])
    m_rot = rot @ coords
    ax.scatter(m_rot[0,:] + 100, m_rot[1,:] + 100, c='purple')
    ax.set_title("B: Affine Matrix (Rotated)")

    # C. Multi-Tiepoint (GCP/Rubber-sheet)
    ax = axs[1, 0]
    m_dist_x = 100 + grid_i * 10 + np.random.normal(0, 2, grid_i.shape)
    m_dist_y = 100 - grid_j * 10 + np.random.normal(0, 2, grid_j.shape)
    ax.scatter(m_dist_x, m_dist_y, c='green')
    indices = [(0,0), (0,4), (4,0), (4,4)]
    for r, c in indices:
        ax.plot(m_dist_x[r,c], m_dist_y[r,c], 'ro')
    ax.set_title("C: Multi-Tiepoint (GCP Warp)")

    # D. PixelIsPoint vs PixelIsArea
    ax = axs[1, 1]
    ax.scatter(grid_i * 10, -grid_j * 10, c='blue', alpha=0.3, label='Area (Corner)')
    ax.scatter(grid_i * 10 + 5, -grid_j * 10 - 5, c='red', marker='x', label='Point (Center)')
    ax.set_title("D: Raster Space (0.5 Pixel Shift)")
    ax.legend()

    plt.tight_layout()
    plt.show()

generate_gamut_plots()
```

Implementation GCPs to Warped VRT

```python
from osgeo import gdal, osr

# 1. Open source unrectified data
src_ds = gdal.Open("source.tif", gdal.GA_ReadOnly)

# 2. Define Tiepoints (GCPs)
gcps = [
    gdal.GCP(440750, 3750950, 0, 0, 0),
    gdal.GCP(441150, 3750950, 0, 1000, 0),
    gdal.GCP(440750, 3750550, 0, 0, 1000),
    gdal.GCP(441150, 3750550, 0, 1000, 1000)
]

# 3. Define Projection
srs = osr.SpatialReference()
srs.ImportFromEPSG(32611)
wkt = srs.ExportToWkt()

# 4. Apply GCPs
src_ds.SetGCPs(gcps, wkt)

# 5. Create a Warped VRT
vrt_ds = gdal.AutoCreateWarpedVRT(src_ds, None, wkt, gdal.GRA_Bilinear)
gdal.GetDriverByName("VRT").CreateCopy("rectified_output.vrt", vrt_ds)

```
