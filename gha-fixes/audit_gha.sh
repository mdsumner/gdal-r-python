#!/bin/bash
# Audit GitHub Actions usage across all accessible repos
# Requires: gh cli authenticated, jq, yq (optional, falls back to grep)
#
# Outputs:
#   /tmp/gha_audit.csv        - Run statistics by repo/workflow
#   /tmp/gha_triggers.csv     - Trigger type breakdown
#   /tmp/gha_antipatterns.txt - Static analysis of workflow files
#
# Usage: bash audit_gha.sh [--skip-static] [--repos-file FILE] [--org ORG] [--user USER]

set -e

SKIP_STATIC=false
REPOS_FILE=""
TARGET_ORG=""
TARGET_USER=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --skip-static) SKIP_STATIC=true; shift ;;
    --repos-file) REPOS_FILE="$2"; shift 2 ;;
    --org) TARGET_ORG="$2"; shift 2 ;;
    --user) TARGET_USER="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "=== GitHub Actions Usage Audit ==="
echo "Started: $(date)"
echo ""

# Check for yq (nice to have for YAML parsing)
HAS_YQ=false
if command -v yq &> /dev/null; then
  HAS_YQ=true
  echo "✓ yq found - will do deep YAML analysis"
else
  echo "○ yq not found - will use grep-based analysis (install yq for better results)"
fi
echo ""

#############################################################################
# PHASE 1: Gather repository list
#############################################################################

if [ -n "$REPOS_FILE" ] && [ -f "$REPOS_FILE" ]; then
  echo "Using provided repos file: $REPOS_FILE"
  cp "$REPOS_FILE" /tmp/repos_unique.txt
elif [ -n "$TARGET_ORG" ]; then
  echo "Targeting org: $TARGET_ORG"
  gh repo list "$TARGET_ORG" --limit 500 --json nameWithOwner,isArchived \
    --jq '.[] | select(.isArchived == false) | .nameWithOwner' > /tmp/repos_unique.txt
elif [ -n "$TARGET_USER" ]; then
  echo "Targeting user: $TARGET_USER"
  gh repo list "$TARGET_USER" --limit 500 --json nameWithOwner,isArchived \
    --jq '.[] | select(.isArchived == false) | .nameWithOwner' > /tmp/repos_unique.txt
else
  echo "Fetching all accessible repositories..."
  
  gh repo list --limit 500 --json nameWithOwner,pushedAt,isArchived \
    --jq '.[] | select(.isArchived == false) | .nameWithOwner' > /tmp/repos.txt
  
  # Also get org repos
  for org in $(gh api user/orgs --jq '.[].login' 2>/dev/null); do
    echo "  Including org: $org"
    gh repo list "$org" --limit 500 --json nameWithOwner,pushedAt,isArchived \
      --jq '.[] | select(.isArchived == false) | .nameWithOwner' >> /tmp/repos.txt
  done
  
  sort -u /tmp/repos.txt > /tmp/repos_unique.txt
  rm -f /tmp/repos.txt
fi

REPO_COUNT=$(wc -l < /tmp/repos_unique.txt)
echo ""
echo "Found $REPO_COUNT non-archived repositories"
echo ""

#############################################################################
# PHASE 2: Collect workflow run statistics with trigger breakdown
#############################################################################

echo "repo,workflow,runs_last_30d,failed,cancelled,push,pull_request,schedule,workflow_dispatch,other" > /tmp/gha_audit.csv

echo "Scanning workflow runs (last 30 days)..."
echo ""

while read -r repo; do
  # Get workflow runs from last 30 days with event type
  runs=$(gh run list --repo "$repo" --limit 200 --json name,status,conclusion,createdAt,event 2>/dev/null || echo "[]")
  
  if [ "$runs" != "[]" ] && [ -n "$runs" ] && [ "$runs" != "null" ]; then
    # Filter to last 30 days and aggregate with trigger breakdown
    echo "$runs" | jq -r --arg repo "$repo" '
      (now - (30 * 24 * 3600)) as $cutoff |
      [.[] | select((.createdAt | fromdateiso8601) > $cutoff)] |
      if length == 0 then empty else
        group_by(.name) |
        .[] |
        {
          workflow: .[0].name,
          total: length,
          failed: [.[] | select(.conclusion == "failure")] | length,
          cancelled: [.[] | select(.conclusion == "cancelled")] | length,
          push: [.[] | select(.event == "push")] | length,
          pull_request: [.[] | select(.event == "pull_request")] | length,
          schedule: [.[] | select(.event == "schedule")] | length,
          workflow_dispatch: [.[] | select(.event == "workflow_dispatch")] | length,
          other: [.[] | select(.event | IN("push","pull_request","schedule","workflow_dispatch") | not)] | length
        } |
        "\($repo),\(.workflow),\(.total),\(.failed),\(.cancelled),\(.push),\(.pull_request),\(.schedule),\(.workflow_dispatch),\(.other)"
      end
    ' 2>/dev/null >> /tmp/gha_audit.csv
    
    run_count=$(echo "$runs" | jq '[.[] | select((.createdAt | fromdateiso8601) > (now - 30*24*3600))] | length')
    if [ "$run_count" -gt 0 ]; then
      echo "  $repo: $run_count runs"
    fi
  fi
