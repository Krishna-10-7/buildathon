# Lightweight Stack for 3 × 1 GB RAM VMs

*Written directly by the main agent (2026-08-22) after the delegated research stream failed twice on API timeouts. This topic is stable engineering knowledge; memory figures below are field-typical **ESTIMATES**, not benchmarked numbers. Verify with `free -m` / `ps aux --sort=-rss` during deployment week.*

---

## TL;DR — the stack

| VM | Role | Chosen stack | Est. peak RSS |
|---|---|---|---|
| **VM1** `merchant-core` | Catalog, orders, Razorpay integration, policy engine, audit ledger, DB | **Python 3.12 + FastAPI + uvicorn (1 worker)** + **SQLite (WAL)** behind **Caddy** | **~350–450 MB** |
| **VM2** `agent-runtime` | Buyer-agent fleet simulator + growth-agent loop (LLM API calls only) | **Python asyncio worker** (no web framework needed beyond a tiny /healthz) | **~250–400 MB** |
| **VM3** `control-tower` | Audit trail UI, metrics dashboard, approval queue for judges | **Server-rendered HTML + htmx + Alpine.js via FastAPI** (or plain static + SSE), Chart.js | **~300–400 MB** |

**Red lines (do NOT put on these VMs):**
- ❌ Next.js production server (~250–500 MB RSS baseline, spikes higher) — build React statically elsewhere and serve files if ever needed, but we won't.
- ❌ Untuned PostgreSQL (default config wants ~25% RAM for shared_buffers era defaults + per-conn overhead; fine when tuned, but SQLite removes an entire failure class).
- ❌ Docker layer on top of everything (adds daemon RSS ~100 MB + complexity). Bare metal + systemd.
- ❌ Local LLM inference. Obviously. All intelligence is cloud API calls.

---

## 1. Framework reality check

| Option | Typical idle RSS | Verdict |
|---|---|---|
| FastAPI + uvicorn, 1 worker | 50–90 MB | ✅ **Choice.** Async native (we need concurrent LLM calls + webhooks), typed, fast to write solo |
| Flask + gunicorn | 40–70 MB | Fine, but sync model complicates the agent fleet runner |
| Express/Fastify (Node) | 60–120 MB | Fine too; pick ONE language across all 3 VMs → **Python everywhere** |
| Go stdlib | 10–20 MB | Lowest footprint but slowest solo-dev velocity for a 1-week deadline |
| Next.js prod (`next start`) | 250–500 MB | ❌ Rejected — half the box gone before business logic starts |

**Rule for 1 GB boxes:** one primary process per VM + Caddy + system daemons. Never co-locate two heavy services.

## 2. Database: SQLite in WAL mode — why it's *correct* here, not just convenient

Our workload: **single writer** (the merchant-core API), many readers (dashboard polls, agent fleet reads catalog). That is exactly SQLite's sweet spot. WAL mode gives concurrent readers + 1 writer without blocking.

```sql
PRAGMA journal_mode = WAL;          -- readers don't block the writer
PRAGMA synchronous  = NORMAL;       -- safe with WAL; full fsync only at checkpoints
PRAGMA busy_timeout = 5000;         -- ms; survive brief write contention
PRAGMA foreign_keys = ON;
PRAGMA wal_autocheckpoint = 1000;   -- pages; keeps -wal file bounded
```

- Zero-install, zero-daemon, backup = copy one file (+ `-wal`). Perfect for demo resets: snapshot the `.db` before the demo, restore in 2 seconds if anything goes sideways.
- **When would we outgrow it?** Only if the buyer-fleet simulation wrote sessions in parallel to the same DB. Mitigation: VM2 writes its own results DB locally and ships summary JSON to VM1/VM3 — never share the SQLite file over a network filesystem.
- Tuned PostgreSQL remains the documented fallback (see §9 Fallbacks) if we ever need multi-writer.

## 3. Frontend/dashboard: server-rendered + htmx + Alpine.js

- Templates rendered server-side (Jinja2), sprinkled with **htmx** for partial swaps and **Alpine.js** for client-side bits. Total JS payload ≈ 30–50 KB. No node_modules anywhere near production.
- **Live updates → SSE (Server-Sent Events)**, not WebSocket: one-directional (server→judge screen), survives proxies trivially, auto-reconnect built into `EventSource`, and costs one long-lived connection per viewer. Judges' dashboard subscribes to `/events` and every new audit record / payment event pushes a row instantly. Polling (every 2–3 s) is the even-lazier fallback and is perfectly acceptable for charts.
- **Charts:** Chart.js (~70 KB) is plenty. uPlot (~45 KB, handles huge series) if we want to show off. Sparklines can be hand-rolled SVG.

