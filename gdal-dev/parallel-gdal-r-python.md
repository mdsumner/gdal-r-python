# Parallelization for GDAL workloads — Python and R

Notes from pixtract development, February 2026.

## The problem

Raster point extraction is I/O bound: reading and decompressing tiles from disk or network is the bottleneck, not the arithmetic of classifying points to tiles or indexing values. Parallelizing tile reads is the obvious win, but the mechanics differ between Python and R.

## Python

### The GIL (Global Interpreter Lock)

The GIL only blocks *Python bytecode* execution. When GDAL's `ReadAsArray` runs, it drops into C code (libgdal) for the actual I/O and decompression. During that C call, the GIL is released. So multiple threads can genuinely do concurrent GDAL reads. This is the same reason numpy operations can be threaded — the heavy lifting is in C.

### ThreadPoolExecutor

Python's `concurrent.futures.ThreadPoolExecutor` is the natural fit:

```python
from concurrent.futures import ThreadPoolExecutor

def read_tile(key, group):
    ds = gdal.Open(path)          # each thread opens its own handle
    tile = ds.GetRasterBand(1).ReadAsArray(xoff, yoff, w, h)
    ds = None
    result[group] = tile[local_row, local_col]

with ThreadPoolExecutor(max_workers=8) as pool:
    futures = [pool.submit(read_tile, k, g) for k, g in zip(keys, groups)]
    for f in futures:
        f.result()
```

Key details:

- Each thread opens its own `gdal.Open()` handle. For `/vsicurl/` this means parallel HTTP connections, which is what you want.
- `max_workers` must be set explicitly. In pixtract, `None` or `1` means serial — you opt in to parallelism.
- The numpy grouping code (argsort, split, fancy indexing) is GIL-bound, but it runs *before* the parallel section. Threads only do GDAL reads and array assignment.

### Where threading helps

- **Network I/O** (`/vsicurl/`, `/vsis3/`): threads wait on HTTP responses — perfect for threading.
- **Compressed tiles** (DEFLATE, ZSTD, LZ4): decompression happens in C with GIL released.
- **Multiple source files** (VRT two-level): each thread opens its own dataset handle.

### Where threading doesn't help

- **Local SSD, uncompressed**: reads are already fast, thread overhead eats the gains.
- Benchmark: serial 0.088s vs parallel/4 at 0.079s on local disk — barely different.

### GDAL_NUM_THREADS

GDAL's `GDAL_NUM_THREADS` config option controls *internal* multi-threaded decompression (for DEFLATE/ZSTD). This is orthogonal to the application-level thread pool and they can compound:

```python
gdal.SetConfigOption("GDAL_NUM_THREADS", "4")  # per-tile decompression
extract_points(dsn, xs, ys, max_workers=8)       # parallel tile reads
```

Eight threads each doing a tile read where GDAL internally uses 4 threads to decompress. Whether this helps or thrashes depends on the machine, but for large network-backed mosaics it's worth knowing about.

### multiprocessing (the alternative)

If you wanted to parallelize the numpy grouping too (truly huge point sets), you'd need `multiprocessing` to get separate GIL-free processes. But then you pay for serializing arrays across process boundaries (or use shared memory via `multiprocessing.shared_memory`). For I/O-bound workloads like tile reads, threading is the right choice — multiprocessing solves a different problem.

### asyncio

For truly massive COG workloads (thousands of HTTP range requests), `asyncio` with GDAL's `/vsicurl/` or `fsspec` could be explored. Not implemented in pixtract yet — ThreadPoolExecutor is simpler and sufficient for current scales.

### Summary of Python options

| Approach | Parallelism | GIL bypass | Overhead | Best for |
|---|---|---|---|---|
| `ThreadPoolExecutor` | Threads (shared memory) | Yes, during C calls | Low | Network I/O, GDAL reads |
| `multiprocessing` | Processes (separate memory) | Yes, fully | High (serialization) | CPU-bound numpy work |
| `asyncio` | Cooperative (single thread) | No | Minimal | Massive concurrent HTTP |
| `GDAL_NUM_THREADS` | Internal GDAL threads | N/A | None | Per-tile decompression |

## R

### The landscape

R has no GIL, but it also has no true threading for R code. All parallelism in R is process-based — you're forking or spawning separate R sessions. The question is how much friction that involves.

### mirai — low-friction multisession

`mirai` (+ `daemons`) provides persistent background R processes. Functions wrapped with `purrr::in_parallel()` are dispatched to daemon workers automatically:

```r
library(mirai)
daemons(24)

extract_pt <- purrr::in_parallel(function(x) {
    # each daemon is a separate R process
    # can open its own GDAL handle, no conflicts
    ds <- new(GDALRaster, dsn)
    pixel_extract(ds, pts)
})

results <- purrr::map(payload_list, extract_pt)
daemons(0)
```

Key properties:

