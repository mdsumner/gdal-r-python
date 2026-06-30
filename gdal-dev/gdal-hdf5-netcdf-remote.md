# Remote netCDF / HDF5 access: two orthogonal problems, one convergence

*A working note from the OISST-on-Coiled investigation, June 2026.*

## What this is

While getting an R + GDAL workload to read OISST over the network inside a
Coiled container, two seemingly-unrelated frictions showed up:

1. The GDAL **netCDF driver fails to open a `/vsi` file** in a default Docker
   container (`userfaultfd` / seccomp), while the Python xarray stack reads the
   same remote `.nc` without complaint.
2. The GDAL **HDF5 driver loses georeferencing** that the netCDF driver
   recovers, even though both open the *same bytes*.

It is tempting to roll these into one verdict — "Python's HDF5 stack is more
mature than GDAL's." That verdict is **wrong**, and the reason it's wrong is the
interesting part. These are two *independent* axes:

- **Axis A — byte transport.** How remote bytes get to the decoder. This is
  where the `userfaultfd`/seccomp wall lives. It is an *I/O architecture*
  difference, not a maturity difference.
- **Axis B — CF georeferencing.** Which decoder bothers to interpret the CF
  conventions into a geotransform + CRS. This is a *driver-investment/history*
  difference, present in **both** ecosystems.

The payoff: the **reference / Zarr path** (kerchunk → Parquet → Icechunk, i.e.
the `blocklist` / `parq2ice` / `zaro` line of work) dissolves **both** axes at
once — but for two *different* reasons, which is worth understanding precisely so
we don't over-claim "Zarr is georeferenced" (it isn't, any more than HDF5 is).

A 2×2 keeps the axes honest:

|                         | **Transport (Axis A)**                              | **CF decode / georeferencing (Axis B)**                  |
| ----------------------- | --------------------------------------------------- | -------------------------------------------------------- |
| **GDAL**                | netCDF driver fakes mmap via `userfaultfd` → seccomp wall; HDF5/ROS3 driver does not | netCDF driver = CF-aware (geo OK); HDF5 driver = CF-blind (geo lost) |
| **Python (xarray)**     | fsspec hands decoder a file-like object → clean, container-safe | `h5py` raw = CF-blind; `xarray.decode_cf` = CF-aware     |

Notice the symmetry in the right column: **both** stacks have a CF-aware path and
a CF-blind path. The georeferencing problem is not GDAL's; it's a property of
*which decoder you pick* in either ecosystem.

---

## Axis A — byte transport: why Python reads netCDF-at-URLs and GDAL's netCDF driver trips seccomp

### The core principle: separate the transport from the decoder

A format decoder (HDF5-C, netCDF-C) was written assuming a **real, local,
seekable file**: it calls `open`/`seek`/`read`, or memory-maps the file and
chases pointers through a B-tree. It has no concept of "issue an HTTP range
request." To read such a format from a remote object you must bridge that gap
somewhere. The two ecosystems bridge it in **different layers**, and that
single choice is the whole story.

### Python: inject a userspace file-like object

When xarray opens a remote `.nc`:

```
fsspec / s3fs / gcsfs / aiohttp        ← does the HTTP range requests
        │  presents a Python file-like object (.read / .seek)
        ▼
h5py  (HDF5 "fileobj" / Python file driver)   ← decodes from that object
        │
        ▼
h5netcdf → xarray
```

`fsspec` returns a Python object that *quacks like a file* — `.read(n)`,
`.seek(pos)` — but services those calls with HTTP range requests under the hood.
`h5py` can be handed that object directly (HDF5's Python file-like virtual file
driver). The decoder **never knows** the bytes came over the network; it thinks
it's reading a local file, and fsspec is lying to it entirely in **userspace**.

No kernel involvement. No special syscall. Therefore **no seccomp surface** — it
works in any container, on any host, no flags. This is why
`xarray.open_dataset(fsspec_url, engine="h5netcdf")` "just works" from a laptop
in Hobart or a locked-down container alike.

Python can do this because it *owns the call boundary*: both the transport
(fsspec) and the decoder (h5py) are Python, so it can inject a fake file object
at a clean, high-level seam.

### GDAL's netCDF driver: fake mmap at the syscall level

GDAL links the **C** netCDF/HDF5 libraries and cannot inject a Python (or any
userspace callback) file object into them the way Python does. To present a
remote `/vsicurl` (or `/vsis3`) object to a C library that wants a memory-mapped
file, GDAL fakes the memory mapping itself, using the Linux **`userfaultfd`**
mechanism:

