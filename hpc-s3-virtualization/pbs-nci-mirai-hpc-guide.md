# Extensible automation on the NCI PBS queue, and mirai on a single HPC node

*A practical guide distilled from building `vrefs` (GDAL-composed, rhdf5-scanned
virtual Zarr reference stores) on NCI Gadi. It covers the workflow that turned an
interactive ordeal into a parameterised batch pipeline, and the specific gotchas
of running `mirai` workers inside a Singularity container on one node. Written for
the budding mirai-on-HPC user; assumes a single compute node (not multi-node MPI).*

## The shape that worked: three independent layers

The thing that made experiments cheap to branch was factoring the workflow into
three pieces that vary independently:

1. **The PBS script** — pure plumbing: resource requests, storage flags, the
   container invocation, environment in, log out. It does not know what R runs.
2. **The run-script** (`*.R`) — orchestration: `source()` the helpers, build
   inputs, call one function. It does not know how the job was launched.
3. **The helpers** — layered functions (`.time_iter`, `.time_probe`, `run_one`,
   `run_n`, `run_all`) so you can act at any scope without rewriting.

A new experiment is then "pick a new point in (job config × what-to-do × scope),"
not a rewrite. Crucially, these seams were discovered by *factoring as you debug*
— each indirection (`Sys.getenv("HELPERS")`, `VREFS_LIB`, env-driven mode) was
added to fix a specific pain, and the cumulative result is a parameterised
harness. Pain-driven factoring beats speculative design: each seam earned its
place. The discipline that keeps it from rotting: add the knob when the second
use-case appears, not before.

The endpoint of this is config-not-code: a single run-script that dispatches on
environment variables (`RUN_MODE`, `RUN_N`, `WORKERS`), passed via
`qsub -v RUN_MODE=profile,RUN_N=12`. Then "new experiment" is a new `qsub` line.

## PBS gotchas (NCI Gadi, PBS Pro)

- **`-l storage=gdata/<proj>+...` is mandatory.** Without declaring every
  `/g/data` (and `scratch`) tree you touch, those filesystems are simply not
  mounted on the compute node, and opens fail or hang. Declare both read trees
  (source data) and write trees (output). Match them with the container binds.
- **`-l wd`** starts the job in the submission directory. The PBS log
  (`<jobname>.o<jobid>`) is written there too.
- **`-j oe`** merges stderr into the stdout log. Without it, stderr (where R's
  `message()` goes) lands in a separate `<jobname>.e<jobid>`. If "output is
  missing," check for a `.e` file before assuming nothing ran.
- **Read the resource epilogue.** NCI appends actual walltime / memory / CPU /
  exit status to the log. `CPU Time ~ 0` with large elapsed = I/O-bound (the
  normal case here). A 3-second clean exit with no output usually means the job
  died in *bash* before launching R (see below), not in R.
- **`/jobfs/...` (node-local scratch) is deleted when the job ends.** Throwaway
  confirm runs can write there; the real product must go to `/g/data`. Helpers
  that default outputs to `tempdir()` are writing to jobfs — fine for tests,
  fatal for the actual store.

## The bash layer bites before R ever runs

Several lost submit-cycles were pure shell, all in the same family — the shell is
whitespace- and quote-sensitive in ways the editor does not show:

