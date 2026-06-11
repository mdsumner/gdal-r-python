#!/usr/bin/env python3
"""
probe_icechunk_gdal.py — systematically test Icechunk (and Zarr) stores against
GDAL's Icechunk driver, classifying each result so you can work through a corpus
(e.g. eeholmes' gridlook list) and leave a reproducible status table.

Reads a datasets JSON (the gridlook_zarr_examples.json shape, or icechunk_stores.json),
runs `gdal mdim info <store-root>` per entry with anonymous + region config,
captures the outcome, and buckets it into:

  OK             - opened, hierarchy printed
  OK_NO_DATA     - opened (metadata) but a data/stats read failed (e.g. pcodec)   [with --stats]
  CRS_RANK       - 'manifest extents has not expected dimension count' (0-D crs/grid-mapping)
  CODEC          - unknown/unsupported codec (pcodec etc.)
  AUTH           - 403 / access denied / signature
  NOT_FOUND      - 404 / bucket or key missing
  NOT_ICECHUNK   - opened by another driver / not an icechunk store
  GRID           - grid/coords not recognized
  TIMEOUT        - exceeded per-store timeout
  OTHER          - anything else (message captured)

Usage:
  python3 probe_icechunk_gdal.py datasets.json [--only-icechunk] [--stats]
        [--timeout 120] [--gdal gdal] [--out results.json] [--csv results.csv]
        [--filter substr] [--limit N]

Notes:
- Designed to be safe to re-run; it shells out to the `gdal` CLI you point at.
- For each entry it derives a /vsis3/ or /vsicurl/ path and a region.
- It does NOT attempt branch/tag or per-array reads by default; add --stats to
  force a real chunk decode on the first 2D array (surfaces codec gaps).
"""
import argparse, csv, json, os, re, subprocess, sys, time
from urllib.parse import urlparse

# ---- classification patterns (ordered; first match wins) --------------------
PATTERNS = [
    ("CRS_RANK",     re.compile(r"manifest extents has not expected dimension count", re.I)),
    ("CODEC",        re.compile(r"codec not available|UnknownCodec|pcodec|unsupported codec", re.I)),
    ("AUTH",         re.compile(r"\b403\b|AccessDenied|SignatureDoesNotMatch|not authorized", re.I)),
    ("NOT_FOUND",    re.compile(r"\b404\b|BucketNotFound|NoSuchBucket|specified key does not exist|ObjectNotFound", re.I)),
    ("GRID",         re.compile(r"not a grid|grid not recognized|no georeferenc", re.I)),
    ("TOO_SMALL",    re.compile(r"too small file", re.I)),
    ("NOT_ICECHUNK", re.compile(r"not recognized as a supported|Invalid Icechunk", re.I)),
]

def derive_path_and_region(entry):
    """Return (gdal_input, region, scheme) from an entry dict."""
    region = entry.get("region")
    # prefer an explicit vsis3 field if present
    if entry.get("vsis3"):
        return entry["vsis3"], region, "s3"
    url = entry.get("url") or entry.get("store_root") or ""
    if url.startswith("/vsis3/") or url.startswith("/vsicurl/") or url.startswith("/vsigs/"):
        return url.rstrip("/"), region, "vsi"
    u = urlparse(url)
    host, path = u.netloc, u.path
    # s3 virtual-hosted: <bucket>.s3[.-]<region>.amazonaws.com/<key>
    m = re.match(r"(?P<bucket>[^.]+)\.s3[.-](?P<region>[a-z0-9-]+)\.amazonaws\.com", host)
    if m:
        region = region or m.group("region")
        return f"/vsis3/{m.group('bucket')}{path}".rstrip("/"), region, "s3"
    # s3 path-style or source.coop style host that already begins with region:
    if host.endswith("amazonaws.com") or "source.coop" in host or "opendata" in host:
        # treat host as bucket (covers us-west-2.opendata.source.coop/...)
        # region may be embedded in host (e.g. us-west-2.opendata.source.coop)
        rm = re.match(r"(?P<region>[a-z]{2}-[a-z]+-\d)\.", host)
        region = region or (rm.group("region") if rm else None)
        return f"/vsis3/{host}{path}".rstrip("/"), region, "s3"
    # gcs
    if "storage.googleapis.com" in host:
        return f"/vsigs/{path.lstrip('/')}".rstrip("/"), region, "gs"
    # fallback: vsicurl the https URL
    if u.scheme in ("http", "https"):
        return f"/vsicurl/{url.rstrip('/')}", region, "curl"
    return url.rstrip("/"), region, "unknown"