```
netCDF-C / HDF5-C   ← wants an mmap'd seekable file, chases B-tree pointers
        │  reads from a virtual memory region
        ▼
GDAL CPLVirtualMem (userfaultfd)   ← intercepts page faults on that region
        │  page fault → translate to a byte-range request
        ▼
/vsicurl | /vsis3   ← issues the HTTP range request, fills the page
```

GDAL hands the C library a virtual memory region. When the library touches a
page that isn't resident, the kernel raises a **page fault**, GDAL's
`userfaultfd` handler catches it, fetches the corresponding byte range over HTTP,
fills the page, and lets the read continue. Clever — it makes a remote object
look exactly like an mmap'd local file to a library that only understands mmap.

**But `userfaultfd` is a syscall, and Docker's default seccomp profile blocks
it.** So the moment the netCDF driver tries to open the `/vsi` object, the
`userfaultfd` syscall is denied and you get the exact error we hit:

> `GDAL FAILURE 1: Opening a /vsi file with the netCDF driver requires Linux
> userfaultfd to be available. If running from Docker, --security-opt
> seccomp=unconfined might be needed. Or you may set the GDAL_SKIP=netCDF
> configuration option to force the use of the HDF5 driver.`

The asymmetry with Python is **architectural, not maturity**: Python injects a
fake *file object* at a userspace seam it controls; GDAL must fake a *memory
mapping* at the syscall seam because it's bridging C libraries it doesn't own.
Different layer → different fragility.

### Two extra wrinkles worth knowing

- **The kernel gate.** Even with seccomp relaxed, `userfaultfd` for unprivileged
  processes can be gated by the `vm.unprivileged_userfaultfd` sysctl and by
  kernel version. So "allow the syscall in seccomp" and "the kernel permits
  unprivileged userfaultfd" are two separate conditions; a base image could
  satisfy one and not the other. Diagnose which layer is blocking before asking
  anyone to "fix seccomp."

- **GDAL is *not* missing remote HDF5 capability.** GDAL's **HDF5 driver** can
  read S3 via HDF5's own **ROS3** read-only S3 virtual file driver (and a `/vsi`
  plugin), which does ordinary range reads with **no `userfaultfd`**. That's
  exactly the `GDAL_SKIP=netCDF` fallback the error suggests. So the breakage is
  narrow and specific: it's *the netCDF driver's `/vsi` bridge under default
  seccomp*, not "GDAL's HDF5 is immature." The HDF5 path works in containers; it
  just (see Axis B) throws the georeferencing away.

### The fix ladder for Axis A (cheapest correctness-preserving first)

1. **Relax seccomp for one syscall.** `coiled run` doesn't expose
   `--security-opt`; on your own-tenancy VMs you may control the runtime. Ask for
   an allowlist of `userfaultfd` specifically (not blanket `unconfined`) — a more
   palatable security ask. *Keeps the netCDF driver and all its CF conveniences.*
2. **Download-to-real-disk, then open locally.** A real on-disk file needs no
   `userfaultfd`; the netCDF driver opens it normally. **Confirmed working on
   Coiled.** Costs the whole-file fetch (fine for ~1.6 MB OISST; bad for
   GHRSST-sized files). *Keeps CF conveniences; loses laziness.* **Note:**
   `/vsimem` is itself virtual and does **not** reliably satisfy the driver —
   download to a real `tempfile(fileext=".nc")`, not `/vsimem`.
3. **`GDAL_SKIP=netCDF` → HDF5 driver.** Reads `/vsi`/S3 directly, no
   `userfaultfd`. *But forfeits CF georeferencing* (Axis B) — you hand-build the
   geotransform, SRS, scale/offset, crop. Usually not worth it.
4. **Reference / Zarr path.** No netCDF driver in the loop at all → Axis A simply
   does not arise. (See convergence section.)

---

## Axis B — CF georeferencing: a driver-investment bias, in both ecosystems

### HDF5 is a container, not a geospatial format

HDF5 specifies *how bytes are laid out* (groups, datasets, chunked B-trees,
attributes). It says **nothing** about coordinate reference systems, geotransforms,
or how to interpret a 2-D array as a georeferenced grid. Georeferencing for a
`.nc` file lives in the **CF (Climate & Forecast) conventions** layered *on top*:
`grid_mapping` variables, coordinate variables (the `lon`/`lat` axes),
`scale_factor`/`add_offset`/`_FillValue` packing, `units`, etc. These are netCDF
**attributes** — invisible to anything that only reads HDF5-as-a-container.

So an OISST file is *simultaneously*:

- a valid **HDF5** file (raw chunked arrays, no geo), and
- a valid **CF-netCDF** file (the same arrays, *plus* CF metadata that makes them
  georeferenced).

