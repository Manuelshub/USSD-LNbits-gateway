#!/usr/bin/env bash
# One-time setup of the local demo stack (docker compose, LNbits FakeWallet).
#
#   1. Lets LNbits call the gateway's paid-invoice webhook (SMS alerts).
#      LNbits keeps these settings in its database, so the env vars in
#      docker-compose.yml only apply to a fresh install.
#   2. Credits the operator wallet (LNBITS_WALLET_ID) with demo sats, which the
#      /demo faucet hands out. FakeWallet sats are not real bitcoin.
#
# Uses the LNbits superuser id from `lnbits-cli superuser` inside the
# container. It is printed nowhere and only sent to the local LNbits.
#
# Usage: scripts/setup_demo.sh [sats]   (default 1,000,000)
set -euo pipefail
cd "$(dirname "$0")/.."

SATS="${1:-1000000}"
LNBITS="${LNBITS_PUBLIC:-http://localhost:5000}"
WALLET_ID="$(grep -E '^LNBITS_WALLET_ID=' .env | cut -d= -f2-)"
[ -n "$WALLET_ID" ] || { echo "LNBITS_WALLET_ID missing in .env" >&2; exit 1; }

SUPERUSER="$(docker compose exec -T lnbits sh -c 'cd /app && uv run lnbits-cli superuser 2>/dev/null' | tail -1 | tr -d '[:space:]')"
[ -n "$SUPERUSER" ] || { echo "Could not read the LNbits superuser id" >&2; exit 1; }

echo "Allowing webhooks to the gateway..."
curl -fsS -X PATCH "$LNBITS/admin/api/v1/settings?usr=$SUPERUSER" \
  -H 'Content-Type: application/json' \
  -d '{"lnbits_callback_url_rules": ["https?://gateway(:\\d+)?(/.*)?", "https?://([a-zA-Z0-9.-]+\\.[a-zA-Z]{2,})(:\\d+)?"], "lnbits_callback_allow_private_ips": true}' \
  >/dev/null

echo "Crediting the operator wallet with $SATS sats..."
curl -fsS -X PUT "$LNBITS/users/api/v1/balance?usr=$SUPERUSER" \
  -H 'Content-Type: application/json' \
  -d "{\"id\": \"$WALLET_ID\", \"amount\": $SATS}" >/dev/null

echo "Done. Open http://localhost:8000/demo (DEMO_MODE=true) and use the faucet."
