# Zero-Budget Plan (₹0 total spend)

*2026-08-22. Constraint from builder: NO extra money. Research verified current (Aug 2026) free-tier options.*

---

## Verdict

| Need | Paid option (dropped) | FREE replacement | Headroom |
|---|---|---|---|
| Growth-agent + persona LLM | Anthropic credits ~$15–20 | **Google Gemini free tier** (AI Studio key, no card) — Flash class ~15 RPM, ~1,500 requests/day per project | Our whole experiment ≈ 2,400–3,200 requests → runs across ~2 days; demo needs <100 |
| Backup LLM | — | **Groq free tier** — `llama-3.1-8b-instant`: 30 RPM / 14.4K RPD / 500K tokens-per-day; `llama-3.3-70b`: 1,000 RPD but token cap binds (~100 calls/day) | Fine for personas & retries |
| Weak backup | — | OpenRouter `:free` models — only 50 req/day unless $10 lifetime credits | Emergency only |
| ~~GitHub Models~~ | — | RETIRED July 30, 2026 — do not use | — |
| Webhook hostname | Domain ~₹600/yr | **DuckDNS free subdomain** + Caddy DNS/HTTP challenge; if VM ports blocked → our reconciliation-sweep polling path already replaces webhooks functionally | Demo-honest either way |
| Payments sandbox | — | Razorpay **test mode is free** | Unlimited-ish |
| Browser payer | — | Playwright + headless Chromium = open source, runs on dev laptop | — |

## Architecture rule this forces (good engineering anyway)

One thin adapter: `app/llm.py` exposing `chat(messages, tools)` behind env config:

```
LLM_PROVIDER = mock | gemini | groq
```

- `mock` = deterministic scripted responses → **Days 1–3 need ZERO keys** (merchant core, policy gates, ledger, ACP/x402/MCP surfaces, Control Tower all buildable + testable).
- Provider swap = one env var. Tool-calling normalized internally (Gemini function calling ↔ Groq tool use).
- Rate-limit discipline baked in: global token-bucket limiter (≤12 RPM), exponential backoff w/ jitter on 429, per-session turn cap ≤8, response caching by prompt hash for replays.

## Measurement impact

- n=150×2 paired sessions still feasible: ~8 turns/session ⇒ ~2 days of free-tier quota, or trim personas to 100×2 in one long day. PREREGISTERED.md states final n honestly.
- Cost line in metrics becomes ₹0 — actually a *pitch point*: "the entire growth stack runs inside free tiers; unit economics at scale = inference cost only."

## Key-creation timing (agreed with builder)

- Builder creates the **free Gemini key** whenever convenient; hard deadline = **start of Day 4** (agent-wiring day), because the growth agent's prompt/persona tuning needs live iteration — leaving it to project-end would leave the most-judged component untuned.
- Everything before Day 4 proceeds keyless on `mock`.

## Sources

- [Gemini rate limits (official)](https://ai.google.dev/gemini-api/docs/rate-limits); third-party summary [TinkerLLM](https://tinkerllm.com/blog/gemini-api-free-tier-limits-rate-quotas/) (Flash ≈15 RPM / ~1,500 RPD; Pro removed from free tier Apr 2026) — treat exact numbers as VERIFY-IN-AI-STUDIO at key creation.
- [OpenRouter limits](https://openrouter.ai/docs/api_reference/limits), [FAQ](https://openrouter.ai/docs/faq)
- [Groq rate limits](https://console.groq.com/docs/rate-limits)
- [GitHub Models retirement](https://github.blog/changelog/2026-07-30-github-models-is-now-retired/)
