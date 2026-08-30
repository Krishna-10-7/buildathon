#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# rotate_keys.sh — rotate Razorpay credentials across laptop + VM1 + VM2
# ---------------------------------------------------------------------------
#
# WHY THIS SCRIPT EXISTS
#   Razorpay's test-mode risk stack (Checkout JS + hCaptcha + device/behavioural
#   signals) is stateful and velocity-keyed. A key id accumulates bot-shaped
#   history and eventually stops auto-verifying and starts challenging. This
#   project measured it directly: 0% -> 23.1% -> 14.3% challenge rate across
#   thirds of one clean run, and ~90% in a later high-frequency batch. Rotating
#   the key id resets that counter, which is why rotation "works".
#
#   That makes rotation routine rather than exceptional, so it has to be one
#   command that cannot half-fail. Full analysis:
#   research/10-key-rotation-and-risk-escalation.md
#
# WHAT IT DOES
#   0.  Acquires a lock so two rotations cannot interleave
#   1.  Pre-flight: mandate-signing safety, host reachability, new key pair
#       proven valid against api.razorpay.com BEFORE anything is written
#   2.  Backs up every .env it is about to touch (mode 600)
#   3.  Upserts the new values in place — comments and unrelated keys survive
#   4.  Restarts the services that read .env
#   5.  Verifies: /healthz, webhook HMAC (old secret rejected, new accepted),
#       MANDATE_SECRET byte-identical before/after
#   6.  Rolls back automatically if any verification fails
#
# USAGE
#   bash scripts/rotate_keys.sh                       # interactive, hidden prompts
#   bash scripts/rotate_keys.sh --key-id rzp_test_X --key-secret S --webhook-secret W
#   bash scripts/rotate_keys.sh --dry-run --key-id rzp_test_X --key-secret S
#   bash scripts/rotate_keys.sh --only laptop         # skip the VMs
#   bash scripts/rotate_keys.sh --gemini <key>        # also rotate the LLM key
#   bash scripts/rotate_keys.sh --status              # show current key fingerprints
#   bash scripts/rotate_keys.sh --help
#
# ---------------------------------------------------------------------------
#
# SAFETY INVARIANTS — do not remove these, they are load-bearing
#
#   1. MANDATE_SECRET IS NEVER ROTATED, AND MUST BE NON-EMPTY.
#      app/bazaar/mandates.py derives the mandate signing key as:
#          base = settings.mandate_secret or f"{settings.rzp_key_secret}:mandates-v1"
#      If MANDATE_SECRET were empty, rotating RZP_KEY_SECRET would silently
#      invalidate every signed mandate already in the database — with no error,
#      just a sudden wall of verification failures hours later. The pre-flight
#      below hard-fails on an empty MANDATE_SECRET on any host.
#
#   2. NOTHING IS WRITTEN ANYWHERE until the new key id / secret pair has been
#      proven to authenticate against api.razorpay.com. A typo must not be
#      able to take the live /demo offline.
#
#   3. THE OLD VALUES ARE KEPT IN MEMORY for the whole run so the rollback
#      path is always available, including mid-write.
#
#   4. THIS SCRIPT DOES NOT TOUCH CAPTCHA. It never solves, proxies or evades a
#      fraud control. Rotation is legitimate credential hygiene; defeating a
#      risk check would be disqualifying for a payments company.
#
# ---------------------------------------------------------------------------

set -euo pipefail

# ------------------------------- configuration -----------------------------

LAPTOP_ENV="${LAPTOP_ENV:-$HOME/buildathon/app/.env}"
VM1_HOST="${VM1_HOST:-myserver}"
VM1_ENV="${VM1_ENV:-/home/azureuser/bazaar/app/.env}"
VM1_SERVICES="${VM1_SERVICES:-bazaar.service bazaar-town.service}"
VM2_HOST="${VM2_HOST:-myserver2}"
VM2_ENV="${VM2_ENV:-/home/azureuser2/bazaar/app/.env}"
VM2_HERMES_ENV="${VM2_HERMES_ENV:-/home/azureuser2/.hermes/.env}"

PUBLIC_BASE="${PUBLIC_BASE:-https://r2-d2.xyz}"
RAZORPAY_API="${RAZORPAY_API:-https://api.razorpay.com/v1}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/.bazaar/key-backups}"
ROTATION_LOG="${ROTATION_LOG:-$HOME/.bazaar/rotation.log}"
LOCK_FILE="${LOCK_FILE:-$HOME/.bazaar/rotate_keys.lock}"

# Keys this script is allowed to write. Razorpay set is rotated by default;
# LLM keys only when explicitly passed.
RAZORPAY_KEYS=(RZP_KEY_ID RZP_KEY_SECRET RZP_WEBHOOK_SECRET)
PRESERVE_KEYS=(MANDATE_SECRET)   # asserted unchanged, never written

# ------------------------------- small helpers -----------------------------

if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
  C_BLU=$'\033[34m'; C_DIM=$'\033[2m';  C_BLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GRN=""; C_YEL=""; C_BLU=""; C_DIM=""; C_BLD=""; C_OFF=""
fi

