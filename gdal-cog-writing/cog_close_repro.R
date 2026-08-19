## Reproduce: with format = "COG" via gdalraster::create() (GDAL >= 3.13),
## nearly all wall time is in ds$close(), independent of how much data
## was written.
##
## Why: the COG driver's Create() (new in GDAL 3.13, cogdriver.cpp
## COGCreate/COGProxyDataset) writes everything to a hidden temporary
## GeoTIFF <dst>.create.tif.tmp (TILED, BIGTIFF, ZSTD or LZW). All the
## actual COG work happens in COGProxyDataset::Close():
##   1. GTIFFBuildOverviewsEx -> <dst>.ovr.tmp  (full-resolution read +
##      resample; CUBIC by default for bands without a color table)
##   2. GTiff CreateCopy with COPY_SRC_OVERVIEWS=YES  (re-reads base and
##      overviews, recompresses every tile with the final codec;
##      SPARSE_OK and NUM_THREADS are forwarded here)
## So close() is roughly two full passes over the whole grid, while the
## tile-write loop only paid for compressing the written tiles into the
## temp file.
##
## No controlledburn needed: write a few tiles into a mostly-empty grid.

library(gdalraster)
cat(gdal_version()[1], "\n")

dm <- c(25600L, 12800L)
ext <- c(-180, -90, 180, 90)
bs <- 512L

tile_dat <- as.integer(1 + (seq_len(bs * bs) - 1) %% 7)

write_tiles <- function(ds) {
  n <- 0L
  for (ty in seq(0L, dm[2] %/% bs - 1L, by = 8L)) {
    for (tx in seq(0L, dm[1] %/% bs - 1L, by = 8L)) {
      ds$write(band = 1, xoff = tx * bs, yoff = ty * bs,
               xsize = bs, ysize = bs, rasterData = tile_dat)
      n <- n + 1L
    }
  }
  n
}

gt <- c(ext[1], (ext[3] - ext[1]) / dm[1], 0,
        ext[4], 0, -(ext[4] - ext[2]) / dm[2])

## ---- case 1: COG driver Create() ----
f_cog <- file.path(tempdir(), "repro_cog.tif")
t0 <- proc.time()[["elapsed"]]
ds <- create(format = "COG", dst_filename = f_cog,
             xsize = dm[1], ysize = dm[2], nbands = 1,
             dataType = "Int32",
             options = c("BLOCKSIZE=512", "COMPRESS=DEFLATE",
                         "SPARSE_OK=YES"),
             return_obj = TRUE)
ds$setGeoTransform(gt)
ds$setNoDataValue(band = 1, 0)
n <- write_tiles(ds)
ds$flushCache()
t_write <- proc.time()[["elapsed"]] - t0

t0 <- proc.time()[["elapsed"]]
ds$close()
t_close <- proc.time()[["elapsed"]] - t0
cat(sprintf("COG create : %d tiles, write %.2fs, close %.2fs\n",
            n, t_write, t_close))

## ---- case 2: plain sparse GTiff, then explicit translate to COG ----
f_tif <- file.path(tempdir(), "repro_gtiff.tif")
t0 <- proc.time()[["elapsed"]]
ds <- create(format = "GTiff", dst_filename = f_tif,
             xsize = dm[1], ysize = dm[2], nbands = 1,
             dataType = "Int32",
             options = c("TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512",
                         "COMPRESS=DEFLATE", "SPARSE_OK=YES"),
             return_obj = TRUE)
ds$setGeoTransform(gt)
ds$setNoDataValue(band = 1, 0)
n <- write_tiles(ds)
ds$flushCache()
t_write <- proc.time()[["elapsed"]] - t0

t0 <- proc.time()[["elapsed"]]
ds$close()
t_close <- proc.time()[["elapsed"]] - t0
cat(sprintf("GTiff create: %d tiles, write %.2fs, close %.2fs\n",
            n, t_write, t_close))

f_cog2 <- file.path(tempdir(), "repro_translated.tif")
t0 <- proc.time()[["elapsed"]]
translate(f_tif, f_cog2,
          cl_arg = c("-of", "COG",
                     "-co", "BLOCKSIZE=512",
                     "-co", "COMPRESS=DEFLATE",
                     "-co", "SPARSE_OK=YES",
                     "-co", "OVERVIEW_RESAMPLING=NEAREST",
                     "-co", "NUM_THREADS=ALL_CPUS"))
cat(sprintf("translate to COG: %.2fs\n", proc.time()[["elapsed"]] - t0))

## expectation: COG close time ~= translate time (same code path),
## GTiff close ~= 0. Run with CPL_DEBUG=COG in the environment to see
## the two phases (overview build, final product) with timestamps.

## variations to explore:
## - options = c(..., "OVERVIEW_RESAMPLING=NEAREST") on the COG create:
##   default is CUBIC for Int32 without color table (cogdriver.cpp
##   GetResampling), so overviews of an ID raster are both slow and
##   wrong by default
## - "NUM_THREADS=ALL_CPUS": forwarded to both overview build and the
##   final CreateCopy compression
## - "OVERVIEWS=NONE": removes phase 1 entirely
