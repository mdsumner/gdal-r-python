# Positron Remote SSH: Performance Troubleshooting Guide

**Created:** 2026-04-15
**Context:** Positron (Windows client) → SSH → Linux VM (Ubuntu)
**User:** mdsumner
**Companion to:** [orphaned-positron-guide.md](./orphaned-positron-guide.md)

## The Problem

When a Positron remote session "feels heavy" — laggy
typing, sluggish interpreter, htop full of `node` - the cause could be one of the issues noted here. 

- An **extension** doing unbounded work (CMake/C++ language servers in big source
  trees, bloated TypeScript projects, etc.)
- The **file watcher** or **search indexer** scanning a directory that should be
  excluded (`_targets/`, `.venv/`, `*.zarr/`, `build/`, `node_modules/`)
- **Competing workloads** on the same VM — cron jobs, `crew`/`targets` pipelines,
  orphaned R processes from previous sessions
- **Misattribution** — nginx, Docker, k3s, etc. showing up in htop and looking
  alarming but actually being fine

This guide is a checklist for distinguishing those cases quickly.

---

## 1. First: Identify What's Actually Heavy

Before fixing anything, get a ranked view of the real culprits. Top-by-RSS is
usually more informative than top-by-CPU because the failure modes here tend to
be memory bloat rather than sustained CPU.

```bash
ps -u $USER -o pid,rss,pcpu,etime,cmd --sort=-rss \
  | grep -E 'node|ark|rsession|Rscript|R ' \
  | head -20
```

Interpret the output:

| Process | Healthy RSS | Red flag |
|---|---|---|
| `server-main.js` (remote server) | 100–200 MB | > 500 MB |
| `extensionHost` (hosts all extensions) | 200–400 MB | > 800 MB |
| `fileWatcher` | 50–100 MB | > 300 MB, or high CPU |
| `ptyHost` | 50–100 MB | — |
| `ark` (Positron R kernel) | 100–300 MB idle | grows unbounded with session |
| Individual language server (e.g. cmake, clangd) | 100–500 MB | > 1 GB |

**Rule of thumb:** a single Positron window has ~6–10 node processes in htop.
That's normal. The question is whether any *one* of them is bloated, not how
many there are.

---

## 2. Extension Host Bloat

The extension host (`--type=extensionHost`) is a single node process that
hosts **all** your extensions. If one extension is misbehaving, the whole
extension host inherits the cost, and all you see in htop is a fat `node`.

### Diagnose

```
Command Palette → "Developer: Show Running Extensions"
```

This gives you a per-extension activation time, CPU, and memory breakdown.
Much more useful than guessing from `ps`.

Some possible offenders:

- **CMake IntelliSense** extensions (`kylinideteam.cmake-intellisence`, others) —
  will try to parse every `CMakeLists.txt` in a big source tree like GDAL.
  Easy to accidentally have two installed at once.
- **C/C++ extensions** with IntelliSense enabled on a tree the size of GDAL.
  `clangd` with a proper `compile_commands.json` is much lighter than the
  Microsoft C/C++ extension's IntelliSense mode.
- **GitLens / Git Graph** on a repo with a huge history.
- **Python / Pylance** on a venv with thousands of installed packages.

### Fix

Disable the offender **for the workspace only**, not globally:

```
Extensions view → find extension → gear icon → "Disable (Workspace)"
```

Then restart the extension host (see §6). Confirm the RSS dropped.

Rule: if you aren't actively using an extension in this workspace, disable it
for this workspace. The cost of a spurious language server running in the
background is much higher than the cost of re-enabling it later.

---

## 3. File Watcher and Search Indexer

Positron (via VS Code architecture) watches the workspace tree for changes and
indexes it for search. Both are normally cheap. They become expensive when the
workspace contains large auto-generated or data directories.

### Diagnose: inotify watch count

On Linux there's a per-user limit on inotify watches. Check where you are:

```bash
# Watches currently held by this user
find /proc/*/fd -lname 'anon_inode:inotify' 2>/dev/null \
  | xargs -I {} sh -c 'readlink {}' 2>/dev/null | wc -l

# The system limit
cat /proc/sys/fs/inotify/max_user_watches
```

If watch count is near the limit, raise it:

```bash
echo 'fs.inotify.max_user_watches=1048576' | sudo tee /etc/sysctl.d/60-inotify.conf
sudo sysctl --system
```

A few watches (single digits, low tens) when you have a GB-scale workspace open
means the watcher **isn't actually trying** — either it's already been told to
exclude things, or the workspace is being opened correctly.

### Fix: exclude heavy directories

Add to `.vscode/settings.json` in the workspace:

