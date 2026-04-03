# Earthdata Authentication: A Practical Guide

## The Problem

NASA Earthdata uses several authentication mechanisms depending on the endpoint, and different tools handle them differently. It's easy to end up with a tangle of credentials, tokens, and config files that "work" without understanding why — until they don't.

This guide covers the auth mechanisms, how to set them up, and how to diagnose issues.

---

## Credential Setup

### 1. ~/.netrc (foundation — set this up first)

This is the most universal mechanism. Many tools (curl, wget, GDAL, R's curl package) read this automatically.

```
machine urs.earthdata.nasa.gov login YOUR_USER password YOUR_PASS
```

Permissions should be restrictive:

```bash
chmod 600 ~/.netrc
```

### 2. Environment Variables

Useful for scripts, CI, and bowerbird. Set in your shell profile or `.Renviron`:

```bash
# In ~/.Renviron or ~/.bashrc
EARTHDATA_USER=your_username
EARTHDATA_PASS=your_password
```

### 3. Bearer Token (for GDAL header file approach)

Generate a token via the URS API:

```bash
# POST is required — GET won't work
TOKEN=$(curl -s -n -X POST https://urs.earthdata.nasa.gov/api/users/tokens \
  | jq -r '.[0].access_token // empty')

# If empty, you may need to create a new token:
TOKEN=$(curl -s -n -X POST https://urs.earthdata.nasa.gov/api/users/token \
  | jq -r '.access_token')
```

Write it to a header file for GDAL:

```bash
echo "Authorization: Bearer $TOKEN" > ~/earthdata_header
```

**Tokens expire** (usually after ~90 days). If your header file stops working, regenerate.

### 4. S3 Credentials (for direct cloud access in us-west-2)

```bash
# Via earthdata login
curl -s -n -L \
  https://data.nsidc.earthdatacloud.nasa.gov/s3credentials \
  | jq .
```

These are temporary (~1 hour) and only work from AWS us-west-2.

---

## Auth Mechanisms by Endpoint Type

### Redirect-based OAuth (most DAAC data servers)

**How it works:** You hit the data URL → 302 redirect to `urs.earthdata.nasa.gov/oauth/authorize` → authenticate there → redirect back with a session cookie → cookie grants access to data.

**Identify it:** The final URL after a failed request lands on `urs.earthdata.nasa.gov/oauth/authorize?client_id=...`

**Requires:** Cookie jar + credentials that follow redirects.

**Hosts that use this:**

- `daacdata.apps.nsidc.org` (current NSIDC DAAC)
- Most `*.earthdatacloud.nasa.gov` endpoints

**Bearer token alone does NOT work** for these endpoints because the token is sent to the data host, which redirects you to URS, and the token doesn't follow the redirect.

### Direct bearer token

**How it works:** You send `Authorization: Bearer <token>` in the request header, and the server accepts it directly without redirecting.

**Identify it:** Request succeeds with just the header, no cookie dance needed.

**Hosts that use this:**

- Some older/simpler endpoints
- OPeNDAP servers (some)
- Harmony API

### Basic auth with preemptive send

**How it works:** Credentials are sent on the first request (before any 401 challenge), which lets the OAuth redirect pick them up.

**Key:** `httpauth = 1` (CURLOPT_HTTPAUTH = CURLAUTH_BASIC) forces preemptive sending.

**This is what makes bowerbird work** — `enforce_basic_auth = TRUE` in `build_curl_config`.

---

## Tool Configuration

### GDAL (/vsicurl/, /vsicurl_streaming/, gdalinfo, etc.)

**Option A: .netrc + cookie jar (most reliable for redirect-based OAuth)**

```bash
export GDAL_HTTP_COOKIEFILE=/tmp/cookies.txt
export GDAL_HTTP_COOKIEJAR=/tmp/cookies.txt
gdalinfo /vsicurl/https://daacdata.apps.nsidc.org/path/to/file.nc
```

Reads credentials from `~/.netrc`. The cookie jar handles the redirect dance.

**Option B: Bearer token header file (for endpoints that accept it directly)**

```bash
export GDAL_HTTP_HEADER_FILE=~/earthdata_header
gdalinfo /vsicurl/https://some-endpoint/path/to/file.nc
```

**Option C: Direct S3 (from us-west-2 only)**

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
gdalinfo /vsis3/nsidc-cumulus-prod-protected/path/to/file.nc
```

**When to use which:**

| Endpoint | Cookie+netrc | Bearer header | S3 |
|----------|:---:|:---:|:---:|
| daacdata.apps.nsidc.org | ✅ | ❌ | — |
| n5eil01u.ecs.nsidc.org (legacy) | ✅ | ✅ | — |
| *.earthdatacloud.nasa.gov | ✅ | varies | — |
| nsidc-cumulus-prod-protected (S3) | — | — | ✅ |

### bowerbird (R)

Uses `bb_handler_earthdata` which sets up curl with:

- `cookiefile` / `cookiejar` (session cookies)
- `followlocation = TRUE` (follow redirects)
- `enforce_basic_auth = TRUE` (preemptive credential send)
- `unrestricted_auth` (send creds across redirect hosts) — **must set `allow_unrestricted_auth = TRUE` in method params**

```r
bb_source(
  name = "My Dataset",
  source_url = "https://daacdata.apps.nsidc.org/pub/DATASETS/...",
  method = list("bb_handler_earthdata",
                level = 2,
                relative = TRUE,
                accept_download = "\\.nc$",
                allow_unrestricted_auth = TRUE),
  user = Sys.getenv("EARTHDATA_USER"),
  password = Sys.getenv("EARTHDATA_PASS")
)
```

### curl (R package, generic)

```r
h <- curl::new_handle()
curl::handle_setopt(h,
  followlocation = TRUE,
  cookiefile = "",
  cookiejar = tempfile(),
  unrestricted_auth = 1L,
  userpwd = paste0(user, ":", pass),
  httpauth = 1L  # CURLAUTH_BASIC — preemptive send
)
r <- curl::curl_fetch_memory(url, handle = h)
```

### wget

```bash
wget --http-user=USER --http-password=PASS \
     --auth-no-challenge \
     --load-cookies cookies.txt --save-cookies cookies.txt \
     --keep-session-cookies \
     URL
```

### curl (command line)

```bash
curl -n -L -b cookies.txt -c cookies.txt URL -o output.nc
```

The `-n` reads `~/.netrc`, `-L` follows redirects, `-b`/`-c` handle cookies.

---

## Diagnosing Auth Issues

### Step 1: Check what's happening

```r
h <- curl::new_handle()
curl::handle_setopt(h,
  followlocation = TRUE,
  cookiefile = "", cookiejar = tempfile(),
  unrestricted_auth = 1L,
  userpwd = paste0(user, ":", pass),
  httpauth = 1L,
  range = "0-0"  # don't download the whole file
)
r <- curl::curl_fetch_memory(url, handle = h)
r$status_code
r$type
r$url  # where did we actually end up?
curl::parse_headers_list(r$headers)
```

### Step 2: Interpret the result

| Symptom | Diagnosis | Fix |
|---------|-----------|-----|
| 401 + same URL | Creds rejected or not sent | Check .netrc / userpwd, add `httpauth = 1L` |
| 200 + `text/html` + URS URL | Landed on login page | Add cookies + `unrestricted_auth` |
| 200 + `text/html` + data URL | Got directory listing, not file | Check URL path |
| 200 + `application/x-netcdf` | Success! | — |
| 403 | App not authorized | Check URS profile → My Applications |
| 302 loop | Cookie not persisting | Ensure `cookiefile` AND `cookiejar` are set |

### Step 3: Check useful response headers

```
last-modified    → if present, bowerbird clobber=1 works (skip unchanged files)
accept-ranges    → if "bytes", partial reads work (GDAL /vsicurl/ efficient)
content-type     → confirms you got data, not an HTML error page
content-length   → sanity check file size
```

### Step 4: Check app authorization

Go to https://urs.earthdata.nasa.gov/profile → "My Applications" (under "Authorized Apps").

The required app name depends on the DAAC endpoint. If you get 403s after successful auth, you probably need to authorize a new app. Common ones:

- `NSIDC_DATAPOOL_OPS` (legacy NSIDC)
- Check the `client_id` in the OAuth redirect URL to identify the app

---

## Endpoint Discovery

When a dataset moves or you're setting up a new source, check:

1. **NSIDC dataset page** (e.g., https://nsidc.org/data/nsidc-0803/versions/2) — lists access options
2. **CMR (Common Metadata Repository):**
   ```bash
   curl -s "https://cmr.earthdata.nasa.gov/search/granules.json?short_name=NSIDC-0803&version=2&page_size=1" | jq '.feed.entry[0].links'
   ```
3. **earthaccess (Python, for discovery only):**
   ```python
   import earthaccess
   results = earthaccess.search_data(short_name="NSIDC-0803", version="2", count=1)
   print(results[0].data_links())
   ```

---

## Quick Decision Tree

```
Need to access Earthdata?
│
├─ Syncing files locally (bowerbird)?
│  → bb_handler_earthdata + allow_unrestricted_auth = TRUE
│  → user/pass from env vars
│
├─ Reading remotely with GDAL?
│  ├─ Try: GDAL_HTTP_HEADER_FILE with bearer token
│  │  └─ 401? → Switch to cookie+netrc approach:
│  │     export GDAL_HTTP_COOKIEFILE=/tmp/cookies.txt
│  │     export GDAL_HTTP_COOKIEJAR=/tmp/cookies.txt
│  └─ From AWS us-west-2? → Use /vsis3/ with temp S3 creds
│
├─ One-off download?
│  → curl -n -L -b cookies.txt -c cookies.txt URL -o file.nc
│
└─ Scripting in R?
   → curl package with httpauth=1L + cookies + unrestricted_auth
```

---

## NSIDC Sea Ice Specific Notes

### NSIDC-0081 (SSMIS) — RETIRED

- **Status:** Forward processing ended 15 January 2026
- **Last data:** ~mid-December 2025
- **Legacy host:** `n5eil01u.ecs.nsidc.org/PM/NSIDC-0081.002/` (known broken — returns browse images)

### NSIDC-0803 v2 (AMSR2) — REPLACEMENT

- **Host:** `daacdata.apps.nsidc.org/pub/DATASETS/nsidc0803_daily_a2_seaice_conc_v2/`
- **Auth:** Redirect-based OAuth → needs cookie+netrc or basic auth+cookies
- **Bearer token header:** Does NOT work (redirect drops it)
- **Last-Modified header:** ✅ Present (bowerbird clobber=1 works)
- **Accept-Ranges:** ✅ bytes (GDAL partial reads work)
- **Grid:** 304×448, 25 km polar stereographic, Hughes 1980 (same as NSIDC-0081)
- **Format:** netCDF, single band sea ice concentration
- **Filename pattern:** `NSIDC-0803_SEAICE_AMSR2_{N|S}_{YYYYMMDD}_v2.0.nc`
- **Coverage:** 2023-01-01 → present (forward processing, daily)
- **App authorization:** Check URS profile if you get 403s

### Format eras for the combined sea ice record

| Era | Source | Format | Notes |
|-----|--------|--------|-------|
| 1987–~2015 | NSIDC-0081 v1/v2 | Binary flat files | Fixed grid, no metadata |
| ~2015–2025 | NSIDC-0081 v2 reprocessed | netCDF | 0, 1, 2, or 3 bands (!); empty shells on missing days |
| 2023–present | NSIDC-0803 v2 | netCDF | Clean single band, proper metadata |

The overlap from 2023–2025 allows cross-calibration. AMSR2 places the ice edge slightly inboard compared to SSMIS, giving somewhat smaller extent values.
