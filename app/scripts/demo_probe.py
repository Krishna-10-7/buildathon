"""Probe the demo server the way the browser does.

Opens the SSE stream, POSTs /api/start, then prints every event until the
trip ends. Verifies the replay path end to end without a browser, and
checks the one invariant that matters for the submission: in replay mode
every beat must carry mode="replay", so a reconstruction can never be
rendered as if it were live.

Dev tool for the sprint; not part of the demo itself.

    uv run python scripts/demo_probe.py [base] [persona] [mode] [variant]
"""

import json
import sys
import threading
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8399"
PERSONA = sys.argv[2] if len(sys.argv) > 2 else "ritika"
MODE = sys.argv[3] if len(sys.argv) > 3 else "replay"
VARIANT = sys.argv[4] if len(sys.argv) > 4 else "paid"

# Fields worth echoing; the full event is far noisier than this.
SHOW = ("mode", "order_id", "rp_order_id", "amount_paise", "payment_id",
        "outcome", "bank", "method", "name", "brain", "budget_paise",
        "status", "stage", "error", "notes", "total_paise", "session_id")

BEATS = ("session_start", "catalog", "plan", "ck_order_created",
         "ck_browser_up", "ck_contact_passed", "ck_method_selected",
         "ck_bank_picked", "ck_bank_confirmed", "ck_captcha_challenge",
         "captured", "outcome")

events: list[dict] = []
stop = threading.Event()


# trust_env=False: this tool only ever talks to a loopback demo server,
# and a machine-wide HTTP_PROXY will otherwise swallow the request and
# hand back a 502 from the proxy instead of the runner.
CLIENT = httpx.Client(trust_env=False, timeout=90)


def reader() -> None:
    with CLIENT.stream("GET", f"{BASE}/api/events") as r:
        for line in r.iter_lines():
            if stop.is_set():
                break
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            events.append(ev)
            if ev.get("t") in ("outcome", "replay_meta_error"):
                stop.set()
                break


threading.Thread(target=reader, daemon=True).start()
time.sleep(2.0)

resp = CLIENT.post(
    f"{BASE}/api/start",
    json={"persona": PERSONA, "mode": MODE, "variant": VARIANT},
    timeout=20)
print(f"POST /api/start -> {resp.status_code} {resp.text.strip()}")
print("-" * 78)

deadline = time.time() + 60
while not stop.is_set() and time.time() < deadline:
    time.sleep(0.5)

for ev in events:
    fields = {k: v for k, v in ev.items()
              if k in SHOW and v is not None}
    print(f"{ev.get('t'):<22} {json.dumps(fields, ensure_ascii=False)[:185]}")

print("-" * 78)
print(f"{len(events)} events received")

if MODE == "replay":
    unflagged = sorted({e["t"] for e in events
                        if e.get("t") in BEATS and e.get("mode") != "replay"})
    print("replay beats missing mode=replay:", unflagged or "NONE — good")
    got = [e["t"] for e in events if e.get("t") in BEATS]
    print("beats seen:", len(got), "of", len(BEATS))
    outcome = next((e for e in events if e.get("t") == "outcome"), None)
    if outcome:
        print("outcome:", outcome.get("outcome"),
              "| order:", outcome.get("order_id"),
              "| gateway:", outcome.get("rp_order_id"),
              "| payment:", outcome.get("payment_id"))
    sys.exit(1 if unflagged else 0)