```json
{
  "files.watcherExclude": {
    "**/_targets/**": true,
    "**/.venv/**": true,
    "**/*.zarr/**": true,
    "**/cache/**": true,
    "**/build/**": true,
    "**/build-*/**": true,
    "**/third_party/**": true,
    "**/.git/objects/**": true
  },
  "search.exclude": {
    "**/_targets/**": true,
    "**/*.zarr/**": true,
    "**/build/**": true,
    "**/autotest/*/data/**": true
  },
  "files.exclude": {
    "**/build/**": true
  }
}
```

Notable defaults in this environment worth excluding:

- `_targets/` — `targets` pipeline stores, often thousands of files
- `*.zarr/` — Zarr stores have chunk-per-file layouts, thousands to millions
- `build/`, `build-*/` — CMake build trees (GDAL, gdalraster, etc.)
- `autotest/*/data/` in GDAL — huge test fixture directories
- `.venv/`, `node_modules/` — standard suspects

### The biggest footgun

**Never open `~` as your workspace.** See note in the orphaned-processes guide.
Always open a specific project directory.

---

## 4. Competing Workloads on the Same VM

Positron feels slow when the VM is busy with something else. On an interactive
server that's also running cron jobs or background pipelines, this is the
default state.

### Diagnose

```bash
# Top CPU and memory users overall
ps -eo pid,user,rss,pcpu,etime,cmd --sort=-pcpu | head -20
ps -eo pid,user,rss,pcpu,etime,cmd --sort=-rss | head -20

# Anything running under your user that isn't Positron?
ps -u $USER -o pid,rss,pcpu,etime,cmd --sort=-pcpu \
  | grep -vE 'node|ark|pts|grep' | head
```

Look for:

- **Multiple R processes** from the same script — classic cron overlap (see §5).
- **`crew_worker` / `mirai::dispatcher`** — a `targets` pipeline is running.
  Check whether it's supposed to be (tar_make in another session) or whether
  it's cron. Workers are children of the orchestrator; the dispatcher gets
  reparented to init (PPID 1) and won't die when the orchestrator does.
- **Zombie Rscripts** — long `etime`, no interactive parent.

### Fix

Kill overlapping runs:

```bash
# Find them
ps -fp <PID1>,<PID2>,...

# Polite — let R run on.exit handlers
kill <PIDs>

# For a crew/targets tree, kill the orchestrator and workers follow:
kill <orchestrator-PID>
sleep 15
# Then the dispatcher separately (PPID 1)
kill <dispatcher-PID>
# Mop up stragglers
pgrep -u $USER -f 'crew_worker|mirai::dispatcher' | xargs -r kill -9
```

---

## 5. Cron Overlap — the `flock` Pattern

The classic cause of "lots of heavy R processes under my user": a cron job runs
every N minutes, but each run now takes longer than N, so new runs pile on top
of old runs.

### Diagnose

```bash
# Every crontab on the system
crontab -l
sudo ls /var/spool/cron/crontabs/
sudo cat /etc/crontab
sudo ls /etc/cron.d/
systemctl list-timers --all

# Recent cron activity
sudo journalctl -u cron -n 100 --no-pager
```

### Fix: wrap with `flock`

The `flock(1)` utility takes a lock on a file; `-n` means non-blocking. If the
previous run hasn't finished, the new invocation exits immediately instead of
running concurrently.

```cron
*/15 * * * * flock -n /tmp/harvest.lock /usr/bin/Rscript /home/user/project/harvest.R >> /home/user/logs/harvest.log 2>&1
```

Optionally throttle further:

```cron
*/15 * * * * flock -n /tmp/job.lock nice -n 19 ionice -c3 /path/to/script.sh
```

This should be the default for every long-running cron job on a shared VM.

### For `targets` pipelines specifically

On top of `flock`, check `tar_option_set()`:

```r
tar_option_set(
  storage = "worker",     # default "main" — round-trips target data through orchestrator
  retrieval = "worker",
  format = "qs"           # smaller than default RDS for large targets
)

crew_controller_local(
  workers = N,
  seconds_idle = 30       # release idle workers instead of squatting on memory
)
```

An orchestrator holding 11 GB of RSS is almost always `storage = "main"` round-tripping.

---

## 6. Restarting Things, Smallest to Largest

When something's wrong, don't just close and reopen the window.
Pick the lightest restart that fixes it.

| Level | Command | Keeps alive | Use when |
|---|---|---|---|
| 1. Restart Extension Host | `Developer: Restart Extension Host` | SSH tunnel, terminals, R session, tabs | An extension is misbehaving; settings changes to extensions need to apply |
| 2. Reload Window | `Developer: Reload Window` | SSH tunnel, remote server | UI is weird; R session can die |
| 3. Restart Interpreter | `Interpreter: Restart R` | Everything else | R kernel specifically is stuck/bloated |
| 4. Kill Remote Server | `Remote: Kill VS Code Server on Host` | Nothing — full reconnect | Remote server itself is wedged; after Positron version update |
| 5. Reboot VM | `sudo reboot` | Nothing | Kernel-level issue; orphaned processes; tmpfs cleanup |

