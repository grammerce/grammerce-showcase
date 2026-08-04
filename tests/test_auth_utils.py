"""
Тесты для auth_utils.py:
- hash_password / verify_password (bcrypt)
- verify_password с устаревшим SHA256 (миграция)
- token store / retrieve / TTL / delete
- require_superadmin — 403 для non-superadmin, 200 для superadmin
"""
from __future__ import annotations

import hashlib
import time
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

# hash_password / verify_password

def test_hash_password_returns_bcrypt_hash():
    from auth_utils import hash_password
    h = hash_password("mypassword")
    assert h.startswith("$2b$") or h.startswith("$2a$")


def test_verify_password_correct():
    from auth_utils import hash_password, verify_password
    h = hash_password("secret123")
    assert verify_password("secret123", h) is True


def test_verify_password_wrong():
    from auth_utils import hash_password, verify_password
    h = hash_password("secret123")
    assert verify_password("wrong", h) is False


def test_verify_password_empty_hash():
    from auth_utils import verify_password
    assert verify_password("any", "") is False


TEST_LEGACY_SALT = "test-legacy-pepper"


def test_verify_password_legacy_sha256(monkeypatch):
    """Пароли из старой системы (SHA256 + общий pepper) должны проходить проверку."""
    import auth_utils

    monkeypatch.setattr(auth_utils, "_LEGACY_SALT", TEST_LEGACY_SALT)
    password = "oldpassword"
    legacy_hash = hashlib.sha256(f"{password}{TEST_LEGACY_SALT}".encode()).hexdigest()
    assert len(legacy_hash) == 64
    assert auth_utils.verify_password(password, legacy_hash) is True


def test_verify_password_legacy_sha256_wrong_password(monkeypatch):
    import auth_utils

    monkeypatch.setattr(auth_utils, "_LEGACY_SALT", TEST_LEGACY_SALT)
    legacy_hash = hashlib.sha256(f"correct{TEST_LEGACY_SALT}".encode()).hexdigest()
    assert auth_utils.verify_password("wrong", legacy_hash) is False


def test_verify_password_legacy_rejected_without_salt(monkeypatch):
    """Без LEGACY_PASSWORD_SALT устаревшие хэши не принимаются (fail-closed).

    Значение — общий для всех пользователей pepper, а не соль: зная его, из
    утёкших хэшей пароли перебираются офлайн. Поэтому оно живёт только
    в окружении, а при его отсутствии старый хэш не проходит проверку.
    """
    import auth_utils

    monkeypatch.setattr(auth_utils, "_LEGACY_SALT", "")
    legacy_hash = hashlib.sha256(f"oldpassword{TEST_LEGACY_SALT}".encode()).hexdigest()
    assert auth_utils.verify_password("oldpassword", legacy_hash) is False


# Token store / retrieve / delete

def test_store_and_retrieve_token():
    from auth_utils import delete_token, get_current_user_from_token, store_token
    token = "test-token-abc"
    user = {"user_id": 42, "email": "test@example.com", "role": "owner", "shop_id": 1}
    store_token(token, user)
    result = get_current_user_from_token(token)
    assert result is not None
    assert result["user_id"] == 42
    assert result["email"] == "test@example.com"
    assert "_expires_at" not in result
    delete_token(token)  # cleanup


def test_get_token_not_found():
    from auth_utils import get_current_user_from_token
    assert get_current_user_from_token("nonexistent-token-xyz") is None


def test_delete_token():
    from auth_utils import delete_token, get_current_user_from_token, store_token
    token = "delete-me-token"
    store_token(token, {"user_id": 1})
    delete_token(token)
    assert get_current_user_from_token(token) is None


def test_token_expires(monkeypatch):
    """Истёкший токен должен возвращать None и удаляться из хранилища."""
    from auth_utils import _AUTH_TOKENS, get_current_user_from_token, store_token
    token = "expiring-token"
    store_token(token, {"user_id": 99})
    # Симулируем истечение TTL, подменяя expires_at на прошлое
    _AUTH_TOKENS[token]["_expires_at"] = time.time() - 1
    result = get_current_user_from_token(token)
    assert result is None
    assert token not in _AUTH_TOKENS


def test_token_sliding_ttl_extends_on_read():
    """Sliding TTL: чтение активного токена продлевает его срок жизни (in-memory ветка)."""
    from auth_utils import (
        _AUTH_TOKENS,
        TOKEN_TTL,
        delete_token,
        get_current_user_from_token,
        store_token,
    )
    token = "sliding-token"
    store_token(token, {"user_id": 7})
    # Искусственно "состарим" токен — до истечения остаётся 10 секунд
    near_expiry = time.time() + 10
    _AUTH_TOKENS[token]["_expires_at"] = near_expiry
    result = get_current_user_from_token(token)
    assert result is not None
    # После чтения срок должен быть продлён примерно на полный TOKEN_TTL
    assert _AUTH_TOKENS[token]["_expires_at"] > near_expiry
    assert _AUTH_TOKENS[token]["_expires_at"] >= time.time() + TOKEN_TTL - 5
    delete_token(token)


# require_superadmin

def _make_request(bearer_token: str | None = None) -> MagicMock:
    """Создать mock-Request с указанным Bearer-токеном."""
    request = MagicMock()
    if bearer_token:
        request.headers.get = lambda key, default="": (
            f"Bearer {bearer_token}" if key == "Authorization" else default
        )
    else:
        request.headers.get = lambda key, default="": default
    return request


@pytest.mark.asyncio
async def test_require_superadmin_raises_403_for_regular_owner():
    from auth_utils import delete_token, require_superadmin, store_token
    token = "owner-token-test"
    store_token(token, {"user_id": 1, "role": "owner", "shop_id": 1, "is_superadmin": False})
    request = _make_request(bearer_token=token)
    with pytest.raises(HTTPException) as exc_info:
        await require_superadmin(request=request, credentials=None)
    assert exc_info.value.status_code == 403
    delete_token(token)


@pytest.mark.asyncio
async def test_require_superadmin_allows_superadmin():
    from auth_utils import delete_token, require_superadmin, store_token
    token = "superadmin-token-test"
    store_token(token, {"user_id": 0, "role": "admin", "is_superadmin": True, "shop_id": 1})
    request = _make_request(bearer_token=token)
    result = await require_superadmin(request=request, credentials=None)
    assert result["is_superadmin"] is True
    delete_token(token)


@pytest.mark.asyncio
async def test_require_superadmin_raises_401_for_no_token():
    from auth_utils import require_superadmin
    request = _make_request(bearer_token=None)
    with pytest.raises(HTTPException) as exc_info:
        await require_superadmin(request=request, credentials=None)
    assert exc_info.value.status_code == 401