- **Persistent daemons**: workers stay alive across calls, no per-task startup cost.
- **Automatic serialization**: function arguments and return values are serialized/deserialized transparently. For large arrays this has a cost, but for tile-sized payloads (a few thousand points + a bbox) it's negligible.
- **No shared memory**: each daemon has its own R session. GDAL handles, temp files, and connections are process-local — no conflicts, no locking.
- **True parallelism**: separate processes mean separate everything — CPU, memory, GDAL instances. No GIL equivalent to worry about.

### furrr / future — the predecessor

Before mirai, the typical approach was `furrr::future_map()` with `future::plan(multicore)`:

```r
library(furrr)
plan(multicore)  # fork-based
results <- future_map(payload_list, extract_fn)
plan(sequential)
```

- **`plan(multicore)`**: fork-based, Unix only. Fast startup (copy-on-write memory), but forked processes sharing GDAL state can cause issues — file handles, curl connections, and internal caches don't survive forks cleanly.
- **`plan(multisession)`**: spawns fresh R sessions. Safer than forking but slower startup. Similar to mirai but less ergonomic.

### parallel::mclapply — the bare metal

```r
parallel::mclapply(payload_list, extract_fn, mc.cores = 24)
```

- Fork-based, lightweight, no dependencies.
- Same fork-safety concerns as `plan(multicore)`.
- No persistent workers — forks on each call.

### Why mirai wins for GDAL workloads

The fork-based approaches (`multicore`, `mclapply`) are risky with GDAL because `/vsicurl/` maintains HTTP connection pools and internal caches that don't survive fork cleanly. You can get corrupted reads, segfaults, or silent wrong results.

mirai's persistent-daemon model avoids this entirely: each daemon is a fresh R session that opens its own GDAL handles. No forking, no shared state, no surprises. The serialization cost is small for the payloads involved (point coordinates + bbox per tile).

### Summary of R options

| Approach | Mechanism | Startup cost | GDAL-safe | Ergonomics |
|---|---|---|---|---|
| `mirai::daemons` | Persistent sessions | Once | Yes | Excellent with `purrr::in_parallel` |
| `furrr` + `multisession` | Spawned sessions | Per plan | Yes | Good |
| `furrr` + `multicore` | Fork | Low (COW) | Risky | Good |
| `parallel::mclapply` | Fork | Per call | Risky | Minimal |
| `parallel::parLapply` | Cluster (sessions) | Once | Yes | Verbose |

## Python vs R comparison

| Dimension | Python (`ThreadPoolExecutor`) | R (`mirai`) |
|---|---|---|
| Mechanism | Threads (shared memory) | Processes (separate memory) |
| GDAL handle per worker | Open per thread | Open per daemon |
| Data sharing | Direct (numpy array, shared) | Serialized (automatic) |
| Startup cost | Negligible | Once (daemons persist) |
| Memory overhead | Low (shared address space) | Higher (N × R sessions) |
| CPU parallelism for R/Python code | No (GIL) | Yes (separate processes) |
| CPU parallelism for C code (GDAL) | Yes (GIL released) | Yes (separate processes) |
| Network I/O parallelism | Yes | Yes |
| Risk of shared-state bugs | Dataset handles must be per-thread | None (fully isolated) |

### The key difference

Python threading works for GDAL because the GIL is released during C calls — threads share memory cheaply but get true parallelism for I/O. The tradeoff is that any Python-level computation (numpy grouping) remains single-threaded.

R's process-based parallelism is heavier (each worker is a full R session) but simpler to reason about — complete isolation, no shared state, no GIL. The tradeoff is serialization overhead for passing data to/from workers.

For tile-grouped raster extraction, both approaches work well because the bottleneck is I/O, the per-task payload is small, and the expensive work (GDAL reads) happens in C regardless.

## Practical recommendations

### For pixtract (Python)

```python
# Local disk: serial is fine
extract_points(dsn, xs, ys)

# Network COG: 8-16 threads
extract_points(dsn, xs, ys, max_workers=8)

# Large compressed tiles: let GDAL parallelize decompression too
gdal.SetConfigOption("GDAL_NUM_THREADS", "4")
extract_points(dsn, xs, ys, max_workers=8)
```

### For R (mirai)

```r
# Start daemons once per session
mirai::daemons(24)

# Dispatch tile payloads
results <- purrr::map(tile_payloads, extract_fn_in_parallel)

# Clean up
mirai::daemons(0)
```

### GDAL-specific tuning (both languages)

- `CPL_VSIL_CURL_CACHE_SIZE`: default 16 MB. Increase for workloads that revisit nearby tiles. For continent-spanning traverses, cache doesn't help — better to sort points by source and process each source once.
- `GDAL_HTTP_MAX_RETRY` / `GDAL_HTTP_RETRY_DELAY`: set for network benchmarks to reduce jitter from transient S3 slowdowns.
- `CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif,.vrt`: prevents GDAL from probing for sidecar files (`.aux.xml`, `.ovr`) which adds extra HTTP requests on first open.
- `gdal.VSICurlClearCache()` (Python) / equivalent in R: flush between benchmark runs for honest cold-cache numbers.
