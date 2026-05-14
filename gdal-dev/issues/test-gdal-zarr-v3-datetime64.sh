#!/usr/bin/env bash
# test_gdal_zarr_v3_datetime64.sh
#
# Reproducer for GDAL Zarr V3 numpy.datetime64 extension data type issue.
# Creates a minimal local Zarr V3 store with a datetime64 time coordinate,
# then exercises GDAL classic 2D raster API access patterns to confirm
# which operations are affected by the unsupported extension type.
#
# Requirements:
#   - GDAL >= 3.8 (with Zarr V3 support)
#   - Python 3 with zarr >= 3.0 and numpy
#
# Usage:
#   chmod +x test_gdal_zarr_v3_datetime64.sh
#   ./test_gdal_zarr_v3_datetime64.sh

set -euo pipefail

STORE="/tmp/test_zarr_v3_datetime64"

echo "=== Step 1: Create minimal Zarr V3 store with numpy.datetime64 time coordinate ==="
echo ""

python3 << 'PYEOF'
import zarr
import numpy as np
import json
from pathlib import Path

store_path = Path("/tmp/test_zarr_v3_datetime64")

# Clean slate
if store_path.exists():
    import shutil
    shutil.rmtree(store_path)

# Create Zarr V3 group
root = zarr.open_group(store_path, mode="w", zarr_format=3)

# Time coordinate: 3 monthly timestamps
# zarr-python 3.x writes this as numpy.datetime64 extension type
times = np.array(['2024-01-01', '2024-02-01', '2024-03-01'], dtype='datetime64[ns]')
root.create_array("time", data=times, chunks=(3,))

# Depth coordinate: plain float64
depths = np.array([5.0, 10.0], dtype='float64')
root.create_array("z", data=depths, chunks=(2,))

# Spatial coordinates: plain float64 (no issue for GDAL)
lats = np.array([-60.0, -50.0, -40.0, -30.0], dtype='float64')
lons = np.array([100.0, 110.0, 120.0, 130.0, 140.0], dtype='float64')
root.create_array("lat", data=lats, chunks=(4,))
root.create_array("lon", data=lons, chunks=(5,))

# 3D data array: Float32, shape (time=3, lat=4, lon=5) — 1 extra dim
data_3d = np.random.rand(3, 4, 5).astype('float32') * 10
root.create_array("sst", data=data_3d, chunks=(1, 4, 5))

# 4D data array: Float32, shape (time=3, z=2, lat=4, lon=5) — 2 extra dims
# This mimics the CHLA-Z structure that failed on GCS
data_4d = np.random.rand(3, 2, 4, 5).astype('float32') * 5
root.create_array("chla", data=data_4d, chunks=(1, 1, 4, 5))

# Add xarray-style _ARRAY_DIMENSIONS so GDAL can resolve dimensions
for name, dims in [
    ("time", ["time"]),
    ("z",    ["z"]),
    ("lat",  ["lat"]),
    ("lon",  ["lon"]),
    ("sst",  ["time", "lat", "lon"]),
    ("chla", ["time", "z", "lat", "lon"]),
]:
    zj_path = store_path / name / "zarr.json"
    with open(zj_path) as f:
        zj = json.load(f)
    zj["attributes"]["_ARRAY_DIMENSIONS"] = dims
    with open(zj_path, "w") as f:
        json.dump(zj, f, indent=2)

# Show what zarr-python wrote
print("time/zarr.json data_type field:")
with open(store_path / "time" / "zarr.json") as f:
    zj = json.load(f)
print(json.dumps(zj["data_type"], indent=2))
print()
print("sst:  shape (3,4,5)  dims [time,lat,lon]     data_type:", end=" ")
with open(store_path / "sst" / "zarr.json") as f:
    print(json.dumps(json.load(f)["data_type"]))
print("chla: shape (3,2,4,5) dims [time,z,lat,lon]  data_type:", end=" ")
with open(store_path / "chla" / "zarr.json") as f:
    print(json.dumps(json.load(f)["data_type"]))