done < /tmp/repos_unique.txt

#############################################################################
# PHASE 3: Static analysis of workflow files for anti-patterns
#############################################################################

echo ""
echo "=== Static Analysis of Workflow Files ==="
echo "" > /tmp/gha_antipatterns.txt

if [ "$SKIP_STATIC" = true ]; then
  echo "Skipping static analysis (--skip-static)"
else
  echo "Fetching and analyzing workflow files (only workflows with recent runs)..."
  echo ""
  
  mkdir -p /tmp/gha_workflows
  
  # Build list of workflows that actually ran (from the CSV we already built)
  tail -n +2 /tmp/gha_audit.csv | cut -d',' -f1,2 | sort -u > /tmp/active_workflows.txt
  
  while read -r repo; do
    # Get workflow names that ran for this repo
    active_for_repo=$(grep "^${repo}," /tmp/active_workflows.txt | cut -d',' -f2 || echo "")
    
    if [ -z "$active_for_repo" ]; then
      continue  # No recent runs for this repo, skip entirely
    fi
    
    # List workflow files
    workflows=$(gh api "repos/$repo/contents/.github/workflows" --jq '.[].name' 2>/dev/null || echo "")
    
    if [ -n "$workflows" ]; then
      for wf in $workflows; do
        if [[ "$wf" == *.yml || "$wf" == *.yaml ]]; then
          # Fetch workflow content
          content=$(gh api "repos/$repo/contents/.github/workflows/$wf" --jq '.content' 2>/dev/null | base64 -d 2>/dev/null || echo "")
          
          if [ -n "$content" ]; then
            issues=""
            
            # Check for anti-patterns
            
            # 1. Push to all branches without path filter
            if echo "$content" | grep -q "on:.*push" || echo "$content" | grep -qE "^\s+push:"; then
              if ! echo "$content" | grep -qE "(paths:|paths-ignore:|branches:)"; then
                issues="${issues}  ⚠ PUSH_ALL_BRANCHES: Triggers on push without branch/path filters\n"
              fi
            fi
            
            # 2. Missing concurrency group
            if ! echo "$content" | grep -q "concurrency:"; then
              # Only flag if it has push or pull_request triggers
              if echo "$content" | grep -qE "(push:|pull_request:)"; then
                issues="${issues}  ○ NO_CONCURRENCY: No concurrency group (can cause redundant runs)\n"
              fi
            fi
            
            # 3. Large matrix without fail-fast
            if echo "$content" | grep -q "matrix:"; then
              if ! echo "$content" | grep -q "fail-fast:"; then
                issues="${issues}  ○ MATRIX_NO_FAILFAST: Matrix strategy without explicit fail-fast setting\n"
              fi
              # Check for large matrices (crude heuristic: multiple arrays)
              matrix_arrays=$(echo "$content" | grep -cE "^\s+\w+:\s*\[" 2>/dev/null) || matrix_arrays=0
              matrix_arrays=$((matrix_arrays + 0))  # force integer
              if [ "$matrix_arrays" -gt 2 ]; then
                issues="${issues}  ⚠ LARGE_MATRIX: Possibly large matrix ($matrix_arrays dimensions)\n"
              fi
            fi
            
            # 4. Scheduled jobs running very frequently
            if echo "$content" | grep -qE "schedule:"; then
              # Check for hourly or more frequent
              if echo "$content" | grep -qE "cron:.*\*/[1-9][0-9]? \* \* \* \*|cron:.*\* \* \* \* \*"; then
                issues="${issues}  ⚠ FREQUENT_SCHEDULE: Schedule runs hourly or more often\n"
              fi
            fi
            
            # 5. No timeout-minutes (can run forever)
            if ! echo "$content" | grep -q "timeout-minutes:"; then
              issues="${issues}  ○ NO_TIMEOUT: No timeout-minutes set (jobs can hang indefinitely)\n"
            fi
            
            # 6. Uploading artifacts without retention setting
            if echo "$content" | grep -q "actions/upload-artifact"; then
              if ! echo "$content" | grep -q "retention-days:"; then
                issues="${issues}  ○ ARTIFACT_NO_RETENTION: Uploads artifacts without retention-days\n"
              fi
            fi
            
            # 7. Using deprecated or inefficient patterns
            if echo "$content" | grep -q "actions/checkout@v[12][^0-9]"; then
              issues="${issues}  ○ OLD_CHECKOUT: Using old actions/checkout version\n"
            fi
            
            # 8. Caching issues
            if echo "$content" | grep -qE "(npm install|pip install|cargo build)" && ! echo "$content" | grep -q "actions/cache"; then
              issues="${issues}  ○ NO_CACHE: Installs dependencies but doesn't use actions/cache\n"
            fi
            
            # 9. Running on all PR events (not just opened/synchronize)
            if echo "$content" | grep -qE "pull_request:\s*$" && ! echo "$content" | grep -qE "types:\s*\["; then
              # Check if next lines have types
              if ! echo "$content" | grep -A5 "pull_request:" | grep -q "types:"; then
                issues="${issues}  ○ PR_ALL_EVENTS: pull_request without type filter (runs on all PR events)\n"
              fi
            fi
            
            # 10. Expensive runners (macos = 10x, windows = 2x multiplier)
            has_macos=$(echo "$content" | grep -ciE "macos-|macos-latest|macos-14|macos-13" || echo "0")
            has_windows=$(echo "$content" | grep -ciE "windows-|windows-latest|windows-2022" || echo "0")
            has_macos=$((has_macos + 0))
            has_windows=$((has_windows + 0))
            
            if [ "$has_macos" -gt 0 ] || [ "$has_windows" -gt 0 ]; then
              # Check if it runs on every push/PR (not just main/scheduled)
              runs_on_all=false
              if echo "$content" | grep -qE "push:" && ! echo "$content" | grep -A3 "push:" | grep -qE "branches:.*main|branches:.*master"; then
                runs_on_all=true
              fi
              if echo "$content" | grep -qE "pull_request:" && ! echo "$content" | grep -qE "if:.*github.ref.*main|if:.*schedule"; then
                runs_on_all=true
              fi
              
              if [ "$runs_on_all" = true ]; then
                if [ "$has_macos" -gt 0 ]; then
                  issues="${issues}  ⚠ MACOS_10X: Uses macos runner (10x minutes) on all pushes/PRs\n"
                fi
                if [ "$has_windows" -gt 0 ]; then
                  issues="${issues}  ⚠ WINDOWS_2X: Uses windows runner (2x minutes) on all pushes/PRs\n"
                fi
              else
                if [ "$has_macos" -gt 0 ]; then
                  issues="${issues}  ○ MACOS_10X: Uses macos runner (10x minutes) - appears limited to main/scheduled\n"
                fi
                if [ "$has_windows" -gt 0 ]; then
                  issues="${issues}  ○ WINDOWS_2X: Uses windows runner (2x minutes) - appears limited to main/scheduled\n"
                fi
              fi
            fi
            
            # 10. workflow_dispatch without inputs (okay but notable)
            # Skip - this is often intentional
            
            if [ -n "$issues" ]; then
              echo "───────────────────────────────────────────────────────────" >> /tmp/gha_antipatterns.txt
              echo "REPO: $repo" >> /tmp/gha_antipatterns.txt
              echo "FILE: $wf" >> /tmp/gha_antipatterns.txt
              echo -e "$issues" >> /tmp/gha_antipatterns.txt
            fi
          fi
        fi
      done
    fi
  done < /tmp/repos_unique.txt
