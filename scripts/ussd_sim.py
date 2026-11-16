#!/usr/bin/env python3
"""Interactive USSD simulator for the gateway (mimics Africa's Talking).

Usage:
    python scripts/ussd_sim.py [--url http://localhost:8000/ussd] [--phone +221771234567]

Type what you would key in on the phone and press Enter. Commands:
    :new   start a fresh session (re-dial)
    :quit  exit
Everything else, including "0", is sent as a keypress.
"""
import argparse
import time
import urllib.parse
import urllib.request


def send(url, session_id, phone, text, code):
    body = urllib.parse.urlencode(
        {"sessionId": session_id, "phoneNumber": phone, "serviceCode": code, "text": text}
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def show(raw):
    kind, _, screen = raw.partition(" ")
    print("\n+" + "-" * 44)
    for line in screen.splitlines() or [""]:
        print("| " + line)
    print("+" + "-" * 44 + f"  [{kind}] ({len(screen)} chars)")
    return kind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/ussd")
    ap.add_argument("--phone", default="+221771234567")
    ap.add_argument("--code", default="*384*96#")
    a = ap.parse_args()

    def new_session():
        sid = f"sim_{int(time.time() * 1000)}"
        print(f"\n>>> Dialing {a.code} as {a.phone} (session {sid})")
        return sid, []

    sid, inputs = new_session()
    try:
        kind = show(send(a.url, sid, a.phone, "", a.code))
    except Exception as e:
        raise SystemExit(f"Cannot reach gateway at {a.url}: {e}")

    while True:
        if kind == "END":
            print("\n(session ended - type :new to re-dial or :quit)")
        try:
            entry = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if entry == ":quit":
            break
        if entry == ":new" or kind == "END":
            if entry != ":new":
                print("(session ended - type :new to re-dial or :quit)")
                continue
            sid, inputs = new_session()
            kind = show(send(a.url, sid, a.phone, "", a.code))
            continue
        inputs.append(entry)
        try:
            kind = show(send(a.url, sid, a.phone, "*".join(inputs), a.code))
        except Exception as e:
            print(f"request failed: {e}")


if __name__ == "__main__":
    main()