def build_cmd(gdal, gdal_input, region, scheme, stats):
    cmd = [gdal, "mdim", "info", gdal_input]
    if scheme in ("s3",):
        cmd += ["--config", "AWS_NO_SIGN_REQUEST", "YES"]
        if region:
            cmd += ["--config", "AWS_REGION", region]
    elif scheme == "gs":
        cmd += ["--config", "GS_NO_SIGN_REQUEST", "YES"]
    elif scheme == "curl":
        pass  # anonymous http
    if stats:
        cmd += ["-stats"]   # forces a chunk decode; surfaces codec gaps
    return cmd

def classify(returncode, out, err, parsed_ok):
    blob = (out or "") + "\n" + (err or "")
    if returncode == 0 and parsed_ok:
        return "OK"
    for label, pat in PATTERNS:
        if pat.search(blob):
            # too_small after auth is usually an auth root cause
            if label == "TOO_SMALL" and re.search(r"\b403\b|AccessDenied", blob):
                return "AUTH"
            return label
    if returncode == 0:
        return "OK_NO_TREE"
    return "OTHER"

def looks_like_tree(out):
    try:
        j = json.loads(out)
        return isinstance(j, dict) and (j.get("arrays") or j.get("dimensions") or j.get("type"))
    except Exception:
        # mdim info may print non-JSON in some modes; fall back to heuristic
        return ('"arrays"' in out) or ('"dimensions"' in out) or ("Driver: Icechunk" in out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("datasets_json")
    ap.add_argument("--only-icechunk", action="store_true")
    ap.add_argument("--stats", action="store_true", help="force a chunk decode (surfaces codec gaps like pcodec)")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--gdal", default="gdal")
    ap.add_argument("--out", default="probe_results.json")
    ap.add_argument("--csv", default="probe_results.csv")
    ap.add_argument("--filter", default=None, help="only entries whose title/url contains this substring")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="print commands, do not run")
    args = ap.parse_args()

    d = json.load(open(args.datasets_json))
    entries = d.get("datasets") or d.get("stores") or []
    if args.only_icechunk:
        entries = [e for e in entries if e.get("is_icechunk", True)]
    if args.filter:
        f = args.filter.lower()
        entries = [e for e in entries if f in (e.get("title","")+e.get("url","")+e.get("name","")).lower()]
    if args.limit:
        entries = entries[:args.limit]

    results = []
    for i, e in enumerate(entries, 1):
        title = e.get("title") or e.get("name") or e.get("url") or "?"
        gi, region, scheme = derive_path_and_region(e)
        cmd = build_cmd(args.gdal, gi, region, scheme, args.stats)
        printable = " ".join(cmd)
        if args.dry_run:
            print(f"[{i}/{len(entries)}] {title}\n    {printable}")
            continue
        t0 = time.time()
        status, out, err, rc = "TIMEOUT", "", "", None
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
            rc, out, err = p.returncode, p.stdout, p.stderr
            status = classify(rc, out, err, looks_like_tree(out))
        except subprocess.TimeoutExpired:
            status = "TIMEOUT"
        dt = round(time.time() - t0, 1)
        first_err = ""
        for line in (err or "").splitlines():
            if "ERROR" in line or "Warning" in line:
                first_err = line.strip(); break
        row = {"title": title, "input": gi, "region": region, "scheme": scheme,
               "status": status, "seconds": dt, "returncode": rc,
               "first_error": first_err, "cmd": printable}
        results.append(row)
        print(f"[{i}/{len(entries)}] {status:12} {dt:6}s  {title}")
        if first_err:
            print(f"             {first_err}")

    if not args.dry_run:
        json.dump({"probed": len(results), "stats_mode": args.stats, "results": results},
                  open(args.out, "w"), indent=2)
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["status","title","region","scheme","seconds","first_error","input"])
            w.writeheader()
            for r in results:
                w.writerow({k: r[k] for k in ["status","title","region","scheme","seconds","first_error","input"]})
        # summary
        from collections import Counter
        c = Counter(r["status"] for r in results)
        print("\n=== summary ===")
        for k, v in sorted(c.items(), key=lambda kv: -kv[1]):
            print(f"  {k:12} {v}")
        print(f"\nwrote {args.out} and {args.csv}")

if __name__ == "__main__":
    main()
