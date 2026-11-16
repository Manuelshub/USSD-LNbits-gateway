"""In-memory stand-ins shared by tests."""

from datetime import datetime, timezone
from unittest.mock import Mock

from src.models import Invoice, PaymentResult


class FakeRedis:
    """The subset of redis.Redis the gateway uses (decode_responses=True)."""

    def __init__(self):
        self.data = {}

    def ping(self):
        return True

    def get(self, key):
        value = self.data.get(key)
        return None if isinstance(value, list) else value

    def set(self, key, value):
        self.data[key] = str(value)
        return True

    def setex(self, key, ttl, value):
        return self.set(key, value)

    def delete(self, *keys):
        return sum(1 for key in keys if self.data.pop(key, None) is not None)

    def incr(self, key):
        value = int(self.data.get(key, 0)) + 1
        self.data[key] = str(value)
        return value

    def expire(self, key, ttl):
        return key in self.data

    def lpush(self, key, value):
        self.data.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, end):
        self.data[key] = self.data.get(key, [])[start:end + 1]

    def lrange(self, key, start, end):
        return self.data.get(key, [])[start:end + 1]


def fake_lnbits_client(balance=50_000, paid=True):
    """Mock LNbitsClient with successful defaults."""
    client = Mock()
    client.get_balance.return_value = balance
    client.get_transactions.return_value = []
    client.create_invoice.return_value = Invoice(
        payment_hash="f" * 64,
        payment_request="lnbc10u1p" + "x" * 250,
        amount=1000,
        memo="USSD Payment",
        created_at=datetime.now(timezone.utc),
    )
    client.check_invoice_status.return_value = {"paid": paid, "amount": 1000}
    client.decode_invoice.return_value = {"amount": 2000, "payment_hash": "e" * 64}
    client.pay_invoice.return_value = PaymentResult(
        success=True, payment_hash="d" * 64, amount=None, fee=0
    )
    return client
