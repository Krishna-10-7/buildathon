# MEASUREMENT DAY — mechanical checklist

The design is frozen in `PREREGISTRATION.md`. This file is only the
operating procedure. Any deviation → add it to the deviation list below,
never silently.

## Preconditions (all must pass before starting)

- [ ] **LLM quota alive**: `ssh myserver2 '... uv run python -c "from bazaar.llm import complete; print(complete(\"ping\", smart=False))"'`
      returns text. Free-tier daily quota resets ~12:30 IST (midnight Pacific).
- [ ] **Key state decided** (research/08): ideally fresh test keys generated
      in Dashboard first (velocity reset); `MANDATE_SECRET` is pinned in all
      three `.env`s so rotation no longer touches mandate signatures.
- [ ] Merchant healthy: `curl https://r2-d2.xyz/healthz` → ok.
- [ ] Fleet synced (VM2 runs the same bundle as VM1): imports check passes.
- [ ] Stock sane: no SKU below ~15 units (`sqlite3` check or Control Tower).
- [ ] Audit chain verified: `curl "https://r2-d2.xyz/audit/recent?limit=1"` → chain_ok true.

### Challenge expectations during the run (research/08 §4)

The fleet browses from datacenter IPs → nonzero hCaptcha rates are expected
regardless of anything we do. That is inside the preregistered design:
`risk_challenged` sessions are excluded symmetrically and counted per arm;
materially asymmetric rates VOID the run (report it, don't massage it).
Long jittered pauses are the only sanctioned mitigation. Never solve or
route around challenges programmatically.

## The run

```bash
# token (VM1):
ssh myserver 'cd ~/bazaar/app && uv run python scripts/experiment_token.py'
# run (VM2), inside tmux:
EXP_TOKEN=<token> uv run python scripts/run_measurement.py \
    --sessions 90 --out artifacts/sessions.jsonl --pause-s 20
```

- 90 sessions ≈ 45 pairs, alternating T,C with persona cycling.
- Expect several hours with pauses. Check in every ~30 min; do not touch
  the storefront between checks (no manual proposals/discounts mid-run).
- The runner aborts by design after 3 consecutive LLM failures — that is a
  quota death, resume after reset. It never aborts on risk_challenged;
  those are recorded and excluded symmetrically at analysis time.

## After the run

```bash
uv run python exp/analysis.py artifacts/sessions.jsonl   # writes artifacts/measurement/report.json
```

- [ ] Report the outcome mix per arm (exclusions INCLUDED in the claim of
      symmetry), the CI verdict exactly per the preregistration rule, and
      any deviations.
- [ ] Copy DAY log entry + README status bullet with the actual numbers.
- [ ] Leave `sessions.jsonl` untouched afterwards (append-only evidence).

## Deviation log

- **2026-08-24 ~14:00 IST — fast-lane LLM pinned.** `gemini-flash-latest`
  alias currently thinks >45 s on trivial prompts (ReadTimeouts); fast lane
  pinned to `gemini-3.6-flash` in config (deployed to all machines). Fleet
  sessions will run on the pinned model.
- **2026-08-24 ~15:30 IST — checkout driver hardened.** `load` wait instead
  of `networkidle`; driver exceptions convert to structured records (a
  browser hiccup can no longer kill the runner or vanish a session);
  stale-hCaptcha-text false-positive fixed (bank-page awareness); netbanking
  path clicks mock-bank Success (flow changed from Day-1 auto-complete);
  180 s human-solve window exists ONLY when headed — headless fleet behavior
  is unchanged: challenge → abandon → symmetric exclusion.
- **2026-08-24 ~15:00 IST — Razorpay test keys rotated** to a fresh
  dashboard-generated test key id (redacted from public files; the old key
  id was previously committed here and has since been scrubbed) —
  research/08 Option A velocity reset; old key
  was auto-failing invisible hCaptcha verifies). `MANDATE_SECRET` was
  already pinned, so mandate signatures are unaffected. Payment closure
  consumed 5 checkout loads across 2 keys today; measurement run starts on
  a clean key.
- **2026-08-24 — payment closure used 3 attempts beyond the planned 2**,
  each sanctioned by a driver-bug fix rather than a risk rejection (full
  accounting in DAY3.md). Not a measurement deviation — recorded for
  honesty about today's checkout velocity on the fresh key (2 loads).