Whether it comes out "georeferenced" depends **entirely on whether the reader
interprets CF** — not on the bytes.

### GDAL: the bias is which driver got the CF investment

- **GDAL netCDF driver — CF-aware.** Reads `grid_mapping`, synthesizes the SRS,
  builds the geotransform from coordinate variables, applies `scale_factor` /
  `add_offset` (`unscale=yes`), exposes subdatasets by name (`sd_name=sst`). This
  is what makes the tidy one-string recipe possible:
  `vrt://…?sd_name=sst&a_srs=EPSG:4326&unscale=yes`.
- **GDAL HDF5 driver — CF-blind.** Treats the file as a generic HDF5 container:
  raw arrays, subdatasets addressed by HDF5 path (`HDF5:"file":/sst`), little or
  no georeferencing, no CF unpacking. You reassemble extent/SRS/scale yourself.

**Same file, opposite georeferencing, purely by driver.** That's the
"driver-bias-history problem" — the CF-decoding effort went into the netCDF
driver, not the HDF5 driver. It's not that one is "right"; it's that only one was
taught CF.

### Python has the *same* split — just at a different package boundary

The Python stack does **not** escape this; it relocates it:

- **`h5py` (raw HDF5) — CF-blind.** Gives you the arrays; no coordinates, no CRS,
  no unpacking. Exactly analogous to GDAL's HDF5 driver.
- **`xarray.decode_cf` — CF-aware.** The CF interpretation (coords, scale/offset,
  masking) happens in **xarray**, not in `h5py`/`h5netcdf`. CRS handling often
  needs a further layer (`rioxarray` / `cf_xarray`).

So the Python equivalents are:

| GDAL                       | Python                          |
| -------------------------- | ------------------------------- |
| netCDF driver (CF-aware)   | `h5netcdf` + `xarray.decode_cf` |
| HDF5 driver (CF-blind)     | raw `h5py`                      |

The lesson: **georeferencing is never "in the format."** It's in whoever decodes
CF. GDAL puts that decoder in a *driver*; Python puts it in *xarray*. Neither raw
HDF5 nor raw Zarr is geospatial on its own.

---

## The convergence: why the reference / Zarr path dissolves both axes

The `blocklist` → kerchunk-Parquet → `parq2ice` → Icechunk line (and `zaro` /
`shearwater` for consumption) resolves **both** axes — but, importantly, for
**two different reasons**. Stating them separately is what keeps the claim
honest.

### Axis A dissolves because there is no netCDF driver any more

A Zarr / kerchunk / Icechunk consumer reads **chunks as plain object-store
byte-range requests** (`object_store`, `fsspec`, `/vsicurl`-range, Arrow
filesystem). Nobody opens a netCDF file through GDAL's netCDF driver, so the
`userfaultfd` mmap-fake never happens. The transport is ordinary range reads in
userspace — container-safe by construction, in **either** language. The seccomp
wall doesn't get fixed; it gets *routed around*.

### Axis B dissolves because the CF metadata is preserved *and* the standard consumer is CF-aware

This is the part to state carefully. **Zarr is also "just a container"** — like
HDF5, raw Zarr has no intrinsic georeferencing. So why is it better on Axis B?
Two reasons, neither of which is "Zarr is geo":

1. **The reference generation preserves the CF metadata.** kerchunk /
   VirtualiZarr extract the source `.nc`'s attributes into the Zarr metadata, and
   reference the coordinate variables as chunked arrays too. The CF information
   (`scale_factor`, `coordinates`, `grid_mapping`, the `lon`/`lat` arrays)
   **travels with the refs**. Nothing is thrown away the way the CF-blind HDF5
   driver throws it away.
2. **The dominant Zarr consumer is CF-aware by default.** Opening a kerchunk /
   Icechunk store through xarray runs `decode_cf` as a matter of course, so the
   preserved CF metadata is interpreted. You land in the CF-aware cell of the 2×2
   automatically, rather than having to opt out of a CF-blind driver.
3. **(Emerging) GeoZarr** is formalizing explicit geospatial conventions for
   Zarr, which will make the georeferencing first-class rather than CF-by-import
   — worth tracking, not yet load-bearing.

So Axis B dissolves not because Zarr is magically georeferenced, but because the
ref layer **preserves** the CF metadata and the **standard read path decodes**
it. The HDF5-driver failure mode (CF-blind reader silently dropping geo) simply
doesn't occur, because you're not using a CF-blind reader.

### The clean two-stage, two-language architecture

The refs become a language-agnostic **interchange contract**:

