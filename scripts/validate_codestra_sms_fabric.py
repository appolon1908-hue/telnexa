#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def validate() -> None:
    path = ROOT / 'docs' / 'integrations' / 'codestra-fabric' / 'manifest.v2.json'
    with path.open('r', encoding='utf-8') as handle:
        manifest = json.load(handle)
    assert manifest['integration_boundary'] == 'MIDDLEWARE_ONLY'
    assert manifest['n8n_direct_access'] is False
    assert manifest['jasmin_direct_access'] is False
    assert manifest['smpp_credentials_in_n8n'] is False
    assert manifest['unknown_submission_reconcile_before_retry'] is True
    assert manifest['service_identity'] == 'telnexa-adapter'
    assert not any(manifest['capabilities'].values())

if __name__ == '__main__':
    validate()
    print('TELNEXA_CODESTRA_SMS_FABRIC=PASS')
