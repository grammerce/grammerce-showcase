"""
Feedback Handler — обработка оценок заказов через inline-кнопки.

Callback format: rate:{order_id}:{rating}
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from bot.config import async_session_factory
from models import Order
from services.profile_service import ProfileService

log = logging.getLogger(__name__)
router = Router(name="feedback")


@router.callback_query(F.data.startswith("rate:"))
async def handle_rating(callback: CallbackQuery, shop_id: int = 1) -> None:
    """Обработать оценку заказа от клиента."""
    try:
        _, order_id_str, rating_str = callback.data.split(":")
        order_id = int(order_id_str)
        rating = int(rating_str)
    except (ValueError, AttributeError):
        await callback.answer("Ошибка данных", show_alert=True)
        return

    if rating < 1 or rating > 5:
        await callback.answer("Неверная оценка", show_alert=True)
        return

    async with async_session_factory() as db:
        # Найти заказ
        order_result = await db.execute(
            select(Order).where(
                Order.id == order_id,
                Order.shop_id == shop_id,
            )
        )
        order = order_result.scalar_one_or_none()
        if not order:
            await callback.answer("Заказ не найден", show_alert=True)
            return

        # Сохранить оценку в meta заказа
        meta = dict(order.meta or {})
        if meta.get("rating"):
            await callback.answer(f"Вы уже оценили этот заказ {'⭐' * meta['rating']}!")
            return

        meta["rating"] = rating
        order.meta = meta

        # Обновить avg_rating в telegram_profiles
        tg_id = callback.from_user.id
        svc = ProfileService(db)
        profile = await svc.get_or_create(shop_id=shop_id, telegram_id=tg_id)
        await svc.update_rating(profile.id, rating)

        # Уведомить админа о низкой оценке
        if rating <= 2:
            from sqlalchemy import select as sel

            from models import Shop
            shop_result = await db.execute(sel(Shop).where(Shop.id == shop_id))
            shop = shop_result.scalar_one_or_none()
            if shop and shop.owner_tg_id:
                # Переиспользуем уже запущенный бот магазина (не плодим сессии).
                # Временный Bot создаём только если бот сейчас не запущен.
                from bot.manager import BotManager
                admin_bot = BotManager.get_bot(shop_id)
                created_temp = False
                if admin_bot is None and shop.bot_token:
                    from aiogram import Bot
                    admin_bot = Bot(token=shop.bot_token, parse_mode="HTML")
                    created_temp = True
                if admin_bot is not None:
                    try:
                        await admin_bot.send_message(
                            shop.owner_tg_id,
                            f"⚠️ <b>Низкая оценка!</b>\n\n"
                            f"Заказ #{order_id}: {'⭐' * rating} ({rating}/5)\n"
                            f"Клиент: @{callback.from_user.username or callback.from_user.id}",
                        )
                    except Exception as e:
                        log.warning("Failed to notify admin about low rating: %r", e)
                    finally:
                        if created_temp:
                            await admin_bot.session.close()

        await db.commit()

    await callback.answer(f"Спасибо за оценку {'⭐' * rating}!")
    await callback.message.edit_text(
        f"✅ Ваша оценка заказа #{order_id}: {'⭐' * rating}\nСпасибо!"
    )
