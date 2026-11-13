# USSD-LNbits Gateway

**A Bitcoin Lightning wallet for any phone. No smartphone, no internet, no app.**

Dial a shortcode such as `*384*96#`, pick English or French, set a PIN, and you have your own Lightning wallet. Send sats to another phone number, receive at a Lightning address that anyone in the world can pay, and get an SMS when money arrives.

Built for the [Dakar Bitcoin Hack 2026](https://dakar-bitcoin-hack-2026.devpost.com/) (BTrust).

## The problem

Hundreds of millions of people in Africa use feature phones: voice, SMS and USSD, no internet. Every Lightning wallet assumes a smartphone and a data plan, so the people who would gain most from cheap, borderless payments (market vendors, rural families receiving remittances) are left out.

## What it does

| | |
|---|---|
| **Own wallet per phone** | First dial creates a separate LNbits account for that number. No shared wallet. |
| **Send to a phone number** | `3 → 1 → 772345678 → 500 → PIN`. Settles instantly inside LNbits, no fee. The recipient gets an SMS. |
| **Receive from anyone** | Every user has a Lightning address, `221771234567@your-domain`, payable from any Lightning wallet (LNURL-pay). |
| **Invoices by SMS** | A Lightning invoice is 250+ characters and a USSD screen holds 160, so "Request an amount" texts you the full invoice to forward. |
| **Payment alerts** | SMS when an invoice or Lightning address payment arrives (LNbits webhook). |
| **PIN on every payment** | PBKDF2-hashed 4-digit PIN; three wrong tries lock sending for 30 minutes. |
| **English and French** | Bilingual first screen; switch any time in Settings. |
| **Balance and history** | Last 5 transactions, paginated to fit the screen. |

```
Lightning Wallet          Envoyer à:                 Envoyer 500 sats à
1. Check Balance          1. Numéro de téléphone     +221772345678?
2. Receive Payment        2. Facture Lightning       Entrez le PIN pour
3. Send Payment           0:Menu                     confirmer:
4. History                                           0:Menu
5. Settings
6. Exit
```

## Architecture

```
Feature phone ──USSD──▶ Africa's Talking ──HTTPS POST /ussd?key=…──▶ Gateway (FastAPI) ──REST──▶ LNbits ──▶ Lightning
      ▲                        │                                       │      │                    │
      └──────── SMS ───────────┘◀──────────── SMS API ─────────────────┘   Redis ◀── paid webhook ─┘
                                                                     (sessions, phone wallets)
Any Lightning wallet ──pays 221771234567@domain (LNURL-pay)──▶ Gateway
```

Each keypress is one HTTP request. The gateway is stateless; sessions and phone wallets live in Redis. Details, sequence diagrams and the threat model: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Stack:** Python 3.12, FastAPI, Redis, LNbits 1.6, Africa's Talking (USSD + SMS), Docker Compose.

## Run it locally

Requirements: Docker with Compose.

```bash
git clone <this repository>
cd dakar-bitcoin
cp .env.example .env
docker compose up -d
```

1. Open LNbits at http://localhost:5000, create a wallet (this is the **operator** wallet; it funds the demo faucet and is never given to a phone), and put its Wallet ID, Admin key and Invoice key in `.env`.
2. In `.env` set `DEMO_MODE=true` and `PUBLIC_URL=http://localhost:8000`, plus `WEBHOOK_SECRET` (`openssl rand -hex 24`).
3. `docker compose up -d` again, then run `scripts/setup_demo.sh` once. It credits the operator wallet with FakeWallet sats and lets LNbits call the gateway's webhook.
4. Open **http://localhost:8000/demo**: a phone simulator with the wallet behind it, its Lightning address, and the SMS it receives.

Demo script: dial as Awa, choose a language, set a PIN twice. Do the same as Moussa. Give Awa 5,000 sats with the faucet. As Awa: `3` Send → `1` Phone number → `772345678` → `500` → PIN. Moussa's balance goes up and his SMS inbox shows the alert.

From a terminal: `python scripts/ussd_sim.py --phone +221771234567`.

## Going live with Africa's Talking

1. Deploy behind HTTPS and set `PUBLIC_URL=https://your-domain`.
2. Set `USSD_CALLBACK_SECRET` and register `https://your-domain/ussd?key=<secret>` as the USSD callback (POST).
3. Set `AT_USERNAME` and `AT_API_KEY` for SMS, `USSD_CODE` to your shortcode.
4. Allow your gateway's URL in LNbits' callback URL rules (Admin UI → Server) so paid alerts work.
5. Keep `DEMO_MODE=false`. Use a funded Lightning backend instead of FakeWallet.

All settings are listed in [.env.example](.env.example).

## Security

- PIN required for every payment, hashed with PBKDF2-SHA256 and a per-user salt; weak PINs refused; lockout after 3 wrong tries.
- `/ussd` protected by a shared secret, since Africa's Talking does not sign callbacks.
- One LNbits account per phone; the operator wallet is never exposed to USSD users.
- Read-only invoice key for everything except paying.
- Phone numbers are masked in every log line (including HTTP access logs); raw USSD input is never logged because it contains PINs; API keys are masked.
- The paid-invoice webhook uses a secret path and re-checks the payment with LNbits before sending an SMS.

Full threat model and remaining gaps: [docs/ARCHITECTURE.md §7](docs/ARCHITECTURE.md#7-security).

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest
```

Unit, integration and property-based (Hypothesis) tests cover registration, PIN lockout, phone-to-phone sends, invoices by SMS, Lightning addresses, the webhook, the callback secret, log masking, and that every screen in both languages fits in 160 characters.

## Constraints

- A USSD screen holds 160 characters; invoices are delivered by SMS and receiving uses Lightning addresses.
- Paying an arbitrary merchant invoice from USSD still means typing it; phone-to-phone covers the everyday case.
- The bundled LNbits uses FakeWallet: sats move between phones on the same LNbits, but nothing settles on the real network.
- USSD sessions time out after about 90 seconds (operator dependent).

## Metrics we will track

Registrations per day and 7-day activation; transfers per week and median amount; Lightning address payments received; session drop-off per menu step; wrong-PIN and lockout rate.

## Next steps

1. Pay a Lightning address or short merchant code from USSD.
2. Wolof menus, with a native speaker.
3. Daily send limits and PIN recovery.
4. Port the LNbits extension in [lnbits_extension/](lnbits_extension/) to the LNbits 1.x API so operators manage phones from LNbits.
5. Signet pilot with Africa's Talking in Senegal.

## License

MIT, see [LICENSE](LICENSE).

## Acknowledgments

BTrust and Dakar Bitcoin Days; LNbits; Africa's Talking; Machankura, who showed Bitcoin over USSD works in Africa.