PYEOF

echo ""
echo "Store created at: ${STORE}"
echo ""
echo "=== Step 2: GDAL version ==="
gdalinfo --version
echo ""

# Helper to run a command, capture exit code and output
run_test() {
    local label="$1"
    shift
    echo "--- TEST: ${label} ---"
    echo "  CMD: $*"
    if output=$("$@" 2>&1); then
        echo "  RESULT: SUCCESS"
        echo "$output" | head -30 | sed 's/^/  | /'
        if [ "$(echo "$output" | wc -l)" -gt 30 ]; then
            echo "  | ... (truncated)"
        fi
    else
        echo "  RESULT: FAILED (exit code $?)"
        echo "$output" | head -15 | sed 's/^/  | /'
    fi
    echo ""
}

echo "=== Step 3: Test GDAL access patterns ==="
echo ""

echo "~~~ 3a: Coordinates (no datetime involved) ~~~"
echo ""

run_test "lat coordinate (float64)" \
    gdalinfo "ZARR:${STORE}":/lat

run_test "z coordinate (float64)" \
    gdalinfo "ZARR:${STORE}":/z

echo "~~~ 3b: 3D sst (time=3, lat=4, lon=5) — 1 extra dim ~~~"
echo ""

run_test "3D bare array (extra dims become bands)" \
    gdalinfo "ZARR:${STORE}/sst"

run_test "3D dimension index :0 (first time step)" \
    gdalinfo "ZARR:${STORE}":/sst:0

run_test "3D gdal_translate :0 to GeoTIFF" \
    gdal_translate -q "ZARR:${STORE}":/sst:0 /tmp/test_sst_slice.tif

run_test "3D VRT band selection (bypasses dimension resolution)" \
    gdalinfo "vrt://ZARR:${STORE}/sst?bands=1"

echo "~~~ 3c: 4D chla (time=3, z=2, lat=4, lon=5) — 2 extra dims ~~~"
echo "~~~ This mimics the CHLA-Z structure that fails on GCS ~~~"
echo ""

run_test "4D bare array (extra dims become bands)" \
    gdalinfo "ZARR:${STORE}/chla"

run_test "4D dimension index :0,0 (first time, first depth)" \
    gdalinfo "ZARR:${STORE}":/chla:0,0

run_test "4D gdal_translate :0,0 to GeoTIFF" \
    gdal_translate -q "ZARR:${STORE}":/chla:0,0 /tmp/test_chla_slice.tif

run_test "4D VRT band selection (bypasses dimension resolution)" \
    gdalinfo "vrt://ZARR:${STORE}/chla?bands=1"

echo "~~~ 3d: Group-level and time array access ~~~"
echo ""

run_test "gdalmdiminfo on group (multidim API)" \
    gdalmdiminfo "${STORE}"

run_test "gdalinfo on group (classic API, subdataset listing)" \
    gdalinfo "ZARR:${STORE}"

run_test "gdalinfo on time array directly (numpy.datetime64 type)" \
    gdalinfo "ZARR:${STORE}/time"

echo "=== Step 4: Observations ==="
echo ""
echo "Check for these effects:"
echo ""
echo "  1. HARD FAILURE: Can the time array be opened at all?"
echo "  2. DIMENSION NAMES: Are they 'time','lat','lon' or 'dim0','dim1','dim2'?"
echo "     (Look for DIM_time_INDEX vs DIM_dim0_INDEX in band metadata)"
echo "  3. DIMENSION VALUES: Are time coordinate values in band metadata?"
echo "     (Look for DIM_time_VALUE=2024-01-01T... in band metadata)"
echo "  4. 4D vs 3D: Does the 4D :0,0 access succeed or fail with"
echo "     'Wrong number of indices of extra dimensions'?"
echo "  5. STDERR: Are ERROR lines emitted even for operations that succeed?"
echo ""
echo "Store left at: ${STORE}"
echo "Clean up with: rm -rf ${STORE} /tmp/test_sst_slice.tif /tmp/test_chla_slice.tif"
