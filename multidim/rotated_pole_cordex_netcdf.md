# Curvilinear climate grids, CRS recovery, and broken georeferencing — session notes

Rotated pole shenanigans, using this file that has rotated pole regular grid rlon/rlat AND curvilinear (in approximate polar stereographic) normal lon/lat coords from the rotated pole: 

https://cordex.dmi.dk/thredds/fileServer/esg_cordex/PolarRes/ANT-12/UU-IMAU/CESM2/historical/r11i1p1f1/RACMO24P-NN/v1-r1/day/tas/v20250116/tas_ANT-12_CESM2_historical_r11i1p1f1_UU-IMAU_RACMO24P-NN_v1-r1_day_19850101-19851231.nc


A reference distilled from working three CORDEX ANT-12 files through `gproj`, GDAL,
terra, and xarray. Context: each file ships **2-D curvilinear `lon`/`lat` arrays**
for a grid that is actually regular in *some other frame*. The goal of `gproj` /
`detect_grid()` is to recover that frame (CRS + extent + dimension) from the geometry.

The three files, which between them cover every case:

| file | regular frame | CRS in file? | earth model | notes |
|---|---|---|---|---|
| **HCLIM43-ALADIN** | projected metres (stereographic) | yes (stere) | **sphere** 6371229 | offset origin via large false E/N |
| **RACMO24P-NN** | rotated angular degrees (`rlon`/`rlat`) | yes (rotated pole) | sphere 6371000 | the *well-made* file |
| **MAR313** | projected metres (stereographic) | **broken/none** | **ellipsoid** WGS84 | corrupt `grid_mapping`; needed repair |

---

## 1. The core idea: a curvilinear `lon`/`lat` is a *symptom*, not the grid

A grid that is regular in a non-geographic frame (projected metres, or rotated
degrees), when inverse-transformed to true `lon`/`lat`, **always** becomes
curvilinear. So 2-D `lon`/`lat` arrays are the *derived* view; the authoritative
regular grid lives elsewhere (projected `x`/`y`, or rotated `rlon`/`rlat`).

**You can never take `range(lon)`/`range(lat)` as an extent.** For a polar cap the
`lon` range hits ±180 because the dateline passes through the *interior*, not an
edge. The only meaningful extent is in the frame where the grid is regular.

## 2. Detection routes on TWO independent axes, not one

```
            CRS present                 CRS absent
gt present  read + verify               read grid, RECOVER crs   (MAR)
            (HCLIM, RACMO)
gt absent   reproject axes from CRS     blind fit (CryoSat-style; lat_ts unrecoverable)
```

- **Read + verify (declared CRS + geotransform):** trust the file, *don't*
  reproject to recover. Projection-agnostic — works for stere-in-metres and
  rotated-in-degrees identically. Use the 2-D `lon`/`lat` only to *verify*
  consistency (catches broken files).
- **Recover CRS (geotransform but no CRS — MAR):** the `x`/`y` axes ARE the grid;
  fit only the projection string. Because `x`/`y` pin the scale, **`lat_ts` becomes
  uniquely recoverable** (sharp minimum), unlike the blind case.
- **Blind fit (neither — only 2-D lon/lat):** the genuinely cryptic case. Fit
  earth-model + `lon_0` by regularity. **`lat_ts` is NOT recoverable** (it's pure
  scale; regularity is flat in it) — must be assumed and flagged.

## 3. Prior fork: *what kind* of regular frame exists at all

Before "has CRS / has gt", ask what frame the grid is regular in — the axis
`standard_name` tells you for free:

- `projection_x_coordinate` / `projection_y_coordinate` → **projected metric** frame.
- `grid_longitude` / `grid_latitude` → **rotated angular** frame (`ob_tran` longlat).
- none declared → discover by fit (and only stereographic-family is fittable).

You **cannot** fit a projected CRS to a rotated-pole grid (the blind stere fit
floored at ~90 km), and you cannot describe a stereographic grid with a pole
rotation. There is no projected form that is regular for rotated pole — **the only
regular frame is the rotated lon/lat one.** Metres are a choice you impose later via
`project()`, not a property of the data.

---

## What the geometry recovers (and the numbers that validated it)

- **`lon_0` must be FIT, not read from the centroid.** At the pole the spherical
  centroid gives `lat_0` but the meridian is degenerate (all meridians meet).
  De-rotation (make rows horizontal) pins `lon_0`. Residual = the loss function.
- **The earth model is the thing that makes a grid "nearly" stereographic.** Fitting
  on an *ellipsoid* against sphere-defined data leaves a residual *floor* that never
  reaches noise; switch to a *sphere* and it collapses to float32 noise. The fit can
  distinguish **sphere vs ellipsoid** (e.g. 1143 m vs 0.44 m floor) but **not WGS84
  vs Hughes** (2.4352 vs 2.4350 — below float32 coordinate noise). Report the
  *class*, snap the specific body by nice-resolution.