fi

#############################################################################
# PHASE 4: Generate reports
#############################################################################

echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "                              REPORTS"
echo "════════════════════════════════════════════════════════════════════════"

echo ""
echo "=== TOP 20: Highest run counts (last 30 days) ==="
echo ""
tail -n +2 /tmp/gha_audit.csv | sort -t',' -k3 -nr | head -20 | \
  awk -F',' 'BEGIN {printf "%-40s %-25s %6s %5s %5s │ %4s %4s %4s %4s\n", "REPO", "WORKFLOW", "TOTAL", "FAIL", "CANC", "push", "PR", "schd", "disp"} 
             {printf "%-40s %-25s %6s %5s %5s │ %4s %4s %4s %4s\n", $1, $2, $3, $4, $5, $6, $7, $8, $9}'

echo ""
echo "=== SCHEDULED JOBS: Frequent runners ==="
echo ""
tail -n +2 /tmp/gha_audit.csv | awk -F',' '$8 > 0 {print}' | sort -t',' -k8 -nr | head -15 | \
  awk -F',' 'BEGIN {printf "%-40s %-25s %6s scheduled runs\n", "REPO", "WORKFLOW", "COUNT"} 
             {printf "%-40s %-25s %6s\n", $1, $2, $8}'

echo ""
echo "=== PUSH-HEAVY: Workflows triggered mostly by push ==="
echo ""
tail -n +2 /tmp/gha_audit.csv | awk -F',' '$3 >= 10 && $6 > 0 && ($6/$3) > 0.7 {printf "%-40s %-25s %d/%d from push (%.0f%%)\n", $1, $2, $6, $3, ($6/$3)*100}' | sort -t'(' -k2 -nr | head -10

