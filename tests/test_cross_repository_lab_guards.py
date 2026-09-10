"""The cross-repository lab refuses production targets or enabled effects."""

import pytest

from scripts.certify_sms_cross_repository import FLAGS, guard_environment


def environment():
    return {
        "BILLING_DATABASE_URL": "postgresql+psycopg://telnexa_ci:fixture@127.0.0.1:5432/telnexa_issue29_ci",
        "TELNEXA_DISPOSABLE_CERTIFICATION": "true",
        "SOURCE_SHA": "a" * 40,
        **{name: "false" for name in FLAGS},
    }


def test_explicit_disposable_lab_configuration_is_accepted():
    guard_environment(environment())


@pytest.mark.parametrize("flag", FLAGS)
def test_effectful_or_omitted_flags_are_denied(flag):
    for value in ("true", "0", "False", ""):
        env = environment()
        env[flag] = value
        with pytest.raises(RuntimeError):
            guard_environment(env)
    env = environment()
    del env[flag]
    with pytest.raises(RuntimeError):
        guard_environment(env)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://telnexa_ci:fixture@10.40.0.1:5432/telnexa_issue29_ci",
        "postgresql+psycopg://telnexa_ci:fixture@127.0.0.1:5432/production",
        "postgresql+psycopg://postgres:fixture@127.0.0.1:5432/telnexa_issue29_ci",
        "postgresql+psycopg://telnexa_ci:fixture@127.0.0.1:5433/telnexa_issue29_ci",
        "postgresql+psycopg://telnexa_ci:fixture@127.0.0.1:5432/telnexa_issue29_ci?host=production",
        "sqlite:////tmp/production.db",
    ],
)
def test_non_disposable_database_targets_are_denied(url):
    with pytest.raises(RuntimeError):
        guard_environment({**environment(), "BILLING_DATABASE_URL": url})


@pytest.mark.parametrize("value", ["", "main", "a" * 39, "G" * 40])
def test_source_identity_must_be_an_exact_sha(value):
    with pytest.raises(RuntimeError):
        guard_environment({**environment(), "SOURCE_SHA": value})


def test_missing_disposable_authorization_is_denied():
    env = environment()
    env.pop("TELNEXA_DISPOSABLE_CERTIFICATION")
    with pytest.raises(RuntimeError):
        guard_environment(env)