- **2026-08-24 ~16:00 IST — first launch of the run crashed at session 1**
  (raw `httpx.ReadTimeout` leaked through the LLM adapter and killed the
  runner; zero sessions written, nothing analyzed). Fixed on both layers —
  adapter converts transport errors to `LLMError` with one retry and read
  timeout raised 45→90 s; runner wraps each session so a crash records
  `infra_error` and continues. Relaunched same hour; that relaunch is THE
  run. No data existed before the crash, so no analysis implications.
- **2026-08-24 ~21:50 IST — provider switched gemini → nvidia after 5
  sessions.** Gemini free-tier quota died for the second time today at ~50
  calls/day against a ~240-call requirement — structurally insufficient for
  90 sessions. Switched the FLEET ONLY (VM2) to `nvidia`
  (`meta/llama-3.3-70b-instruct`, json_mode verified end-to-end); merchant
  VM1 stays gemini. Preregistration pins no provider (line 26 treats model
  load as drift balanced by alternation), and zero valid sessions existed,
  so the entire analyzed dataset remains single-provider. The 5 aborted-run
  records stay in sessions.jsonl untouched: frozen rules count their
  llm_error outcomes as analyzed ₹0 sessions (3T/2C — roughly symmetric,
  conservative toward null). Restart restarted alternation at T instead of
  continuing at C (first 11 slots: 6T/5C instead of strict T,C,T,C…);
  logged here rather than editing assignment code mid-experiment.
- **2026-08-24 ~21:45 IST — deployment gap caught pre-launch**: VM2's
  `config.py` predated the nvidia fields (llm.py had synced, config.py had
  not) — every fleet session would have AttributeError'd. Synced from the
  laptop bundle before launch; no session ran against the stale file.
- **2026-08-24 ~23:00 IST — adapter retry depth 1→2, pauses 20s→45s,
  runner restarted (~8 sessions in).** NIM free-queue ReadTimeouts hit ~29%
  of v3 sessions (each records as llm_error = analyzed ₹0 under frozen
  rules); 3 attempts × 150 s window absorbs measured double-stalls. Pauses
  widened because hCaptcha pressure visibly escalated on the Azure IP
  (4/4 recent browser sessions challenged) — long jittered pauses are the
  sanctioned mitigation per research/08 §4. Restart re-runs tag/alternation
  sequence from m000=T again (same cosmetic wart as the earlier restart;
  logged cumulatively). Ops hardening only — assignment logic, analysis
  code, and recorded data untouched.
