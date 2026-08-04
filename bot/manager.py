"""
BotManager - Multi-Bot Management for Multi-Tenant SaaS

This module manages multiple Telegram bots, one per shop.
Each bot runs in its own polling loop with isolated state.

Uses a single shared Dispatcher to avoid router attachment conflicts
(aiogram routers can only be attached to one Dispatcher at a time).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress

from aiogram import Bot, Dispatcher
from sqlalchemy import select

from bot.config import async_session_factory
from bot.handlers import (
    admin_router,
    contact_router,
    feedback_router,
    menu_router,
    promo_router,
    registration_router,
)
from bot.middlewares.shop_context import ShopContextMiddleware
from models import Shop

log = logging.getLogger(__name__)


class BotManager:
    """
    Manages multiple Telegram bots for multi-tenant shops.

    Each shop has its own:
    - Bot instance (with unique token)
    - Polling task (running concurrently)

    A single shared Dispatcher handles all bots to avoid
    router attachment conflicts (aiogram routers can only
    be attached to one Dispatcher at a time).

    Usage:
        await BotManager.start_all_bots()  # On app startup
        await BotManager.stop_all_bots()   # On app shutdown
        await BotManager.restart_bot(shop_id)  # On token change
    """

    # Shared dispatcher — created once, all bots use it
    _dp: Dispatcher | None = None

    # Dict[shop_id, Tuple[Bot, asyncio.Task]]
    _instances: dict[int, tuple[Bot, asyncio.Task]] = {}

    # Concurrency limiter for update processing (prevents task explosion at 100+ bots)
    _update_semaphore: asyncio.Semaphore = asyncio.Semaphore(200)

    @classmethod
    def _get_or_create_dispatcher(cls) -> Dispatcher:
        """Get or create the shared dispatcher (singleton)."""
        if cls._dp is None:
            # Use RedisStorage if REDIS_URL is available, fallback to MemoryStorage
            redis_url = os.getenv("REDIS_URL")
            if redis_url:
                from aiogram.fsm.storage.redis import RedisStorage
                storage = RedisStorage.from_url(redis_url)
                log.info("FSM storage: Redis (%s)", redis_url.split("@")[-1] if "@" in redis_url else redis_url)
            else:
                from aiogram.fsm.storage.memory import MemoryStorage
                storage = MemoryStorage()
                log.warning("FSM storage: MemoryStorage (no REDIS_URL set)")
            dp = Dispatcher(storage=storage)

            # token_shop_map: token → shop_id, used by ShopContextMiddleware
            dp["token_shop_map"] = {}

            # Register middleware
            dp.message.middleware(ShopContextMiddleware())
            dp.callback_query.middleware(ShopContextMiddleware())

            # Register all routers ONCE — this is why we use a single Dispatcher
            # Guard: skip routers already attached (e.g. if _dp was reset externally)
            # feedback_router обязателен: без него колбэки оценок заказов (rate:*) мертвы.
            for router in [registration_router, promo_router, contact_router, admin_router, feedback_router, menu_router]:
                if router.parent_router is None:
                    dp.include_router(router)

            cls._dp = dp

        return cls._dp

    @classmethod
    async def start_all_bots(cls) -> int:
        """
        Start polling for all active shops in the database.

        Returns:
            Number of bots started successfully
        """
        started = 0

        async with async_session_factory() as session:
            result = await session.execute(
                select(Shop).where(
                    Shop.is_active == True,
                    Shop.bot_token.isnot(None),
                )
            )
            shops = result.scalars().all()

        # Start all bots in parallel for fast startup (100 bots: ~2-3s instead of ~20s)
        results = await asyncio.gather(
            *[cls.start_bot(shop.id, shop.bot_token) for shop in shops],
            return_exceptions=True,
        )
        started = sum(1 for r in results if r is True)

        log.info("Started %d/%d bots (parallel)", started, len(shops))
        return started

    @classmethod
    async def start_bot(cls, shop_id: int, bot_token: str) -> bool:
        """
        Start a bot for a specific shop.

        Args:
            shop_id: Database ID of the shop
            bot_token: Telegram bot token

        Returns:
            True if started successfully
        """
        # Stop existing instance if running
        if shop_id in cls._instances:
            log.info("Stopping existing bot for shop %s before restart", shop_id)
            await cls.stop_bot(shop_id)

        try:
            dp = cls._get_or_create_dispatcher()

            # Create bot instance
            bot = Bot(token=bot_token, parse_mode="HTML")

            # Register token → shop_id mapping for ShopContextMiddleware
            dp["token_shop_map"][bot_token] = shop_id

            # Start custom polling loop in background task
            task = asyncio.create_task(
                cls._poll_bot(bot=bot, shop_id=shop_id),
                name=f"bot_polling_shop_{shop_id}"
            )

            cls._instances[shop_id] = (bot, task)
            log.info("Bot for shop %s started successfully", shop_id)
            return True

        except Exception as e:
            log.error("Failed to start bot for shop %s: %r", shop_id, e)
            return False

    @classmethod
    async def _process_update(cls, dp: Dispatcher, bot: Bot, update, shop_id: int) -> None:
        """Process a single update with concurrency control."""
        async with cls._update_semaphore:
            await dp.feed_update(bot=bot, update=update, shop_id=shop_id, bot_token=bot.token)

    @classmethod
    async def _poll_bot(cls, bot: Bot, shop_id: int) -> None:
        """
        Custom polling loop for a single bot.
        Uses the shared Dispatcher to process updates.
        """
        dp = cls._get_or_create_dispatcher()
        offset: int | None = None
        allowed_updates = dp.resolve_used_update_types()

        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            log.warning("Failed to delete webhook for shop %s: %r", shop_id, e)

        log.info("Polling loop started for shop %s", shop_id)

        while True:
            try:
                updates = await bot.get_updates(
                    offset=offset,
                    timeout=30,
                    allowed_updates=allowed_updates,
                )
                for update in updates:
                    if offset is None or update.update_id >= offset:
                        offset = update.update_id + 1
                    # Semaphore limits concurrent update processing across all bots
                    asyncio.create_task(cls._process_update(dp, bot, update, shop_id))

            except asyncio.CancelledError:
                log.info("Polling cancelled for shop %s", shop_id)
                break
            except Exception as e:
                log.error("Polling error for shop %s: %r", shop_id, e)
                await asyncio.sleep(3)

    @classmethod
    async def stop_bot(cls, shop_id: int) -> bool:
        """
        Stop a specific shop's bot.

        Args:
            shop_id: Database ID of the shop

        Returns:
            True if stopped successfully
        """
        if shop_id not in cls._instances:
            log.warning("No bot instance for shop %s to stop", shop_id)
            return False

        bot, task = cls._instances.pop(shop_id)

        # Remove token from mapping
        if cls._dp and bot.token in cls._dp["token_shop_map"]:
            del cls._dp["token_shop_map"][bot.token]

        try:
            # Cancel polling task
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

            # Close bot session
            await bot.session.close()

            log.info("Bot for shop %s stopped successfully", shop_id)
            return True

        except Exception as e:
            log.error("Error stopping bot for shop %s: %r", shop_id, e)
            return False

    @classmethod
    def get_bot(cls, shop_id: int) -> Bot | None:
        """Get the running Bot instance for a shop (for sending notifications without creating new sessions)."""
        entry = cls._instances.get(shop_id)
        return entry[0] if entry else None

    @classmethod
    async def stop_all_bots(cls) -> int:
        """
        Stop all running bots.

        Returns:
            Number of bots stopped
        """
        stopped = 0
        shop_ids = list(cls._instances.keys())

        for shop_id in shop_ids:
            if await cls.stop_bot(shop_id):
                stopped += 1

        log.info("Stopped %d bots", stopped)
        return stopped

    @classmethod
    async def restart_bot(cls, shop_id: int) -> bool:
        """
        Restart a bot with fresh token from database.

        Use this after token update in Cabinet.

        Args:
            shop_id: Database ID of the shop

        Returns:
            True if restarted successfully
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(Shop).where(Shop.id == shop_id)
            )
            shop = result.scalar_one_or_none()

        if not shop:
            log.error("Shop %s not found for restart", shop_id)
            return False

        if not shop.bot_token:
            log.error("Shop %s has no bot_token for restart", shop_id)
            return False

        return await cls.start_bot(shop_id, shop.bot_token)

    @classmethod
    def is_running(cls, shop_id: int) -> bool:
        """Check if a bot is running for a shop."""
        return shop_id in cls._instances

    @classmethod
    def get_running_count(cls) -> int:
        """Get number of running bots."""
        return len(cls._instances)