- **`set -u` + `Rscript -e "...$var..."`.** A double-quoted `-e` string is
  expanded by *bash first*: any `$` meant for R (e.g. `src$access`) is read as a
  shell variable, found unset under `set -u`, and aborts the job ("unbound
  variable") before R starts. **Fix: put the R in its own file**, run
  `Rscript file.R`, and pass values as environment variables read with
  `Sys.getenv()`. Bash then parses none of your R.
- **Singularity does not inherit the host environment by default.** Pass values
  in with `--env NAME="$VALUE",...` (or `SINGULARITYENV_NAME=...`). The R inside
  the container reads them via `Sys.getenv()`.
- **Backslash line-continuations break on trailing whitespace.** A `\` must be
  the last character on the line; a single trailing space makes bash end the
  command there and run the next line separately (symptom: "exec requires at
  least 2 args, only received 1"). **Fix: put the `singularity exec ...` call on
  one line, or use a bash array** — no continuations to corrupt.
- **`mkdir -p "$(dirname "$LOG")"` before redirecting** to it, or `set -e` kills
  the job when the redirect target's directory doesn't exist.
- **Use absolute paths inside the container.** `~` may not expand (and may point
  elsewhere) inside the container; relative paths depend on the container's CWD.
  Keep run-scripts, the `.sif`, helpers, and outputs under a bound `/g/data`
  tree and reference them absolutely.

A robustness pattern that ends the "where did my output go" guessing: redirect
the whole container call to a known file on `/g/data` and `tail -f` it:
`singularity exec ... Rscript run.R > /g/data/.../run.log 2>&1`. This captures
stdout *and* stderr in one place you can watch live, independent of PBS's
deferred `.o`/`.e` capture. Inside R, `cat()`+`flush(stdout())` and a loud
`stopifnot(nrow(src) > 0)` beat silent `message()` for "did anything happen."

## Editing on HPC: don't paste big files into nano

Pasting into a terminal editor sends text as fast keystrokes, so editor features
fire per character. **Auto-indent** re-indents each pasted line on top of its own
indentation (indentation accumulates), and **hard-wrap** inserts real newlines
mid-line — together they scramble nested code. Short flat snippets slip under the
threshold; large indented files (functions) get mangled.

The durable fix is to take the editor out of the loop: **edit on GitHub, then pull
the file down** with `curl`/`git pull`. The batch job already does this
(`source()` a raw URL or a checked-out file), so make your editing match. For
short one-off files, a quoted heredoc (`cat > f <<'EOF' ... EOF`) writes verbatim
with no editor and no `$`-expansion. Reserve nano for tiny edits where a few lines
of auto-indent don't matter.

## Containers: a writable lib over an immutable image

The `.sif` is read-only squashfs, so you cannot `install.packages()` into it
persistently. The pattern:

- **Heavy, stable, compiled deps (GDAL, rhdf5, R itself) live baked in the
  image.** Rebuild the image rarely.
- **The package you iterate on lives in a writable, bind-mounted lib**
  (`~/lib` or `/g/data/.../lib`), loaded with `lib.loc=`. A `VREFS_LIB` env var
  makes the path swappable per environment. Reinstall it every push; never
  rebuild the image for it.
- **ABI safety:** install the writable-lib package *from inside the container*
  (its R, its toolchain), so any compiled code matches the runtime that loads it.
  Pure-R packages are forgiving; anything with C is not — keep compiled deps
  (rhdf5) baked, only pure-R iteration in the writable lib.

This is the same "thin swappable layer over a stable substrate" idea as the data
architecture: the image is the engine; helpers + run-scripts + the writable-lib
package are the dumb, version-pinnable bits. The job is then reproducible from two
pinnable sources: the image hash and the git SHA.

## mirai on a single node

The work here (per-file chunk-index scans) is embarrassingly parallel,
near-zero-CPU, and I/O-latency-bound — the textbook case for many workers each
*waiting* on the filesystem. Parallelism does not speed up any single scan; it
**overlaps the idle latency** of many scans. Notes specific to mirai-in-container:

- **Use `mirai` (separate processes), not fork (`mclapply`).** HDF5 / ros3 hold
  C-level state and are not fork-safe; forked workers risk intermittent
  segfaults. `mirai` daemons are independent R processes, each with its own
  library init and its own file handles — the safe choice.
- **Inside one container, daemons inherit `.libPaths()` and the environment**
  (they are child processes of the same R), so `mirai::daemons(n)` often "just
  works" with no launch config. *Test with `n = 2` first* to confirm they see the
  writable lib before scaling.
- **Daemons do NOT inherit attached packages or your defined functions.** A
  closure mapped to workers must resolve everything it calls: reference package
  functions namespaced (`pkg::fn`, `pkg:::internal_fn`), or prime daemons once
  with `mirai::everywhere({ .libPaths(...); library(pkg) })`. A worker that fails
  to resolve a symbol returns an *error object*, and a downstream `rbind` over a
  list containing it produces a malformed frame that crashes much later,
  somewhere unrelated — so a parallel bug often surfaces far from its cause.
  When a parallel run fails, **dump the per-worker results
  (`str(lapply(parts, dim))`)** to see which returned errors before chasing the
  downstream symptom.
- **Make the parallel unit self-contained and the reduce order-independent.**
  Here each worker returns a *globally-placed* ref table (the per-source
  coordinate shift is computed inside the worker), so the reduce is a plain
  `rbind` whose result is identical to the serial loop regardless of completion
  order. Design the map so workers emit final-position output; keep the reduce a
  dumb concatenation.
- **Default to serial; gate parallelism behind an option/env var.** Keep
  `lapply` as the fallback (`getOption("pkg.workers", 0)` / `Sys.getenv`), so the
  serial path stays available for debugging and the worker count is a `qsub -v`
  knob, not a code edit. Always confirm the serial path still works on the exact
  input before blaming the parallel recast.

## The Lustre knee: more workers is not more speed

The deepest performance lesson, and the one that inverts intuition:

- **Two caches, two warmings.** The compute node's **page cache** (client RAM)
  warms by *re-reading the same file* (a second read of one file goes from
  seconds to milliseconds). The Lustre **metadata server (MDS)** cache warms by
  *touching files in the same directory* (directory/inode locality speeds
  *opens*). They are different layers; neither makes the dominant cost cheap.
- **The dominant cost is the cold per-file index walk.** Walking an HDF5 chunk
  B-tree reads many small, *dependent* byte ranges scattered through the file
  (read a node, learn where the next is, read it) — latency-bound, and cold for
  every distinct file (the page cache can't help across different files). MDS
  warmth speeds the *open*, which was never the expensive part.
- **Benchmark cold, not warm.** Re-running the same file set is page-cache-warm
  and lies about production speed; production touches each file once, cold.
  Caches are per-node and evaporate when the job ends — the cold number is the
  one to budget. (`elapsed` >> `user`+`system` is the I/O-wait signature.)
- **Parallelism overlaps idle latency, up to a knee.** Below the knee, N workers
  overlap N files' waiting — near-linear speedup. *Past* the knee, concurrent
  cold reads queue at the MDS/OSTs: the extra worker waits in line rather than
  overlapping, so more workers tie or *slow down*. The knee is an I/O-subsystem
  limit, unrelated to core count — setting `daemons ≈ ncpus` is usually *past* it.
- **Diagnose by per-file rate vs. worker count.** Observed here: serial ~20 s/file
  (scan-bound); 44 files / 44 daemons ~1 wave, ~1 s/file; 192 files / 44 daemons
  the per-file rate *degraded* (~40%) because sustained 44-wide concurrency
  exceeded the knee. The fix is to tune daemons *down* to the knee (compare total
  wall-time at, say, 16 / 24 / 44 over the full set), not up.
- **`Rprof` can't see I/O-bound C calls — and that's the proof.** A sampling
  profiler samples the R stack; time parked inside a `.Call` (libhdf5 walking the
  B-tree) is invisible. A profile that accumulates only a few seconds of samples
  across a multi-minute run *is* the I/O-bound diagnosis: there is no R hotspot to
  optimise, so the answer is parallelise (and use OS tools / the
  `elapsed`-vs-CPU gap to characterise the invisible part).

## Watch the bottleneck move

Optimising one stage relocates the cost to the next; track *all* stages, not the
blended per-file number:

- Vectorising an O(n) placement loop removed the write cost (the same step that
  feels slow in the Python/VirtualiZarr equivalent — it's the positional
  scatter, not parquet I/O).
- Parallelising the scan removed the dominant I/O cost.
- The serial `gdal mdim mosaic` (O(files), single process, ~0.18 s/file cold)
  then emerged as the largest *serial* component (~36 s for 192 files). It is the
  next bottleneck at multi-variable / full-record scale, addressed by building
  per-period sub-mosaics concurrently and chaining — deferred until it bites.

A blended "per-file" number hides a serial component that grows linearly. Always
break time into stages (log `[mosaic]` / scan / write separately) so you optimise
the one that actually dominates at *your* scale.

## Checklist

1. Confirm you're in the fast regime: scan a *local* path, not a remote URL
   (a single helper that prints the path kind catches silent remote scans).
2. R in its own file; values via `--env` + `Sys.getenv`; one-line container call;
   absolute paths; redirect to a `/g/data` log you can `tail -f`.
3. Declare every `storage=` tree; bind them into the container; outputs to
   `/g/data`, not jobfs.
4. Edit on GitHub, pull down; keep nano for tiny edits only.
5. Writable lib over immutable image; install from inside the container.
6. Parallelise with `mirai` (not fork); namespace/prime worker closures; serial
   default; dump per-worker results when it fails.
7. Benchmark cold; sweep daemon count to find the Lustre knee; tune down, not up.
8. Break timing into stages; the bottleneck moves when you fix one.
9. Validate correctness independently of speed (value round-trip at both ends of
   the span) — a fast build of wrong numbers is worthless.
