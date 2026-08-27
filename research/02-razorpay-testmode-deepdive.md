# Razorpay Test-Mode Deep Dive — Headless Agent Payments (Buildathon Track: AI Growth & Agentic Commerce)

Research date: 2026-08-22. All findings verified against Razorpay official docs (razorpay.com/docs), newsroom, GitHub and npm/PyPI registries unless marked **UNVERIFIED**.

---

## TL;DR — the payment integration plan

The locked end-to-end flow for **headless agent purchases in test mode** (no human, no dashboard):

1. **Generate Test API keys** (`rzp_test_...`) in Dashboard → Account & Settings → API Keys (Test tab). Base URL is the SAME as live: `https://api.razorpay.com/v1/`.
2. **Create order (server-side):**
   ```bash
   curl -u $KEY_ID:$KEY_SECRET -X POST https://api.razorpay.com/v1/orders \
     -H "content-type: application/json" \
     -d '{"amount": 50000, "currency": "INR", "receipt": "agent-run-0001", "notes": {"agent":"growth-agent","campaign":"cart-recovery"}}'
   ```
   → returns `order_RB58...`, `status: "created"`. `receipt` must be unique per account (max 40 chars) — it is the de-facto idempotency key.
3. **Complete payment via scripted browser** (the only fully-supported path — see "Headless simulation" below for why there is no pure-API completion): headless Chromium (Playwright/Puppeteer) loads a minimal local page with Standard Checkout (`https://checkout.razorpay.com/v1/checkout.js`, options: `key`, `amount`, `currency`, `order_id`, `prefill`) OR opens a Payment Link `short_url` (`https://rzp.io/i/...`); script enters test card `4111 1111 1111 1111` / any CVV / any future expiry (or VPA `success@razorpay` / `failure@razorpay`), then clicks **Success/Failure** on the mock bank page to deterministically pick the outcome.
4. **Receive webhooks**: configure a Test-mode webhook in Dashboard → Settings → Webhooks (separate URL field for Test mode). Handle `payment.captured`, `payment.failed`, `order.paid`. Verify `X-Razorpay-Signature` = HMAC-SHA256(raw request body, webhook secret). Return 2XX within 5 s; dedupe on `x-razorpay-event-id`.
5. **Confirm server-side** (webhook is at-least-once + can arrive out of order; never trust the client handler alone):
   `GET https://api.razorpay.com/v1/payments/{pay_id}` → check `status == "captured"`, read `error_code/error_description/error_source/error_step/error_reason` on failures.
6. **Dashboard** reads from `GET /v1/orders/{id}/payments`, `GET /v1/payment_links/{id}`, subscription events (`subscription.charged`, `subscription.pending`, `subscription.halted`).
7. Optional agentic garnish: drive steps 2–6 through the **Razorpay Payment CLI** or **Razorpay MCP Server** (`https://mcp.razorpay.com/mcp`) so the demo shows agents transacting natively.

---

## 1. Orders & Payments in test mode