## 4. Process supervision: systemd everywhere

Every service gets a unit like:

```ini
# /etc/systemd/system/merchant-core.service
[Unit]
Description=Merchant core API
After=network-online.target
Wants=network-online.target

[Service]
User=app
WorkingDirectory=/opt/buildathon/app
ExecStart=/opt/buildathon/app/.venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 --port 8000 --workers 1
Environment=ENV=prod
Restart=always
RestartSec=3
MemoryMax=700M            # hard cap so the box never OOM-dies; service restarts instead
MemoryHigh=550M           # soft throttle first
# Logs go to journald automatically

[Install]
WantedBy=multi-user.target
```

Ops habits for demo week:
- `systemctl status <svc>` + `journalctl -u <svc> -f` is our entire observability stack.
- Each service exposes `GET /healthz` (checks DB open + Razorpay key present); Caddy or a cron'd curl alerts nothing fancy — a terminal watch window during the demo is enough.

## 5. Memory safety net: swap + sysctl

On each VM (swap is insurance, not capacity planning):

```bash
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl vm.swappiness=10          # prefer shedding page cache before swapping
```

Also set `MemoryMax=` caps (above) so a leak restarts one service instead of kernel-OOM-ing the whole box mid-demo.

## 6. Networking between VMs

- **Caddy on VM1 and VM3** (~30–45 MB RSS): automatic Let's Encrypt TLS, dead-simple config, serves as reverse proxy + static file server.

```caddyfile
# /etc/caddy/Caddyfile (VM1)
api.ourdomain.in {
    reverse_proxy 127.0.0.1:8000
}
```

- **Webhook ingress:** Razorpay must reach VM1 over public HTTPS. A DNS name + Caddy TLS is the clean way. (Whether test-mode webhooks strictly require HTTPS is being verified in research report 02; ngrok/cloudflared on VM1 is the dev-time fallback tunnel.)
- **VM↔VM calls** (VM2→VM1 API, VM1→VM3 events): simplest is public URLs over TLS with a shared bearer token. If the provider allows private networking, Tailscale (~40–60 MB RSS) is nice-to-have, not needed. Don't burn a day on mesh networking for a demo.

## 7. Python environment

- **uv** for venv + dependency management (fast, one binary). `uv venv && uv pip install -r requirements.txt`.
- **Single asyncio process** — not gunicorn multi-worker. Our concurrency is I/O-bound (HTTP + LLM APIs); `uvicorn --workers 1` + `asyncio.Semaphore(8–16)` around outbound LLM calls gives us a fleet simulation in <150 MB. Multiple workers would multiply RSS × N for zero benefit here.

## 8. Demo resilience checklist

- [ ] `scripts/reset_demo.py`: truncate transactional tables, reseed catalog/personas, clear audit chain genesis — one command, tested, <10 s.
- [ ] Pre-demo snapshot: `cp merchant.db merchant.db.prestemo`.
- [ ] Pre-warm every LLM call path 10 min before stage time (cold TLS + cold prompts cost latency on first hit; prompt caching helps repeat hits).
- [ ] `make health` — curls `/healthz` on all three VMs, prints a green/red line.
- [ ] Fallback: pre-recorded 90-second screen capture of the full happy-path + failure-path, on the desktop, wired to a keyboard shortcut. Hope to never show it; relieved if we have it.
- [ ] Time-sync (`systemd-timesyncd`) on all VMs — hash-chained audit timestamps and webhook signature windows break silently on clock skew.

## 9. Fallbacks (only if something breaks)

| If… | Then… |
|---|---|
| SQLite write contention appears under fleet load | Fleet writes its own local results DB; only aggregates POST to VM1 |
| uvicorn memory creep over hours | `MemoryMax` restart policy already handles it; plus a nightly `systemctl restart` cron during build week |
| Public TLS/domain blocked by provider | cloudflared tunnel on VM1 (free, ~30 MB) for webhook ingress |
| We truly need multi-writer DB | Tuned Postgres: `shared_buffers=128MB`, `effective_cache_size=256MB`, `work_mem=4MB`, `max_connections=20`, `synchronous_commit=on` — fits, but adds a daemon to babysit |

---

## Sources & confidence

- Memory figures: field-typical estimates from widely-known production experience — treat as ±30%, verify on real boxes with `ps aux --sort=-rss`. **ESTIMATE**
- Config snippets (systemd directives, sysctl, SQLite pragmas, Caddy): standard documented behavior of those tools.
- Razorpay webhook ingress requirements: deliberately NOT asserted here — owned by `02-razorpay-testmode-deepdive.md`.