- **2026-08-25 ~00:20 IST — NIM hard-aborted; fleet switched nvidia →
  openrouter (`stealth/ox-alpha`).** After the retry-depth fix, NIM still
  produced a 25-minute total-stall cluster: 3 consecutive sessions failed
  all 3 attempts each → runner's design abort fired. At that point tonight
  NIM's llm_error rate was ~41% of tagged sessions (15 junk ₹0 rows).
  Switched the fleet to OpenRouter's zero-priced `stealth/ox-alpha`
  (adapter gained an OpenAI-compatible lane cloned from the proven
  groq/nvidia branch; json_mode verified end-to-end from VM2 at ~8 s/call
  vs NIM's 60 s+ stalls). Rolling-window throttling observed and compatible
  with the fleet's ~1-call-per-minutes cadence; unknown daily cap accepted
  deliberately — abort guard bounds any damage identically to the Gemini/
  NIM incidents. Runner relaunched with session budget recomputed so total
  tagged sessions land at exactly 90. Provider is NOT pinned by the
  preregistration; alternation balances provider drift across arms; every
  switch recorded here. Mixed-provider dataset acknowledged in analysis.
- **2026-08-25 ~00:40–14:20 IST — two anomalies during the openrouter era,
  both documented for the analysis writeup:** (1) the relaunched VM2 runner
  kept erroring as `nvidia` despite `.env` saying openrouter (fresh-import
  probes resolved openrouter correctly; root cause not conclusively
  identified before the process era ended — its env contained no override,
  so suspicion falls on stale-process/config interaction; no further
  nvidia-labeled errors after that process era closed); (2) an ~8.7-hour
  record gap (20:35→05:17 UTC) consistent with a hung browser call the
  per-session crash-guard cannot catch (it traps exceptions, not hangs),
  ending when a new runner appeared at 05:20 UTC launched via the standard
  launcher + token from a tmux pane (not by this session's cron, which only
  reports) — most plausibly a manual relaunch after the gap was noticed.
  That new runner was verified healthy on openrouter (baskets planned in
  seconds) but hit near-100% hCaptcha challenge rate on the Azure IP.
- **2026-08-25 ~11:30 IST — VENUE SWITCH: fleet moved VM2 → laptop
  residential IP** (the previously-flagged tripwire). Rationale: with the
  LLM lane fixed (~8 s/call), the binding constraint became the Azure IP's
  captcha reputation — roughly 9 of 10 browser-reaching sessions overnight
  died as symmetric-excluded `risk_challenged`, so remaining sessions would
  have yielded almost zero analyzable rows. research/08 sanctions IP/pacing
  hygiene as the only mitigations; the residential IP produced today's one
  clean end-to-end payment on identical keys. Mechanics: VM2 runner stopped
  cleanly between sessions; remainder recomputed against the frozen 90
  target; laptop runs the same preregistered runner writing to a SEPARATE
  evidence file (`artifacts/sessions_laptop.jsonl`) to be appended into the
  master `sessions.jsonl` post-run (append-only order preserved). Venue is
  mixed mid-run — logged; alternation balances exposure across arms as with
  provider drift. Laptop must stay awake ~3 h for the run.
- **2026-08-25 ~12:10 IST — checkout race patched for future launches
  (live run keeps current code).** On slower machines the "Processing your
  payment" overlay can win the race against the bank-pick click
  (`Locator.click` 8 s timeout → driver_error on that attempt). Fix:
  catch the race, don't re-click into the moving page, fall through to
  authorize (the auto-submit already fired; `_click_bank_button` waits for
  the mock bank). NOT deployed to the in-flight run — restarting to
  activate a ~6%-of-attempts fix was judged costlier than the exclusions
  it prevents; it activates on any subsequent relaunch.
- **2026-08-25 ~12:30 IST — race patch ACTIVATED via restart, then Razorpay
  keys rotated again mid-run (velocity reset #2, user-generated).** The
  race hit BOTH attempts of a session (whole session lost as infra_error)
  — restart to activate the patch became clearly worth it. Minutes later
  the user supplied fresh test keys (dashboard-generated): laptop-era
  challenges were landing at the FINAL gate ("before authorize"), the
  research/08 signature of key-velocity rather than IP reputation — the
  morning's fresh key had absorbed ~45 checkout loads across two venues.
  New keys deployed to all three machines; merchant service restarted via
  systemd (healthz ok, webhook secret and pinned MANDATE_SECRET
  unaffected); fleet relaunched (launcher recomputes remainder against the
  frozen 90 automatically). Expectation per research/08: challenge rate
  collapses on the fresh key, mirroring this morning's clean payment.
- **2026-08-25 ~12:40–13:15 IST — MULTI-RUNNER INTEGRITY INCIDENT:
  discovered, forensically audited, voided, hardened.** The most serious
  operational failure of the measurement.
  - **Discovery**: monitor stdout showed 9 paid events; disk showed far
    fewer among the last records, plus ~dozens of `crash-NNN` ghost
    records ("Connection closed while reading from the driver"). Full
    enumeration (`Win32_Process`) found **THREE concurrent runner trees**
    alive at once: launched 11:01 (`--sessions 49`), 11:23 (`--sessions
    44`), and 11:26 IST (`--sessions 40`). Root cause: TaskStop kills the
    monitor pipe (`bash | grep`) but NOT the launcher's uv→python child
    tree on Windows, and an earlier orphan-check suppressed errors and
    reported none. Every "restart" stacked a new runner instead of
    replacing one.
  - **Why this matters**: `flip_arm` sets GLOBAL merchant state before each
    session. Concurrent runners' flips interleave with other runners'
    sessions, so an arm TAG can diverge from the arm a session actually
    experienced.
  - **All three trees killed** (taskkill /T from bash roots, 12 PIDs);
    verified zero survivors including browser/driver orphans; evidence
    snapshotted (`sessions_laptop.jsonl.incident-20260825-1245`; original
    untouched, 73 records).
  - **Forensics**: record `ts` = session START (file is completion-ordered,
    not ts-ordered). Runner attribution via alternation parity + each
    runner's i0 landing exactly 1 s after its launch. Pricing-signature
    audit grounded in `experiment.py`: control normalizes EVERY product to
    base price; treatment replays executed discounts (masala-chai ₹211.65
    vs base ₹249 is the standing marker). Results across the 16 real
    records: TWO PROVEN MISLABELS — `meera-41496ed5` (tagged T, charged
    BASE-price chai ⇒ ran under effective control) and `ritika-e121753a`
    (tagged C, PAID the ₹211.65 treatment price ⇒ flip landed mid-session,
    between planning and payment); three hamper-only paid rows are
    unverifiable (hamper has no executed discount, so pricing cannot
    distinguish arms); the rest are internally consistent.
  - **Additional finding**: ALL THREE runners silently hung ~06:26 UTC —
    last records 06:24–06:36 UTC yet processes were alive at the 12:45 IST
    kill. Same hang class as the earlier 8.7 h gap: the per-session guard
    traps exceptions, not hangs. ~50 min produced nothing.
  - **RULING, upgraded from blanket-void to MECHANICAL ADMISSION**: the
    merchant's own audit ledger logs every `experiment.arm_switch` with a
    timestamp (78 switches in the laptop era), and the orders DB holds each
    payment's exact capture time. New script
    `scripts/verify_arm_integrity.py` (run on VM1, output preserved in this
    log's history) applies one mechanical rule: a paid row is ADMITTED iff
    no opposing switch exists in [session_start − 2 s, payment_capture +
    1 s]. Result over all 13 laptop paid rows: **5 ADMITTED** (ord_1d4cba…,
    ord_a28613…, ord_9521b4…, ord_9053768…, ord_a5a40d… — four treatment,
    one control), **8 VOIDED**, each with the exact offending switch
    printed. The ledger test is strictly stronger than pricing forensics:
    two rows that PASSED the price-signature check were voided by ledger
    flips landing seconds before capture. Non-paid laptop rows count only
    from the provably single-writer era (started < 05:53:20Z): 4
    symmetric-exclusion rows. Voided records stay in the file, excluded at
    merge time — nothing deleted; snapshot preserved.
  - **Budget ruling**: frozen 90-target counts trustworthy tagged
    sessions: 41 (VM2) + 5 (laptop solo era) + 4 (laptop ledger-admitted
    paid) = **50 ⇒ 40 fresh sessions required**, written to a NEW evidence
    file `artifacts/sessions_laptop2.jsonl`.
  - **Hardening shipped before relaunch** (both machines, compile-checked):
    (1) per-session watchdog `--session-timeout 600` — a hung session now
    records `hang-NNN` infra_error and the run continues; (2) per-session
    duration in stdout so stalls are visible immediately; (3) launcher
    single-instance lock directory — a second launch REFUSES instead of
    stacking; (4) launcher takes the session budget as an explicit human
    ruling argument instead of recounting a possibly-corrupt file.
  - **Ops lesson (permanent)**: on Windows, stopping a monitor does NOT
    stop what it launched. After ANY stop: enumerate
    `Win32_Process` for survivors BEFORE relaunching; the launcher lock is
    the backstop, not the primary check.
- **2026-08-25 ~13:30–16:00 IST — RUN COMPLETE; final accounting and
  verdict.** Fresh post-hardening run: 40/40 sessions in ~2 h — 33 paid,
  2 llm_error, 5 risk_challenged (T:4/C:1), zero hangs (watchdog never
  fired), single-instance lock held throughout. Launcher fix shipped
  post-run: `exec uv …` discarded the EXIT trap so the lock dir went stale
  after completion (observed + fixed + synced).
  - **VM2 recount**: master `sessions.jsonl` holds **45** records (41
    tagged + 4 untagged legacy), not the 41 assumed mid-incident; final
    dataset therefore n=94, an overshoot of the ~90 target — documented,
    nothing trimmed.
  - **Merge**: per the logged ruling via `scripts/merge_laptop_into_master.py`
    → VM2 master (45) + laptop valid (49 = 5 solo-era + 4 ledger-admitted
    paid + 40 fresh); 64 incident records voided at merge, preserved on
    disk. Analysis input: `artifacts/sessions_final.jsonl` on VM2;
    originals untouched.
  - **VERDICT (frozen analyzer, verbatim)**: `NULL/NEGATIVE — reported as-is
    per preregistration`. Revenue/session T ₹477.90 vs C ₹562.90; diff
    −₹241.45; CI95 [−₹294.34, +₹131.55]; permutation p=0.486;
    significant_at_005=false. Exclusions per arm reported inside the claim:
    T:20 / C:12. Secondary observations only: conversion arm-flat (71.4% /
    70.0%), attach UP (0.80 / 0.48), AOV DOWN (₹669 / ₹804) → discount-
    driven basket-downgrade hypothesis for future work.
  - Cron night-watch retired; all monitors closed. Remaining project work:
    demo video with these real numbers, then the key-rotation hard gate
    before anything public.