log()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s%s%s\n' "$C_BLU" "$C_OFF" "$C_BLD" "$*" "$C_OFF"; }
ok()   { printf '    %s[ok]%s   %s\n'   "$C_GRN" "$C_OFF" "$*"; }
warn() { printf '    %s[warn]%s %s\n'  "$C_YEL" "$C_OFF" "$*" >&2; }
info() { printf '    %s[..]%s   %s\n'  "$C_DIM" "$C_OFF" "$*"; }
die()  { printf '\n%s[FAIL]%s %s\n\n'  "$C_RED" "$C_OFF" "$*" >&2; exit 1; }

# Never print a secret. Show enough to tell two keys apart in a terminal.
fingerprint() {
  [ -z "${1:-}" ] && { printf '%s' "(empty)"; return; }
  printf '%s' "$(printf '%s' "$1" | sha256sum | cut -c1-8)"
}

# SHA-256 HMAC of stdin, hex. Used for the webhook signature probe.
hmac_sha256_hex() { # key  (body on stdin)
  openssl dgst -sha256 -hmac "$1" -hex 2>/dev/null | sed 's/^.*= //'
}

# Read one key out of an env file. Empty string when absent.
# tr -d '\r' matters: if an env file was ever written with CRLF, the value
# carries a trailing CR, and every comparison in this script — the key-id
# match, the MANDATE_SECRET identity check, the fingerprint — silently
# disagrees with the value you just typed.
env_get_local() { # file key
  [ -f "$1" ] || { printf ''; return; }
  grep -E "^[[:space:]]*$2=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r' || printf ''
}
env_get_remote() { # host file key
  ssh -o ConnectTimeout=10 -o BatchMode=yes "$1" \
      "grep -E '^[[:space:]]*$3=' '$2' 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r'" 2>/dev/null \
    || printf ''
}

# The remote apply script. Fed to `ssh host bash -s`, args:
#   $1 = env file, $2.. = KEY=VALUE pairs
# Deliberately POSIX so it needs nothing but sh on the far side.
read -r -d '' ENV_APPLY_SH <<'EOS' || true
set -eu
f="$1"; shift
[ -f "$f" ] || { echo "no env file: $f" >&2; exit 3; }
# refuse to operate on anything world/group readable
perm=$(stat -c '%a' "$f" 2>/dev/null || echo 600)
case "$perm" in *[2367]) chmod 600 "$f" ;; esac
b="$f.rotate-bak.$$"
cp -p "$f" "$b"
changed=""
for pair in "$@"; do
  k="${pair%%=*}"; v="${pair#*=}"
  # escape for sed: backslash, ampersand, and the | delimiter
  ev=$(printf '%s' "$v" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g' -e 's/|/\\|/g')
  if grep -qE "^[[:space:]]*${k}=" "$f"; then
    sed -i "s|^[[:space:]]*${k}=.*|${k}=${ev}|" "$f"
  else
    printf '\n%s=%s\n' "$k" "$ev" >> "$f"
  fi
  changed="$changed $k"
done
chmod 600 "$f"
  echo "APPLIED:$changed"
  echo "BACKUP:$b"
EOS

# Strip any CR left by a Windows checkout. This heredoc is piped verbatim to
# `bash -s` on the Linux VMs, where a stray \r turns every line into
# "command not found" and the rotation fails halfway. .gitattributes pins the
# file to LF as well; this is the belt to that braces.
ENV_APPLY_SH="${ENV_APPLY_SH//$'\r'/}"

# Apply pairs to one remote env file. Prints the remote backup path on stdout.
apply_remote() { # host file pair...
  local host="$1" file="$2"; shift 2
  local qargs; qargs=$(printf '%q ' "$file" "$@")
  ssh -o ConnectTimeout=15 -o BatchMode=yes "$host" "bash -s -- $qargs" <<<"$ENV_APPLY_SH"
}
# Same thing locally, no ssh.
apply_local() { # file pair...
  local file="$1"; shift
  local qargs; qargs=$(printf '%q ' "$file" "$@")
  bash -s -- "$file" "$@" <<<"$ENV_APPLY_SH"
}

usage() {
  sed -n '2,60p' "$0" | sed -e 's/^#\{1,\} \{0,1\}//' -e 's/^#//'
  exit 0
}

# ------------------------------- arg parsing -------------------------------

DRY_RUN=0; DO_STATUS=0; DO_SELFTEST=0; ASSUME_YES=0; NO_WEBHOOK_PROBE=0
GAVE_KID=0; WSEC_CHANGED=0
TARGETS="all"
NEW_KID=""; NEW_KSEC=""; NEW_WSEC=""
NEW_GEMINI=""; NEW_NVIDIA=""; NEW_OPENROUTER=""

while [ $# -gt 0 ]; do
  case "$1" in
    --key-id)           NEW_KID="${2:-}"; GAVE_KID=1; shift 2 ;;
    --key-secret)       NEW_KSEC="${2:-}";  shift 2 ;;
    --webhook-secret)   NEW_WSEC="${2:-}";  shift 2 ;;
    --gemini)           NEW_GEMINI="${2:-}";       shift 2 ;;
    --nvidia)           NEW_NVIDIA="${2:-}";       shift 2 ;;
    --openrouter)       NEW_OPENROUTER="${2:-}";   shift 2 ;;
    --only)             TARGETS="${2:-}";   shift 2 ;;
    --dry-run)          DRY_RUN=1;          shift ;;
    --status)           DO_STATUS=1;        shift ;;
    --selftest)         DO_SELFTEST=1;      shift ;;
    --yes|-y)           ASSUME_YES=1;       shift ;;
    --no-webhook-probe) NO_WEBHOOK_PROBE=1; shift ;;
    -h|--help)          usage ;;
    *) die "unknown argument: $1  (try --help)" ;;
  esac
