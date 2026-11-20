#!/usr/bin/env bash
# One-command demo on real Lightning (regtest, no real money). Safe to rerun.
#
#   0. Create .env if needed, with an operator wallet, fresh secrets and demo
#      settings.
#   1. Mine spendable coins on bitcoind.
#   2. Fund lnd-world (the outside Lightning network) and lnd-operator.
#   3. Open a channel lnd-world -> lnd-operator, pushing part of it to the
#      operator so payments can flow both ways.
#   4. Pay a real Lightning invoice from lnd-world into the operator's LNbits
#      wallet, proving LNbits runs on Lightning (and funding the demo faucet).
#
# Usage: scripts/regtest_bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/.."

C="docker compose -f docker-compose.yml -f docker-compose.regtest.yml"
btc() { $C exec -T bitcoind bitcoin-cli -regtest -rpcuser=regtest -rpcpassword=regtest -rpcport=18443 "$@"; }
op() { $C exec -T -u lnd lnd-operator lncli --network=regtest "$@"; }
world() { $C exec -T -u lnd lnd-world lncli --network=regtest "$@"; }
mine() { btc generatetoaddress "$1" "$MINER" >/dev/null; }

wait_for() {  # wait_for <description> <command...>
  local what=$1; shift
  for _ in $(seq 1 60); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  echo "Timed out waiting for $what" >&2; exit 1
}

env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }
env_set() {  # env_set KEY VALUE: replace or append
  # No "sed -i": it differs between GNU (Linux) and BSD (macOS) sed.
  if grep -qE "^$1=" .env; then
    sed "s|^$1=.*|$1=$2|" .env > .env.tmp && mv .env.tmp .env
  else
    echo "$1=$2" >> .env
  fi
}

[ -f .env ] || cp .env.example .env
[ -n "$(env_get WEBHOOK_SECRET)" ] || env_set WEBHOOK_SECRET "$(openssl rand -hex 24)"
[ -n "$(env_get USSD_CALLBACK_SECRET)" ] || env_set USSD_CALLBACK_SECRET "$(openssl rand -hex 24)"
[ -n "$(env_get PUBLIC_URL)" ] || env_set PUBLIC_URL http://localhost:8000
env_set DEMO_MODE true

echo "Starting containers..."
$C up -d >/dev/null 2>&1 || true   # the gateway may stop until the wallet exists
wait_for "LNbits" curl -fs http://localhost:5000/api/v1/health

case "$(env_get LNBITS_ADMIN_KEY)" in
  ""|your_*)
    echo "Creating the operator wallet in LNbits..."
    ACCOUNT=$(curl -fsS -X POST http://localhost:5000/api/v1/account \
      -H 'Content-Type: application/json' -d '{"name": "USSD operator"}')
    env_set LNBITS_WALLET_ID "$(echo "$ACCOUNT" | jq -r .id)"
    env_set LNBITS_ADMIN_KEY "$(echo "$ACCOUNT" | jq -r .adminkey)"
    env_set LNBITS_INVOICE_KEY "$(echo "$ACCOUNT" | jq -r .inkey)"
    ;;
esac
$C up -d --force-recreate gateway >/dev/null 2>&1

echo "Mining..."
btc loadwallet miner >/dev/null 2>&1 || btc createwallet miner >/dev/null 2>&1 || true
MINER=$(btc -rpcwallet=miner getnewaddress)
if [ "$(btc getblockcount)" -lt 101 ]; then mine 101; fi
# LND reports "not synced" while the newest block is hours old, which is
# always the case when the stack is restarted later. A fresh block fixes it.
mine 1

synced() { [ "$($1 getinfo | jq -r .synced_to_chain)" = "true" ]; }
wait_for "lnd-operator to sync" synced op
wait_for "lnd-world to sync" synced world

fund() {  # fund <node fn> <btc amount>
  local balance
  balance=$($1 walletbalance | jq -r .confirmed_balance)
  if [ "$balance" -lt 100000000 ]; then
    btc -rpcwallet=miner sendtoaddress "$($1 newaddress p2wkh | jq -r .address)" "$2" >/dev/null
  fi
}
echo "Funding nodes..."
fund world 10
fund op 1
mine 6
confirmed() { [ "$($1 walletbalance | jq -r .confirmed_balance)" -gt 0 ]; }
wait_for "lnd-world funds" confirmed world

OP_KEY=$(op getinfo | jq -r .identity_pubkey)
# Reconnect by hostname every run: after a restart the containers get new IPs
# and LND would keep dialing the old ones.
world connect "$OP_KEY@lnd-operator:9735" >/dev/null 2>&1 || true
if [ "$(world listchannels | jq '.channels | length')" -eq 0 ]; then
  echo "Opening channel outside-world -> ussd-operator..."
  if [ "$(world pendingchannels | jq '.pending_open_channels | length')" -eq 0 ]; then
    world openchannel --node_key "$OP_KEY" --local_amt 15000000 --push_amt 5000000 >/dev/null
  fi
  mine 6
fi
active() { [ "$(world listchannels --active_only | jq '.channels | length')" -gt 0 ]; }
wait_for "channel to become active" active world

INVOICE_KEY=$(env_get LNBITS_INVOICE_KEY)
OPERATOR_MSAT=$(curl -fsS http://localhost:5000/api/v1/wallet -H "X-Api-Key: $INVOICE_KEY" | jq -r .balance)
if [ "$OPERATOR_MSAT" -lt 100000000 ]; then
echo "Paying the operator's LNbits wallet over Lightning..."
BOLT11=$(curl -fsS -X POST http://localhost:5000/api/v1/payments \
  -H "X-Api-Key: $INVOICE_KEY" -H 'Content-Type: application/json' \
  -d '{"out": false, "amount": 1000000, "memo": "Regtest bootstrap"}' | jq -r .bolt11)
# A brand-new channel can take a few seconds before it carries payments.
wait_for "the payment to go through" world payinvoice --force "$BOLT11"
fi

echo
echo "Done."
echo "  channel: outside-world -> ussd-operator, $(world listchannels | jq -r '.channels[0].capacity') sats"
echo "  operator LNbits wallet: $(curl -fsS http://localhost:5000/api/v1/wallet -H "X-Api-Key: $INVOICE_KEY" | jq -r '.balance / 1000 | floor') sats, received over Lightning"
wait_for "the gateway" curl -fs http://localhost:8000/health
echo "  open http://localhost:8000/demo"