Level 1 handles 90% of "feels heavy" cases.

---

## 7. Misattribution — Things That Look Bad But Aren't

Not everything in htop that looks alarming is actually a problem. Quick
reference for common false alarms:

### nginx: dozens of worker processes

Normal. nginx runs one worker per CPU core by default (`worker_processes auto`).
Each worker is ~8 MB RSS, 0% CPU when idle. An 8-core VM with `proxy_cache`
enabled will show 10+ nginx processes; a 16-core one will show 20+. All healthy.

```bash
nproc                                            # core count
ps -eo cmd | grep -c '[n]ginx: worker'           # worker count — should match
ps -eo pid,user,cmd | grep '[n]ginx'             # roles (master/worker/cache)
```

### Many node processes per Positron window

Normal. A single Positron window spawns `server-main`, `extensionHost`,
`fileWatcher`, `ptyHost`, `sharedProcess`, plus one node process per language
server. 8–12 `node` entries in htop for one connection is expected.

The question is never "how many?", it's "is any *one* of them bloated?" (§1).

### ark at 100–300 MB

Normal for an R session with packages loaded. Only a concern if it grows
unbounded over time, which usually means something in your R session is
leaking (open GDAL datasets not closed, growing lists, etc.).

### fail2ban, systemd-journald, etc. at low CPU

Normal background services. Ignore unless they're actively chewing CPU.

---

## 8. Public-Facing Services: Drift Check

Unrelated to Positron performance directly, but worth checking periodically on
any VM with a public IP: **what's exposed and do you still want it exposed?**

```bash
# What nginx routes exist
sudo nginx -T | grep -E 'server_name|listen|proxy_pass'

# What's actually listening publicly
sudo ss -tlnp | grep -v '127\.0\.0\.'

# Has anything been hitting exposed endpoints?
sudo grep -E 'tile|api|health|admin|\.env|\.git' /var/log/nginx/access.log \
  | awk '{print $1}' | sort | uniq -c | sort -rn | head -20
```

The internet background radiation rate is high — any public v4 address gets
scanned continuously — but specific, non-wordlist paths (`/tile`, `/chunk`,
custom API routes) usually see effectively zero traffic unless something links
to them publicly.

Rule: dev-grade services should bind to `127.0.0.1` only, and you reach them
via SSH tunnel:

```bash
ssh -L 8080:localhost:8080 vm-hostname
# then http://localhost:8080/ on your laptop
```

No public nginx route needed, zero attack surface. Remove `proxy_pass` lines
for anything not actually in production use.

---

## 9. Quick Reference

| Task | Command |
|---|---|
| Rank own processes by memory | `ps -u $USER -o pid,rss,pcpu,etime,cmd --sort=-rss \| head` |
| Show Positron extension resource use | Command Palette → `Developer: Show Running Extensions` |
| Restart extension host | Command Palette → `Developer: Restart Extension Host` |
| Check inotify watch count | `find /proc/*/fd -lname 'anon_inode:inotify' 2>/dev/null \| xargs -I {} readlink {} 2>/dev/null \| wc -l` |
| Count nginx workers vs cores | `nproc; ps -eo cmd \| grep -c '[n]ginx: worker'` |
| Listening public ports | `sudo ss -tlnp \| grep -v '127\.0\.0\.'` |
| All scheduled jobs | `crontab -l; sudo cat /etc/crontab; systemctl list-timers --all` |
| Kill a targets/crew tree | `kill <orchestrator>; sleep 15; kill <dispatcher>` |

---

## Notes

- The single biggest lesson: **don't run production scheduled work on the same
  VM you use interactively**. Cron pile-up, extension bloat, and public-facing
  services all compete for the same RAM and CPU budget. Move scheduled jobs to
  a dedicated runner (OpenStack VM, GitHub Actions, Pawsey compute) and keep
  the interactive box interactive.
- Extension host bloat is usually caused by **one specific extension** on a
  **large source tree**. GDAL + CMake language servers is the canonical pairing.
- The file watcher is rarely the actual problem on modern kernels with a high
  `max_user_watches` — but when it is, it's usually because something inside the
  workspace shouldn't be watched (Zarr stores, `_targets/`, `build/`).
- `flock -n` in cron entries is cheap insurance against the oldest shared-VM
  problem in the book. Add it by default.
- When the extension host is bloated, **Restart Extension Host** is almost
  always the right first move. It's 2 seconds and keeps your SSH session,
  terminals, and R process alive.
- See the companion [orphaned-positron-guide.md](./orphaned-positron-guide.md)
  for cleanup of stale server-side processes left behind by disconnected sessions.
