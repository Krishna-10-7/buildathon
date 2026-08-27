# ARCHITECTURE.md — system architecture with diagrams

Companion to [SOLUTION.md](SOLUTION.md) (what & why) and
[AGENT-DESIGN.md](AGENT-DESIGN.md) (the buyer agents in depth).

Two Azure VMs, zero paid services. **VM1** hosts the governed merchant
(`r2-d2.xyz`, systemd-managed, Caddy TLS). **VM2** is the measurement
driver that aims AI shoppers at it. Everything below maps to real modules
in `app/bazaar/*` (merchant core) and `app/exp/*` (demand side).

---

## 1. Deployment topology

```mermaid
flowchart LR
    subgraph world["Browsers"]
        B["Control Tower UI<br/>GET /control"]
        J["Live-town viewer<br/>judge presses START"]
    end

    subgraph vm1["VM1 - merchant host - r2-d2.xyz, 1 GB VM, Ubuntu"]
        direction TB
        CAD["Caddy - TLS :443, reverse proxy :8000"]
        subgraph svc["bazaar.service - systemd, Restart=always, MemoryMax 650M"]
            direction LR
            EDGES["Thin HTTP edges"]
            CORE["Domain cores"]
            ADPT["Adapters"]
            EDGES --> CORE --> ADPT
        end
        DB[("SQLite - WAL,<br/>integer-paise money,<br/>hash-chained audit")]
        ADPT --- DB
    end

    subgraph rzp["Razorpay cloud - test mode"]
        RAPI["Orders API"]
        RISK["Checkout JS + risk engine<br/>captcha challenges"]
        WH["payment.captured webhooks"]
    end

    subgraph vm2["VM2 - measurement driver, 1 GB VM, tmux session"]
        direction TB
        RUN["run_measurement.py<br/>session loop, pause jitter,<br/>circuit breaker"]
        PER["exp/personas.py<br/>Ritika / Arjun / Meera"]
        BRAIN["LLM adapter<br/>Gemini / OpenRouter / NIM / mock"]
        PW["Playwright Chromium<br/>exp/checkout.py"]
        OUT[("artifacts/sessions.jsonl")]
    end

    subgraph local["Laptop - demo instruments"]
        SHOW["scripts/live_show.py<br/>pixel town + SSE"]
        QUEST["bazaar-quest.html story cutscene"]
    end

    B -->|"HTTPS"| CAD
    CAD --> EDGES
    RUN --> PER
    PER --> BRAIN
    PER --> PW
    PW -->|"real checkout drive"| CAD
    RUN -->|"flip arm, EXP_TOKEN"| EDGES
    RUN --> OUT
    ADPT -->|"create order"| RAPI
    RISK -.->|"served into"| PW
    WH -->|"HMAC-SHA256 verified POST"| CAD
    SHOW -->|"drives ONE real persona trip"| CAD
    J -.->|"SSE events"| SHOW
    QUEST -.->|"offline storytelling"| J
```

## 2. Layering inside the merchant core (clean architecture)

Dependency direction is one-way and structural, not aspirational:

```mermaid
flowchart TD
    subgraph edges["HTTP/MCP edges - parse, delegate, map errors. Zero business rules"]
        E1["catalog_api"] 
        E2["orders_api"]
        E3["webhooks"]
        E4["governance_api"]
        E5["mandates_api"]
        E6["experiment_api"]
        E7["agents_api"]
        E8["audit_api read-only"]
        E9["control_page presentation only"]
        E10["mcp_server tool surface"]
    end
    subgraph cores["Domain cores - the rules live here"]
        C1["policy.py - PURE,<br/>clamp-not-reject,<br/>no I/O at all"]
        C2["proposals - only writer<br/>of proposals/approvals"]
        C3["mandates - signed spend<br/>envelopes, enforced pre-gateway"]
        C4["orders - server-side pricing,<br/>atomic stock reservation"]
        C5["experiment - audited arm switch"]
        C6["agents/growth - read + propose,<br/>cannot execute anything"]
    end
    subgraph adapters["Adapters - swappable infrastructure"]
        A1["db.py SQLite WAL"]
        A2["rzp.py Razorpay REST"]
        A3["llm.py provider swap:<br/>gemini / OpenAI-compat / NIM / mock"]
        A4["audit.py hash chain -<br/>the only ledger writer"]
    end
    edges --> cores --> adapters
```

Invariants this buys us:

- **Client-sent amounts are never trusted** — `orders.py` prices every
  basket from the catalog server-side.
- **Money-affecting change has exactly one door** — `propose()` → human
  `decide` → `execute`; low-risk actions may auto-execute, everything else
  sits in `pending_review`.
- **The ledger has one writer** (`audit.append`) and one reader edge
  (`GET /audit/recent`, which replays the chain and returns
  `first_bad_seq`).
- **The growth agent cannot spend** — its pipeline ends at `propose()`,
  structurally.
