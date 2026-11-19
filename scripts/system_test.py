#!/usr/bin/env python3
"""Full-stack system test against the running regtest demo.

Exercises every component together: bitcoind, both LND nodes and their
channel, LNbits on LND, Redis, the gateway (USSD menus, PINs, Lightning
addresses, paid-invoice webhook, SMS), the security controls, and restarts.
Every USSD screen is also checked against the 160-character limit.

    scripts/regtest_bootstrap.sh
    .venv/bin/python scripts/system_test.py            # add --no-restart to skip the restart check

Uses fresh random phone numbers, so it can be rerun; each run creates a few
LNbits accounts and moves a few thousand regtest sats. No real money.
Exit code 0 when every check passes.
"""
import argparse
import concurrent.futures
import json
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8000"
CODE = "*384*96#"
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.regtest.yml"]
SCREEN_LIMIT = 160

results = []          # (section, name, ok, detail)
screens_seen = []     # every USSD body, for the length check
section = ""


# -- helpers -----------------------------------------------------------------

def env(key):
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def http(method, path, data=None, params=None, timeout=30):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except OSError:          # not listening yet (e.g. during a restart)
        return 0, ""


def jget(path, **params):
    status, text = http("GET", path, params=params or None)
    try:
        return status, json.loads(text)
    except ValueError:
        return status, {}


def sh(*args, timeout=60):
    return subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def lncli(node, *args):
    r = sh(*COMPOSE, "exec", "-T", "-u", "lnd", node, "lncli", "--network=regtest", *args)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip().startswith("{") else None


def btc(*args):
    r = sh(*COMPOSE, "exec", "-T", "bitcoind", "bitcoin-cli", "-regtest", "-rpcuser=regtest",
           "-rpcpassword=regtest", "-rpcport=18443", *args)
    return r.stdout.strip()


