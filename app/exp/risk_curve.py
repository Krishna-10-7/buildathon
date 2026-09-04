"""risk_curve.py — the live challenge-rate readout for /demo.

The merchant does not see challenges; they happen in the buyer's browser
on Razorpay's side and are recorded in the buyer-side evidence JSONL. So
the demo cannot compute a challenge rate from its own DB — it reads the
committed, precomputed study (`app/artifacts/risk_venue.json`, produced by
`scripts/risk_venue_report.py`).

Everything here is REAL evidence, not a live probe. The point is to tell
a viewer, before they press LIVE, what the gate is going to do to them:
on a datacenter IP — which is where this demo is actually hosted — the
historical challenge rate is the `datacenter_rate` below, which is exactly
why /demo defaults to replaying a verified trip instead of rolling the
dice live.

The rate is computed from the committed study rather than typed in. The
~88% figure that used to be hardcoded here was right once and would have
stayed on screen after the data moved — the same way the withdrawn
"escalation over the run" claim stayed in three docs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Reads from app/artifacts/ (not evidence/) on purpose: this file is loaded
# at request time by /demo/risk, so it has to ship with the deployed app.
ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "risk_venue.json"


@dataclass
class RiskReadout:
    ok: bool
    datacenter_rate: float | None  # fraction 0..1
    residential_rate: float | None  # fraction 0..1
    datacenter_ci: tuple[float, float] | None
    residential_ci: tuple[float, float] | None
    reached: int
    # Per-venue denominators. Without these the page used to print a
    # fabricated n (it scaled the combined total by a made-up factor), so a
    # viewer could not check the rate against the sample it came from.
    datacenter_n: int = 0
    residential_n: int = 0
    datacenter_challenged: int = 0
    residential_challenged: int = 0
    p_value: float | None = None
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
            "datacenter_n": self.datacenter_n,
            "residential_n": self.residential_n,
            "datacenter_challenged": self.datacenter_challenged,
            "residential_challenged": self.residential_challenged,
            "p_value": self.p_value,
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

    # The significance figure is read from the study, not restated here.
    # A p-value typed into a verdict string is a p-value that keeps being
    # quoted long after the data behind it changed.
    p_value = ((data.get("tests") or {})
               .get("datacenter_vs_residential", {}).get("p"))

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

    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x * 100:.0f}%"

    def pfmt(p: float | None) -> str:
        if p is None:
            return "p unavailable"
        if p < 1e-4:
            return f"p = {p:.1e}"
        return f"p = {p:.3f}"

    return RiskReadout(
        ok=True,
        datacenter_rate=dc_rate,
        residential_rate=res_rate,
        datacenter_ci=wilson(dc_c, dc_n),
        residential_ci=wilson(res_c, res_n),
        reached=dc_n + res_n,
        datacenter_n=dc_n,
        residential_n=res_n,
        datacenter_challenged=dc_c,
        residential_challenged=res_c,
        p_value=p_value,
        verdict=(f"identical buyer challenged at {pct(dc_rate)} from a "
                 f"datacenter IP ({dc_c}/{dc_n}) vs {pct(res_rate)} from a "
                 f"residential one ({res_c}/{res_n}), {pfmt(p_value)}"),
        note=("same code, same merchant, same key — venue, not the calendar, "
              "decides whether an AI buyer can pay"))
