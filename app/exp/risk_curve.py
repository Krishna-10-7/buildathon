"""risk_curve.py — the live challenge-rate readout for /demo.

The merchant does not see challenges; they happen in the buyer's browser
on Razorpay's side and are recorded in the buyer-side evidence JSONL. So
the demo cannot compute a challenge rate from its own DB — it reads the
committed, precomputed study (`artifacts/risk_venue.json`, produced by
`scripts/risk_venue_report.py`).

Everything here is REAL evidence, not a live probe. The point is to tell
a viewer, before they press LIVE, what the gate is going to do to them:
on a datacenter IP — which is where this demo is actually hosted — the
historical challenge rate is ~88%, which is exactly why /demo defaults
to replaying a verified trip instead of rolling the dice live.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "risk_venue.json"


@dataclass
class RiskReadout:
    ok: bool
    datacenter_rate: float | None  # fraction 0..1
    residential_rate: float | None  # fraction 0..1
    datacenter_ci: tuple[float, float] | None
    residential_ci: tuple[float, float] | None
    reached: int
    verdict: str = ""
    note: str = ""

    def to_event(self) -> dict:
        return {
            "t": "risk_curve",
            "ok": self.ok,
            "datacenter_rate": self.datacenter_rate,
            "residential_rate": self.residential_rate,
            "datacenter_ci": self.datacenter_ci,
            "residential_ci": self.residential_ci,
            "reached": self.reached,
            "verdict": self.verdict,
            "note": self.note,
        }


def load(path: Path | None = None) -> RiskReadout:
    p = path or ARTIFACT
    if not p.exists():
        return RiskReadout(
            ok=False, datacenter_rate=None, residential_rate=None,
            datacenter_ci=None, residential_ci=None, reached=0,
            verdict="risk study unavailable",
            note="run scripts/risk_venue_report.py to regenerate")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return RiskReadout(
            ok=False, datacenter_rate=None, residential_rate=None,
            datacenter_ci=None, residential_ci=None, reached=0,
            verdict="risk study unreadable")

    ph = data.get("phases", {})
    p1 = ph.get("p1_datacenter", {})
    p2 = ph.get("p2_residential", {})
    p3 = ph.get("p3_datacenter", {})
    dc_n = (p1.get("reached") or 0) + (p3.get("reached") or 0)
    dc_c = (p1.get("challenged") or 0) + (p3.get("challenged") or 0)
    res_n = p2.get("reached") or 0
    res_c = p2.get("challenged") or 0
    dc_rate = dc_c / dc_n if dc_n else None
    res_rate = res_c / res_n if res_n else None

    # Wilson from the per-phase cells is in the JSON; recompute a combined
    # interval here so the readout does not depend on the writer emitting it.
    def wilson(c: int, n: int, z: float = 1.96):
        if n == 0:
            return None
        pp = c / n
        d = 1 + z * z / n
        centre = (pp + z * z / (2 * n)) / d
        half = z * ((pp * (1 - pp) / n + z * z / (4 * n * n)) ** 0.5) / d
        return (max(0.0, centre - half), min(1.0, centre + half))

    return RiskReadout(
        ok=True,
        datacenter_rate=dc_rate,
        residential_rate=res_rate,
        datacenter_ci=wilson(dc_c, dc_n),
        residential_ci=wilson(res_c, res_n),
        reached=dc_n + res_n,
        verdict=("identical buyer challenged at ~88% from a datacenter IP "
                 "vs ~13% from a residential one (p < 1e-11)"),
        note=("same code, same merchant, same key — venue, not the calendar, "
              "decides whether an AI buyer can pay"))
