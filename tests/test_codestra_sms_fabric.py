"""No-network regression tests for the design contract's fail-closed boundary."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from scripts.validate_codestra_sms_fabric import validate

ROOT = Path(__file__).resolve().parents[1]
RELATIVE = Path("docs/integrations/codestra-fabric")


@pytest.fixture
def fabric(tmp_path):
    shutil.copytree(ROOT / RELATIVE, tmp_path / RELATIVE)
    return tmp_path


def test_current_source_contract_is_valid():
    assert validate()["service_identity"] == "telnexa-gateway"


@pytest.mark.parametrize(
    "field,value",
    [
        ("service_identity", "telnexa-adapter"),
        ("integration_boundary", "DIRECT"),
        ("n8n_direct_access", True),
        ("jasmin_direct_access", True),
        ("smpp_credentials_in_n8n", True),
        ("event_delivery", "DIRECT_RELAY"),
        ("unknown_submission_blind_resubmit", True),
        ("capabilities", {}),
    ],
)
def test_unsafe_manifest_is_rejected(fabric, field, value):
    path = fabric / RELATIVE / "manifest.v2.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        validate(fabric)


@pytest.mark.parametrize("value", [True, 0, "false"])
def test_capability_must_be_literal_false(fabric, value):
    path = fabric / RELATIVE / "manifest.v2.json"
    data = json.loads(path.read_text())
    data["capabilities"]["SMS_DELIVERY"] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        validate(fabric)


def test_anonymous_provider_callback_is_rejected(fabric):
    path = fabric / RELATIVE / "sms-api.openapi.yaml"
    data = yaml.safe_load(path.read_text())
    data["paths"]["/internal/v1/provider-events/jasmin"]["post"]["security"] = []
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        validate(fabric)


def test_missing_tenant_binding_is_rejected(fabric):
    path = fabric / RELATIVE / "sms-api.openapi.yaml"
    data = yaml.safe_load(path.read_text())
    data["paths"]["/v1/sms/messages"]["parameters"] = []
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        validate(fabric)


def test_fabric_openapi_schema():
    from openapi_spec_validator import validate_spec

    path = ROOT / RELATIVE / "sms-api.openapi.yaml"
    validate_spec(yaml.safe_load(path.read_text()))
