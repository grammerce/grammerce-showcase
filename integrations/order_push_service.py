"""
OrderPushService — отправка заказов в POS-систему.

Логика:
1. Проверить что магазин имеет POS и auto_push_orders=true
2. Отправить заказ (только позиции с external_id — POS-товары)
3. При успехе: сохранить external_order_id, integration_status="pushed"
4. При ошибке: retry 3 раза с backoff, затем integration_status="failed"
5. Уведомить админа при финальном провале
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models import Order, OrderItem, Shop

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [10, 60, 300]  # секунды


class OrderPushService:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def on_order_created(self, order_id: int) -> None:
        """
        Хук — вызывается из create_order.
        Если POS подключён и auto_push_orders=true → ставит заказ в очередь.
        """
        order_result = await self.db.execute(
            select(Order).where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()
        if not order:
            return

        shop_result = await self.db.execute(select(Shop).where(Shop.id == order.shop_id))
        shop = shop_result.scalar_one_or_none()
        if not shop:
            return

        settings = shop.integration_settings or {"type": "none"}
        if settings.get("type") == "none":
            return
        if not settings.get("auto_push_orders", False):
            return

        order.integration_status = "pending"
        await self.db.commit()

        # Запуск пуша в фоне (не блокируем ответ клиенту)
        asyncio.create_task(self._push_with_retry(order_id))

    async def push_order(self, order_id: int) -> bool:
        """
        Одна попытка отправить заказ в POS.
        Возвращает True при успехе.
        """
        from database import async_session_factory
        from integrations.factory import get_pos_connector

        async with async_session_factory() as session:
            order_result = await session.execute(
                select(Order)
                .options(selectinload(Order.items).selectinload(OrderItem.product))
                .where(Order.id == order_id)
            )
            order = order_result.scalar_one_or_none()
            if not order:
                return False

            shop_result = await session.execute(select(Shop).where(Shop.id == order.shop_id))
            shop = shop_result.scalar_one_or_none()
            if not shop:
                return False

            connector = get_pos_connector(shop)
            if connector is None:
                order.integration_status = "none"
                await session.commit()
                return False

            # Собираем позиции — только товары с external_id (POS-товары)
            items = []
            for item in order.items:
                if item.product and item.product.external_id:
                    items.append({
                        "external_id": item.product.external_id,
                        "quantity": item.quantity,
                        "price": int(item.price_at_moment),
                    })

            if not items:
                log.info("Order #%s: no POS items, skipping push", order_id)
                order.integration_status = "none"
                await session.commit()
                return False

            meta = order.meta or {}
            result = await connector.push_order(
                order_id=order_id,
                items=items,
                total_amount=int(order.total_amount),
                discount_amount=int(order.discount or 0),
                customer_name=meta.get("customer_name"),
                customer_phone=meta.get("customer_phone"),
                payment_method=meta.get("payment_method", "cash"),
            )

            if result.success:
                order.external_order_id = result.external_order_id
                order.integration_status = "pushed"
                log.info("Order #%s pushed to POS: ext_id=%s", order_id, result.external_order_id)
            else:
                log.warning("Order #%s push failed: %s", order_id, result.error)
                return False

            await session.commit()
            return True

    async def _push_with_retry(self, order_id: int) -> None:
        """Отправить с retry-логикой. Вызывается в фоне."""
        from database import async_session_factory
        from integrations.factory import get_pos_connector

        for attempt in range(MAX_RETRIES):
            try:
                async with async_session_factory() as session:
                    order_result = await session.execute(
                        select(Order)
                        .options(selectinload(Order.items).selectinload(OrderItem.product))
                        .where(Order.id == order_id)
                    )
                    order = order_result.scalar_one_or_none()
                    if not order:
                        return

                    shop_result = await session.execute(select(Shop).where(Shop.id == order.shop_id))
                    shop = shop_result.scalar_one_or_none()
                    if not shop:
                        return

                    connector = get_pos_connector(shop)
                    if connector is None:
                        return

                    items = []
                    for item in order.items:
                        if item.product and item.product.external_id:
                            items.append({
                                "external_id": item.product.external_id,
                                "quantity": item.quantity,
                                "price": int(item.price_at_moment),
                            })

                    if not items:
                        order.integration_status = "none"
                        await session.commit()
                        return

                    meta = order.meta or {}
                    result = await connector.push_order(
                        order_id=order_id,
                        items=items,
                        total_amount=int(order.total_amount),
                        discount_amount=int(order.discount or 0),
                        customer_name=meta.get("customer_name"),
                        customer_phone=meta.get("customer_phone"),
                        payment_method=meta.get("payment_method", "cash"),
                    )

                    if result.success:
                        order.external_order_id = result.external_order_id
                        order.integration_status = "pushed"
                        await session.commit()
                        log.info("Order #%s pushed OK (attempt %d)", order_id, attempt + 1)
                        return

                    log.warning("Order #%s push attempt %d failed: %s", order_id, attempt + 1, result.error)

            except Exception as e:
                log.error("Order #%s push attempt %d exception: %r", order_id, attempt + 1, e)

            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])

        # Все попытки исчерпаны
        async with async_session_factory() as session:
            order_result = await session.execute(select(Order).where(Order.id == order_id))
            order = order_result.scalar_one_or_none()
            if order:
                order.integration_status = "failed"
                await session.commit()
                log.error("Order #%s: all %d push attempts failed → integration_status=failed", order_id, MAX_RETRIES)
                await self._notify_admin_push_failed(order)

    async def _notify_admin_push_failed(self, order: Order) -> None:
        """Уведомить владельца магазина о провале отправки в POS."""
        try:
            from aiogram import Bot

            from database import async_session_factory

            async with async_session_factory() as session:
                shop_result = await session.execute(select(Shop).where(Shop.id == order.shop_id))
                shop = shop_result.scalar_one_or_none()
                if not shop or not shop.bot_token or not shop.owner_tg_id:
                    return

                bot = Bot(token=shop.bot_token, parse_mode="HTML")
                try:
                    await bot.send_message(
                        shop.owner_tg_id,
                        f"⚠️ <b>Заказ #{order.id} не отправлен в POS</b>\n\n"
                        f"Все {MAX_RETRIES} попытки отправки завершились ошибкой.\n"
                        f"Проверьте подключение POS в настройках.",
                    )
                finally:
                    await bot.session.close()
        except Exception as e:
            log.error("Failed to notify admin about push failure for order #%s: %r", order.id, e)
