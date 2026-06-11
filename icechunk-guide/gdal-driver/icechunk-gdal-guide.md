# The Intrepid Icechunker's Guide to GDAL

A field guide to reading [Icechunk](https://icechunk.io) stores with GDAL's
Icechunk driver (new in **GDAL 3.14**, by Even Rouault). Written from hands-on
reverse-engineering of the driver plus the official docs — so you know not just
*what* to type but *why it works*, and how to read the failures.

> Build dependencies: **libzstd** and the **Zarr driver**. The Icechunk driver
> does not decode arrays itself — it resolves the Icechunk graph and hands the
> store to GDAL's Zarr driver. That single fact explains most of what follows.

---

## 1. Mental model: what the driver actually does

An Icechunk repository is a *transactional, content-addressed* store. It is **not**
a plain Zarr-on-S3 hierarchy: the chunks live under `chunks/` keyed through
manifests, and the logical view at any point is defined by a **snapshot** reached
via a **branch** or **tag**.

The driver works in two layers:

1. A **`/vsiicechunk/` virtual file system** parses the Icechunk on-disk format
   (FlatBuffers: `repo` → `refs`/branches/tags → `snapshot` → `manifest` →
   chunk byte-ranges) and *presents the store as Zarr-shaped keys*
   (`zarr.json`, `c/0/0/…`).
2. The **Icechunk driver** prepends `/vsiicechunk/{` and appends `}` to your
   path, then opens it with the **Zarr driver**. All dimension/coordinate/
   attribute/codec handling is therefore Zarr-driver quality.

Chunk reads are served via `/vsisubfile/{offset}_{size},{object}` — a byte-range
window into the physical chunk object. This means GDAL's normal `/vsis3/`,
`/vsicurl/` caching, range-coalescing and retry all apply **for free**.

### Three kinds of chunk reference (all supported)
- **inline** — tiny chunks stored directly in the manifest (served from memory).
- **native** — chunks under the repo's `chunks/` directory.
- **virtual** — chunk content is a URL+offset+length into a *remote* netCDF/HDF5
  (the kerchunk/VirtualiZarr pattern). The driver auto-morphs the URL to a VSI
  prefix (`https://`→`/vsicurl/https://`, `s3://`→`/vsis3/`, `gs://`→`/vsigs/`,
  `az://`/`azure://`→`/vsiaz/`).

---

## 2. The connection name (start here)

**The nominal connection name is the Icechunk directory** — the folder that
contains the `repo` file and the `snapshots/`, `manifests/`, `transactions/`
directories. GDAL detects it by checking that those siblings exist.

```bash
# Preferred: just point at the store root. GDAL interrogates it directly.
gdal mdim info /vsis3/<bucket>/<prefix> \
  --config AWS_NO_SIGN_REQUEST YES --config AWS_REGION <region>
```

You do **not** need braces, `/repo`, or `/vsiicechunk/` for normal use. Those are
lower-level forms (below) that are handy when you want to understand or override
the resolution.

### Fingerprinting a store
A `vsi ls` of the root shows the Icechunk v2 layout:

```
repo  chunks  manifests  overwritten  snapshots  transactions
```

(v1 may use `refs/` in place of, or alongside, `repo`.) If you see
`snapshots/` + `transactions/` + (`repo` OR `refs/`), it's an Icechunk store and
the bare-root connection name will work.

---

## 3. All the ways to open one

| Form | When to use |
|---|---|
| `<store-root>` | **Default.** GDAL auto-detects the directory. |
| `ICECHUNK:<store-root>` | Force the Icechunk driver explicitly. Required to add `?branch=`/`?tag=`. |
| `<store-root>/repo` | Point at the `repo` file directly (header-sniffed). Works on older builds. |
| `/vsiicechunk/{<store-root>}` | Low-level VSI access (e.g. `gdal vsi ls`) to inspect Zarr-shaped keys. |
| `ZARR:"/vsiicechunk/{<store-root>}":/<array>` | Address a single array/subdataset. |

### Branch / tag selection
By default GDAL opens the **`main`** branch. To choose another:

```bash
# alternate branch
gdal mdim info "ICECHUNK:<store-root>?branch=<branch-name>"
# a tag
gdal mdim info "ICECHUNK:<store-root>?tag=<tag-name>"
```

List what's available:

```bash
gdal driver icechunk list-branches <store-root> --config AWS_NO_SIGN_REQUEST YES
gdal driver icechunk list-tags     <store-root> --config AWS_NO_SIGN_REQUEST YES
```

Example output:

```json
[ { "name": "main", "commit_message": "Update at 2026-06-05T12:22:32Z" } ]
```

---

## 4. Worked examples

### List the hierarchy (metadata only — always cheap)
```bash
gdal mdim info /vsis3/dynamical-ecmwf-aifs-single/ecmwf-aifs-single-forecast/v0.1.0.icechunk \
  --config AWS_NO_SIGN_REQUEST YES --config AWS_REGION us-west-2
```

### Inspect Zarr-shaped keys (low-level)
```bash
gdal vsi ls "/vsiicechunk/{/vsis3/dynamical-ecmwf-aifs-single/ecmwf-aifs-single-forecast/v0.1.0.icechunk}"
# -> zarr.json, dew_point_temperature_2m, geopotential_height_500hpa, ...
```

### One array, with stats (forces a real chunk decode)
```bash
gdal mdim info "ICECHUNK:<store-root>" --array temperature_2m
# or address it as a subdataset / gdal_translate target:
gdal_translate 'ZARR:"/vsiicechunk/{<store-root>}":/temperature_2m' out.tif -srcwin 0 0 1440 721 -b 1
```

### http(s) (source.coop) store
```bash
gdal mdim info /vsicurl/https://data.source.coop/bkr/metoffice/metoffice_global_wave.icechunk
```

---

## 5. Credentials & regions

- All the public examples here read **anonymously**:
  - S3: `--config AWS_NO_SIGN_REQUEST YES` (+ `--config AWS_REGION <region>`)
  - GCS: `--config GS_NO_SIGN_REQUEST YES`
- The `/vsiicechunk/` wrapper carries **no** credentials — they apply to the
  inner `/vsis3/`, `/vsigs/`, `/vsicurl/` fetch.
- **Catalog names are not buckets.** e.g. Arraylake's `earthmover-public/era5`
  is a *logical* name; the physical store is
  `/vsis3/earthmover-icechunk-era5/icechunkV2` (region `us-east-1`). Pointing
  `/vsis3/` at the catalog name gives `BucketNotFound`.
- **source.coop encodes the region in the host but still needs `AWS_REGION`.**
  A bucket like `us-west-2.opendata.source.coop` *looks* self-describing, but
  GDAL does not parse the region out of the host — you must still pass
  `--config AWS_REGION us-west-2`. (https access via `/vsicurl/https://...`
  sidesteps this; `/vsis3/` needs the explicit region.)

---

## 6. Reading the failures (a troubleshooting key)

| Symptom | Meaning | Fix |
|---|---|---|
| `BucketNotFound` | You used a *catalog* name, not the S3 bucket/prefix. | Find the real bucket/prefix (e.g. from the store's docs or `repr(repo)` in Python). |
| `too small file` | You pointed `/vsiicechunk/{…}` at the **store root** in an older build that expected the **`repo` file**. | Use the bare store root (new builds) or add `/repo` inside the braces. |
| `Invalid /vsiicechunk/ syntax …` | Missing braces or missing inner VSI scheme. | `"/vsiicechunk/{/vsis3/bucket/prefix}"` — quote it; keep the inner `/vsis3/`. |
| Opens, lists metadata, **but data read fails on codec** | The Zarr driver lacks that codec. **pcodec** is the common one (Earthmover ERA5 uses it). | No fix until GDAL's Zarr driver gains the codec. Metadata works; values don't. |
| `grid not recognized` / "not a grid" | Grouped store, or coords/CRS the consumer didn't resolve. | Point at the specific group; check `spatial_ref`/`GeoTransform`/`dimension_names`. |
| Variables hidden / all-hidden | Store converted Zarr **v2→v3** without migrating `_ARRAY_DIMENSIONS` → `dimension_names`. | A store-side metadata gap (e.g. `era5_weatherbench2`); not a GDAL bug. |
| `manifest extents has not expected dimension count` on a `crs`/`spatial_ref` array | The driver's manifest extent-rank check appears not to allow a **0-D (scalar) grid-mapping container** (standard CF/GeoZarr). One such variable aborts the *whole* open. | Driver-side; reportable. (Seen on the v1-layout ISMIP6 AIS store: `config.yaml`+`refs/`.) Confirm the offending array's `shape` is `[]` to be sure. |
| `403` then `too small file` when opening a `ZARR:"…":/array` subdataset | Almost always your **shell wrapped the line** and dropped the trailing `--config` flags (look for `-bash: --config: command not found`), so the request went unsigned → S3 `403` → driver reads the error body as a tiny "repo". | Re-run as **one line** with `--config` attached. Only if it still 403s with config properly applied is it a real config-propagation bug through the `ZARR:`/`/vsiicechunk/` re-entry. |

### The "static mask" gotcha (and why it's *not* a bug)
A land-sea mask or other mostly-constant field often has **unmaterialized
chunks** — chunk `[0,0]` may have *no manifest entry*. Per the Zarr spec that
resolves to the **fill value** (commonly `NaN`). The current driver handles this
correctly: a missing manifest ref makes the VSI report "not found", and the Zarr
driver synthesizes the fill. If you ever see a *hard error* on a missing chunk,
that's a regression worth reporting — with the array name and chunk index.

---

## 7. Performance notes

- **Metadata is cheap, decode is not.** `gdal mdim info` (no `--array … -stats`)
  reads only `repo`+`snapshot`+`manifest` — a few small range GETs. Stats /
  pixel reads pull and decode chunks.
- Manifests are LRU-cached; repeated reads of the same array reuse them.
- `/vsisubfile/` rides GDAL's VSI caching, so set the usual
  `CPL_VSIL_CURL_*` / `VSI_CACHE` knobs as you would for any `/vsis3/` workload.
- Big daily-updating cubes can be slow on first touch (cold cache, many chunks).

---

## 8. Debugging

```bash
CPL_DEBUG=ON gdal mdim info <store-root> ... 2>&1 | grep -iE "icechunk|snapshot|branch|zarr|ERROR"
```

Look for the resolution chain: `Opening repo` → `branch 'main'` →
`snapshot '<base32-id>'`. Seeing the snapshot id proves the Icechunk path
(not a plain-Zarr mirage) actually ran. The Crockford-base32 snapshot id should
match what you get from the Python client's `readonly_session("main")`.

---

## 9. Companion files

- `icechunk_stores.json` — verified store URIs + the full addressing matrix +
  per-store codec/region/snapshot notes.
- `gridlook_zarr_examples.json` — @eeholmes' gridlook test corpus (Zarr **and**
  Icechunk; statuses are gridlook/browser results — re-test in GDAL).

---

## 10. Quick reference card

```bash
# detect
gdal vsi ls /vsis3/<bucket>/<prefix>/        # expect repo chunks manifests snapshots transactions

# open (metadata)
gdal mdim info /vsis3/<bucket>/<prefix> --config AWS_NO_SIGN_REQUEST YES --config AWS_REGION <region>

# branches / tags
gdal driver icechunk list-branches /vsis3/<bucket>/<prefix> --config AWS_NO_SIGN_REQUEST YES
gdal mdim info "ICECHUNK:/vsis3/<bucket>/<prefix>?branch=<name>"

# low-level keys
gdal vsi ls "/vsiicechunk/{/vsis3/<bucket>/<prefix>}"

# one array
gdal mdim info "ICECHUNK:/vsis3/<bucket>/<prefix>" --array <name>

# http(s) store
gdal mdim info /vsicurl/https://data.source.coop/<...>.icechunk
```