```
HARVEST (once, or incrementally)            CONSUME (lazily, forever)
─────────────────────────────────          ─────────────────────────────────
R + GDAL get-refs / rhdf5 H5Dchunk_iter     Python xarray + Dask + gdalxarray
  → enumerate chunk byte-offsets              → open the ref/Icechunk store
  → emit kerchunk-Parquet / Icechunk          → ds.sst.mean(...) fans across
  (your prior art: blocklist, parq2ice)         chunks automatically on Coiled
            │                                            ▲
            └──────────── reference store ───────────────┘
                       (the interchange format)
```

- **Harvest** is metadata-bound (read chunk indices, not payloads), embarrassingly
  parallel, latency-sensitive → the *ideal same-region Coiled job*. R/GDAL is
  strong here precisely because `get-refs` over `/vsis3`-HDF5 never leaves the
  `/vsi` world, so no URL re-mapping and (if it uses the HDF5/index path rather
  than the netCDF driver) potentially no `userfaultfd` either — **worth
  confirming**.
- **Consume** is where xarray-on-Dask earns its keep: the dataset's chunking *is*
  the parallelism, and Coiled+Dask provision and schedule it natively — the
  "baked-in distributed engine" that pure-R/crew couldn't borrow. Each ecosystem
  does what it's best at, joined by the ref store.

---

## Practical decision matrix

| You want to…                                       | Best path                                     | Axis A | Axis B | Notes |
| -------------------------------------------------- | --------------------------------------------- | ------ | ------ | ----- |
| One-off scalar/grid from a few remote `.nc`        | download-to-disk + netCDF driver              | dodged | OK     | simplest; confirmed on Coiled |
| Keep the lazy `/vsicurl` netCDF stream             | netCDF driver + seccomp `userfaultfd` allow   | needs allow | OK | ask Coiled; own-tenancy may already allow |
| Read remote netCDF in Python, any container        | fsspec + h5netcdf + xarray (decode_cf)        | clean  | OK (xarray) | the reference "just works" path |
| Avoid CF reassembly but escape seccomp in GDAL     | ~~`GDAL_SKIP=netCDF` (HDF5 driver)~~          | clean  | **lost** | usually not worth it |
| Recurring, parallel, same-region, escape both      | reference/Zarr (kerchunk→Icechunk), xarray+Dask | dissolved | dissolved | the destination; your tooling |
| Bridge GDAL `/vsis3` → a non-GDAL reader (rhdf5)   | hand-map to https (for now)                   | n/a    | n/a    | see "open question: VSIGetActualURL" |

---

## Follow this thread → code exercises to flesh out later

Each is a small, self-contained experiment with a clear *what it proves*. Marked
**[confirmed]** where we already have the result, **[hypothesis]** where we're
predicting.

### E1 — Prove the transport mechanism is `userfaultfd`
*Goal:* show the netCDF-driver `/vsi` open actually issues `userfaultfd`, and the
HDF5/ROS3 path does not.
```bash
# In a container WITH seccomp relaxed, strace the syscall:
strace -f -e trace=userfaultfd \
  Rscript -e 'library(gdalraster); new(GDALRaster, "vrt:///vsicurl/<oisst-url>?sd_name=sst")'
# Expect: userfaultfd(...) calls for the netCDF driver path.
# Repeat with GDAL_SKIP=netCDF → expect NO userfaultfd calls (ROS3/HDF5 path).
```
*Proves:* the seccomp wall is specifically the netCDF driver's mmap-fake, not
generic remote I/O. Also test `vm.unprivileged_userfaultfd` to separate the
kernel gate from the seccomp gate.

### E2 — Time the four GDAL read paths, same file, same region
*Goal:* quantify the cost of each Axis-A option from a us-east-1 VM next to the
S3 mirror.
```r
# paths: (a) /vsicurl/https NCEI  (b) /vsis3 same-region  (c) download-to-disk
#        (d) GDAL_SKIP=netCDF HDF5/ROS3 over /vsis3
# For each: open, read sst window, record wall time; old file (1981) vs new (2026).
```
*Proves:* whether the ~9 s old-file floor is NCEI's serving penalty (collapses
under same-region S3) or intrinsic to the early files (persists). Sizes the
real-archive run.

### E3 — Python container-safety baseline
*Goal:* confirm the fsspec+h5netcdf path needs no seccomp at all.
```python
import fsspec, xarray as xr
url = "s3://noaa-cdr-sea-surface-temp-optimum-interpolation-pds/.../oisst-...20200101.nc"
ds = xr.open_dataset(fsspec.open(url, anon=True).open(), engine="h5netcdf")
print(ds.sst)   # in a DEFAULT-seccomp container — expect success
```
*Proves:* Axis A is an injection-point property, not a maturity gap — same remote
netCDF, no userfaultfd, no flags.

