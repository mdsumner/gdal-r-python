---
name: "reprojection-window-queries"
description: "Use when working out which source pixels, tiles or overview level a view in another CRS needs (tile planning, COG level selection, bbox transforms, warp windows), especially polar or global data."
---

# Reprojection window queries

The question: for a view in some CRS (an extent, plus the size of one
screen pixel), which part of a source grid in another CRS is needed, and
at what resolution (which overview level)? GDAL's warper answers this every
time it runs. A tiled-map planner (aobcore cog_plan, a browser tile
traversal) has to answer it too. Most bugs come from answering it with a
bounding box when the transform is not close to affine.

## Two directions

- Inverse (the warp direction): start from the output window, map its
  points back into the source CRS, and take the source window that covers
  them. GDAL does this in GDALWarpOperation::ComputeSourceWindow for every
  output chunk.
- Forward (the mesh direction): map source points (a lattice per tile) into
  the view CRS, then keep what lands in the view. aobcore's cog_plan works
  this way. So does GDALSuggestedWarpOutput2, which forward-transforms the
  source edges to propose an output extent and resolution.

A bounding-box transform is the smallest possible case of either: 4
corners in, 4 corners out. It is exact only for an affine transform. For
anything curved it under-covers (the edges bow out between corners) or
misses what lies inside (a pole, the antimeridian).

## What GDAL does (and why)

- Densify, never corners only. ComputeSourceWindow samples points along
  each edge of the output window (SAMPLE_STEPS, default 21 per edge) and
  inverse-transforms them. OCTTransformBounds (GDAL 3.4+, PROJ's
  proj_trans_bounds) does the same for bbox transforms, with densify_pts
  defaulting to 21.
- Fall back to the whole grid. When edge points fail to transform, or the
  edge alone cannot bound the interior (a pole inside the window), GDAL
  samples a full grid of points (SAMPLE_GRID=YES, forced automatically
  when too many edge points fail).
- Pad. SOURCE_EXTRA adds source pixels around the window for resampling
  kernels and residual curvature.
- Poles and the antimeridian. OCTTransformBounds checks whether a pole
  lies inside the input bounds and extends the output to include it. For
  geographic output it may return xmin > xmax to mean "crosses the
  antimeridian". Callers must handle both.
- Resolution. GDALSuggestedWarpOutput2 keeps about the same number of
  pixels along the diagonal: output pixel = transformed diagonal length
  divided by source diagonal pixels. That is one global number, fine for
  near-affine cases and poor when scale varies hugely across the grid.
- Overview choice. The warper (-ovr AUTO, the default) picks the overview
  whose resolution is the closest one not coarser than what the output
  needs, from the ratio of source window size to output window size.

## Failure modes seen in practice

- Mean scale swamped by a singularity. A global lon/lat grid measured in
  a polar stereographic view (EPSG:3031) has pixels that grow without
  bound toward the far pole. A mean or total-area pixel size came out
  around 1e20 m per pixel, so "coarsest sufficient level" never matched
  and the renderer always fetched full resolution (aobcore #15). Use a
  robust statistic (the median cell), or measure the scale locally at the
  view.
- Far-side tiles. The same grid's far polar cap projects to enormous,
  meaningless footprints and forces the densest mesh on every tile. Drop
  tiles whose least-stretched pixel exceeds k times the level's typical
  pixel (aobcore max_stretch, default 8), or clip to the view CRS's area
  of use.
- Seams. A tile that crosses the antimeridian, or contains the pole,
  becomes a streak in lon/lat output when its vertices are projected one
  by one (aobcore #14). Split the lattice at the seam, or detect triangles
  whose projected x-span exceeds half the world width.
- Transform failures are data, not errors. Non-finite results mark points
  outside the target's domain. Drop them, count them, and say so; never
  let one NaN poison a min/max.

## Recipe: the inverse query for a view

Given a view extent V in CRS T, a device pixel size u (T units), and a
source grid in CRS S with levels:

1. Sample V as a grid, not only its edges (for example 17 x 17 points;
   more for large or strongly curved views).
2. Inverse-transform the samples T to S. Drop non-finite results.
3. Check the singular points: forward-transform each pole (and, for a
   geographic source, points on the antimeridian) S to T. If a pole lands
   inside V, the source window must include that pole's full row or
   column of longitudes.
4. Build the source window from the surviving points, with antimeridian
   awareness: if the longitudes span more than 180 degrees and a split is
   plausible, test the two-piece window [xmin, 180] + [-180, xmax].
   Add padding (a few source pixels).
5. Estimate the needed source resolution locally: map one device pixel
   at several sample points back to S (finite differences, the local
   Jacobian), and take a low quantile or the median. Do not use the
   extent ratio when the scale varies a lot across V.
6. Pick the level: the coarsest whose pixel is no larger than that
   estimate (or the nearest, by rule).
7. Intersect the window with that level's tile grid to get tile indices,
   then byte ranges.

The forward (mesh) direction needs the same care: project a lattice per
tile, refine it until the triangles are within tolerance of the exact
projection, and measure pixel size robustly.

## Quick checks when a view is wrong

- Did anything transform only 4 corners? Densify.
- Is there a pole or the antimeridian inside the view or the source? Test
  it explicitly.
- Is one pixel-size number standing in for a field that varies by orders
  of magnitude? Use the median or a local estimate.
- Are failed transforms silently dropped, or silently turned into huge
  numbers? Count them.
- Does the chosen level make sense against the view's device pixel? Print
  both.

## References

- GDAL: GDALWarpOperation::ComputeSourceWindow; warp options SAMPLE_GRID,
  SAMPLE_STEPS, SOURCE_EXTRA; GDALSuggestedWarpOutput2; gdalwarp -ovr.
- GDAL/PROJ: OCTTransformBounds, proj_trans_bounds (densify_pts).
- aobcore: R/cog.R level_pixel_size(), level_meshes(), cog_plan()
  max_stretch; issues #11, #14; PR #15.
- allboa/design decision 0003 (R-planned tiles now; browser traversal,
  which is this inverse query, as later upstream work).
