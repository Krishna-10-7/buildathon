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

Steps 2–5 below are automated. From the repo root:

```bash
bash app/scripts/rotate_keys.sh --status     # confirm all 3 hosts agree today
bash app/scripts/rotate_keys.sh --selftest   # prove the file surgery is safe
bash app/scripts/rotate_keys.sh              # interactive; hides what you type
```

It pre-flights (mandate-safety, host reachability, and — before writing a
single byte — proves the new key pair against `api.razorpay.com`), backs up
every `.env`, restarts `bazaar.service` + `bazaar-town.service` on VM1,
verifies `/healthz`, checks the webhook HMAC accepts the new secret **and
rejects the old one**, and rolls back automatically on any failure.

1. Generate all five replacements FIRST, keep them out of chat entirely
   (paste directly into files via editor).
2. `rotate_keys.sh` covers Razorpay Key Id + Secret + Webhook Secret across
   laptop, VM1 and VM2. LLM keys are opt-in flags:
   `bash app/scripts/rotate_keys.sh --gemini <k> --nvidia <k> --openrouter <k>`
3. Restart + verify is inside the script (VM1 services, `/healthz`, webhook
   HMAC, `MANDATE_SECRET` byte-identical before/after).
4. VM2 hermes env (`~/.hermes/.env`, item 5) is updated automatically when
   `--openrouter` is supplied.
5. Manually: persona payment smoke (ONE session, not the fleet),
   `audit/recent?limit=1` chain_ok.

**`MANDATE_SECRET` is deliberately NOT rotated.** `mandates.py` derives the
signing key as `MANDATE_SECRET or f"{RZP_KEY_SECRET}:mandates-v1"` — rotating
it would silently void every signed mandate in the database. The script
asserts it is unchanged on every host and aborts if it is empty anywhere.

**After running it:** update the webhook secret in Razorpay Dashboard →
Settings → Webhooks to match. Inbound events will 400 until you do.

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