def check(name, ok, detail=""):
    results.append((section, name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
    return ok


def wait_until(fn, timeout=20, interval=0.5):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return fn()


class Phone:
    """One handset. Each dial() is a USSD session; key() sends one keypress,
    with the cumulative text Africa's Talking would send."""

    def __init__(self, number, real_callback=False):
        self.number = number
        self.real = real_callback
        self.sid = None
        self.typed = []

    def _post(self, text):
        data = {"sessionId": self.sid, "serviceCode": CODE, "phoneNumber": self.number, "text": text}
        if self.real:
            status, body = http("POST", "/ussd", data, params={"key": env("USSD_CALLBACK_SECRET")})
        else:
            status, body = http("POST", "/demo/ussd", data)
        screens_seen.append(body)
        return body

    def dial(self):
        self.sid = f"st{random.randrange(10**12)}"
        self.typed = []
        return self._post("")

    def key(self, *keys):
        out = ""
        for k in keys:
            self.typed.append(str(k))
            out = self._post("*".join(self.typed))
        return out

    def run(self, *keys):
        """Dial, then press keys. Returns the last screen."""
        out = self.dial()
        return self.key(*keys) if keys else out

    @property
    def local(self):
        return self.number[4:]   # +221771234567 -> 771234567

    @property
    def username(self):
        return self.number.lstrip("+")

    def balance(self):
        return jget("/demo/api/wallet", phone=self.number)[1].get("balance_sats")

    def sms(self):
        return [m["message"] for m in jget("/demo/api/sms", phone=self.number)[1].get("messages", [])]


def new_number():
    return "+22177" + "".join(random.choice("0123456789") for _ in range(7))


# -- checks ------------------------------------------------------------------

def infrastructure():
    status, h = jget("/health")
    check("gateway /health: Redis and LNbits connected",
          status == 200 and h.get("redis") == "connected" and h.get("lnbits") == "connected", str(h))
    blocks = btc("getblockcount")
    check("bitcoind (regtest) has a mature chain", blocks.isdigit() and int(blocks) >= 101, f"{blocks} blocks")
    op, world = lncli("lnd-operator", "getinfo"), lncli("lnd-world", "getinfo")
    check("both LND nodes synced to chain",
          op and world and op["synced_to_chain"] and world["synced_to_chain"])
    chans = lncli("lnd-world", "listchannels") or {}
    active = [c for c in chans.get("channels", []) if c.get("active")]
    check("active Lightning channel outside-world <-> operator", bool(active),
          f"capacity {active[0]['capacity']} sats" if active else "none")
    r = sh(*COMPOSE, "exec", "-T", "lnbits", "sh", "-c", "echo $LNBITS_BACKEND_WALLET_CLASS")
    check("LNbits funding source is LND (not FakeWallet)", "Lnd" in r.stdout, r.stdout.strip())


def registration(a, b):
    s = a.dial()
    check("new number sees bilingual language choice", "1. English" in s and "2. Francais" in s)
    s = a.key("1", "4826", "4826")
    check("EN registration: wallet + Lightning address",
          "Wallet created!" in s and f"{a.username}@" in s)
    s = b.run("2", "5937", "5937")
    check("FR registration", "Portefeuille créé!" in s)
    c = Phone(new_number())
    check("weak PIN refused", "too easy" in c.run("1", "1111"))
    check("mismatched PINs refused", "do not match" in c.run("1", "4826", "4827"))
    check("refused PINs create no wallet", jget("/demo/api/wallet", phone=c.number)[1].get("registered") is False)
    check("registered phone goes straight to its menu, in its language",
          "1. Check Balance" in a.dial() and "Voir le solde" in b.dial())


def receive(a):
    status, meta = jget(f"/.well-known/lnurlp/{a.username}")
    check("LNURL-pay metadata for the phone's Lightning address",
          status == 200 and meta.get("tag") == "payRequest" and meta.get("callback"),
          f"min {meta.get('minSendable')} max {meta.get('maxSendable')} msat")
    if meta.get("callback"):
        cb = urllib.parse.urlparse(meta["callback"])
        status, body = http("GET", cb.path, params={"amount": 1})
        check("LNURL callback rejects an amount below the minimum", "ERROR" in body.upper(), body[:80])
    status, _ = jget("/.well-known/lnurlp/221700000000")
    check("unknown Lightning address is not served", status in (404, 400) or "ERROR" in _.__repr__().upper(), str(status))

    before = a.balance()
    status, paid = (lambda s_t: (s_t[0], json.loads(s_t[1]) if s_t[1].startswith("{") else {}))(
        http("POST", "/demo/api/world/pay", {"phone": a.number, "amount": 3000}))
    check("outside wallet pays the Lightning address over the channel (LNURL-pay)",
          status == 200 and paid.get("amount_sats") == 3000,
          f"{paid.get('elapsed_ms')} ms, fee {paid.get('fee_sats')}" if paid else f"HTTP {status}")
    after = wait_until(lambda: a.balance() if a.balance() == (before or 0) + 3000 else None)
    check("phone balance credited", after == (before or 0) + 3000, f"{before} -> {after}")
    check("payment SMS sent to the phone", wait_until(lambda: any("3,000" in m for m in a.sms())))

    # Request an amount: invoice by SMS, paid from outside, webhook, status.
    a.dial()
    s = a.key("2")
    check("Receive screen shows the Lightning address", f"{a.username}@" in s)
    s = a.key("1", "1500")
    check("'Request an amount' says the invoice went by SMS", "SMS" in s, s.replace("\n", " | ")[:90])
    sms = wait_until(lambda: next((m for m in a.sms() if "lnbcrt" in m), None))
    bolt = re.search(r"lnbcrt\S+", sms or "")
    check("SMS carries the full BOLT11 invoice", bool(bolt))
    if bolt:
        n_sms = len(a.sms())
        r = sh(*COMPOSE, "exec", "-T", "-u", "lnd", "lnd-world", "lncli", "--network=regtest",
               "payinvoice", "--force", bolt.group(0), timeout=90)
        check("outside node pays the SMS invoice", r.returncode == 0, (r.stderr or "")[-120:].strip())
        check("LNbits paid webhook -> 'received' SMS",
              wait_until(lambda: len(a.sms()) > n_sms and any("1,500" in m for m in a.sms()[:2]), timeout=30))
        s = a.key("1")
        check("status check shows Paid", "Paid" in s, s.replace("\n", " | ")[:80])


def send_to_phone(a, b):
    a_before, b_before = a.balance(), b.balance()
    s = a.dial(); s = a.key("3", "1", b.local, "500")
    check("confirm screen names amount and recipient", f"Send 500 sats to {b.number}?" in s)
    s = a.key("4826")
    check("send with PIN succeeds", f"Sent 500 sats to {b.number}" in s, s.replace("\n", " | ")[:80])
    check("balances move: sender -500, recipient +500",
          wait_until(lambda: a.balance() == a_before - 500 and b.balance() == b_before + 500),
          f"{a_before}->{a.balance()}, {b_before}->{b.balance()}")
    check("recipient SMS in their language (FR)", wait_until(lambda: any("reçu 500 sats" in m for m in b.sms())),
          (b.sms() or [""])[0][:70])
    stranger = new_number()
    check("unregistered recipient refused", "no wallet yet" in a.run("3", "1", stranger[4:]))
    check("sending to yourself refused", "cannot send to yourself" in a.run("3", "1", a.local))
    check("non-numeric amount refused", "numbers only" in a.run("3", "1", b.local, "abc"))
    s = a.run("3", "1", b.local, "99999999")
    check("amount above balance refused before the PIN", "Insufficient balance" in s and "PIN" not in s)
    s = a.run("3", "1", b.local, "100", "0000")
    check("wrong PIN counted", "Tries left: 2" in s)
    s = a.key("0001", "0002")
    check("3 wrong PINs lock sending", s.startswith("END") and "Too many wrong PINs" in s)
    s = a.run("3", "1", b.local, "100", "4826")
    check("lockout survives a new session, even with the right PIN", "Too many wrong PINs" in s)
    check("no money moved while locked", a.balance() == a_before - 500)


def cash_out(b):
    world_before = (lncli("lnd-world", "channelbalance") or {}).get("local_balance", {}).get("sat")
    status, inv = http("POST", "/demo/api/world/invoice", {"amount": 200})
    bolt = json.loads(inv).get("bolt11") if status == 200 else None
    b_before = b.balance()
    b.dial()
    s = b.key("3", "2", bolt or "x")
    check("FR: pasted invoice decoded, PIN asked", "Payer 200 sats?" in s, s.replace("\n", " | ")[:80])
    s = b.key("5937")
    check("cash out over Lightning succeeds", "Envoyé" in s, s.replace("\n", " | ")[:80])
    world_after = wait_until(lambda: (lncli("lnd-world", "channelbalance") or {}).get("local_balance", {}).get("sat")
                             if (lncli("lnd-world", "channelbalance") or {}).get("local_balance", {}).get("sat") != world_before else None)
    check("sats arrive at the outside node", world_after and int(world_after) - int(world_before) == 200,
          f"{world_before} -> {world_after}")
    check("phone balance debited", wait_until(lambda: b.balance() is not None and b.balance() <= b_before - 200),
          f"{b_before} -> {b.balance()}")
    check("garbage invoice: clear message, not 'service unavailable'",
          "Facture invalide" in b.run("3", "2", "lnbcgarbage"))


def history_and_settings(b):
    s = b.run("1")
    check("balance screen matches the wallet", f"{b.balance():,}" in s, s.replace("\n", " | ")[:60])
    s = b.run("4")
    check("history lists transactions", "reçu" in s or "envoyé" in s, s.replace("\n", " | ")[:80])
    s = b.run("5", "1", "1")
    check("language switch FR -> EN", "updated" in s.lower() or "1. Check Balance" in b.dial())
    s = b.run("5", "2", "5937", "6048", "6048")
    check("change PIN", "PIN changed" in s, s.replace("\n", " | ")[:60])
    s = b.run("3", "2", "lnbcgarbage")
    check("menu works after settings changes", "Invalid invoice." in s)
    s = b.run("5", "2", "5937")
    check("old PIN no longer accepted", "Wrong PIN" in s or "incorrect" in s.lower(), s.replace("\n", " | ")[:60])


def security(a):
    data = {"sessionId": "secchk", "serviceCode": CODE, "phoneNumber": a.number, "text": ""}
    secret = env("USSD_CALLBACK_SECRET")
    status_none, _ = http("POST", "/ussd", data)
    status_bad, _ = http("POST", "/ussd", data, params={"key": "wrong"})
    if secret:
        check("/ussd without the callback secret -> 403", status_none == 403)
        check("/ussd with a wrong secret -> 403", status_bad == 403)
        real = Phone(a.number, real_callback=True)
        check("/ussd with the secret serves the menu (Africa's Talking path)", "1. Check Balance" in real.run())
    else:
        check("USSD_CALLBACK_SECRET set for this test", False, "set it in .env to test the callback guard")
    status, body = http("POST", "/webhooks/lnbits/not-the-secret", {"payment_hash": "00" * 32})
    check("paid webhook with a wrong secret is rejected", status in (401, 403, 404), str(status))
    logs = sh("docker", "logs", "--since", f"{int(time.time() - START) + 5}s", "dakar-bitcoin-gateway-1").stdout + \
        sh("docker", "logs", "--since", f"{int(time.time() - START) + 5}s", "dakar-bitcoin-gateway-1").stderr
    leaked = [p for p in PHONES if p.lstrip("+") in logs]
    check("no full phone number in the gateway logs", not leaked, f"leaked: {leaked}" if leaked else "masked")
    check("no typed PIN sequence in the gateway logs", "4826*4826" not in logs and "1*4826" not in logs)
    long = [s for s in screens_seen if len(s.split(" ", 1)[-1]) > SCREEN_LIMIT]
    check(f"all {len(screens_seen)} screens fit in {SCREEN_LIMIT} characters", not long,
          f"longest {max(len(s.split(' ', 1)[-1]) for s in screens_seen)}" if screens_seen else "")


def resilience(a, do_restart):
    s = a.run("6")
    check("Exit ends the session (END)", s.startswith("END"))
    a.dial()
    s = a.key("1", "0")
    check("0 returns to the menu", "1. Check Balance" in s)

    def one(_):
        p = Phone(a.number)
        t = time.time()
        out = p.run("1")
        return "Balance" in out, time.time() - t
    with concurrent.futures.ThreadPoolExecutor(20) as ex:
        res = list(ex.map(one, range(40)))
    worst = max(t for _, t in res)
    check("40 concurrent sessions answered correctly", all(ok for ok, _ in res), f"slowest {worst:.2f} s")
    check("every response well under the ~10 s USSD timeout", worst < 5)

    if not do_restart:
        return
    bal = a.balance()
    sh(*COMPOSE, "restart", "gateway", timeout=120)
    ok = wait_until(lambda: jget("/health")[0] == 200, timeout=60)
    check("gateway restarts and reports healthy", ok)
    check("wallets survive a gateway restart (state in Redis)", a.balance() == bal and "1. Check Balance" in a.dial(),
          f"balance {bal} -> {a.balance()}")


# -- main --------------------------------------------------------------------

START = time.time()
PHONES = []

def main():
    global section
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-restart", action="store_true", help="skip restarting the gateway container")
    args = ap.parse_args()

    a, b = Phone(new_number()), Phone(new_number())
    PHONES.extend([a.number, b.number])
    print(f"System test against {BASE}  (phones {a.number}, {b.number})\n")

    for title, fn, fargs in [
        ("Infrastructure", infrastructure, ()),
        ("Registration", registration, (a, b)),
        ("Receive: Lightning address, invoice by SMS, webhook", receive, (a,)),
        ("Send to a phone number, PIN and lockout", send_to_phone, (a, b)),
        ("Cash out to an outside invoice", cash_out, (b,)),
        ("Balance, history, settings", history_and_settings, (b,)),
        ("Security and privacy", security, (a,)),
        ("Resilience", resilience, (a, not args.no_restart)),
    ]:
        section = title
        print(title)
        try:
            fn(*fargs)
        except Exception as e:  # keep going; report the crash as a failure
            check(f"{title}: ran without crashing", False, f"{e.__class__.__name__}: {e}")
        print()

    failed = [r for r in results if not r[2]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed in {time.time() - START:.0f} s")
    for sec, name, _, detail in failed:
        print(f"  FAILED [{sec}] {name}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