- **`lat_ts` is pure scale → regularity-invariant.** Different `lat_ts` rescales `dx`
  uniformly but stays regular. So: fit earth+`lon_0` for regularity, then *read*
  `lat_ts` from the file (HCLIM `lat_ts=-90` gave 11000 m; an assumed -71 gave
  10700 — a 2.8% error, exactly the -90↔-71 scale ratio 1.0280). Never report
  resolution from a fit with an assumed `lat_ts`.
- **The reconstructed CRS can be more precise than the file's own coordinates** — the
  residual floor for a correctly-identified grid is the float32 noise of the stored
  `lon`/`lat` themselves (~sub-metre at 12 km cells).

## The residual metric — two fixes learned the hard way

- **Angular frames need wrap-safe diffs.** A rotated grid crossing its antimeridian
  produces a spurious ~360° residual at the seam. Wrap diffs to `[-180,180)` before
  measuring deviation. (RACMO went from "residual 360 / not regular" to ~1e-12.)
- **Don't reproject to recover when the file has native axes.** Reprojecting 2-D
  `lon`/`lat` into an angular CRS inherits PROJ's ±180 longitude wrap and shatters the
  extent. RACMO's native `rlon` runs `143.95…209.95` (crossing 180, *not* wrapped);
  reprojecting gave a bogus full-360° extent. **Read the stored axis; don't
  reproject-and-recover.** terra/GDAL get it right precisely because they read native
  axes, not the 2-D arrays.
- **"Regular?" is always a *relative-tolerance* decision.** `range(diff(rlon))` spans
  ~6e-13 of a cell (float64 decimal round-off on 0.1° steps) — genuinely regular.
  Never test `all(diff == diff[1])`; test `diff(range(diff)) < tol*step` with `tol`
  ~1e-9…1e-6.

---

## The MAR file: a broken-georeferencing case study (report-to-author material)

The data variable said `grid_mapping = "Polar Stereographic"` but the mapping
*variable* was named `stereographic`. Four escalating defects:

1. **Dangling reference** — `grid_mapping` value must be the *variable name*. GDAL
   can't resolve it → silently drops the CRS (no warning). Hence `gdalinfo` showed a
   geotransform but no Coordinate System.
2. **Non-CF parameter spelling** — the mapping had no `grid_mapping_name` and used
   WKT/ESRI names (`central_meridian`, `latitude_of_origin`). A CF reader rejects it
   even with the pointer fixed (`missing 'grid_mapping_name'`). Fixing the pointer
   alone is **necessary but not sufficient**.
3. **Wrong hemisphere** — `latitude_of_projection_origin = 90` for an Antarctic grid
   (should be −90).
4. **Metadata vs data conflict** — claimed `authority = EPSG:3031` / `lon_0 = 0`, but
   the coordinates are uniquely `lon_0 = 20` (rectangular grid → no rotational
   aliasing). The geometry is more trustworthy than the label; a *different* file in
   the same domain declares `straight_vertical_longitude_from_pole = 20`, confirming
   it's the domain convention.

**On `authority`:** not a CF attribute (GDAL ignores it). `lon_0=0` → EPSG:3031;
`lon_0=20` → *no EPSG code exists* (custom CRS). The CF-1.7 way to assert an
authority is `crs_wkt` (WKT2), which only carries an `ID["EPSG",…]` if the CRS
actually matches one. A correctly-formed mapping just states `grid_mapping_name` +
CF params and lets the reader build the CRS.

**km units:** MAR's `x`/`y` are in km. GDAL scales them to metres **only once a CRS
exists** to reconcile against (CF projected CRS is metre-based). No CRS → no
reference → raw km values. Same bytes, different extent, depending solely on whether
a CRS resolved.

---

## Tooling recipes that worked

### Repair the broken grid_mapping (RNetCDF; on a copy)
```r
library(RNetCDF); nc <- open.nc("copy.nc", write = TRUE)
att.put.nc(nc, "tas", "grid_mapping", "NC_CHAR", "stereographic")          # fix pointer
att.put.nc(nc, "stereographic", "grid_mapping_name", "NC_CHAR", "polar_stereographic")
att.put.nc(nc, "stereographic", "latitude_of_projection_origin",        "NC_DOUBLE", -90)
att.put.nc(nc, "stereographic", "straight_vertical_longitude_from_pole", "NC_DOUBLE", 20)
att.put.nc(nc, "stereographic", "standard_parallel",                    "NC_DOUBLE", -71)
# keep semi_major_axis + inverse_flattening (CF-valid); delete central_meridian,
# latitude_of_origin, the self-referential grid_mapping attr, and authority.
close.nc(nc)
```
After this GDAL rebuilt exactly: `+proj=stere +lat_0=-90 +lat_ts=-71 +lon_0=20 +a=6378137 +rf=298.2572`.