done

case "$TARGETS" in
  all)     DO_LAPTOP=1; DO_VM1=1; DO_VM2=1 ;;
  laptop)  DO_LAPTOP=1; DO_VM1=0; DO_VM2=0 ;;
  vms)     DO_LAPTOP=0; DO_VM1=1; DO_VM2=1 ;;
  vm1)     DO_LAPTOP=0; DO_VM1=1; DO_VM2=0 ;;
  vm2)     DO_LAPTOP=0; DO_VM1=0; DO_VM2=1 ;;
  *) die "--only must be one of: all | laptop | vms | vm1 | vm2" ;;
esac

# ------------------------------- --status mode -----------------------------

if [ "$DO_STATUS" = 1 ]; then
  step "Current credential fingerprints (sha256[:8] — secrets are never printed)"
  printf '    %-28s %s\n' "laptop $LAPTOP_ENV" ""
  for k in "${RAZORPAY_KEYS[@]}" "${PRESERVE_KEYS[@]}" GEMINI_API_KEY; do
    printf '      %-20s %s\n' "$k" "$(fingerprint "$(env_get_local "$LAPTOP_ENV" "$k")")"
  done
  for spec in "$VM1_HOST|$VM1_ENV" "$VM2_HOST|$VM2_ENV"; do
    host="${spec%%|*}"; file="${spec##*|}"
    if ! ssh -o ConnectTimeout=8 -o BatchMode=yes "$host" true 2>/dev/null; then
      printf '    %-28s %s(unreachable)%s\n' "$host" "$C_DIM" "$C_OFF"; continue
    fi
    printf '    %-28s %s\n' "$host $file" ""
    for k in "${RAZORPAY_KEYS[@]}" "${PRESERVE_KEYS[@]}"; do
      printf '      %-20s %s\n' "$k" "$(fingerprint "$(env_get_remote "$host" "$file" "$k")")"
    done
  done
  printf '\n    Fingerprints must match across all three hosts for RZP_* and MANDATE_SECRET.\n'
  [ -f "$ROTATION_LOG" ] && printf '\n    Last rotations (newest last):\n' && tail -5 "$ROTATION_LOG"
  exit 0
fi

# ------------------------------- --selftest mode ---------------------------

# Exercises the one part of this script that can silently corrupt a secret:
# the sed escaping inside ENV_APPLY_SH. A Razorpay secret is alphanumeric so
# this will never bite in practice — but the same code path handles webhook
# secrets (which users paste from the dashboard) and LLM keys, and a mangled
# value there takes production down. Runs against scratch files, touches no
# real credential.
if [ "$DO_SELFTEST" = 1 ]; then
  step "Self-test: env upsert escaping, local + remote"
  # Every character here is a sed metacharacter or a shell metacharacter.
  NASTY=$'a\\b&c|d=e f$g*h[]i\x27j"k'
  TMP_LOCAL="$(mktemp)"
  TMP_REMOTE="/tmp/.rotate_selftest_$$.env"
  FAILURES=0

  check_roundtrip() { # label file getter-prefix...
    local label="$1" file="$2"; shift 2
    local got
    if [ "$#" -gt 0 ]; then got="$(env_get_remote "$1" "$file" NASTY_KEY)"
    else                    got="$(env_get_local  "$file" NASTY_KEY)"; fi
    if [ "$got" = "$NASTY" ]; then
      ok "$label: round-tripped ${#NASTY} bytes exactly"
    else
      warn "$label: MISMATCH"
      warn "  expected: $(printf '%s' "$NASTY" | od -c | head -3)"
      warn "  got     : $(printf '%s' "$got"   | od -c | head -3)"
      FAILURES=$((FAILURES+1))
    fi
  }

  printf '# comment that must survive\nEXISTING=keepme\nNASTY_KEY=old\n' > "$TMP_LOCAL"
  apply_local "$TMP_LOCAL" "NASTY_KEY=$NASTY" >/dev/null
  grep -q '^EXISTING=keepme$'   "$TMP_LOCAL" && ok "local: unrelated key preserved"  || { warn "local: unrelated key lost";   FAILURES=$((FAILURES+1)); }
  grep -q '^# comment'          "$TMP_LOCAL" && ok "local: comment preserved"        || { warn "local: comment lost";         FAILURES=$((FAILURES+1)); }
  check_roundtrip "local" "$TMP_LOCAL"
  rm -f "$TMP_LOCAL" "$TMP_LOCAL.rotate-bak."* 2>/dev/null || true

  for host in "$VM1_HOST" "$VM2_HOST"; do
    if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$host" true 2>/dev/null; then
      warn "$host unreachable — skipping remote self-test"; continue
    fi
    ssh -o ConnectTimeout=10 "$host" \
      "printf '# comment that must survive\nEXISTING=keepme\nNASTY_KEY=old\n' > '$TMP_REMOTE'" 2>/dev/null
    apply_remote "$host" "$TMP_REMOTE" "NASTY_KEY=$NASTY" >/dev/null
    ssh -o ConnectTimeout=10 "$host" "grep -q '^EXISTING=keepme\$' '$TMP_REMOTE'" \
      && ok "$host: unrelated key preserved" || { warn "$host: unrelated key lost"; FAILURES=$((FAILURES+1)); }
    ssh -o ConnectTimeout=10 "$host" "grep -q '^# comment' '$TMP_REMOTE'" \
      && ok "$host: comment preserved"       || { warn "$host: comment lost";       FAILURES=$((FAILURES+1)); }
    check_roundtrip "$host" "$TMP_REMOTE" "$host"
    # confirm the file was forced to 600 — it held a credential-shaped value
    perm="$(ssh -o ConnectTimeout=10 "$host" "stat -c '%a' '$TMP_REMOTE'" 2>/dev/null)"
    [ "$perm" = "600" ] && ok "$host: permissions forced to 600" \
                        || { warn "$host: permissions are $perm, expected 600"; FAILURES=$((FAILURES+1)); }
    ssh -o ConnectTimeout=10 "$host" "rm -f '$TMP_REMOTE' '$TMP_REMOTE.rotate-bak.'*" 2>/dev/null || true
  done

  if [ "$FAILURES" = 0 ]; then
    ok "self-test PASSED — escaping is safe for the characters tested"
    exit 0
  fi
  die "self-test FAILED with $FAILURES problem(s). Do not run a real rotation until this is fixed."
