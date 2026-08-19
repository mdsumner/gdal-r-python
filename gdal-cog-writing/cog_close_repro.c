/* Reproduce: with the COG driver's Create() path (GDAL >= 3.13),
 * nearly all wall time is spent in GDALClose(), not in the write loop.
 *
 * The COG driver's Create() writes to a hidden temporary GeoTIFF
 * (<dst>.create.tif.tmp, LZW/ZSTD, BIGTIFF). At close, COGProxyDataset::Close()
 * runs GDALCOGCreator().Create() on that temp dataset:
 *   1. GTIFFBuildOverviewsEx() -> <dst>.ovr.tmp  (reads full base raster)
 *   2. GTiff CreateCopy(COPY_SRC_OVERVIEWS=YES)  (re-reads base + overviews,
 *      recompresses every tile with the final codec)
 * so close does ~2x-plus full-raster decode/encode regardless of how little
 * data was written.
 *
 * Baseline: identical writes to a plain GTiff (TILED, SPARSE_OK) where close
 * is just a cache flush; the COG cost then shows up in GDALTranslate instead.
 *
 * Build: cc cog_close_repro.c -o cog_close_repro $(gdal-config --cflags --libs)
 * Run:   ./cog_close_repro [xsize ysize]   (default 25600 12800)
 * Env:   CPL_DEBUG=COG shows the overview/copy phases during close.
 */

#include "gdal.h"
#include "cpl_conv.h"
#include "cpl_string.h"
#include "gdal_utils.h"
#include <stdio.h>
#include <string.h>
#include <time.h>

static double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

/* write a few scattered 512x512 tiles of data, leave the rest empty */
static void write_some_tiles(GDALDatasetH ds, int xsize, int ysize)
{
    static GInt32 buf[512 * 512];
    int i;
    for (i = 0; i < 512 * 512; i++)
        buf[i] = 1 + (i % 7);

    int step = 8; /* every 8th tile in each direction */
    int tx, ty;
    for (ty = 0; ty * 512 < ysize; ty += step)
    {
        for (tx = 0; tx * 512 < xsize; tx += step)
        {
            int xoff = tx * 512, yoff = ty * 512;
            int w = xsize - xoff < 512 ? xsize - xoff : 512;
            int h = ysize - yoff < 512 ? ysize - yoff : 512;
            GDALDatasetRasterIO(ds, GF_Write, xoff, yoff, w, h, buf, w, h,
                                GDT_Int32, 1, NULL, 0, 0, 0);
        }
    }
}

static void set_georef(GDALDatasetH ds)
{
    double gt[6] = {-180.0, 0.0140625, 0, 90.0, 0, -0.0140625};
    GDALSetGeoTransform(ds, gt);
    GDALSetRasterNoDataValue(GDALGetRasterBand(ds, 1), 0.0);
}

int main(int argc, char **argv)
{
    int xsize = argc > 1 ? atoi(argv[1]) : 25600;
    int ysize = argc > 2 ? atoi(argv[2]) : 12800;

    GDALAllRegister();
    printf("GDAL %s, raster %d x %d, Int32, 512 blocks\n\n",
           GDALVersionInfo("RELEASE_NAME"), xsize, ysize);

    double t0, t_write, t_close;

    /* ---- case 1: COG driver Create() ---- */
    {
        GDALDriverH drv = GDALGetDriverByName("COG");
        if (drv == NULL || GDALGetMetadataItem(drv, GDAL_DCAP_CREATE, NULL) == NULL)
        {
            printf("COG driver has no Create() capability (GDAL < 3.13); skipping case 1\n");
        }
        else
        {
            char **opt = NULL;
            opt = CSLSetNameValue(opt, "BLOCKSIZE", "512");
            opt = CSLSetNameValue(opt, "COMPRESS", "DEFLATE");
            opt = CSLSetNameValue(opt, "SPARSE_OK", "YES");
            t0 = now();
            GDALDatasetH ds = GDALCreate(drv, "/tmp/repro_cog.tif", xsize,
                                         ysize, 1, GDT_Int32, opt);
            CSLDestroy(opt);
            set_georef(ds);
            write_some_tiles(ds, xsize, ysize);
            GDALFlushCache(ds);
            t_write = now() - t0;
            t0 = now();
            GDALClose(ds);
            t_close = now() - t0;
            printf("COG Create():   write %7.2f s   close %7.2f s\n",
                   t_write, t_close);
        }
    }

    /* ---- case 2: GTiff Create() + translate to COG ---- */
    {
        GDALDriverH drv = GDALGetDriverByName("GTiff");
        char **opt = NULL;
        opt = CSLSetNameValue(opt, "TILED", "YES");
        opt = CSLSetNameValue(opt, "BLOCKXSIZE", "512");
        opt = CSLSetNameValue(opt, "BLOCKYSIZE", "512");
        opt = CSLSetNameValue(opt, "COMPRESS", "DEFLATE");
        opt = CSLSetNameValue(opt, "SPARSE_OK", "YES");
        t0 = now();
        GDALDatasetH ds = GDALCreate(drv, "/tmp/repro_gtiff.tif", xsize, ysize,
                                     1, GDT_Int32, opt);
        CSLDestroy(opt);
        set_georef(ds);
        write_some_tiles(ds, xsize, ysize);
        GDALFlushCache(ds);
        t_write = now() - t0;
        t0 = now();
        GDALClose(ds);
        t_close = now() - t0;
        printf("GTiff Create(): write %7.2f s   close %7.2f s\n", t_write,
               t_close);

        t0 = now();
        GDALDatasetH src = GDALOpen("/tmp/repro_gtiff.tif", GA_ReadOnly);
        const char *args[] = {"-of",      "COG",
                              "-co",      "BLOCKSIZE=512",
                              "-co",      "COMPRESS=DEFLATE",
                              "-co",      "SPARSE_OK=YES",
                              NULL};
        GDALTranslateOptions *topt =
            GDALTranslateOptionsNew((char **)args, NULL);
        GDALDatasetH dst =
            GDALTranslate("/tmp/repro_translated_cog.tif", src, topt, NULL);
        GDALTranslateOptionsFree(topt);
        GDALClose(dst);
        GDALClose(src);
        printf("translate GTiff -> COG:        %7.2f s\n", now() - t0);
    }

    return 0;
}
