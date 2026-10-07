"""SPEC section 11.9: a password or token must never reach a log or a diagnostic.

All credentials in this file are invented. The values are chosen to be
recognisable so that a test can assert they did not survive - if you see one of
these strings in an assertion failure, the redaction layer has a hole.
"""

from __future__ import annotations

import logging

import pytest

from pypackhub.core.secrets import (
    REDACTED,
    CredentialStore,
    CredentialStoreError,
    RedactingFilter,
    redact,
    redact_mapping,
    redact_url,
)

pytestmark = pytest.mark.security

PASSWORD = "sup3r-s3cret-password"
QUERY_TOKEN = "abc123def456ghi789"
JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
PYPI_TOKEN = "pypi-AgEIcHlwaS5vcmcCJDY4NzBjOGE4LWU5ZGEtNDlhNy1hZmExLTRlY2E3ZmI5ZGVhZA"

USERINFO_URL = f"https://ci-user:{PASSWORD}@mirror.example/simple"
TOKEN_URL = f"https://api.example/v1/packages?token={QUERY_TOKEN}&page=2"
AUTH_HEADER = f"Authorization: Bearer {JWT}"

SECRETS_IN_TEXT = [
    (USERINFO_URL, PASSWORD),
    (TOKEN_URL, QUERY_TOKEN),
    (AUTH_HEADER, JWT),
    (f"export PYPI_TOKEN={PYPI_TOKEN}", PYPI_TOKEN),
    ("password=correct-horse-battery-staple", "correct-horse-battery-staple"),
    ("api-key: 0f8fad5b-d9cb-469f-a165-70867728950e", "0f8fad5b-d9cb-469f-a165-70867728950e"),
    ("pip install --index-url https://bare-token@mirror.example/simple", "bare-token"),
]


@pytest.mark.parametrize("text,secret", SECRETS_IN_TEXT)
def test_secrets_do_not_survive_redaction(text: str, secret: str) -> None:
    assert secret not in redact(text)


def test_ordinary_text_is_left_alone() -> None:
    text = "https://pypi.org/simple/ requests==2.31.0 installed in 3 seconds"

    assert redact(text) == text


def test_non_secret_query_values_survive() -> None:
    assert "page=2" in redact(TOKEN_URL)


def test_url_redaction_keeps_the_host_and_the_path() -> None:
    cleaned = redact_url(USERINFO_URL)

    assert "mirror.example" in cleaned
    assert "/simple" in cleaned
    assert PASSWORD not in cleaned
    assert "ci-user" not in cleaned


def test_redaction_is_idempotent() -> None:
    once = redact(USERINFO_URL)

    assert redact(once) == once


def test_redact_mapping_hides_secret_keys() -> None:
    cleaned = redact_mapping(
        {
            "index_url": USERINFO_URL,
            "password": "hunter2",
            "theme": "dark",
        }
    )

    assert cleaned["password"] == REDACTED
    assert PASSWORD not in cleaned["index_url"]
    assert cleaned["theme"] == "dark"


def test_the_log_filter_cleans_records_before_they_are_formatted() -> None:
    logger = logging.getLogger("pypackhub.tests.redaction")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.filters.clear()
    logger.addFilter(RedactingFilter())

    captured: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
            captured.append(record.getMessage())

    handler = Capture()
    logger.addHandler(handler)

    try:
        logger.info("fetching %s", USERINFO_URL)
        logger.info("using token %s", PYPI_TOKEN)
        logger.info("header was %s", AUTH_HEADER)
        logger.info("mapping %(url)s failed", {"url": TOKEN_URL})
    finally:
        logger.removeHandler(handler)

    joined = "\n".join(captured)

    assert PASSWORD not in joined
    assert PYPI_TOKEN not in joined
    assert JWT not in joined
    assert QUERY_TOKEN not in joined
    assert "mirror.example" in joined
    assert len(captured) == 4


class FakeKeyring:
    """A keychain that lives in a dictionary and can be told to misbehave."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.failure: str | None = None

    def _maybe_fail(self) -> None:
        if self.failure is not None:
            raise RuntimeError(self.failure)

    def get_password(self, service: str, key: str) -> str | None:
        self._maybe_fail()
        return self.values.get((service, key))

    def set_password(self, service: str, key: str, secret: str) -> None:
        self._maybe_fail()
        self.values[(service, key)] = secret

    def delete_password(self, service: str, key: str) -> None:
        self._maybe_fail()
        self.values.pop((service, key), None)


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    fake = FakeKeyring()
    monkeypatch.setattr(CredentialStore, "_keyring", staticmethod(lambda: fake))

    return fake


def test_credentials_round_trip_through_the_keychain(keychain: FakeKeyring) -> None:
    store = CredentialStore()

    store.set("mirror.example", PASSWORD)

    assert store.get("mirror.example") == PASSWORD
    assert keychain.values[("pypackhub", "mirror.example")] == PASSWORD

    store.delete("mirror.example")

    assert store.get("mirror.example") is None


def test_deleting_a_missing_credential_is_not_an_error(keychain: FakeKeyring) -> None:
    CredentialStore().delete("never-stored")


def test_keychain_errors_are_wrapped_and_redacted(keychain: FakeKeyring) -> None:
    keychain.failure = f"backend refused {USERINFO_URL}"

    with pytest.raises(CredentialStoreError) as info:
        CredentialStore().get("mirror.example")

    assert PASSWORD not in str(info.value)
    assert "mirror.example" in str(info.value)


def test_the_store_never_renders_a_secret() -> None:
    assert PASSWORD not in repr(CredentialStore())