### REST flow (verified)
- **Base URL identical for test and live:** `https://api.razorpay.com/v1/` — only the key pair changes (docs: API Sandbox Setup).
- **Auth:** HTTP Basic, `-u KEY_ID:KEY_SECRET`. Mixing test/live keys → `400 Authentication failed`.
- **POST /v1/orders** fields (verified from Create Order reference): `amount` (integer, smallest subunit; INR min ₹1 = `100`), `currency`, `receipt` (≤40 chars, unique per account), `notes` (max 15 pairs × 256 chars), `partial_payment` / `first_payment_min_amount` (referenced in error table even though not in main param table).
- **Order response:** `id` (`order_XXX`), `entity:"order"`, `amount`, `amount_paid`, `amount_due`, `currency`, `receipt`, `status` (`created` → `attempted` → `paid`; terminal once `paid`, no further payments even after refund), `attempts`, `offer_id`, `created_at`.
- **400 taxonomy on order create:** auth failure, amount < min, missing/negative/non-integer amount, invalid currency, receipt >40 chars / non-ASCII / duplicate (**duplicate receipt acts as idempotency guard**), malformed JSON ("EOF."), concurrent order lock.
- **Payment entity** (verified from Payments Entity page): `id` (`pay_XXX`), `status` ∈ {`created`, `authorized`, `captured`, `refunded`, `failed`}, `method` ∈ {`card`,`netbanking`,`wallet`,`emi`,`upi`}, `captured` (bool), `amount_refunded`, `refund_status` (null/partial/full), `fee`, `tax`, `vpa`, `bank`, `wallet`, `card`/`card_id`, `international`, `acquirer_data`, and the failure block: `error_code`, `error_description`, `error_source`, `error_step`, `error_reason`.
- **Payments API constraint (docs' own words):** you may only *retrieve* payments or change `authorized` → `captured` (`POST /v1/payments/{id}/capture`). There is **no create-payment API** — collecting money always goes through a Razorpay payment product (Checkout / Links / etc.).

### Client libraries
| Library | Package | Latest verified | Status |
|---|---|---|---|
| Node | `razorpay` (npm) | 2.9.8, published ~Jul 14 2026 | Actively maintained (repo razorpay/razorpay-node) |
| Python | `razorpay` (PyPI) | 2.0.1, released Mar 9 2026 | Maintained; classifiers stale ("Python 3.4–3.6") but works on modern Python |
| PHP | razorpay-php | v2.9.3, Jun 8 2026 (security fix AES-GCM nonce) | Maintained |
| Others | Java, Go, Ruby, .NET | — | Official repos exist under github.com/razorpay |

Both Node and Python SDKs ship webhook + payment signature verification helpers (`Razorpay.validateWebhookSignature(...)` in Node; Python docs files `webhook.md`, `paymentVerfication.md`). Python SDK also has `client.enable_retry(True)`.

### Headless / no-browser payment simulation — THE critical question
**There is NO official API to force a card/UPI/netbanking payment success or failure programmatically.** Verified evidence:
- Payments API explicitly cannot collect payments (see above).
- GitHub issue razorpay/razorpay-python#113 ("test the whole flow without a front end") — Razorpay team's answer was to mock the client/signature verification in unit tests. No headless endpoint exists.
- All documented test flows end at a **mock bank page with Success/Failure buttons** inside Checkout UI.

Options ranked for our demo:

| Option | Verdict |
|---|---|
| **A. Playwright-driven Standard Checkout or Payment Link** (headless Chromium loads checkout.js page / `short_url`, fills test card/VPA, clicks Success/Failure button) | ✅ Recommended. Fully deterministic outcome control, ~50 lines of automation, runs in CI. This is the cleanest headless test-mode simulation available. |
| B. Legacy BharatQR test-pay endpoint: `POST https://api.razorpay.com/v1/bharatqr/pay/test` with `{"reference": "<17-char QR id>", "amount": 500, "method": "card"\|"upi"}` | ⚠️ Works **without any browser** and fires real webhooks, but only applies to legacy BharatQR codes (not Orders/Checkout flow). Niche fallback. |
| C. New QR Codes API (`/v1/payments/qr_codes`) | ❌ Dead end: test-mode QR codes "disappear after two seconds" and cannot be scanned; only live-mode QRs are scannable. |
| D. Pure-API fake completion (fabricate `payment_id` + signature) | ❌ Not possible against real Razorpay state; only valid as local unit-test mocking. |

Server-side signature check after client callback (documented in Standard Checkout integration): HMAC-SHA256 of `` `${razorpay_order_id}|${razorpay_payment_id}` `` keyed with `key_secret`, compared against `razorpay_signature`. (Exact doc URL for this subsection not re-fetched — **UNVERIFIED URL**, formula is standard across Razorpay docs.)

---

## 2. Test cards & UPI (verified tables)

Cards work ONLY in test mode (live mode errors: `card issuer is invalid`, `invalid card input`). Any CVV, any future expiry.

| Network | Scope | Card number | Behavior |
|---|---|---|---|
| Visa | Domestic (India) | `4111 1111 1111 1111` | Success flow |
| Mastercard | Domestic | `5267 3181 8797 5449` | Success flow |
| Mastercard | Domestic | `5104 0155 5555 5558` | Success flow |
| Mastercard | International | `5555 5555 5555 4444` | Success flow |
| Mastercard | International | `5105 1051 0510 5100` | May trigger address-collection window — use address: "21 Applegate Apartment", "Rockledge Street", New York, NY, US, zip 11561 |
| Visa | International | `4012 8888 8888 1881` | Success flow |
| Visa | International | `5104 0600 0000 0008` | Success flow |

- **OTP simulation:** random OTP of 4–10 digits always succeeds; OTP shorter than 4 digits fails; a 5-digit OTP starting with `1` fails (Standard Checkout guide). 
- **Error-scenario cards:** the official Test Card Details page has dedicated sections "Error Scenarios" with BAD_REQUEST_ERROR and GATEWAY_ERROR cards per network, plus Subscriptions and EMI card sections — the table numbers did not render in fetches; exact numbers **UNVERIFIED** (pick them live from https://razorpay.com/docs/payments/payments/test-card-details/ during build week). RuPay test numbers also **UNVERIFIED**.
- **Subscriptions note:** test card tokens are valid only **3 days** from creation; subsequent debits work only within that window.

### UPI test VPAs
| VPA | Behavior |
|---|---|
| `success@razorpay` | Payment succeeds |
| `failure@razorpay` | Payment fails (fires `payment.failed`) |

Caveat from docs: in test mode, UPI **cancellation results in a successful payment** — cancellation is only properly testable live.

### Netbanking / wallets
Select any listed bank/wallet → mock page with explicit **Success** / **Failure** buttons. No real portals. Wallet mock accepts any amount.

---

## 3. Webhooks

- **Config:** Dashboard → Settings → Webhooks; **separate URLs for Live and Test modes**; subscribe per-event. Ports **80 or 443 only**.
- **Events relevant to us:** `payment.authorized`, `payment.captured`, `payment.failed`, `payment.downtime.started/resolved/updated`, `order.paid`, plus subscription events (`subscription.activated`, `subscription.charged`, `subscription.pending`, `subscription.halted` — from subscriptions test docs) and Route events (`transfer.processed`, `transfer.failed`, `settlement.processed`).
- **Payload shape** (verified from `payment.captured` sample): top level `event`, `contains:["payment"]`, `account_id`, `created_at`, `payload.payment.entity.{id,status,amount,currency,method,order_id,captured,fee,tax,error_*,acquirer_data,...}`. No separate `order.paid` sample published on the payloads page (**schema UNVERIFIED** — expect `payload.order.entity` alongside `payload.payment.entity`).
- **Signature:** header `X-Razorpay-Signature` = HMAC-SHA256 where **key = webhook secret** (set in Dashboard, separate from API key secret) and **message = raw request body**. Verify BEFORE parsing/casting the body; use timing-safe compare. Node helper: `Razorpay.validateWebhookSignature(rawBody, signature, secret)`; Java: `Utils.verifyWebhookSignature(...)`.
- **Response contract:** return any **2XX within 5 seconds**; non-2XX or timeout ⇒ delivery failure.
- **Retries:** exponential backoff, retried for up to **24 hours** from event creation; if still failing, the webhook is **auto-disabled** until manually re-enabled in Dashboard.
- **Delivery semantics:** at-least-once ⇒ duplicates expected ⇒ dedupe on unique header **`x-razorpay-event-id`**. Out-of-order delivery happens (e.g., `payment.authorized` after `payment.captured`) — handle idempotently.
- **Dev-time endpoints / tunnels:** localhost is NOT reachable. Docs suggest tunneling (they name `zrok`) or a staging URL, and warn that many interception domains are **blacklisted**, explicitly including **`ngrok.io`**, `requestbin.com`, `webhook.site`, `beeceptor.com`, `hookbin.com`, `interact.sh`, `canarytokens.com`. Whether `*.trycloudflare.com` (cloudflared quick tunnels) passes the filter is **UNVERIFIED** — plan a fallback (own domain behind Cloudflare, or a cheap public VM).
- Handy tip from validate/test page: default OTP for setup/edit/delete of test-mode webhooks is `754081`.
- Test vs Live payload bodies are identical, so staging tests transfer cleanly.

---

## 4. Payment Links API

Lifecycle: create link → customer opens `short_url` → pays → link becomes `paid`/`partially_paid` → `expire_by`/cancel paths.

**POST /v1/payment_links** (verified fields): `amount`*, `currency`, `accept_partial`, `first_min_partial_amount`, `upi_link`, `description`, `reference_id` (unique, ≤40), `customer{name,contact,email}`, `expire_by` (unix ts; max 6 months out), `notify{sms,email}`, `reminder_enable`, `notes`, `callback_url`, `callback_method` (only `get`).

Response: `id` prefix **`plink_`**, `short_url` (`https://rzp.io/i/...`), `status` ∈ {`created`, `partially_paid`, `paid`, `expired`, `cancelled`}, `payments` stays `null` until first capture, `reminders[]`.

Other endpoints: Fetch (all/by id, standard & UPI variants), Update (PATCH), Cancel (POST), Send-or-resend notifications (POST), Manage reminders (POST), plus customisation endpoints (theme, business name, method restrictions via `options.checkout.method.*`).

Test-mode specifics:
- Cap: **30 Payment Links per business** in test mode.
- **UPI payment links are NOT supported in test mode** (live keys required).
- Completion still requires opening `short_url` in a browser and choosing method + Success/Failure — i.e., automatable by Playwright but **not completable by pure REST**. Docs literally instruct testers to copy the URL into a browser and click Success/Failure.
- If using `callback_url`, verify the returned `razorpay_signature`.

---

## 5. Subscriptions / Mandates / e-mandate feasibility

Verified building blocks:
- **Plans:** `POST /v1/plans` with `period` (`daily|weekly|monthly|quarterly|yearly`), `interval` (min 7 for daily), `item{name, amount(subunits), currency}` → `plan_XXX`.
- **Subscriptions:** `POST /v1/subscriptions` with `plan_id`*, `total_count`* (mutually exclusive with `expire_by`), `quantity`, `customer_notify` (bool), `start_at`, `addons`, `notes`, `offer_id`. Statuses include `created`, `authenticated`, `active`, `pending`, `halted`, `completed`, `expired`, `cancelled`. Subscription Link variant adds `notify_info{email,contact}`.
- **Authentication in test mode:** pay via Standard Checkout passing only `key` + `subscription_id`; immediate-start subs charge full amount and go `active` (fires `subscription.activated` then `subscription.charged`); future-start subs charge ₹5 token amount, immediately refunded, land in `authenticated`.
- **Simulating recurring charges:** Dashboard **"Charge this now"** button triggers the next due charge immediately; you choose its outcome. Failure ⇒ `active → pending` (`subscription.pending`), next charge advanced one day; **4 consecutive failures ⇒ `halted`** (`subscription.halted`). Halted subs get an Issue Invoice button instead.
- **Limitations:** subsequent debits only within **3 days** of token creation; cannot test update-subscription after post-auth charges.

**Verdict for the "failed-subscription recovery" story: feasible but medium-weight.** The happy path + deterministic recovery loop (auth → Charge-this-now failure ×N → halted → agent intervenes → re-charge success) is demonstrable in test mode with cards. BUT:
- The official test-subscriptions page documents only **cards**. e-Mandate/eNACH and UPI Autopay testing in test mode is **not documented** there — treat as unsupported/risky for a 1-week scope (**UNVERIFIED** whether they work; a third-party Juspay comparison claims card mandates and UPI-collect mandates are testable on Razorpay, unconfirmed by Razorpay's own pages).
- Note the sponsor-alignment jackpot: Razorpay's own **Subscription Recovery Agent** (FTX'26 Agent Studio, voice-led, built with ElevenLabs) is exactly this story — mirror their framing.

---

## 6. Route / Settlements (brief)

- Route supports automatic transfers (on order creation), on-payment transfers, direct transfers. **Test transfers can be simulated via Dashboard methods**; you cannot create live transfers to linked accounts created in test mode.
- Settlements APIs return settlement reports/data; the **Payment CLI supports Settlements + Instant Settlements including in test mode**, and Route transfers/linked accounts — trivially displayable on our dashboard.
- Useful only if it costs < an hour: show a transfer created against an order + settlements list. Otherwise skip.

---

## 7. Rate limits, idempotency, error taxonomy

- **Rate limits:** no numeric limit published in docs. Docs say: watch for **HTTP 429**, implement exponential backoff with jitter, prefer webhooks over polling; limit increases via support. Sample 429 body: `{"error":{"code":"BAD_REQUEST_ERROR","description":"Too many requests",...}}`.
- **Idempotency-Key header: NOT supported** (no mention anywhere in current docs; contrast with Stripe). Recommended duplicate-prevention patterns instead:
  - Unique `receipt` on order create (server rejects duplicates with 400) — de-facto idempotency.
  - Unique `reference_id` on payment links.
  - Order state machine: a `paid` order rejects further payments; an order in `attempted` with authorized payment blocks new attempts.
  - Webhook dedupe via `x-razorpay-event-id`.
- **Error object:** `{error:{code, description, field, source, step, reason, metadata:{payment_id, order_id}}}`.
- **Top-level codes:** `BAD_REQUEST_ERROR`, `SERVER_ERROR`, `GATEWAY_ERROR` (latter two referenced across docs; only BAD_REQUEST_ERROR shown on the main errors page).
- **Failure detail fields on payments:** `error_source` (e.g., `customer`, or gateway/bank/network/razorpay), `error_step` (e.g., `payment_authentication`; values vary by method), `error_reason` (e.g., `invalid_otp` / `incorrect_otp`) — "can be handled programmatically".
- Hard-decline vs transient mapping beyond these examples: **UNVERIFIED** — docs do not publish a definitive retryability classification. Working heuristic for the demo: `BAD_REQUEST_ERROR` = permanent/integration bug; `GATEWAY_ERROR` or bank-side declines with populated `error_source=bank/gateway` = transient/retryable; anything with `error_step=payment_authentication` and reason like otp failures = customer-side retryable.

---

## 8. Razorpay + agentic commerce, 2025–2026 (sponsor-strategy alignment)

Timeline (all from razorpay.com/newsroom unless noted):

| Date | Item |
|---|---|
| Apr 2025 | **MCP Server launch** — first Indian payment gateway with MCP. Remote server `https://mcp.razorpay.com/mcp` (Basic auth `key:secret`) or local Docker (`razorpay/mcp`). Tools incl. `create_order`, `fetch_order`, `fetch_order_payments`, `create_payment_link`, `fetch_payment_link`, refunds, QR, settlements, payouts. Repo: github.com/razorpay/razorpay-mcp-server. |
| Oct 9, 2025 | **Agentic Payments on ChatGPT** pilot with NPCI + OpenAI (first unveiled at Global Fintech Fest 2025); Axis Bank + Airtel Payments Bank rails, UPI Reserve Pay/UPI Circle; BigBasket first merchant. |
| Feb 20, 2026 | **Agentic Payments on Claude** with NPCI at India AI Impact Summit — Zomato, Swiggy, Zepto purchases in-conversation; UPI Reserve Pay spending limits. |
| Mar 12, 2026 | **Razorpay Agent Studio** at FTX'26 — "world's first AI-native Agent Studio for payments," **built on Claude Agent SDK**. Production agents: Abandoned Cart Conversion (voice, Nugget by Zomato/SuperU), Dispute Responder, **Subscription Recovery (voice, ElevenLabs)**, Cashflow Forecaster. Plus Agentic Experience Platform (Claude Code / Replit / Emergent integrations, "under 10 minutes"). |
| Mar 23, 2026 | Sarvam partnership — voice-first conversational commerce. |
| Apr 6, 2026 | Payments inside OpenAI Codex; app in ChatGPT. |
| May 27, 2026 | **Payment Command Line Interface** — orders, payments, refunds, links, settlements, subscriptions, disputes, Route, Smart Collect from the terminal; install: `curl -fsSL https://razorpay.com/cli/latest/install.sh \| bash`; auth: `razorpay configure --key-id rzp_test_... --key-secret ...`; JSON/YAML/TOML output; positioned for Claude Code, Cursor, Codex, "any terminal-native agent"; marketing line: "No browser. No dashboard. No human in the loop." |
| Jun 1, 2026 | RazorpayX AI banking agents (conversational payouts). |

Pitch alignment: our demo should (a) use test keys + webhooks exactly as above, (b) expose the growth loop (failed-payment/abandoned-cart recovery → recovered revenue) mirroring their Subscription Recovery / Abandoned Cart agents, and (c) optionally let the agent act through the CLI/MCP server to echo "Dashboards → Humans, APIs → Developers, CLI & MCP → Agents."

---

## Failure-trigger matrix (deterministic, test mode)

| Scenario we want on stage | Deterministic trigger |
|---|---|
| Card payment success | `4111 1111 1111 1111`, any CVV/expiry, click **Success** (mock bank page) |
| Card auth failure | Same card, click **Failure** on mock page |
| Card OTP failure | Enter OTP <4 digits, or 5-digit OTP starting with `1` |
| Specific decline codes (insufficient funds etc.) | Error-scenario test cards (BAD_REQUEST_ERROR / GATEWAY_ERROR sections) — grab exact numbers from test-cards page during build (**numbers UNVERIFIED**) |
| UPI success | VPA `success@razorpay` |
| UPI failure (fires `payment.failed`) | VPA `failure@razorpay` |
| Netbanking/wallet failure | Pick any bank/wallet → click **Failure** |
| International card address challenge | `5105 1051 0510 5100` (+ the NY address above) |
| Subscription charge failure cascade | Dashboard → subscription → **Charge this now** → choose Failure → `pending`; repeat 4× → `halted` |
| Subscription recovered | After intervention, successful Charge-this-now → `active` + `subscription.charged` |
| Webhook retry behavior | Respond non-2xx (or sleep >5 s) → observe exponential-backoff redeliveries; same `x-razorpay-event-id` |
| Duplicate-order protection | Reuse a `receipt` → 400 duplicate-request error |
| Double-pay protection | Attempt second payment on `paid` order → blocked |
| Rate-limit handling | Burst calls until 429, backoff-retry |

Not deterministically triggerable in test mode: UPI cancel (cancels become successes), real bank timeouts, live issuer declines.

---

## Open questions / things that DON'T work in test mode

1. **No programmatic payment completion API** — biggest architectural fact. Plan for a tiny Playwright service ("simulated buyer") as part of the stack; frame it as the buyer-agent's wallet UI. Legacy exception: `/v1/bharatqr/pay/test` (BharatQR only).
2. **UPI payment links don't exist in test mode**; UPI cancellation turns into success.
3. **New QR Codes API unusable in test** (codes vanish after 2 s, unscannable).
4. **30-link cap** on test-mode payment links — recycle/cancel links between demo runs.
5. **Subscription debits expire 3 days after token creation** — re-auth before demos; update-subscription untestable post-charges.
6. **e-mandate / UPI Autopay test flow undocumented** — skip for the 1-week scope.
7. **ngrok.io blacklisted** for webhook endpoints (per docs); cloudflared status unknown (**UNVERIFIED**) — have a fallback public HTTPS endpoint on ports 80/443.
8. Error-scenario/RuPay/EMI/subscriptions card-number tables didn't render in fetches — pull them live from the test-cards page (**UNVERIFIED**).
9. Changelog tables are JS-collapsed; per-entry changelog details not extracted (newsroom used instead).
10. `payment_capture` auto-capture flag on order/checkout params appeared only in older doc versions — current Create Order page doesn't list it (**UNVERIFIED**; default behavior today captures automatically on success).
11. Numeric API rate limit unpublished.

## Sources (accessed 2026-08-22)

- https://razorpay.com/docs/api/sandbox-setup/ (same base URL, test keys)
- https://razorpay.com/docs/api/orders/create/ (create order fields/errors, duplicate-receipt idempotency)
- https://razorpay.com/docs/api/orders/ (orders overview)
- https://razorpay.com/docs/api/payments/ + /docs/api/payments/entity/ (payments constraints, entity schema, statuses, error_* fields)
- https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/ + .../integration-steps/ (checkout.js, options object, order create curl)
- https://razorpay.com/docs/payments/payments/test-card-details/ (test cards, mock Success/Failure page, intl MC address)
- https://razorpay.com/docs/payments/payments/test-upi-details/ (success@razorpay / failure@razorpay; cancellation caveat)
- https://d6xcmfyh68wv8.cloudfront.net/docs/payments/payment-gateway/web-integration/standard/test-integration/ (legacy-rendered card table, netbanking/wallet mocks)
- https://razorpay.com/docs/developer-tools/integrations/standard-checkout/ (OTP simulation rules, rzp_test_ keys)
- https://razorpay.com/docs/webhooks/ (config, ports 80/443, event examples, test-mode webhooks)
- https://razorpay.com/docs/webhooks/validate-test/ (HMAC-SHA256 X-Razorpay-Signature, raw-body rule, retries/dedupe/out-of-order, blacklisted domains incl. ngrok.io, zrok suggestion, OTP 754081 tip)
- https://razorpay.com/docs/webhooks/payloads/payments/ (events list + payment.captured payload sample)
- https://razorpay.com/docs/api/payment-links/ + /docs/api/payments/payment-links/create-standard/ (plink fields/statuses; 30-link cap; UPI links not in test)
- https://razorpay.com/docs/payments/payment-links/create/ (test-pay-a-link instructions)
- https://razorpay.com/docs/payments/subscriptions/test/ (auth payment, Charge this now, pending/halted, 3-day tokens)
- https://razorpay.com/docs/api/payments/subscriptions/create-subscription/ + /docs/api/payments/subscriptions/create-plan/ (plans/subscriptions fields)
- https://razorpay.com/docs/payments/route/integration-guide/ (test transfers; live-transfer restriction)
- https://razorpay.com/docs/api/understand/ + /docs/errors/common/ (429 rate limiting, backoff guidance)
- https://razorpay.com/docs/errors/ (error object structure)
- https://razorpay.com/docs/payments/qr-codes/create/ + /docs/payments/qr-codes/faqs/ + /docs/api/qr-codes/close/ (test QR unusable)
- https://razorpay.com/docs/payments/payment-methods/bharatqr/testing/ (/v1/bharatqr/pay/test)
- https://github.com/razorpay/razorpay-python (+ issue #113 headless question), https://github.com/razorpay/razorpay-node, registry.npmjs.org/razorpay/latest (2.9.8), pypi.org/pypi/razorpay/json (2.0.1)
- Newsroom: /razorpay-becomes-indias-first-payment-gateway-to-launch-mcp-server..., /razorpay-launches-the-worlds-first-ai-native-agent-studio-for-payments-at-ftx26-powered-by-anthropics-claude/, /razorpay-brings-payment-command-line-interface-to-india-built-for-developers-and-the-ai-agent-era/
- https://razorpay.com/cli/, https://razorpay.com/docs/cli/, https://razorpay.com/docs/mcp-server/ + tools-reference, https://github.com/razorpay/razorpay-mcp-server