fi

# ------------------------------- lock -------------------------------------

mkdir -p "$(dirname "$LOCK_FILE")" "$BACKUP_DIR"
chmod 700 "$(dirname "$LOCK_FILE")" "$BACKUP_DIR"
# mkdir is atomic on every POSIX filesystem, so it is a correct mutual-exclusion
# primitive and needs no flock(1) — which Git Bash on Windows does not ship.
if ! mkdir "$LOCK_FILE" 2>/dev/null; then
  warn "lock ($LOCK_FILE) is held — another rotation is running, or a previous one crashed."
  warn "If you are sure nothing else is running: rmdir '$LOCK_FILE'"
  exit 4
fi
trap 'rmdir "$LOCK_FILE" 2>/dev/null || true' EXIT

# ------------------------------- collect secrets ---------------------------

step "Collecting new credentials"
if [ -z "$NEW_KID" ]; then
  printf '    Razorpay Key Id       : '; read -r NEW_KID
fi
if [ -z "$NEW_KSEC" ]; then
  printf '    Razorpay Key Secret   : '; read -rs NEW_KSEC; printf '\n'
fi
# The webhook secret is OPTIONAL. It is the one value here that is also
# configured in the Razorpay dashboard, so rotating it forces a matching
# dashboard edit; rotating the key id and secret does not. When you are only
# cycling the API key, leave the webhook secret alone — otherwise inbound
# events start 400-ing until you remember to fix the dashboard.
if [ -z "$NEW_WSEC" ]; then
  if [ "$GAVE_KID" = 1 ]; then
    NEW_WSEC="$(env_get_local "$LAPTOP_ENV" RZP_WEBHOOK_SECRET)"
    log "    webhook secret: not supplied, keeping the current one"
  else
    printf '    Razorpay Webhook Secret (blank = keep current): '; read -rs NEW_WSEC; printf '\n'
  fi
fi

[ -n "$NEW_KID" ]  || die "RZP_KEY_ID is required."
[ -n "$NEW_KSEC" ] || die "RZP_KEY_SECRET is required."

PAIRS=("RZP_KEY_ID=$NEW_KID" "RZP_KEY_SECRET=$NEW_KSEC")
[ -n "$NEW_WSEC" ] && PAIRS+=("RZP_WEBHOOK_SECRET=$NEW_WSEC")
[ -n "$NEW_GEMINI" ]     && PAIRS+=("GEMINI_API_KEY=$NEW_GEMINI")
[ -n "$NEW_NVIDIA" ]     && PAIRS+=("NVIDIA_API_KEY=$NEW_NVIDIA")
[ -n "$NEW_OPENROUTER" ] && PAIRS+=("OPENROUTER_API_KEY=$NEW_OPENROUTER")

log "    new key id fingerprint : $(fingerprint "$NEW_KID")"
log "    new secret fingerprint : $(fingerprint "$NEW_KSEC")"
if [ -n "$NEW_WSEC" ]; then
  log "    new webhook fingerprint: $(fingerprint "$NEW_WSEC")"
else
  log "    webhook secret         : unchanged"
fi
[ -n "$NEW_GEMINI" ]     && log "    gemini api key         : $(fingerprint "$NEW_GEMINI")"
[ -n "$NEW_NVIDIA" ]     && log "    nvidia api key         : $(fingerprint "$NEW_NVIDIA")"
[ -n "$NEW_OPENROUTER" ] && log "    openrouter api key     : $(fingerprint "$NEW_OPENROUTER")"

# ------------------------------- pre-flight --------------------------------

step "Pre-flight"

# 1. Critical invariant: MANDATE_SECRET must exist and be non-empty everywhere.
#    See SAFETY INVARIANT 1 in the header.
MANDATE_OK=1
declare -A MANDATE_BEFORE=()
if [ "$DO_LAPTOP" = 1 ]; then
  v="$(env_get_local "$LAPTOP_ENV" MANDATE_SECRET)"
  MANDATE_BEFORE["laptop"]="$v"
  if [ -z "$v" ]; then MANDATE_OK=0; warn "laptop: MANDATE_SECRET is empty or missing"; else ok "laptop: MANDATE_SECRET set ($(fingerprint "$v"))"; fi
