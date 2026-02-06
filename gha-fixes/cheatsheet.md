# GitHub Actions: Common Fixes Cheatsheet

Quick copy-paste patterns for cleaning up wasteful workflows.

---

## 1. Only run on push to main (not every branch)

```yaml
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
```

Or if you want PRs from any branch but only push on main:

```yaml
on:
  push:
    branches: [main]
  pull_request:
```

---

## 2. Add concurrency to cancel superseded runs

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

For workflows where you want to protect main (never cancel main runs):

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}
```

---

## 3. Skip CI for docs-only changes

```yaml
on:
  push:
    branches: [main]
    paths-ignore:
      - '**.md'
      - 'docs/**'
      - '.github/ISSUE_TEMPLATE/**'
      - 'LICENSE'
      - '.Rbuildignore'
      - 'NEWS.md'
  pull_request:
    paths-ignore:
      - '**.md'
      - 'docs/**'
```

Or the inverse (only run when code changes):

```yaml
on:
  push:
    paths:
      - 'R/**'
      - 'src/**'
      - 'DESCRIPTION'
      - 'NAMESPACE'
```

---

## 4. Limit PR event types

By default `pull_request` fires on opened, synchronize, reopened, and more. Usually you only need:

```yaml
on:
  pull_request:
    types: [opened, synchronize]
```

---

## 5. Reduce schedule frequency

Before:
```yaml
on:
  schedule:
    - cron: '0 * * * *'  # hourly = 720 runs/month
```

After:
```yaml
on:
  schedule:
    - cron: '0 9 * * 1'  # Weekly, Monday 9am UTC = 4 runs/month
```

Or convert to manual trigger:
```yaml
on:
  workflow_dispatch:
    inputs:
      reason:
        description: 'Why are you running this?'
        required: false
```

---

## 6. Add timeout to prevent runaway jobs

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 30
```

For R package checks, 30-60 minutes is usually plenty.

---

## 7. Set artifact retention

```yaml
- uses: actions/upload-artifact@v4
  with:
    name: results
    path: output/
    retention-days: 5
```

Default is 90 days which eats storage quota.

---

## 8. Matrix: add fail-fast and limit combinations

```yaml
strategy:
  fail-fast: true  # stop all jobs if one fails
  matrix:
    os: [ubuntu-latest]  # drop windows/macos if not needed
    r: [release]         # drop oldrel/devel for routine checks
```

Full matrix only on main or weekly:

```yaml
jobs:
  quick-check:
    if: github.event_name == 'pull_request'
    strategy:
      matrix:
        os: [ubuntu-latest]
        r: [release]
        
  full-check:
    if: github.ref == 'refs/heads/main'
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        r: [devel, release, oldrel-1]
```

---

## 9. Cache dependencies

For R:
```yaml
- uses: r-lib/actions/setup-r-dependencies@v2
  with:
    cache-version: 2
```

(r-lib/actions already handles caching well)

For pip:
```yaml
- uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: pip-${{ hashFiles('requirements.txt') }}
```

---

## 10. Skip CI entirely via commit message

Add this condition to jobs you want to be skippable:

```yaml
jobs:
  build:
    if: "!contains(github.event.head_commit.message, '[skip ci]')"
```

Then commit with `git commit -m "docs: update readme [skip ci]"`

---

## R-CMD-check specific (r-lib/actions)

The standard r-lib/actions/check-r-package workflow is already pretty optimized, but common tweaks:

```yaml
on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  R-CMD-check:
    runs-on: ${{ matrix.config.os }}
    timeout-minutes: 60
    
    strategy:
      fail-fast: true
      matrix:
        config:
          - {os: ubuntu-latest, r: release}
          # Only add these if you really need cross-platform
          # - {os: macos-latest, r: release}
          # - {os: windows-latest, r: release}
```

---

## Workflow for "run full checks weekly, minimal on PR"

```yaml
name: R-CMD-check

on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: '0 9 * * 1'  # Monday 9am UTC

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  R-CMD-check:
    runs-on: ${{ matrix.config.os }}
    timeout-minutes: 60
    
    strategy:
      fail-fast: true
      matrix:
        config:
          - {os: ubuntu-latest, r: release}
          # Expand matrix only for scheduled runs or main branch
          - {os: macos-latest, r: release}
            if: github.event_name == 'schedule' || github.ref == 'refs/heads/main'
```

(Note: matrix `if` is a bit clunky - often easier to use two separate jobs)
