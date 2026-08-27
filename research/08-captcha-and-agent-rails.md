# Research 08 — Test-mode checkout challenges & the sanctioned paths around them

Written 2026-08-23 after ~10 automated checkout attempts across 2 machines,
2 IPs, 4 configurations all drew hCaptcha challenges. Purpose: stop burning
trials blind; know WHY it triggers, what the SANCTIONED doors are, and where
the line is that we will not cross.

---

## 1. Why we're challenged (diagnosis)

hCaptcha/Razorpay risk scoring combines signals, roughly in order of weight:

| Signal | Our situation | Verdict |
|---|---|---|
| **IP reputation** | Day-1 success ran from the LAPTOP (residential ISP). Every challenged attempt from VM2 = **Azure datacenter IP** — challenged "almost by default" per hCaptcha ecosystem guidance; humans never browse from hosting ASNs | **Primary cause for VM runs** |
| **Velocity** | ~15 automated checkouts on one `rzp_test_` key in 2 days | Per-key/per-session risk compounds; each retry scores worse than the last |
| **Automation markers** | Headless Chromium / Chrome channel headless; fresh profiles | Compounds, not decisive alone |
| **Cold sessions** | New profiles, no cookie history | Compounds |

Supporting evidence: public 2026 E2E suites (e.g. InstaNode's Playwright
Razorpay tests) drive full TEST-mode flows — cards, OTP 1234, netbanking —
without documented captcha walls ⇒ automation per se isn't universally
blocked; OUR key+IP+velocity combination is. That means this is fixable
context, not a wall.

## 2. The line (and why crossing it would also be stupid)

We will NOT: captcha-solver services, `navigator.webdriver` stripping /
AutomationControlled flags, header or fingerprint forgery, hCaptcha
accessibility-cookie reuse. These exist to defeat bot detection; using them
turns a legitimate merchant into what the control thinks it sees. Practical
corollary: ToS violations + solver services mean sharing checkout context
with third parties, and getting the test keys flagged/banned mid-buildathon
would be unrecoverable. The scrolltest automation guide reaches the same
conclusion from pure pragmatism: don't solve captchas in automation.

## 3. Sanctioned options, ranked

### Option A — Fresh test keys + residential IP + low frequency (PRIMARY)
- Generate NEW `rzp_test_` keys in Dashboard (instant, free, our own account):
  resets whatever per-key velocity state exists.
- Run the closing payment from the **laptop** (residential IP) — exactly the
  context that succeeded Day 1.
- One session, headed real Chrome, persistent profile, human-paced pauses.
- ⚠️ BEFORE rotating keys: set `MANDATE_SECRET` explicitly in `.env` (VM1+VM2).
  It currently falls back to a hash of `rzp_key_secret` — rotating keys
  without pinning the secret invalidates every existing mandate signature.
- Expected outcome: silent pass (Day-1 precedent). If still challenged: a
  human is right there and solves it once — the honest fallback we've
  already declared: *agent drives 100%, human proves humanity once.*

### Option B — File a support ticket (parallel, zero cost)
Ask Razorpay: test-mode checkout challenges automated integration testing;
is there an allowlist or recommended pattern? Legitimate merchants ask this.
Even a slow reply documents diligence and might reveal a sanctioned flag.

### Option C — Reframe: use the agent-native rail (STRATEGIC WIN)
Research found Razorpay's own 2026 answer to "agents vs checkout":
- **UPI Reserve Pay (live)**: consent-based pre-authorized payments within
  approved spending limits — i.e., EXACTLY our `mandates.py` envelope
  semantics (budget cap, category scope, revoke), which we built from AP2 +
  NPCI UAP research before knowing this shipped.
- **Chat2Checkout MCP** (`agent.razorpay.com/mcp`): official MCP store server.
- **Agent Studio** (FTX'26): agent builder on Claude Agent SDK.
Reserve Pay itself is gated (Typeform signup, no public sandbox), so we
cannot integrate it this week — but the ALIGNMENT is demo gold: *"Razorpay's
risk engine challenging browser bots is correct behavior; the sponsor's own
answer is mandate rails — which our merchant implements natively."* We are
not fighting their philosophy; we independently built its merchant half.

### Option D — What does NOT work (verified so we never retry blindly)
- Pure-API completion: none exists; CLI (`razorpay payments capture`) only
  captures ALREADY-authorized payments; dashboard has no simulate-payment.
- Different instrument in same browser session (UPI tab absent in current
  checkout build; card path blocked domestic-only).
- Cooldown alone without context change (same IP/key/profile class).

## 4. Decision tree for tomorrow (~12:30 IST, after Gemini quota reset)

1. Pin `MANDATE_SECRET` in `.env` on VM1+VM2 → restart bazaar service.
2. Rotate to fresh test keys (dashboard) → update `.env`s → restart.
3. Laptop, headed Chrome, persistent profile, ONE persona payment, slow.
   - Passes silently → payment closed; proceed to measurement planning.
   - Challenge shown → solve it by hand once (narrated honestly), payment
     closed anyway.
4. Measurement-day implication: the fleet runs from datacenter IPs, so
   expect nonzero challenge rates there regardless. Preregistration already
   declares the rule: materially asymmetric challenge rates between arms
   void the run. Mitigation inside the rules: long jittered pauses,
   alternation, report exclusion counts per arm honestly. If excluded
   sessions dominate → report the void per preregistration rather than
   massaging it.

## 5. Sources

- Razorpay CLI docs/product (capture-only, no browser-free completion):
  razorpay.com/docs/cli/, razorpay.com/cli/, github.com/razorpay/razorpay-cli
- Standard Checkout test mode (OTP rules, mock banks, capture semantics):
  razorpay.com/docs/developer-tools/integrations/standard-checkout/
- Agentic stack: razorpay.com/agentic-payments/,
  razorpay.com/newsroom/razorpay-npci-launch-agentic-payments-on-claude…,
  razorpay.com/newsroom/razorpay-launches-the-worlds-first-ai-native-agent-studio…,
  agent.razorpay.com/
- Challenge-trigger diagnosis: hcaptcha.com/bot-detection,
  nonecap.com/learn/why-am-i-getting-hcaptcha/,
  mrscraper.com/blog/how-to-avoid-triggering-captcha-challenges
- Automation pragmatics: scrolltest.com Playwright iframe guide
  ("don't solve captchas in automation"), InstaNode E2E commits (test-mode
  flows pass without walls when not flagged).