echo ""
echo "=== HIGH FAILURE RATE: >30% failures, min 5 runs ==="
echo ""
tail -n +2 /tmp/gha_audit.csv | awk -F',' '$3 >= 5 && ($4/$3) > 0.3 {printf "%-40s %-25s %d/%d failed (%.0f%%)\n", $1, $2, $4, $3, ($4/$3)*100}' | sort -t'(' -k2 -nr | head -10

echo ""
echo "=== HIGH CANCELLATION: >20% cancelled, min 5 runs (missing concurrency?) ==="
echo ""
tail -n +2 /tmp/gha_audit.csv | awk -F',' '$3 >= 5 && ($5/$3) > 0.2 {printf "%-40s %-25s %d/%d cancelled (%.0f%%)\n", $1, $2, $5, $3, ($5/$3)*100}' | sort -t'(' -k2 -nr | head -10

echo ""
echo "=== STATIC ANALYSIS: Anti-patterns found ==="
echo ""
if [ -s /tmp/gha_antipatterns.txt ]; then
  # Summary counts
  echo "Pattern counts:"
  grep -c "PUSH_ALL_BRANCHES" /tmp/gha_antipatterns.txt 2>/dev/null | xargs -I{} echo "  ⚠ PUSH_ALL_BRANCHES: {} workflows"
  grep -c "NO_CONCURRENCY" /tmp/gha_antipatterns.txt 2>/dev/null | xargs -I{} echo "  ○ NO_CONCURRENCY: {} workflows"
  grep -c "LARGE_MATRIX" /tmp/gha_antipatterns.txt 2>/dev/null | xargs -I{} echo "  ⚠ LARGE_MATRIX: {} workflows"
  grep -c "FREQUENT_SCHEDULE" /tmp/gha_antipatterns.txt 2>/dev/null | xargs -I{} echo "  ⚠ FREQUENT_SCHEDULE: {} workflows"
  grep -c "ARTIFACT_NO_RETENTION" /tmp/gha_antipatterns.txt 2>/dev/null | xargs -I{} echo "  ○ ARTIFACT_NO_RETENTION: {} workflows"
  grep -c "NO_CACHE" /tmp/gha_antipatterns.txt 2>/dev/null | xargs -I{} echo "  ○ NO_CACHE: {} workflows"
  echo ""
  echo "Expensive runners (high priority!):"
  grep -c "MACOS_10X.*on all" /tmp/gha_antipatterns.txt 2>/dev/null | xargs -I{} echo "  ⚠ MACOS on all pushes/PRs: {} workflows (10x multiplier!)"
  grep -c "WINDOWS_2X.*on all" /tmp/gha_antipatterns.txt 2>/dev/null | xargs -I{} echo "  ⚠ WINDOWS on all pushes/PRs: {} workflows (2x multiplier)"
  grep -c "MACOS_10X.*limited" /tmp/gha_antipatterns.txt 2>/dev/null | xargs -I{} echo "  ○ MACOS limited to main/scheduled: {} workflows"
  grep -c "WINDOWS_2X.*limited" /tmp/gha_antipatterns.txt 2>/dev/null | xargs -I{} echo "  ○ WINDOWS limited to main/scheduled: {} workflows"
  echo ""
  echo "Detailed findings in: /tmp/gha_antipatterns.txt"
else
  echo "No anti-patterns detected (or static analysis skipped)"
fi

#############################################################################
# PHASE 5: Actionable recommendations
#############################################################################

echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "                         QUICK WINS CHECKLIST"
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "1. HIGH-IMPACT: Add concurrency groups to cancel superseded runs"
echo "   concurrency:"
echo "     group: \${{ github.workflow }}-\${{ github.ref }}"
echo "     cancel-in-progress: true"
echo ""
echo "2. PUSH-HEAVY REPOS: Add path filters to skip irrelevant changes"
echo "   on:"
echo "     push:"
echo "       paths:"
echo "         - 'src/**'"
echo "         - '!**.md'"
echo ""
echo "3. SCHEDULED JOBS: Reduce frequency or convert to workflow_dispatch"
echo ""
echo "4. FAILING WORKFLOWS: Fix or disable - they're burning minutes"
echo ""
echo "5. ARTIFACTS: Set retention-days to avoid storage bloat"
echo "   retention-days: 5"
echo ""

echo "════════════════════════════════════════════════════════════════════════"
echo "Output files:"
echo "  /tmp/gha_audit.csv        - Run statistics (importable to spreadsheet)"
echo "  /tmp/gha_antipatterns.txt - Detailed anti-pattern findings"
echo ""
echo "Completed: $(date)"
echo "════════════════════════════════════════════════════════════════════════"

# Cleanup
rm -f /tmp/repos_unique.txt /tmp/active_workflows.txt