### E4 — CF-decode comparison (Axis B made concrete)
*Goal:* show the same bytes georeferenced vs not, across drivers/libraries.
```r
# GDAL netCDF driver: geotransform + SRS present, scale/offset applied
# GDAL HDF5 driver  (GDAL_SKIP=netCDF, HDF5:"...":/sst): raw, no geo, packed ints
```
```python
# h5py raw: arrays only, no coords        | xarray decode_cf: coords + unpacked
```
*Proves:* georeferencing is a decoder property in *both* ecosystems; tabulate the
2×2 with real values (geotransform, a sample unpacked vs packed pixel).

### E5 — Reference harvest with `rhdf5::H5Dchunk_iter`
*Goal:* enumerate chunk byte-offsets for OISST `sst` without reading payloads.
```r
# Needs the https form (rhdf5 doesn't speak /vsis3): map /vsis3/... -> https for now.
# H5Dchunk_iter over sst -> (offset, size, filter mask) per chunk -> kerchunk-Parquet rows.
# Compare against GDAL `gdal mdim get-refs` over the SAME /vsis3 file (no URL mapping).
```
*Proves:* the harvest is metadata-bound and fast same-region; and contrasts the
R+rhdf5 (needs https mapping) vs GDAL-native (`/vsis3` direct) routes. Watch the
per-file index-read distribution for B-tree-scatter outliers in old files.

### E6 — Does `gdalxarray`-on-Dask inherit the seccomp wall?
*Goal:* the hinge question for the Python-distributed story.
```python
# Open OISST via gdalxarray as a Dask-backed dataset; trigger a per-chunk read
# inside a Dask task in a DEFAULT-seccomp worker. Inspect: does the per-chunk
# fetch open through GDAL's netCDF driver (→ userfaultfd, fails per worker) or via
# the multidim/Zarr/ref path (→ clean)?
```
*Proves:* whether the elegant `ds.sst.mean()`-fans-across-Coiled story is
driver-clean or silently re-imports the seccomp problem N× across workers. If the
former, harvest-to-refs first is mandatory, not optional.

### E7 — Zarr/ref round-trip preserves georeferencing
*Goal:* confirm Axis B really dissolves, not just Axis A.
```python
# Open the kerchunk/Icechunk OISST store via xarray; assert:
#  - no netCDF driver involved (Axis A)
#  - ds.sst has coords, CRS-able, scale/offset applied (Axis B preserved)
# Diff the resulting field against the GDAL-netCDF-driver result for one day.
```
*Proves:* the ref layer carried the CF metadata and the standard consumer decoded
it — i.e. both axes resolved, and the numbers match the trusted netCDF-driver
path.

---

## Open questions / things to verify (don't assert until checked)

- **`VSIGetActualURL`-shaped gap.** Each `IVSIS3LikeHandleHelper` already computes
  the request URL (`GetURL()` in `cpl_aws.cpp` / `cpl_google_cloud.cpp`,
  honouring endpoint/region/addressing/signing config). What's missing is a
  *generic, public* "resolved https for this `/vsi` path" entry point across all
  handlers. Before proposing one to gdal-dev, check whether a recent
  `VSIGetFileMetadata(path, …)` already surfaces it under some domain — the ask
  may be "expose/document what exists," not "build it." (The `canonical` symbols
  in `cpl_aws.cpp` are SigV4 *canonical-request* strings — a false friend, not the
  object URL.)
- **Does `gdal mdim get-refs` over `/vsis3`-HDF5 avoid `userfaultfd`?** If the
  chunk-index path uses the HDF5 driver / index reads rather than the netCDF
  driver's mmap-fake, the harvest may be seccomp-clean where the scalar job
  wasn't. Confirm with E1-style strace.
- **OISST Icechunk obsolescence.** A maintained, daily-updating public Icechunk
  store for OISST is plausibly imminent (NODD trajectory; the per-file Zarr refs
  already spotted on the S3 mirror). If it lands, the OISST *harvest* retires —
  but the harvest *capability* stays valuable for the long-tail AAD / Southern
  Ocean datasets nobody else will publish. Build your tooling for those; ride
  someone else's refs for OISST.
- **`/vsimem` vs real disk for the download fallback.** Confirmed that real-disk
  `tempfile(fileext=".nc")` satisfies the netCDF driver and `/vsimem` is suspect;
  worth a definitive note on *why* (`/vsimem` being itself virtual) if it ever
  needs re-litigating.
