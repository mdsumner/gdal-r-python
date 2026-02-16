# S3, Zarr, and GDAL Virtual Filesystems: A Practical Landscape

## The core question

Every S3 object has an HTTPS URL. Every `/vsis3/` path *could* be
rewritten as `/vsicurl/https://...`. So why do both exist, and when does
it matter?

## Two ways to talk to a bucket

`/vsicurl/` speaks plain HTTP. It reads files with GET range requests
and discovers directory contents by fetching the URL and scraping HTML
`<a href>` links from whatever the server returns. This works for any
HTTP endpoint but directory listing is unreliable — many S3 buckets
don't serve HTML indexes at all.

`/vsis3/` speaks the S3 REST API. It reads files with the same GET range
requests but discovers directory contents using `ListObjectsV2`, which
returns a structured listing of every object matching a prefix in a
single API call. `/vsigs/` and `/vsiaz/` do the same for GCS and Azure
respectively.

For reading a single known file (a COG, a NetCDF), the difference is
negligible. Both use HTTP range requests under the hood and performance
is effectively identical.

## Why it matters for Zarr

Zarr is a directory of files, not a single file. A store contains
`.zgroup`, `.zarray`, `.zattrs`, and chunk files scattered across a tree.
GDAL's Zarr driver needs to discover this structure somehow.

**With consolidated metadata** (`.zmetadata`): one file describes the
entire store. GDAL fetches it in a single request and knows every
variable, dimension, and attribute. Directory listing is unnecessary and
`/vsicurl/` and `/vsis3/` perform identically.

**Without consolidated metadata**: GDAL must probe for `.zarray` and
`.zattrs` files per-variable, per-group. With `/vsis3/`, a single
`ListObjectsV2` call with a prefix returns everything. With `/vsicurl/`,
GDAL falls back to individual HEAD requests or HTML scraping, which is
slower and may fail entirely.

The `ZARR:` prefix is the escape hatch. It tells GDAL "this is Zarr,
don't try to auto-detect the format by listing files" — GDAL then knows
exactly which metadata files to look for. This makes `/vsicurl/` viable
for Zarr even without reliable directory listing, but `/vsis3/` still
has the edge for non-consolidated stores because it can efficiently
enumerate the store contents.

The GDAL Zarr docs confirm this directly: the `CACHE_TILE_PRESENCE`
option "will rarely work for `/vsicurl/` itself, but more cloud-based
file systems (such as `/vsis3/`, `/vsigs/`, `/vsiaz/`, etc) which have a
dedicated directory listing operation."

## Can every s3:// become https://?

Yes, mechanically. Every S3 object has HTTPS URLs in two forms:

- Virtual-hosted: `https://BUCKET.s3.REGION.amazonaws.com/KEY`
- Path-style: `https://s3.REGION.amazonaws.com/BUCKET/KEY`

Whether the bucket actually *responds* depends on three independent
permission layers:

**GetObject** — can you read individual files anonymously? Most public
data buckets allow this. If so, both `/vsicurl/https://...` and
`/vsis3/BUCKET/KEY` with `AWS_NO_SIGN_REQUEST=YES` work for reading
chunks and metadata files.

**ListBucket** — can you list directory contents? Many public buckets
allow anonymous `GetObject` but *not* `ListBucket`. This is the common
case where `/vsis3/` without `ZARR:` prefix fails (tries to list, gets
403) but `/vsicurl/` with `ZARR:` prefix works fine (never tries to
list, just reads known paths). With `ZARR:` prefix on either, this
distinction largely disappears.

**Endpoint routing** — some S3-compatible stores (Cloudferro for CMEMS,
OSN for Pangeo) expose a straightforward HTTPS endpoint that works with
`/vsicurl/` but have different or absent S3 API endpoints. Conversely,
some AWS buckets have restrictive CORS or redirect rules that make the
HTTPS path less reliable than the S3 API path.

## Practical guidance

For a portable script that works across providers (AWS, GCS, Cloudferro,
OSN, Azure):

1. Use `/vsicurl/https://...` as the default. It works uniformly, needs
   no environment variables, and doesn't assume S3 API availability.

2. Always use the `ZARR:` prefix for Zarr stores. This is non-optional
   for `/vsicurl/` (where directory listing is unreliable) and a good
   habit for `/vsis3/` too.

3. For AWS S3 specifically, `/vsis3/` is cleaner and marginally faster
   for non-consolidated stores. It requires
   `Sys.setenv(AWS_NO_SIGN_REQUEST = "YES")` for public buckets.

4. For GCS, prefer `/vsicurl/https://storage.googleapis.com/BUCKET/...`
   over `/vsigs/` for public buckets — avoids needing GCS credential
   configuration. Use `/vsigs/` when you need authenticated access or
   directory listing.

5. For non-AWS S3-compatible stores (Cloudferro, MinIO, Ceph), use
   `/vsicurl/` with the provider's HTTPS endpoint. The `/vsis3/` path
   requires setting `AWS_S3_ENDPOINT` to the provider's domain, which
   adds configuration complexity for no gain on public data.

6. Consolidated metadata (`consolidated=True` in xarray terms) is the
   single biggest factor in open performance. Whether the store has
   `.zmetadata` matters more than the choice of VSI handler.

## Summary table

| Scenario | Recommended | Why |
|---|---|---|
| Public Zarr, any provider | `ZARR:"/vsicurl/https://..."` | Portable, no env vars |
| Public Zarr on AWS S3 | `ZARR:"/vsis3/BUCKET/KEY"` | Cleaner paths, native listing |
| Private S3 bucket | `/vsis3/` | Needs credential chain |
| GCS public bucket | `/vsicurl/https://storage.googleapis.com/...` | Avoids GCS auth config |
| Azure with SAS token | `/vsiaz/` or `/vsicurl/` with token in URL | Token must propagate |
| Non-consolidated Zarr, perf matters | `/vsis3/` or `/vsigs/` | Native `ListObjects` |
| Kerchunk JSON reference | `ZARR:"/vsicurl/https://...ref.json"` | GDAL >= 3.11 |

## GDAL config options that matter

```
AWS_NO_SIGN_REQUEST=YES          # anonymous S3 access
GDAL_HTTP_MULTIPLEX=YES          # HTTP/2 multiplexing (can help with many small requests)
GDAL_HTTP_MERGE_CONSECUTIVE_RANGES=YES  # merge adjacent range requests
GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR  # don't list sibling files on open
CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.zarray,.zattrs,.zmetadata,.zgroup,.json  # optional, restrict probing
VSI_CACHE=TRUE                   # enable LRU cache for range reads
VSI_CACHE_SIZE=100000000         # 100 MB cache (default 25 MB)
```
