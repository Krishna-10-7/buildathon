"""Merge laptop evidence into the master sessions file (incident-aware).

Implements the 2026-08-25 multi-runner integrity ruling (MEASUREMENT-DAY.md,
deviation log) EXACTLY as logged — no re-derivation, no judgment:

  From sessions_laptop.jsonl (incident file, untouched on disk):
    - every record whose session STARTED before 2026-08-25T05:53:20Z
      (provably single-writer era), PLUS
    - paid records admitted by scripts/verify_arm_integrity.py on VM1
      (ledger-proven continuous arm through payment): the five ord_ ids
      hardcoded below.
    All other incident-window records are VOID: skipped here, never deleted.
  From sessions_laptop2.jsonl (post-hardening run): everything.

Output: merged JSONL on stdout (redirect to a file), master order preserved.
Usage: uv run python scripts/merge_laptop_into_master.py > artifacts/sessions_merged.jsonl
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
INCIDENT = HERE / "artifacts" / "sessions_laptop.jsonl"
POST = HERE / "artifacts" / "sessions_laptop2.jsonl"

SOLO_ERA_CUTOFF = "2026-08-25T05:53:20"          # second runner's launch
ADMITTED_PAID = {                                  # verify_arm_integrity.py output
    "ord_1d4cba907c0f46",  # L3  T ₹340.65 (also solo era)
    "ord_a286138683e64b",  # L7  T ₹340.65
    "ord_9521b4de42fb4f",  # L35 T ₹340.65
    "ord_9053768dd41d41",  # L49 T ₹1499.00
    "ord_a5a40db5febd41",  # L53 C ₹598.00
}

kept, voided = 0, 0
for line in INCIDENT.read_text(encoding="utf-8").splitlines():
    rec = json.loads(line)
    ok_solo = rec["ts"] < SOLO_ERA_CUTOFF
    ok_admitted = rec.get("order_id") in ADMITTED_PAID
    if ok_solo or ok_admitted:
        print(json.dumps(rec, ensure_ascii=False))
        kept += 1
    else:
        voided += 1

if POST.exists():
    for line in POST.read_text(encoding="utf-8").splitlines():
        print(line)
        kept += 1

print(f"merged={kept} voided_from_incident={voided}", file=__import__("sys").stderr)
