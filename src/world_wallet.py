"""Demo-only "outside world" Lightning wallet (an LND node over REST).

Stands in for any Lightning wallet on the internet: it pays a phone user's
Lightning address through the full LNURL-pay flow, and issues invoices that
USSD users can pay to cash out. Configured with WORLD_LND_REST, WORLD_LND_CERT
and WORLD_LND_MACAROON (see docker-compose.regtest.yml); absent in production.
"""

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

import requests

from src.logging_config import get_logger

logger = get_logger(__name__)


class WorldWalletError(Exception):
    pass


@dataclass
class AddressPayment:
    """What happened while paying a Lightning address, step by step."""
    address: str
    amount_sats: int
    steps: List[str] = field(default_factory=list)
    payment_hash: str = ""
    fee_sats: int = 0
    elapsed_ms: int = 0


class WorldWallet:
    def __init__(self, rest_url: str, cert_path: str, macaroon_path: str,
                 lnurl_base: str = "http://localhost:8000"):
        self.rest_url = rest_url.rstrip("/")
        self.cert_path = cert_path
        self.macaroon_path = macaroon_path
        # Where this wallet reaches the gateway's LNURL endpoints over HTTP.
        self.lnurl_base = lnurl_base.rstrip("/")

    @classmethod
    def from_env(cls) -> Optional["WorldWallet"]:
        rest = os.getenv("WORLD_LND_REST", "")
        cert = os.getenv("WORLD_LND_CERT", "")
        macaroon = os.getenv("WORLD_LND_MACAROON", "")
        if not (rest and cert and macaroon):
            return None
        return cls(rest, cert, macaroon)

    # -- LND REST --------------------------------------------------------

    def _lnd(self, method: str, path: str, **kwargs):
        try:
            with open(self.macaroon_path, "rb") as f:
                macaroon = f.read().hex()
            response = requests.request(
                method, f"{self.rest_url}{path}",
                headers={"Grpc-Metadata-macaroon": macaroon},
                verify=self.cert_path, timeout=30, **kwargs,
            )
        except (OSError, requests.RequestException) as e:
            raise WorldWalletError(f"Outside-world node unreachable: {e.__class__.__name__}")
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            raise WorldWalletError(data.get("message") or f"LND HTTP {response.status_code}")
        return data

    def info(self) -> dict:
        node = self._lnd("GET", "/v1/getinfo")
        balance = self._lnd("GET", "/v1/balance/channels")
        channels = self._lnd("GET", "/v1/channels").get("channels", [])
        return {
            "alias": node.get("alias", ""),
            "pubkey": node.get("identity_pubkey", ""),
            "network": (node.get("chains") or [{}])[0].get("network", ""),
            "block_height": node.get("block_height"),
            "balance_sats": int((balance.get("local_balance") or {}).get("sat", 0)),
            "channels": [
                {
                    "peer_alias": c.get("peer_alias", ""),
                    "capacity": int(c.get("capacity", 0)),
                    "local_balance": int(c.get("local_balance", 0)),
                    "remote_balance": int(c.get("remote_balance", 0)),
                    "active": c.get("active", False),
                }
                for c in channels
            ],
        }

    def create_invoice(self, amount_sats: int, memo: str) -> str:
        data = self._lnd("POST", "/v1/invoices", json={"value": str(amount_sats), "memo": memo})
        return data["payment_request"]

    def _pay_bolt11(self, bolt11: str) -> dict:
        data = self._lnd("POST", "/v1/channels/transactions", json={"payment_request": bolt11})
        if data.get("payment_error"):
            raise WorldWalletError(data["payment_error"])
        return data

    # -- LNURL-pay ---------------------------------------------------------

    def pay_address(self, username: str, amount_sats: int) -> AddressPayment:
        """Pay ``username@<gateway>`` like any Lightning wallet would.

        1. GET /.well-known/lnurlp/<username>  (LUD-16)
        2. GET <callback>?amount=<msat>         (LUD-06)
        3. Check the invoice's description hash matches the metadata
        4. Pay the invoice over Lightning
        """
        started = time.monotonic()
        result = AddressPayment(address=username, amount_sats=amount_sats)

        meta = self._get_json(f"{self.lnurl_base}/.well-known/lnurlp/{username}")
        if meta.get("status") == "ERROR":
            raise WorldWalletError(meta.get("reason", "Unknown Lightning address"))
        identifier = next(
            (v for k, v in json.loads(meta["metadata"]) if k == "text/identifier"), username
        )
        result.address = identifier
        result.steps.append(f"Resolved {identifier}")

        msat = amount_sats * 1000
        if not meta["minSendable"] <= msat <= meta["maxSendable"]:
            raise WorldWalletError("Amount outside the address's limits")
        callback = meta["callback"].replace(_origin(meta["callback"]), self.lnurl_base)
        invoice = self._get_json(callback, params={"amount": msat})
        if invoice.get("status") == "ERROR":
            raise WorldWalletError(invoice.get("reason", "Could not get an invoice"))
        bolt11 = invoice["pr"]
        result.steps.append(f"Got invoice {bolt11[:24]}...")

        decoded = self._lnd("GET", f"/v1/payreq/{bolt11}")
        expected_hash = hashlib.sha256(meta["metadata"].encode()).hexdigest()
        if decoded.get("description_hash") != expected_hash or int(decoded.get("num_satoshis", 0)) != amount_sats:
            raise WorldWalletError("Invoice does not match the Lightning address")
        result.steps.append("Checked amount and description hash")

        paid = self._pay_bolt11(bolt11)
        route = paid.get("payment_route") or {}
        result.payment_hash = _b64_to_hex(paid.get("payment_hash", ""))
        result.fee_sats = int(route.get("total_fees", 0))
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        result.steps.append(f"Paid over Lightning in {result.elapsed_ms} ms")
        logger.info(
            "Outside-world wallet paid a Lightning address",
            extra={'event_type': 'world_payment', 'amount_sats': amount_sats}
        )
        return result

    @staticmethod
    def _get_json(url: str, params: Optional[dict] = None) -> dict:
        try:
            response = requests.get(url, params=params, timeout=30)
            return response.json()
        except (requests.RequestException, ValueError) as e:
            raise WorldWalletError(f"LNURL request failed: {e.__class__.__name__}")


def _origin(url: str) -> str:
    scheme, rest = url.split("://", 1)
    return f"{scheme}://{rest.split('/', 1)[0]}"


def _b64_to_hex(value: str) -> str:
    """LND REST returns bytes fields base64-encoded."""
    try:
        return base64.b64decode(value).hex()
    except (ValueError, TypeError):
        return ""
