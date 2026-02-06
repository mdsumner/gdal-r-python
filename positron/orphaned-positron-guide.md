# Positron Remote SSH: Orphaned Processes & Cleanup Guide

**Created:** 2026-02-07  
**Context:** Positron (Windows client) → SSH → Linux VM (Ubuntu)  
**User:** mdsumner

## The Problem

When Positron connects to a remote VM via SSH, it launches server-side processes:

- **REH server** (`node` process) — the Remote Extension Host
- **kcserver** (Kallichore) — the kernel supervisor
- **ark** — the R kernel process
- **supervisor-wrapper** bash shells

When you close Positron on Windows, sleep your laptop, or lose network connectivity,
the SSH tunnel drops — but **these server-side processes keep running**. They are
designed to survive brief disconnections so you can reconnect, but there is no
heartbeat/timeout mechanism to shut them down when the client is truly gone.

Each time Positron updates on Windows, a **new server version** is deployed to the VM
(under `~/.positron-server/bin/<commit-hash>/`). The old version's processes remain
running, and the new version doesn't clean them up.

Over time this leads to:

- Dozens of orphaned processes consuming PIDs and (minimal) memory
- Stale Unix domain sockets in `/run/user/<uid>/`
- Multiple old server versions consuming gigabytes of disk
- Potential interference with new sessions ("Discovering interpreters" hangs)

---

## 1. Manual Cleanup

### Kill orphaned processes

```bash
# See what's running
ps aux | grep -E '(ark|kcserver|supervisor-wrapper|positron-server)' | grep -v grep

# Kill all ark and kcserver processes (nuclear option — do this when NOT connected)
pkill -f kcserver
pkill -f 'ark.*--startup-notif'
pkill -f 'supervisor-wrapper'

# If you ARE currently connected in Positron and want to keep the current session,
# identify the current server commit hash first:
ls -lt ~/.positron-server/bin/ | head -2
# Then kill only processes from OTHER versions:
# ps aux | grep '<old-hash>' | grep -v grep | awk '{print $2}' | xargs -r kill
```

### Clean up stale temp files and sockets

```bash
# Stale Kallichore sockets (adjust UID as needed)
rm -f /run/user/$(id -u)/kc-*.sock

# Stale temp files
rm -f /tmp/kallichore-*.log /tmp/kallichore-*.json /tmp/kallichore-*.out.log
rm -f /tmp/registration_r-*.json /tmp/kernel_log_r-*.txt
rm -rf /tmp/kernel-*/
```

### Remove old server versions

```bash
# List all server versions and their sizes
du -sh ~/.positron-server/bin/*/

# Identify the current (most recent) version
ls -lt ~/.positron-server/bin/ | head -5

# Remove all EXCEPT the current version
# Replace <current-hash> with the one you want to keep:
cd ~/.positron-server/bin/
ls | grep -v '<current-hash>' | xargs -r rm -rf

# Also clean old extension installs if present
du -sh ~/.positron-server/extensions/
# You can safely remove extensions for old server versions
```

---

## 2. Automated Cleanup with Cron

Create a cleanup script and schedule it to run periodically.

### Create the script

```bash
cat > ~/bin/positron-cleanup.sh << 'EOF'
#!/bin/bash
# positron-cleanup.sh — Kill orphaned Positron remote processes
# and clean up stale temp files.
#
# This script is safe to run when Positron is NOT connected.
# If Positron IS connected, it will kill the active session too.

LOG="$HOME/.positron-cleanup.log"
echo "$(date -Iseconds) — Running Positron cleanup" >> "$LOG"

# Kill orphaned kernel-related processes
for pattern in 'kcserver' 'ark.*--startup-notif' 'supervisor-wrapper'; do
    count=$(pgrep -fc "$pattern" 2>/dev/null || true)
    if [ "$count" -gt 0 ]; then
        echo "  Killing $count processes matching '$pattern'" >> "$LOG"
        pkill -f "$pattern" 2>/dev/null || true
    fi
done

# Clean stale sockets
rm -f /run/user/$(id -u)/kc-*.sock 2>/dev/null

# Clean stale temp files
rm -f /tmp/kallichore-*.log /tmp/kallichore-*.json /tmp/kallichore-*.out.log 2>/dev/null
rm -f /tmp/registration_r-*.json /tmp/kernel_log_r-*.txt 2>/dev/null
rm -rf /tmp/kernel-*/ 2>/dev/null

# Keep only the most recent server version
POSITRON_BIN="$HOME/.positron-server/bin"
if [ -d "$POSITRON_BIN" ]; then
    NEWEST=$(ls -t "$POSITRON_BIN" | head -1)
    if [ -n "$NEWEST" ]; then
        REMOVED=0
        for dir in "$POSITRON_BIN"/*/; do
            dirname=$(basename "$dir")
            if [ "$dirname" != "$NEWEST" ]; then
                rm -rf "$dir"
                REMOVED=$((REMOVED + 1))
            fi
        done
        [ "$REMOVED" -gt 0 ] && echo "  Removed $REMOVED old server versions (kept $NEWEST)" >> "$LOG"
    fi
fi

echo "$(date -Iseconds) — Cleanup complete" >> "$LOG"
EOF

chmod +x ~/bin/positron-cleanup.sh
```

### Schedule with cron

```bash
# Run cleanup daily at 4am (adjust to your timezone/preference)
(crontab -l 2>/dev/null; echo "0 4 * * * $HOME/bin/positron-cleanup.sh") | crontab -

# Verify
crontab -l
```

### Or: run on demand after disconnecting

If you prefer not to use cron (e.g. you don't want it killing an active session),
just SSH in and run:

```bash
~/bin/positron-cleanup.sh
```

---

## 3. Quick Reference

| Task | Command |
|---|---|
| List orphaned processes | `ps aux \| grep -E '(ark\|kcserver)' \| grep -v grep` |
| Kill all Positron kernels | `pkill -f kcserver; pkill -f 'ark.*--startup-notif'` |
| Check disk usage of server versions | `du -sh ~/.positron-server/bin/*/` |
| Check overall disk usage | `df -h /` |
| View cleanup log | `cat ~/.positron-cleanup.log` |
| Restart interpreter in Positron | Command Palette → "Interpreter: Restart R" |
| Reload Positron window | Command Palette → "Developer: Reload Window" |

---

## Notes

- This is fundamentally the same issue VS Code Remote SSH has — Positron inherits
  the behaviour from the VS Code architecture.
- The `~/.positron-server/` directory is the remote equivalent of the Positron
  install — it contains the server binary and extensions for each version.
- Memory impact of orphaned processes is usually small; **disk usage** from old
  server versions is the bigger concern.
- If "Discovering interpreters" gets stuck, try: kill orphans → clean sockets →
  reload window.
