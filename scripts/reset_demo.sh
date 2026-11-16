#!/usr/bin/env bash
# Forget all demo phones so the next dial starts at registration.
# Clears the gateway's Redis records (phone wallets, sessions, PIN lockouts,
# demo SMS). LNbits accounts and Lightning channels are left alone.
set -euo pipefail
cd "$(dirname "$0")/.."
for pattern in 'phone_wallet:*' 'session:*' 'pin_failures:*' 'invoice_owner:*' 'demo_sms:*'; do
  docker compose exec -T redis sh -c "redis-cli --scan --pattern '$pattern' | xargs -r redis-cli del" >/dev/null
done
echo "Demo phones cleared. Dial again to register."
