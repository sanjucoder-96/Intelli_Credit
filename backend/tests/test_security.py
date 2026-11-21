"""Phase-8 security — realistic IntelliCredit attack paths (priority #8, unit).

Focused, deterministic security checks (no DB): the role→permission matrix, JWT
type confusion, password-verify safety, storage path-traversal / illegal keys,
and trace/secret redaction. Tenant-isolation and upload-handler attacks that
need a live database live in ``test_security_isolation.py``.
"""

from __future__ import annotations

import pytest

from app.agents.trace import _REDACT_KEYS, _redact
from app.api.routers.cases import _ALLOWED_EXTENSIONS
from app.core.config import get_settings
from app.core.errors import UnauthorizedError, ValidationFailedError
from app.core.security import PasswordHasher, TokenService
from app.domain.enums import ROLE_PERMISSIONS, Permission, Role
from app.infrastructure.gemini import GeminiClient, GeminiUnconfiguredError
from app.infrastructure.storage.local import LocalFilesystemStorage

get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Role → permission separation (the authorization source of truth).
# --------------------------------------------------------------------------- #


def test_analyst_cannot_review_and_officer_cannot_write_or_run() -> None:
    analyst = ROLE_PERMISSIONS[Role.ANALYST]
    officer = ROLE_PERMISSIONS[Role.CREDIT_OFFICER]
    admin = ROLE_PERMISSIONS[Role.ADMIN]

    # Segregation of duties: the analyst prepares, the officer reviews.
    assert Permission.HITL_REVIEW not in analyst
    assert Permission.HITL_REVIEW in officer
    assert Permission.DOCUMENT_UPLOAD in analyst
    assert Permission.DOCUMENT_UPLOAD not in officer
    assert Permission.RUN_START not in officer
    assert Permission.CASE_WRITE not in officer

    # Only admin manages models / users / flags.
    for perm in (Permission.MODELS_MANAGE, Permission.USERS_MANAGE, Permission.FLAGS_MANAGE):
        assert perm not in analyst and perm not in officer and perm in admin
    assert admin == frozenset(Permission)  # admin holds every permission


def test_every_role_maps_to_a_permission_set() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)


# --------------------------------------------------------------------------- #
# JWT: type confusion + tamper resistance.
# --------------------------------------------------------------------------- #


def _tokens() -> TokenService:
    return TokenService(get_settings().auth)


def test_refresh_token_cannot_be_used_as_an_access_token() -> None:
    from uuid import uuid4

    svc = _tokens()
    refresh = svc.create_refresh_token(uuid4())
    with pytest.raises(UnauthorizedError):
        svc.decode(refresh, expected_type="access")


def test_garbage_and_tampered_tokens_are_rejected() -> None:
    svc = _tokens()
    for bad in ("", "not.a.jwt", "a.b.c", "eyJhbGciOiJub25lIn0.e30."):
        with pytest.raises(UnauthorizedError):
            svc.decode(bad, expected_type="access")


# --------------------------------------------------------------------------- #
# Password verification never leaks via exceptions.
# --------------------------------------------------------------------------- #


def test_password_verify_returns_false_never_raises() -> None:
    hasher = PasswordHasher()
    stored = hasher.hash("correct horse battery staple")
    assert hasher.verify(stored, "correct horse battery staple") is True
    assert hasher.verify(stored, "wrong") is False
    # A malformed stored hash must not raise (uniform failure — no enumeration).
    assert hasher.verify("not-a-valid-argon2-hash", "anything") is False


# --------------------------------------------------------------------------- #
# Storage: path traversal + illegal keys are refused.
# --------------------------------------------------------------------------- #


def test_storage_rejects_path_traversal_and_illegal_keys(tmp_path) -> None:  # noqa: ANN001
    store = LocalFilesystemStorage(tmp_path)
    # Traversal (`..`) and illegal characters (backslash, space, null) are refused.
    for evil in (
        "../etc/passwd",
        "cases/../../secret",
        "..\\windows",
        "a b/c",
        "cases/x/../../y",
        "cases/\x00/x",
    ):
        with pytest.raises(ValidationFailedError):
            store._path_for(evil)


async def test_storage_content_addresses_keys_ignoring_malicious_filenames(tmp_path) -> None:  # noqa: ANN001
    from uuid import uuid4

    store = LocalFilesystemStorage(tmp_path)
    # A hostile filename must not inject path separators into the stored key.
    blob = await store.put(case_id=uuid4(), filename="../../evil/passwd.pdf", data=b"%PDF-1.3\n")
    assert ".." not in blob.key
    assert blob.key.startswith("cases/")
    # The blob is retrievable by its safe, server-generated key.
    assert await store.get(blob.key) == b"%PDF-1.3\n"


def test_upload_extension_allowlist_excludes_executables() -> None:
    for safe in (".pdf", ".xlsx", ".csv", ".png", ".zip"):
        assert safe in _ALLOWED_EXTENSIONS
    for danger in (".exe", ".sh", ".js", ".dll", ".bat", ".php"):
        assert danger not in _ALLOWED_EXTENSIONS


# --------------------------------------------------------------------------- #
# Secret handling: redaction + no key in the unconfigured error.
# --------------------------------------------------------------------------- #


def test_trace_redaction_drops_secret_bearing_keys() -> None:
    assert {"prompt", "api_key", "password", "token"} <= _REDACT_KEYS
    payload = {"model": "gemini-flash", "api_key": "SECRET", "prompt": "hi", "tokens_in": 10}
    redacted = _redact(payload)
    assert "api_key" not in redacted and "prompt" not in redacted
    assert redacted["model"] == "gemini-flash" and redacted["tokens_in"] == 10


def test_unconfigured_gemini_error_names_the_env_var_not_a_key() -> None:
    from app.core.config import GeminiConfig

    client = GeminiClient(GeminiConfig(api_key=None))
    assert client.configured is False
    with pytest.raises(GeminiUnconfiguredError) as exc:
        client._ensure_client()
    # The error guides configuration by env-var NAME; it never echoes a secret value.
    message = str(exc.value)
    assert "IC_GEMINI__API_KEY" in message
    assert "SECRET" not in message
