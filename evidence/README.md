# evidence/

Everything in this directory is **input or output of a published number**.
Nothing here is code, and nothing here is hand-written.

That distinction is the whole reason the folder exists. It used to be
`app/artifacts/`, sitting inside the code tree next to `bazaar/`, and the
consequence was predictable: nobody could tell which files were the
program and which were the receipts. So a reader checking a figure had to
guess where its input lived.

## Rules

1. **Append-only.** Session logs are never edited, reordered or trimmed.
   Records that are excluded from a result stay on disk and are filtered
   at merge time — see `sessions_laptop.jsonl.incident-20260825-1245`.
2. **Every number in the README comes out of a script that reads this
   directory.** If a figure cannot be regenerated from here, it is not
   evidence. (This rule exists because a figure that ignored it — the
   "escalation over the run" claim — survived in three documents before
   anyone tested it. It is now withdrawn, with the test recorded in
   `app/scripts/risk_venue_report.py`'s output.)
3. **If a file here changes, the published numbers may have changed.** Run
   the reports before believing the README again.

## Layout

| Path | What | Produced by | Consumed by |
|---|---|---|---|
| `sessions.jsonl` | earliest buyer run | `scripts/run_persona.py` | transactability report |
| `sessions_laptop.jsonl` | laptop run, incident era (2026-08-25) | `scripts/run_persona.py` | risk-venue report (residential) |
| `sessions_laptop2.jsonl` | **the clean post-hardening run, n=40** — the headline corpus | `scripts/run_persona.py` | transactability report, replay fixture |
| `sessions_vm2_prereg.jsonl` | VM2 datacenter run, preregistration | `scripts/run_measurement.py` | risk-venue report (datacenter) |
| `sessions_vm2_master.jsonl` | VM2 datacenter run, main | `scripts/run_measurement.py` | risk-venue report (datacenter) |
| `sessions_laptop.jsonl.incident-20260825-1245` | pre-repair snapshot of the corrupted incident-era log. **Kept, not deleted**: it is the only record of what the file looked like before the multi-runner bug was found. Cited in `MEASUREMENT-DAY.md` | — (forensic copy) | human readers |
| `prompt_injection_gauntlet.json` | the 20-case injection corpus and its outcomes | `scripts/test_prompt_injection.py` | README §"prompt injection" |
| `measurement/report.json` | the growth A/B result — **NULL**, n=94 | `exp/analysis.py` | README, `MEASUREMENT-DAY.md` |
| `screenshots/` | every capture: per-attempt checkout pages, README heroes, the sprite sheet | `scripts/capture_screenshots.py`, `exp/checkout.py`, `scripts/render_scene.py` | README, docs |

## Not here, on purpose

Two files stay in `app/artifacts/` because the **deployed app reads them at
request time** and this directory does not ship to the server:

- `app/artifacts/replay_fixture.json` — what `/demo` replays
- `app/artifacts/risk_venue.json` — what `/demo/risk` renders

Everything else that lands in `app/artifacts/` (browser profiles, persona
identities, ad-hoc captures) is runtime state and is gitignored.

## Reproduce

```bash
cd app
uv run python scripts/transactability_report.py                 # 33/40, ₹25,724.15, 0/38
uv run python scripts/risk_venue_report.py                      # venue study
uv run python scripts/verify_arm_integrity.py --self-check      # window predicate
uv run python scripts/replay_source.py                          # /demo fixture
```

CI runs all four on every push — see `.github/workflows/ci.yml`.
