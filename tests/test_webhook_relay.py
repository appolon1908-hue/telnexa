import hashlib
import hmac
import importlib.util
import os
from pathlib import Path
import unittest

os.environ.setdefault("WEBHOOK_HMAC_SECRET", "test-only-secret")
spec = importlib.util.spec_from_file_location("relay", Path(__file__).parents[1] / "docker/webhook-relay/server.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class SignatureTest(unittest.TestCase):
    def test_signature_covers_timestamp_separator_and_exact_body(self):
        secret = b"shared-secret"
        timestamp = "1786766400"
        body = b'{"event":"inbound"}'
        expected = hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        self.assertEqual(relay.make_signature(secret, timestamp, body), expected)
        self.assertNotEqual(relay.make_signature(secret, timestamp, body + b" "), expected)


if __name__ == "__main__":
    unittest.main()
