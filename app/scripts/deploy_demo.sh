#!/usr/bin/env bash
# Deploy the /demo presentation layer to the merchant VM.
#
# The VM is NOT a git clone — it is a manually synced copy at
# /home/azureuser/bazaar/app — so this script is the deploy path.
#
# Scope discipline: it only ever touches demo PRESENTATION files.
#   - app/.env is never written (key rotation owns that: rotate_keys.sh)
#   - bazaar.db is never written (it is the evidence)
#   - only bazaar-town.service restarts; the merchant API stays up
#
# Every file it overwrites is backed up on the VM first, timestamped, so
# a bad deploy is one `cp` away from reverted.

set -euo pipefail

HOST="${DEPLOY_HOST:-azureuser@20.193.254.214}"
KEY="${DEPLOY_KEY:-C:/Users/hp/Desktop/server-key-krishna.pem}"
REMOTE_APP="${DEPLOY_APP:-/home/azureuser/bazaar/app}"
SERVICE="${DEPLOY_SERVICE:-bazaar-town.service}"
LOCAL_APP="$(cd "$(dirname "$0")/.." && pwd)"

# Presentation-only. Add to this list deliberately; nothing here may be a
# secrets file, a database, or anything the experiment depends on.
FILES=(
  "bazaar_live.html"
  "scripts/live_show.py"
  "scripts/replay_source.py"
  "scripts/demo_probe.py"
  "scripts/demo_smoke.py"
  "scripts/show_order.py"
  "exp/checkout.py"
  "exp/risk_curve.py"
  "bazaar/envelope.py"
  "artifacts/replay_fixture.json"
  "artifacts/risk_venue.json"
)

SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=20)

say() { printf '\033[1;36m== %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$KEY" ] || die "ssh key not found: $KEY"

say "checking reachability"
ssh "${SSH_OPTS[@]}" "$HOST" \
  "test -d '$REMOTE_APP' || exit 9" \
  || die "cannot reach $HOST or $REMOTE_APP missing"

TS="$(ssh "${SSH_OPTS[@]}" "$HOST" 'date +%Y%m%d-%H%M%S')"
say "backing up on VM as $TS"
ssh "${SSH_OPTS[@]}" "$HOST" "
  set -e
  B='$REMOTE_APP/.deploybak-$TS'
  mkdir -p \"\$B\"
  $(for f in "${FILES[@]}"; do
      printf '  [ -f "%s/%s" ] && cp -p "%s/%s" "$B/%s" || true\n' \
        "$REMOTE_APP" "$f" "$REMOTE_APP" "$f" "$(basename "$f")"
    done)
  echo \"  backup -> \$B\"
"

say "copying ${#FILES[@]} files"
for f in "${FILES[@]}"; do
  [ -f "$LOCAL_APP/$f" ] || die "missing local file: $LOCAL_APP/$f"
  scp "${SSH_OPTS[@]}" -q "$LOCAL_APP/$f" "$HOST:$REMOTE_APP/$f" \
    || die "scp failed for $f"
  printf '  %-34s ok\n' "$f"
done

say "compiling on VM (syntax gate)"
ssh "${SSH_OPTS[@]}" "$HOST" "
  cd '$REMOTE_APP'
  ./.venv/bin/python -m compileall -q scripts/live_show.py \
    scripts/replay_source.py exp/checkout.py bazaar/envelope.py \
    >/dev/null 2>&1 \
    || /home/azureuser/.local/bin/uv run python -m compileall -q \
         scripts/live_show.py scripts/replay_source.py exp/checkout.py \
         bazaar/envelope.py
  /home/azureuser/.local/bin/uv run python scripts/replay_source.py 2>&1 | tail -4
" || die "replay self-check failed on VM — deploy halted, service untouched"

say "restarting $SERVICE"
sudo_restart="sudo systemctl restart '$SERVICE'"
ssh "${SSH_OPTS[@]}" "$HOST" "$sudo_restart" || die "restart failed"

sleep 5

say "verifying"
ssh "${SSH_OPTS[@]}" "$HOST" "
  set -e
  echo '--- service ---'
  sudo systemctl is-active '$SERVICE'
  echo '--- /api/state ---'
  curl -sS --noproxy '*' -m 10 http://127.0.0.1:8321/api/state
  echo
  echo '--- /api/replay (evidence counters) ---'
  curl -sS --noproxy '*' -m 10 http://127.0.0.1:8321/api/replay \
    | head -c 400
  echo
  echo '--- /api/risk (venue study) ---'
  curl -sS --noproxy '*' -m 10 http://127.0.0.1:8321/api/risk | head -c 300
  echo
  echo '--- /demo/ ---'
  curl -sS --noproxy '*' -m 10 -o /dev/null -w 'http %{http_code}\n' \
    http://127.0.0.1:8321/
  echo '--- /demo/risk (standalone page) ---'
  curl -sS --noproxy '*' -m 10 -o /dev/null -w 'http %{http_code}\n' \
    http://127.0.0.1:8321/risk
  echo '--- /api/envelope (live Reserve Pay enforcement) ---'
  curl -sS --noproxy '*' -m 20 -X POST http://127.0.0.1:8321/api/envelope \
    | head -c 300
  echo
  echo '--- /demo/envelope (standalone page) ---'
  curl -sS --noproxy '*' -m 10 -o /dev/null -w 'http %{http_code}\n' \
    http://127.0.0.1:8321/envelope
  echo '--- envelope demo store is separate from the merchant ledger ---'
  ls -1 $REMOTE_APP/.data/envelope_demo.db 2>/dev/null || echo '(not yet created)'
" || die "verification failed"

say "done"
cat <<EOF

Deployed at $TS. If anything looks wrong:

  ssh -i $KEY $HOST
  ls $REMOTE_APP/.deploybak-$TS
  sudo cp -p $REMOTE_APP/.deploybak-$TS/<file> $REMOTE_APP/<file>
  sudo systemctl restart $SERVICE
EOF
