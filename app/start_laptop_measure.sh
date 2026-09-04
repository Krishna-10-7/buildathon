#!/bin/bash
# Laptop-venue measurement launcher (venue-switch deviation, 2026-08-25).
# Same preregistered runner as VM2's start_measure.sh.
#
# v2 hardening after the multi-runner incident (see MEASUREMENT-DAY.md):
#   - SINGLE-INSTANCE LOCK: concurrent runners share the merchant's global
#     experiment arm and cross-contaminate arm tags. The lock directory is
#     created atomically; a second launch refuses instead of stacking.
#     If a previous run was force-killed, remove artifacts/.runner_lock first.
#   - FRESH EVIDENCE FILE per integrity ruling (sessions_laptop2.jsonl);
#     the incident file stays untouched (append-only evidence).
#   - SESSION BUDGET IS EXPLICIT ($1): voided/incident records must be
#     subtracted by a human ruling, not recounted from a corrupt file.
#     Current ruling: 90 - 41 (VM2) - 9 (laptop: 5 solo-era + 4 ledger-admitted).
cd "$(dirname "$0")" || exit 1
mkdir -p artifacts
if ! mkdir artifacts/.runner_lock 2>/dev/null; then
  echo "REFUSING TO START: artifacts/.runner_lock exists — a runner may" \
       "already be live. Verify with Get-CimInstance, then rm -rf the lock." >&2
  exit 1
fi
trap 'rm -rf artifacts/.runner_lock' EXIT

KEY=$(ssh myserver2 'grep "^OPENROUTER_API_KEY=" ~/.hermes/.env | cut -d= -f2-')
grep -q "^OPENROUTER_API_KEY=" .env || printf "OPENROUTER_API_KEY=%s\n" "$KEY" >> .env
sed -i "s/^LLM_PROVIDER=.*/LLM_PROVIDER=openrouter/" .env

SESSIONS="${1:?usage: start_laptop_measure.sh <sessions>  (budget is a human ruling, see header)}"
OUT="${2:-../evidence/sessions_laptop2.jsonl}"
echo "LAUNCH: single-instance lock held; sessions=$SESSIONS out=$OUT"
# NOTE: deliberately NOT exec'd — exec replaces the shell image and the
# EXIT trap never runs, leaving a stale lock behind (observed live).
PYTHONUNBUFFERED=1 EXP_TOKEN=85c9e5960231eaceab734048b3e4aa0d01a379a9186a8bda6a77e3c83d25ab9e \
  uv run python scripts/run_measurement.py \
    --api-base https://r2-d2.xyz \
    --sessions "$SESSIONS" \
    --out "$OUT" \
    --pause-s 45
