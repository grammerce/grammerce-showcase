"""
ShopContextMiddleware - Injects shop_id into handler data.

shop_id and bot_token are passed directly by BotManager._poll_bot()
via dp.feed_update(..., shop_id=..., bot_token=...).
This middleware validates that shop_id was injected and aborts if not.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

log = logging.getLogger(__name__)


class ShopContextMiddleware(BaseMiddleware):
    """
    Middleware that ensures shop_id is always available in handler data.

    shop_id is injected by BotManager._poll_bot() via feed_update().
    If shop_id is missing — the update is dropped with an error log.
    This prevents multi-tenant cross-contamination (wrong shop receiving messages).

    Usage in handlers:
        @router.message(...)
        async def handler(message: Message, shop_id: int):
            # shop_id is automatically available
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if "shop_id" not in data:
            log.error(
                "shop_id missing from handler data — BotManager did not inject it. "
                "Update dropped to prevent multi-tenant data leak. event=%r",
                type(event).__name__,
            )
            return None  # drop the update, do not call handler
        data.setdefault("bot_token", "")
        return await handler(event, data)