### Minimal anonymized reprex (NCO — never CDO; CDO regenerates the grid and erases the bug)
```bash
# strip to skeleton + one time slice + small footprint, NetCDF-3 (tiny: ~2.5 KB not 34 KB)
ncks   -h -3 -O -C -v tas,stereographic,x,y,time -d time,0 -d x,0,,40 -d y,0,,40 in.nc step1.nc
# anonymize: nuke ALL globals, re-add only Conventions; -h on BOTH suppresses the
# history stamp that would otherwise re-leak the original filename
ncatted -h -O -a ,global,d,, -a Conventions,global,c,c,"CF-1.6" step1.nc reprex.nc
```
- `-C -v x,y` keeps `x`/`y` as coordinate vars while dropping `bounds`/`height`.
- NetCDF-4 size is HDF5 *container* overhead (chunk B-trees, filter pipelines),
  **not** compression — turning deflate off makes it *bigger*. `ncks -3` removes it.

---

## GDAL rotated-pole behaviour (classic raster path) — and the workaround

Classic `gdalinfo` on RACMO refuses a geotransform (corners `0…660 / 0…531`) and
emits a **geolocation array** (2-D `lon`/`lat`) — even though regular `rlon`/`rlat`
are on disk. Two gates cause it:

1. **Axis-name check** rejects `grid_longitude`/`grid_latitude` as X/Y
   (`dimension #2 (rlon) is not a Longitude/X dimension`).
2. **Dim-verification mode** prefers the 2-D auxiliaries over the 1-D dim coords.

Both are config-gated. The even-spacing tolerance is a *hardcoded 0.1°* (generous;
the 1e-13 jitter passes trivially — not the problem here).

**Workaround (non-destructive, makes classic GDAL build the rotated affine grid):**
```bash
gdalinfo --config GDAL_NETCDF_VERIFY_DIMS STRICT \
         --config GDAL_NETCDF_IGNORE_XY_AXIS_NAME_CHECKS YES \
         NETCDF:"file.nc":tas
```
Both are needed. Output then gives the rotated-frame extent `143.95…209.95 /
-28.05…25.05` AND true geographic corners (GDAL does the `ob_tran` inverse for you).

**GDAL feature-request framing (additive, not behavioural):**
- Warn on a *dangling* `grid_mapping` reference (currently silent).
- Warn when `grid_mapping_name` is recognized but required CF params are absent
  (silently defaults → *wrong* CRS, worse than none).
- Make `grid_longitude`/`grid_latitude` + `rotated_latitude_longitude` pass the X/Y
  check **by default** — the escape hatch (`IGNORE_XY_AXIS_NAME_CHECKS`) already
  exists, so the default is the anachronism. Keep the geoloc arrays available.
- The `dimension #2 (rlon) is not a Longitude/X dimension` warning fires even on the
  *success* path (after the flags) — self-contradicting output.

---

## The architectural punchline: dimension-native beats flatten-to-affine

The same RACMO file, four readers, **one fork**:

| reader / path | result |
|---|---|
| GDAL **MDIM** (`gdal mdim info`) | correct, zero config — `(time, rlat, rlon)`, rotated CRS on the array |
| terra `rast(md=TRUE)` | correct — reads `rlon`/`rlat` via the MDIM path |
| xarray `gdalxarray` (`multidim=True`) | correct — same MDIM path |
| GDAL **classic** raster | needs `STRICT` + `IGNORE_XY_AXIS_NAME_CHECKS`, with contradictory warnings |

The **multidimensional** model is dimension-native: `rlon`/`rlat` are first-class
indexing coordinates, the CRS lives on the array, the 2-D `lon`/`lat` are auxiliaries
— no geotransform-vs-geolocation fork to fall into, because the model never flattens
to GDAL's 2-D six-parameter raster. Every contortion in this session is the cost of
that flattening; the rotated axes are exactly where it leaks.

**For `ndr`/`gproj`: build on the multidim raster API.** The abstraction that doesn't
flatten is the one that doesn't have to be coerced back into correctness. CF
`grid_longitude`/`grid_latitude` rotated grids read correctly with no flags via MDIM
because dimension-native coordinates are the right abstraction for "regular in an
angular frame."

---

## Cheat-sheet

- Curvilinear `lon`/`lat` = symptom; find the frame where the grid is regular.
- Route on `standard_name`: `projection_x/y_coordinate` (metric) vs
  `grid_longitude/latitude` (rotated angular) vs none (fit).
- Read declared axes/geotransform; reproject-to-recover only as last resort (it
  inherits the ±180 wrap).
- `lon_0` fit by de-rotation; earth-model by residual *floor* (sphere vs ellipsoid
  only); `lat_ts` read from file (unrecoverable from lon/lat alone, recoverable from
  x/y).
- Regularity is always a *relative* tolerance; wrap angular diffs.
- `grid_mapping` value = the mapping *variable's name*; needs `grid_mapping_name` +
  CF-spelled params; `authority` isn't CF.
- Reprex with `ncks` (faithful), never CDO (regenerates). `ncks -3` for size; `-h` on
  NCO tools to avoid history leaks.
- Prefer the **multidim** API; the classic 2-D raster path is where rotated/curvilinear
  grids fight you.
