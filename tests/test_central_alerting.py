import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

class CentralAlertingTests(unittest.TestCase):
    def test_rule_metadata_is_complete(self):
        paths = [ROOT / "config/alerts.yml", ROOT / "config/prometheus/alerts.yml"]
        data = yaml.safe_load(next(p for p in paths if p.exists()).read_text())
        for group in data["groups"]:
            for rule in group["rules"]:
                self.assertTrue({"severity", "environment", "service", "codestra_business", "owner"}.issubset(rule["labels"]), rule["alert"])
                self.assertTrue({"summary", "description", "runbook_url"}.issubset(rule["annotations"]), rule["alert"])

    def test_transport_requires_verified_private_mtls(self):
        paths = [ROOT / "config/prometheus.central.yml", ROOT / "config/prometheus/prometheus.central.yml"]
        data = yaml.safe_load(next(p for p in paths if p.exists()).read_text())
        transport = data["alerting"]["alertmanagers"][0]
        self.assertEqual(transport["scheme"], "https")
        self.assertEqual(transport["api_version"], "v2")
        self.assertEqual(transport["static_configs"][0]["targets"], ["10.40.0.1:19093"])
        self.assertFalse(transport["tls_config"]["insecure_skip_verify"])
        self.assertTrue(all(transport["tls_config"].get(k) for k in ["ca_file", "cert_file", "key_file", "server_name"]))
        overlay = yaml.safe_load((ROOT / "compose.central-alerting.yml").read_text())
        self.assertNotIn("ports", overlay["services"]["prometheus"])
        self.assertNotIn("image", overlay["services"]["prometheus"])
        self.assertTrue(all(":?" in item["file"] for item in overlay["secrets"].values()))