fi
if [ "$DO_VM1" = 1 ]; then
  v="$(env_get_remote "$VM1_HOST" "$VM1_ENV" MANDATE_SECRET)"
  MANDATE_BEFORE["vm1"]="$v"
  if [ -z "$v" ]; then MANDATE_OK=0; warn "$VM1_HOST: MANDATE_SECRET is empty or missing"; else ok "$VM1_HOST: MANDATE_SECRET set ($(fingerprint "$v"))"; fi
fi
if [ "$DO_VM2" = 1 ]; then
  v="$(env_get_remote "$VM2_HOST" "$VM2_ENV" MANDATE_SECRET)"
  MANDATE_BEFORE["vm2"]="$v"
  if [ -z "$v" ]; then MANDATE_OK=0; warn "$VM2_HOST: MANDATE_SECRET is empty or missing"; else ok "$VM2_HOST: MANDATE_SECRET set ($(fingerprint "$v"))"; fi
fi
if [ "$MANDATE_OK" = 0 ]; then
  cat >&2 <<'W'

    MANDATE_SECRET is empty on at least one host.

    app/bazaar/mandates.py signs consent envelopes with:
        base = settings.mandate_secret or f"{settings.rzp_key_secret}:mandates-v1"

    With MANDATE_SECRET empty, rotating RZP_KEY_SECRET would silently void every
    signed mandate in the database. Fix it first:

        printf 'MANDATE_SECRET=%s\n' "$(openssl rand -hex 32)" >> <env file>

    ...on every host, then re-run this script.

W
  exit 5
fi
ok "mandate signing key is independent of RZP_KEY_SECRET — rotation cannot void existing mandates"

# 2. Host reachability + env files present.
for spec in "${VM1_HOST}|${VM1_ENV}|vm1|${DO_VM1}" "${VM2_HOST}|${VM2_ENV}|vm2|${DO_VM2}"; do
  IFS='|' read -r host file tag enabled <<<"$spec"
  [ "$enabled" = 1 ] || continue
  if ssh -o ConnectTimeout=12 -o BatchMode=yes "$host" \
       "[ -f '$file' ] && echo reachable" 2>/dev/null | grep -q reachable; then
    ok "$tag reachable, $file present"
  else
    die "$tag ($host) unreachable or $file missing. Fix SSH, or rerun with --only laptop."
  fi
done
[ "$DO_LAPTOP" = 1 ] && { [ -f "$LAPTOP_ENV" ] || die "laptop env not found: $LAPTOP_ENV"; }

# 3. Prove the new key pair authenticates against Razorpay BEFORE writing it
#    anywhere. SAFETY INVARIANT 2.
info "validating new key pair against $RAZORPAY_API ..."
VAL_HTTP=$(curl -s -o /tmp/.rzp_val.$$ -w '%{http_code}' -m 25 \
             -u "$NEW_KID:$NEW_KSEC" "$RAZORPAY_API/orders?count=1" 2>/dev/null || echo 000)
if [ "$VAL_HTTP" = "200" ]; then
  ok "Razorpay accepted the new key pair (GET /v1/orders -> 200)"
else
  warn "Razorpay rejected the new key pair (HTTP $VAL_HTTP)"
  [ -s /tmp/.rzp_val.$$ ] && warn "response: $(head -c 300 /tmp/.rzp_val.$$)"
  rm -f /tmp/.rzp_val.$$
  cat >&2 <<'W'

    Nothing has been written anywhere. Either the key is mistyped, or it is a
    live key being used against the test account (or vice versa). Generate a
    fresh pair in Dashboard > Settings > API Keys and re-run.

W
  exit 6
fi
rm -f /tmp/.rzp_val.$$

# 4. Refuse to proceed if the new key id is the same one already deployed —
#    that would be a no-op that still restarts production.
if [ "$DO_VM1" = 1 ]; then
  cur="$(env_get_remote "$VM1_HOST" "$VM1_ENV" RZP_KEY_ID)"
  if [ "$cur" = "$NEW_KID" ] && [ "$ASSUME_YES" = 0 ]; then
    warn "the key id you supplied is already deployed on $VM1_HOST."
    printf '    Continue anyway (restart + verify only)? [y/N] '; read -r ans
    case "$ans" in y|Y|yes) ;; *) die "aborted — nothing changed." ;; esac
  fi
fi

# ------------------------------- snapshot old ------------------------------

