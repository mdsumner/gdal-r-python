"""Time the components of the sparse-write-then-COG workflow.

On GDAL >= 3.13, COG Create() close == (overview build + GTiff CreateCopy)
run implicitly. Here we time those pieces explicitly on any GDAL.
"""
import time
import numpy as np
from osgeo import gdal

gdal.UseExceptions()

XSIZE, YSIZE = 25600, 12800

tile = (1 + (np.arange(512 * 512, dtype=np.int32) % 7)).reshape(512, 512)

t0 = time.perf_counter()
drv = gdal.GetDriverByName("GTiff")
ds = drv.Create("/tmp/base.tif", XSIZE, YSIZE, 1, gdal.GDT_Int32,
                ["TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512",
                 "COMPRESS=DEFLATE", "SPARSE_OK=YES"])
ds.SetGeoTransform([-180, 360.0 / XSIZE, 0, 90, 0, -180.0 / YSIZE])
band = ds.GetRasterBand(1)
band.SetNoDataValue(0)
ntiles = 0
for ty in range(0, YSIZE // 512, 8):
    for tx in range(0, XSIZE // 512, 8):
        band.WriteArray(tile, tx * 512, ty * 512)
        ntiles += 1
ds.FlushCache()
t_write = time.perf_counter() - t0

t0 = time.perf_counter()
ds = None
t_close = time.perf_counter() - t0

t0 = time.perf_counter()
src = gdal.Open("/tmp/base.tif")
out = gdal.Translate("/tmp/out_cog.tif", src, format="COG",
                     creationOptions=["BLOCKSIZE=512", "COMPRESS=DEFLATE",
                                      "SPARSE_OK=YES"])
out = None
src = None
t_cog = time.perf_counter() - t0

import os
print(f"GDAL {gdal.__version__}, {XSIZE}x{YSIZE} Int32, "
      f"{ntiles} data tiles of {XSIZE//512 * YSIZE//512}")
print(f"sparse GTiff write loop : {t_write:7.2f} s")
print(f"sparse GTiff close      : {t_close:7.2f} s")
print(f"COG CreateCopy step     : {t_cog:7.2f} s   "
      "(this is what runs inside close() for COG Create on 3.13)")
print(f"base.tif  {os.path.getsize('/tmp/base.tif')/1024:8.0f} KB")
print(f"out_cog.tif {os.path.getsize('/tmp/out_cog.tif')/1024:6.0f} KB")
