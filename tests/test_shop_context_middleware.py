"""
Тесты для ShopContextMiddleware:
- При наличии shop_id в data — handler вызывается
- При отсутствии shop_id — update дропается (handler НЕ вызывается, возвращается None)
- bot_token имеет дефолт "" если не передан
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.middlewares.shop_context import ShopContextMiddleware


@pytest.fixture
def middleware():
    return ShopContextMiddleware()


@pytest.fixture
def mock_event():
    return MagicMock()


@pytest.mark.asyncio
async def test_handler_called_when_shop_id_present(middleware, mock_event):
    """Если shop_id инжектирован — handler вызывается нормально."""
    handler = AsyncMock(return_value="ok")
    data = {"shop_id": 5, "bot_token": "token123"}
    result = await middleware(handler, mock_event, data)
    handler.assert_awaited_once_with(mock_event, data)
    assert result == "ok"


@pytest.mark.asyncio
async def test_update_dropped_when_shop_id_missing(middleware, mock_event):
    """Если shop_id отсутствует — handler НЕ вызывается, возвращается None."""
    handler = AsyncMock(return_value="ok")
    data = {}  # нет shop_id
    result = await middleware(handler, mock_event, data)
    handler.assert_not_awaited()
    assert result is None


@pytest.mark.asyncio
async def test_bot_token_defaults_to_empty_string(middleware, mock_event):
    """bot_token по умолчанию = '' если не передан."""
    handler = AsyncMock(return_value=None)
    data = {"shop_id": 1}
    await middleware(handler, mock_event, data)
    assert data["bot_token"] == ""


@pytest.mark.asyncio
async def test_bot_token_not_overwritten_if_present(middleware, mock_event):
    """bot_token не перезаписывается если уже задан."""
    handler = AsyncMock(return_value=None)
    data = {"shop_id": 1, "bot_token": "mytoken"}
    await middleware(handler, mock_event, data)
    assert data["bot_token"] == "mytoken"


@pytest.mark.asyncio
async def test_shop_id_zero_is_treated_as_present(middleware, mock_event):
    """shop_id=0 технически присутствует в data — handler должен вызваться."""
    handler = AsyncMock(return_value="called")
    data = {"shop_id": 0}
    result = await middleware(handler, mock_event, data)
    handler.assert_awaited_once()
    assert result == "called"