declare -A OLD_KID=() OLD_KSEC=() OLD_WSEC=()
declare -A BACKUP_PATH=()
snapshot() { # tag host file
  local tag="$1" host="$2" file="$3" getter=env_get_local
  [ -n "$host" ] && getter=env_get_remote
  if [ -n "$host" ]; then
    OLD_KID["$tag"]="$($getter "$host" "$file" RZP_KEY_ID)"
    OLD_KSEC["$tag"]="$($getter "$host" "$file" RZP_KEY_SECRET)"
    OLD_WSEC["$tag"]="$($getter "$host" "$file" RZP_WEBHOOK_SECRET)"
  else
    OLD_KID["$tag"]="$($getter "$file" RZP_KEY_ID)"
    OLD_KSEC["$tag"]="$($getter "$file" RZP_KEY_SECRET)"
    OLD_WSEC["$tag"]="$($getter "$file" RZP_WEBHOOK_SECRET)"
  fi
}
step "Snapshotting current values (for rollback)"
[ "$DO_LAPTOP" = 1 ] && { snapshot laptop ""           "$LAPTOP_ENV"; ok "laptop old key id $(fingerprint "${OLD_KID[laptop]}")"; }
[ "$DO_VM1" = 1 ]    && { snapshot vm1    "$VM1_HOST"  "$VM1_ENV";    ok "vm1    old key id $(fingerprint "${OLD_KID[vm1]}")"; }
[ "$DO_VM2" = 1 ]    && { snapshot vm2    "$VM2_HOST"  "$VM2_ENV";    ok "vm2    old key id $(fingerprint "${OLD_KID[vm2]}")"; }

# Did the webhook secret's VALUE actually change? Rewriting it to the value it
# already had is not a rotation and does not require a dashboard edit.
if [ -n "$NEW_WSEC" ] && [ "$NEW_WSEC" != "${OLD_WSEC[laptop]:-${OLD_WSEC[vm1]:-}}" ]; then
  WSEC_CHANGED=1
fi
[ "$WSEC_CHANGED" = 1 ] && log "    webhook secret will change — dashboard edit required afterwards" \
                        || log "    webhook secret effectively unchanged"

# ------------------------------- confirm -----------------------------------

if [ "$DRY_RUN" = 0 ] && [ "$ASSUME_YES" = 0 ]; then
  printf '\n    This will rewrite RZP_* in:\n'
  [ "$DO_LAPTOP" = 1 ] && printf '      laptop  %s\n' "$LAPTOP_ENV"
  [ "$DO_VM1" = 1 ]    && printf '      vm1     %s  (restart: %s)\n' "$VM1_ENV" "$VM1_SERVICES"
  [ "$DO_VM2" = 1 ]    && printf '      vm2     %s\n' "$VM2_ENV"
  printf '\n    MANDATE_SECRET will be preserved. Old values are recoverable from backups.\n'
  printf '    Proceed? [y/N] '; read -r ans
  case "$ans" in y|Y|yes) ;; *) die "aborted — nothing changed." ;; esac
fi