- **MCP and REST share the same functions**, so the two surfaces can never
  drift apart.

## 3. Anatomy of one money action (paid-trip happy path)

```mermaid
sequenceDiagram
    autonumber
    participant P as Persona exp/personas
    participant CG as constrain_basket code gate
    participant O as orders.py on VM1
    participant MD as mandates.py
    participant POL as policy.py pure engine
    participant RP as Razorpay test gateway
    participant WB as webhooks.py
    participant AU as audit ledger

    P->>P: LLM plans basket from live catalog within stated budget
    P->>CG: proposed items
    CG->>CG: drop unknown sku, out-of-stock, line cap,<br/>qty clamp, budget overrun -> notes
    CG->>O: clamped lines
    O->>MD: mandate presented?
    MD-->>O: signature valid, bounds satisfied, spend available
    O->>POL: resolved context
    POL-->>O: Decision (clamped values, reasons)
    O->>RP: create order, server-priced amount
    O->>AU: append order_created
    P->>RP: browser checkout: contact, method, bank
    RP-->>P: redirect, capture poll
    RP->>WB: payment.captured + X-Razorpay-Signature
    WB->>WB: verify HMAC-SHA256 raw body, dedupe event id
    WB->>MD: draw down mandate spend (captures only)
    WB->>AU: append payment_captured
```

Failure paths are first-class branches, not exceptions to hide:

```mermaid
sequenceDiagram
    autonumber
    participant P as Persona
    participant RP as Risk engine
    participant LLM as LLM lane
    Note over P: quota outage -> typed llm_error session
    LLM--xP: 3 consecutive failures
    Note over P: fleet circuit breaker ABORTS run,<br/>keeps written sessions, reports honestly
    P->>RP: checkout attempt
    alt captcha challenge
        P->>P: abandon instantly, never solve programmatically
        Note over P: outcome=risk_challenged, wait 30-75s like a human would
    else bank race / driver fault
        Note over P: outcome=infra_error, attempt capped (one-attempt policy)
    else nothing fits budget or taste
        Note over P: outcome=walked_away, counted as a VALID session
    end
```

## 4. Governance loop (how agent actions touch the storefront)

```mermaid
flowchart TD
    SNAP["GET /agent/snapshot<br/>numeric state: margins, stock, orders"] --> GA["growth agent - LLM smart lane"]
    GA -->|"strict-JSON strategy"| PROP["proposals.propose<br/>status = pending_review"]
    PROP --> DEC{"Human decides<br/>POST /proposals/id/decide"}
    DEC -->|"approve"| EXE["proposals.execute<br/>single dispatch point"]
    DEC -->|"reject"| NOOP["audited, nothing changes"]
    LOW["risk=low actions"] -->|"may auto-execute"| EXE
    EXE --> CAT["catalog price edits + bundles activate"]
    CAT --> ARM["experiment.set_arm<br/>control: base prices, bundles off<br/>treatment: executed actions applied"]
    PROP --> LED["audit.append on EVERY transition"]
    EXE --> LED
    ARM --> LED
    LED --> CHAIN[("tamper-evident chain<br/>self_hash = sha256 of prev hash + record<br/>verify replays all, reports first_bad_seq")]
```

The same chain also records arm flips, mandate issuance/revocation,
webhook applications, and order/payment events — one timeline, any
dispute replayable.

## 5. Data & evidence plane

| Store | What lives there |
|---|---|
| SQLite (VM1) | products, orders, payments, proposals/approvals, mandates, bundles — integer paise throughout |
| Audit ledger (VM1) | append-only hash chain, 650+ records, `first_bad_seq: null` at last verification |
| `artifacts/sessions.jsonl` (VM2) | one JSON line per shopping trip: persona, LLM brain, verbatim analysis, basket + clamp notes, attempts, typed outcome |
| `PREREGISTRATION.md` | hypotheses + arms written before the run |
| `artifacts/sessions_final.jsonl` | the frozen n=94 dataset (41 paid / 30 risk_challenged / 18 llm_error / 5 infra_error) |

## 6. Operational posture

- **systemd**: `bazaar.service` with `Restart=always`, `RestartSec=3`,
  `MemoryMax=650M` on a 1 GB box; self-healing proven by controlled
  restart during the live period.
- **Caddy**: automatic TLS on `r2-d2.xyz`, proxying to uvicorn on
  localhost:8000.
- **Zero new dependencies**: Razorpay, Gemini/Groq/NIM calls are raw
  `httpx`; storage is SQLite; browser automation is Playwright — the only
  heavyweight, and it lives on the *driver* VM, not the merchant.
- **Keyless mode**: `LLM_PROVIDER=mock` runs the entire pipeline with no
  keys at all (rehearsal ids `ord_REHEARSAL…` can never masquerade as
  captured payments).
- **Secrets**: `.env` on the host only; `EXP_TOKEN` is minted on VM1 and
  piped to VM2 without ever being printed; webhook secret verified via
  HMAC before any state change.
