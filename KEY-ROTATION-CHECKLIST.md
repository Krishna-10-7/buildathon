# KEY ROTATION CHECKLIST — HARD GATE before anything goes public

Every credential below has appeared in plaintext chat during development.
None may survive into any public artifact (repo history, video frames,
screenshots, submission attachments).

## Rotate (generate NEW value, then replace everywhere)

| # | Credential | Where it lives | How to rotate |
|---|---|---|---|
| 1 | Razorpay Key Id + Secret | `.env` on laptop, VM1 (`myserver`), VM2 (`myserver2`); dashboard | Dashboard → Settings → API Keys → Regenerate. Test mode only. |
| 2 | Razorpay Webhook Secret | same three `.env`s | Dashboard → Settings → Webhooks → edit endpoint → new secret |
| 3 | Gemini API key | same three `.env`s | Google AI Studio → delete old key → create new |
| 4 | NVIDIA NIM key | same three `.env`s | build.nvidia.com → revoke + issue |
| 5 | OpenRouter key | VM2 `~/.hermes/.env` only | openrouter.ai/settings/keys → create new, delete old |

## Order of operations

1. Generate all five replacements FIRST, keep them out of chat entirely
   (paste directly into files via editor).
2. Update laptop `.env` → scp to VM1 + VM2 `~/bazaar/app/.env`.
3. Restart merchant service on VM1; verify `healthz` ok + one webhook test.
4. Update VM2 hermes env separately (item 5).
5. Verify: persona payment smoke (ONE session, not the fleet),
   `audit/recent?limit=1` chain_ok, webhook signature still validating.

## Scrub check before publishing

- [ ] `git log -p | grep -iE "rzp_test|nvapi-|sk-or-v1|AQ\.Ab"` over full
      history — if any hit: history rewrite BEFORE push (filter-repo), or
      fresh repo without history.
- [ ] `.env` confirmed in `.gitignore`, absent from any tarball/zip submitted.
- [ ] Demo video frames: no dashboard key fields visible (crop/blur).
- [ ] Old keys REVOKED in each provider dashboard after replacement verified.

## Never-do

- Never paste a replacement key into chat "for convenience" — that restarts
  this whole list.