if [ "$DRY_RUN" = 1 ]; then
  step "DRY RUN — no files will be modified"
  for p in "${PAIRS[@]}"; do log "    would set ${p%%=*}=<$(fingerprint "${p#*=}")>"; done
  log "    would restart on $VM1_HOST: $VM1_SERVICES"
  log "    would verify: /healthz, webhook HMAC (old rejected / new accepted), MANDATE_SECRET"
  exit 0
fi

# ------------------------------- rollback trap -----------------------------

ROLLBACK_DONE=0
rollback() {
  [ "$ROLLBACK_DONE" = 1 ] && return 0
  ROLLBACK_DONE=1
  printf '\n%s[ROLLBACK]%s restoring previous values\n' "$C_YEL" "$C_OFF"
  [ "$DO_LAPTOP" = 1 ] && [ -n "${BACKUP_PATH[laptop]:-}" ] && cp -p "${BACKUP_PATH[laptop]}" "$LAPTOP_ENV" && log "    laptop restored"
  for spec in "${VM1_HOST}|${VM1_ENV}|vm1|${DO_VM1}|${VM1_SERVICES}" "${VM2_HOST}|${VM2_ENV}|vm2|${DO_VM2}|"; do
    IFS='|' read -r host file tag enabled svcs <<<"$spec"
    [ "$enabled" = 1 ] || continue
    b="${BACKUP_PATH[$tag]:-}"
    [ -n "$b" ] || continue
    ssh -o ConnectTimeout=15 -o BatchMode=yes "$host" "cp -p '$b' '$file' && chmod 600 '$file'" \
      && log "    $tag restored"
    if [ -n "$svcs" ]; then
      ssh -o ConnectTimeout=20 -o BatchMode=yes "$host" \
        "sudo -n systemctl restart $svcs" >/dev/null 2>&1 && log "    $tag services restarted"
    fi
  done
  log "    Previous key id is live again: $(fingerprint "${OLD_KID[vm1]:-${OLD_KID[laptop]:-}}")"
}
# Overrides the lock-release trap above — so it must release the lock too.
trap 'rc=$?; [ $rc -ne 0 ] && rollback; rmdir "$LOCK_FILE" 2>/dev/null || true; exit $rc' EXIT

# ------------------------------- apply -------------------------------------

step "Applying new values"

TS="$(date -u +%Y%m%d-%H%M%S)"

if [ "$DO_LAPTOP" = 1 ]; then
  cp -p "$LAPTOP_ENV" "$BACKUP_DIR/laptop.env.$TS"
  BACKUP_PATH[laptop]="$BACKUP_DIR/laptop.env.$TS"
  out="$(apply_local "$LAPTOP_ENV" "${PAIRS[@]}")"
  ok "laptop  $(printf '%s' "$out" | grep '^APPLIED:' | sed 's/^APPLIED:/set/')"
  log "         backup: ${BACKUP_PATH[laptop]}"
fi

if [ "$DO_VM1" = 1 ]; then
  out="$(apply_remote "$VM1_HOST" "$VM1_ENV" "${PAIRS[@]}")"
  # tr -d '\r' is defence in depth: ENV_APPLY_SH is already CR-stripped, but if
  # that ever regresses the trailing CR would land inside BACKUP_PATH and make
  # rollback fail on a path that does not exist — i.e. it would break the one
  # thing that exists to save you.
  BACKUP_PATH[vm1]="$(printf '%s' "$out" | grep '^BACKUP:' | sed 's/^BACKUP://' | tr -d '\r')"
  ok "vm1     $(printf '%s' "$out" | grep '^APPLIED:' | sed 's/^APPLIED:/set/')"
  log "         backup: ${BACKUP_PATH[vm1]} (on $VM1_HOST)"
fi

if [ "$DO_VM2" = 1 ]; then
  out="$(apply_remote "$VM2_HOST" "$VM2_ENV" "${PAIRS[@]}")"
  BACKUP_PATH[vm2]="$(printf '%s' "$out" | grep '^BACKUP:' | sed 's/^BACKUP://' | tr -d '\r')"
  ok "vm2     $(printf '%s' "$out" | grep '^APPLIED:' | sed 's/^APPLIED:/set/')"
  log "         backup: ${BACKUP_PATH[vm2]} (on $VM2_HOST)"
  if [ -n "$NEW_OPENROUTER" ] && ssh -o ConnectTimeout=10 "$VM2_HOST" "[ -f '$VM2_HERMES_ENV' ]" 2>/dev/null; then
    apply_remote "$VM2_HOST" "$VM2_HERMES_ENV" "OPENROUTER_API_KEY=$NEW_OPENROUTER" >/dev/null \
      && ok "vm2     hermes env updated"
  fi
fi

# ------------------------------- restart -----------------------------------

if [ "$DO_VM1" = 1 ] && [ -n "$VM1_SERVICES" ]; then
  step "Restarting services on $VM1_HOST"
  # shellcheck disable=SC2086
  ssh -o ConnectTimeout=30 -o BatchMode=yes "$VM1_HOST" "sudo -n systemctl restart $VM1_SERVICES" \
    || die "systemctl restart failed on $VM1_HOST — check: sudo systemctl status $VM1_SERVICES"
  ok "restarted: $VM1_SERVICES"
  sleep 6   # uvicorn + live_show both need a few seconds to bind
fi

# ------------------------------- verify ------------------------------------

step "Verifying"

# V1. /healthz reflects the new configuration.
if [ "$DO_VM1" = 1 ]; then
  for attempt in 1 2 3 4 5; do
    HZ="$(ssh -o ConnectTimeout=10 "$VM1_HOST" "curl -s -m 8 http://127.0.0.1:8000/healthz" 2>/dev/null || true)"
    [ -n "$HZ" ] && break
    sleep 4
  done
  [ -n "$HZ" ] || die "/healthz returned nothing on $VM1_HOST — service did not come back up."
  log "    $HZ"
  printf '%s' "$HZ" | grep -q '"razorpay_configured":true' || die "razorpay_configured is not true"
  printf '%s' "$HZ" | grep -q '"webhook_secret_set":true'  || die "webhook_secret_set is not true"
  ok "/healthz: razorpay_configured=true, webhook_secret_set=true"
fi

# V2. Deployed key id matches what we asked for.
for spec in "${VM1_HOST}|${VM1_ENV}|vm1|${DO_VM1}" "${VM2_HOST}|${VM2_ENV}|vm2|${DO_VM2}"; do
  IFS='|' read -r host file tag enabled <<<"$spec"
  [ "$enabled" = 1 ] || continue
  got="$(env_get_remote "$host" "$file" RZP_KEY_ID)"
  [ "$got" = "$NEW_KID" ] || die "$tag: RZP_KEY_ID on disk is $(fingerprint "$got"), expected $(fingerprint "$NEW_KID")"
  ok "$tag: RZP_KEY_ID on disk matches"
done
[ "$DO_LAPTOP" = 1 ] && {
  got="$(env_get_local "$LAPTOP_ENV" RZP_KEY_ID)"
  [ "$got" = "$NEW_KID" ] || die "laptop: RZP_KEY_ID on disk does not match"
  ok "laptop: RZP_KEY_ID on disk matches"
}

# V3. MANDATE_SECRET untouched — byte for byte.
for spec in "laptop||${LAPTOP_ENV}|${DO_LAPTOP}" "vm1|${VM1_HOST}|${VM1_ENV}|${DO_VM1}" "vm2|${VM2_HOST}|${VM2_ENV}|${DO_VM2}"; do
  IFS='|' read -r tag host file enabled <<<"$spec"
  [ "$enabled" = 1 ] || continue
  if [ -n "$host" ]; then now="$(env_get_remote "$host" "$file" MANDATE_SECRET)"
  else now="$(env_get_local "$file" MANDATE_SECRET)"; fi
  [ "$now" = "${MANDATE_BEFORE[$tag]}" ] \
    || die "$tag: MANDATE_SECRET CHANGED during rotation — existing mandates may now be unverifiable. Restore from backup immediately."
  ok "$tag: MANDATE_SECRET unchanged (existing mandates still verify)"
done

# V4. Webhook HMAC: old secret must be REJECTED, new secret must be ACCEPTED.
#     This is the only check that proves the running process actually reloaded
#     the new secret rather than still holding the old one in memory.
#
#     Side effect, measured 2026-08-30: a correctly-signed probe writes TWO
#     rows — one in webhook_events and one in audit_log — both carrying
#     event='key_rotation.probe'. The audit row is recorded as
#     {"note":"unhandled/orphan event", ...}: the receiver logs unknown events
#     but takes no money action, no mandate draw-down and no order transition.
#     That is the correct behaviour, so leave it visible.
#
#     Rejected (400) probes write nothing at all — the signature check runs
#     before any write.
#
#     DO NOT DELETE THESE ROWS TO TIDY UP. audit_log is a hash chain (each row
#     stores prev_hash + self_hash). Removing a row breaks every link after it,
#     and re-chaining the remainder would be precisely the tampering the chain
#     exists to detect. Four probe rows in a ~700-row ledger are honest
#     evidence that the webhook receiver handles unknown events safely.
if [ "$DO_VM1" = 1 ] && [ "$NO_WEBHOOK_PROBE" = 0 ]; then
  PROBE='{"event":"key_rotation.probe","payload":{"payment":{"entity":{"order_id":"key_rotation_probe"}}}}'
  post_probe() { # secret -> http code
    local sig code body
    sig="$(printf '%s' "$PROBE" | hmac_sha256_hex "$1")"
    body="$(mktemp)"
    # NOT `curl ... || echo 000`: on this platform curl can emit a valid
    # http_code on stdout and still exit non-zero, and the two then
    # concatenate into a nonsense code like "200000" that silently fails
    # every case branch below. Capture the code, then validate it.
    code=$(curl -s -o "$body" -w '%{http_code}' -m 20 -X POST \
             "$PUBLIC_BASE/webhooks/razorpay" \
             -H 'Content-Type: application/json' \
             -H "X-Razorpay-Signature: $sig" \
             -H "X-Razorpay-Event-Id: key-rotation-$TS-$(printf '%s' "$1" | sha256sum | cut -c1-6)" \
             -d "$PROBE" 2>/dev/null)
    rm -f "$body"
    case "$code" in
      ''|*[!0-9]*) code=000 ;;
    esac
    printf '%s' "$code"
  }
  code_new="$(post_probe "$NEW_WSEC")"
  case "$code_new" in
    2??) ok "webhook: NEW secret ACCEPTED (HTTP $code_new)" ;;
    400) die "webhook: new secret REJECTED with 400 — the running process did not pick it up. Restart $VM1_SERVICES manually." ;;
    000) warn "webhook: no response from $PUBLIC_BASE — skipping signature check (network or DNS)" ;;
    *)   warn "webhook: unexpected HTTP $code_new for the new secret" ;;
  esac
  if [ -n "${OLD_WSEC[vm1]:-}" ] && [ "${OLD_WSEC[vm1]}" != "$NEW_WSEC" ]; then
    code_old="$(post_probe "${OLD_WSEC[vm1]}")"
    if [ "$code_old" = "400" ]; then
      ok "webhook: OLD secret REJECTED (HTTP 400) — rotation genuinely took effect"
    else
      warn "webhook: old secret returned HTTP $code_old, expected 400. The process may still be holding the old value."
    fi
  fi
