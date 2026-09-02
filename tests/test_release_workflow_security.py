from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text()
CI_WORKFLOW = (ROOT / ".github/workflows/ci.yml").read_text()
GITLEAKS_IGNORE = (ROOT / ".gitleaksignore").read_text()


def test_release_runs_only_from_protected_main() -> None:
    assert "branches: [main]" in WORKFLOW
    assert not re.search(r"(?m)^    tags:", WORKFLOW)
    assert "github.ref == 'refs/heads/main'" in WORKFLOW
    assert "github.ref_protected == true" in WORKFLOW
    assert "test \"$GITHUB_REF_PROTECTED\" = 'true'" in WORKFLOW
    assert "ref: ${{ github.sha }}" in WORKFLOW
    assert "persist-credentials: false" in WORKFLOW


def test_release_dependencies_are_immutable() -> None:
    assert not re.search(r"uses:\s+[^\s]+@v\d+(?:\s|$)", WORKFLOW)
    assert WORKFLOW.count("@sha256:") == 3
    for action in (
        "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
        "docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f",
        "docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9",
        "docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8",
        "sigstore/cosign-installer@6f9f17788090df1f26f669e9d70d6ae9567deba6",
        "actions/attest-build-provenance@977bb373ede98d70efdf65b84cb5f73e068dcc2a",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        assert action in WORKFLOW


def test_all_workflow_actions_are_pinned_to_exact_commits() -> None:
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        source = path.read_text()
        references = re.findall(r"uses:\s+([^\s#]+)", source)
        assert references, path
        for reference in references:
            assert re.search(r"@[0-9a-f]{40}$", reference), (path, reference)


def test_all_checkouts_disable_persisted_credentials() -> None:
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        source = path.read_text()
        checkout_starts = [
            match.start() for match in re.finditer(r"uses:\s+actions/checkout@", source)
        ]
        for index, start in enumerate(checkout_starts):
            end = checkout_starts[index + 1] if index + 1 < len(checkout_starts) else len(source)
            step = source[start:end]
            next_step = re.search(r"(?m)^\s{6}- (?:name:|uses:)", step[1:])
            if next_step:
                step = step[: next_step.start() + 1]
            assert "persist-credentials: false" in step, path


def test_ci_separates_exact_source_and_merge_result_validation() -> None:
    assert "branches: [main]" in CI_WORKFLOW
    assert 'branches: [main, "agent/**", "codex/**"]' not in CI_WORKFLOW
    assert "name: Exact source quality and tests" in CI_WORKFLOW
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in CI_WORKFLOW
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"' in CI_WORKFLOW
    assert "name: Exact merge-result quality and tests" in CI_WORKFLOW
    assert "if: github.event_name == 'pull_request'" in CI_WORKFLOW
    assert "ref: ${{ github.sha }}" in CI_WORKFLOW
    assert 'test "$(git rev-parse HEAD)" = "${{ github.sha }}"' in CI_WORKFLOW
    assert "name: Secret scan" in CI_WORKFLOW


def test_scan_precedes_production_promotion_and_uses_exact_digest() -> None:
    scan = WORKFLOW.index("Scan exact candidate digest before production promotion")
    collision = WORKFLOW.index("Reject an immutable source-tag collision before signing")
    sign = WORKFLOW.index("Sign the passing immutable digest with GitHub OIDC")
    attest = WORKFLOW.index("Attest the passing immutable digest")
    verify = WORKFLOW.index("Independently verify signing identity and bind evidence")
    promote = WORKFLOW.index("Promote only the fully certified digest to the immutable source tag")
    assert scan < collision < sign < attest < verify < promote
    assert '"${IMAGE}@${CANDIDATE_DIGEST}"' in WORKFLOW
    assert "--severity HIGH,CRITICAL" in WORKFLOW
    assert 'final_image="${IMAGE}:sha-${SOURCE_SHA}"' in WORKFLOW
    assert 'test "$final_digest" = "$CANDIDATE_DIGEST"' in WORKFLOW


def test_release_is_retry_safe_and_attestation_is_verified() -> None:
    assert "cancel-in-progress: false" in WORKFLOW
    assert WORKFLOW.count("if existing=") == 2
    assert 'test "$existing" = "$CANDIDATE_DIGEST"' in WORKFLOW
    assert "id: final_tag" in WORKFLOW
    assert WORKFLOW.count("if: steps.final_tag.outputs.exists != 'true'") == 2
    assert "echo 'exists=true' >> \"$GITHUB_OUTPUT\"" in WORKFLOW
    assert "cosign sign --yes" in WORKFLOW
    assert "cosign verify" in WORKFLOW
    assert "--certificate-identity" in WORKFLOW
    assert "--certificate-oidc-issuer" in WORKFLOW
    assert "sha256sum -c SHA256SUMS" in WORKFLOW


def test_release_workflow_has_no_runtime_deployment_or_delivery_action() -> None:
    forbidden = (
        "ssh ",
        "scp ",
        "docker compose up",
        "systemctl restart",
        "sendmail",
        "production canary",
    )
    lowered = WORKFLOW.lower()
    assert all(token not in lowered for token in forbidden)


def test_full_history_secret_scan_only_ignores_reviewed_false_positive_fingerprints() -> None:
    assert "git --redact --no-banner /repo" in WORKFLOW
    fingerprints = {line for line in GITLEAKS_IGNORE.splitlines() if line}
    assert fingerprints == {
        "515b29481dd638f227eadc611c0a7dc2b92acbdd:.github/workflows/step4-format-artifact.yml:generic-api-key:28",
        "67ec59e447bb73334f1edb7042e937977cf2ec8e:labs/middleware_telnexa/certify.py:generic-api-key:72",
        "4b4e5c73463df1c03723438d97bffd9b73393b95:docker-compose.yml:generic-api-key:45",
        "ed555db547bf161123ee6a3277a4262c3c6005e7:DEPLOYMENT_REPORT.md:generic-api-key:54",
    }