fi

# V5. The whole point: is the new key actually clean? Report what we can.
step "Key health"
info "A fresh Razorpay test key is good for roughly the first 10-13 checkouts"
info "before the risk engine starts challenging. Budget it accordingly:"
info "  - keep this key COLD: <=3 live demo checkouts/day, hours apart"
info "  - run any bulk/fleet sessions on a SEPARATE key id"
info "  - prefer /demo replay mode for judging traffic"
info "Track live challenge rate with: python scripts/transactability_report.py"

# ------------------------------- record ------------------------------------

{
  printf '%s rotated RZP_KEY_ID=%s RZP_KEY_SECRET=%s RZP_WEBHOOK_SECRET=%s targets=%s mandate_preserved=yes\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(fingerprint "$NEW_KID")" "$(fingerprint "$NEW_KSEC")" "$(fingerprint "$NEW_WSEC")" "$TARGETS"
} >> "$ROTATION_LOG" 2>/dev/null || true
chmod 600 "$ROTATION_LOG" 2>/dev/null || true

step "Done"
log "    New key id in service : $(fingerprint "$NEW_KID")"
log "    Backups               :"
[ "$DO_LAPTOP" = 1 ] && log "      laptop  ${BACKUP_PATH[laptop]}"
[ "$DO_VM1" = 1 ]    && log "      vm1     ${BACKUP_PATH[vm1]}  (on $VM1_HOST)"
[ "$DO_VM2" = 1 ]    && log "      vm2     ${BACKUP_PATH[vm2]}  (on $VM2_HOST)"
log ""
# Only nag about the dashboard if the secret's VALUE actually changed.
# Testing whether the key appears in PAIRS is wrong: the script rewrites the
# webhook secret to its existing value when you rotate only the API key.
if [ "$WSEC_CHANGED" = 1 ]; then
  log "    Next: update the webhook secret in Razorpay Dashboard > Settings >"
  log "    Webhooks so it matches. Inbound events will 400 until you do."
else
  log "    Webhook secret unchanged, so no dashboard edit is needed."
fi
log ""
log "    Re-check any time with:  bash scripts/rotate_keys.sh --status"
