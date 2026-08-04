"""
ProfileService — управление TelegramProfile.

Ответственности:
- get_or_create: upsert-профиль при первом контакте
- update_after_order: обновить cached_* (только в режиме 'local')
- update_from_pos: записать данные из POS-системы
- track_funnel: обновить воронку
- update_rating: пересчитать avg_rating
- track_activity: обновить last_activity_date
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import TelegramProfile


class ProfileService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # get_or_create

    async def get_or_create(
        self,
        shop_id: int,
        telegram_id: int,
        *,
        username: str | None = None,
        name: str | None = None,
        phone: str | None = None,
    ) -> TelegramProfile:
        """
        Вернуть существующий профиль или создать новый.
        Обновляет username/name/phone если они изменились.
        """
        result = await self._db.execute(
            select(TelegramProfile).where(
                TelegramProfile.shop_id == shop_id,
                TelegramProfile.telegram_id == telegram_id,
            )
        )
        profile = result.scalar_one_or_none()

        if profile is None:
            profile = TelegramProfile(
                shop_id=shop_id,
                telegram_id=telegram_id,
                username=username,
                name=name,
                phone=phone,
                first_contact_date=datetime.now(UTC),
                last_activity_date=datetime.now(UTC),
            )
            self._db.add(profile)
            await self._db.flush()
        else:
            # Обновляем контактные данные, если переданы
            changed = False
            if username is not None and profile.username != username:
                profile.username = username
                changed = True
            if name is not None and profile.name is None:
                profile.name = name
                changed = True
            if phone is not None and profile.phone is None:
                profile.phone = phone
                changed = True
            if changed:
                profile.updated_at = datetime.now(UTC)

        return profile

    # update_after_order

    async def update_after_order(
        self,
        profile_id: int,
        order_amount: float,
        order_date: datetime | None = None,
    ) -> None:
        """
        Пересчитать cached_orders_count, cached_ltv, cached_avg_check, cached_last_order.
        Выполняется ТОЛЬКО для data_source='local'.
        Если POS подключён (data_source != 'local') — пропускаем (POS обновит сам).
        """
        result = await self._db.execute(
            select(TelegramProfile).where(TelegramProfile.id == profile_id)
        )
        profile = result.scalar_one_or_none()
        if profile is None or profile.data_source != "local":
            return

        profile.cached_orders_count += 1
        profile.cached_ltv = float(profile.cached_ltv or 0) + order_amount
        profile.cached_avg_check = profile.cached_ltv / profile.cached_orders_count
        profile.cached_last_order = order_date or datetime.now(UTC)
        profile.last_activity_date = datetime.now(UTC)
        profile.updated_at = datetime.now(UTC)

    # update_from_pos

    async def update_from_pos(
        self,
        shop_id: int,
        telegram_id: int,
        pos_data: dict,
    ) -> None:
        """
        Записать кешированные данные, пришедшие из POS-системы.
        pos_data ожидает ключи: orders_count, ltv, avg_check, last_order, data_source.
        """
        result = await self._db.execute(
            select(TelegramProfile).where(
                TelegramProfile.shop_id == shop_id,
                TelegramProfile.telegram_id == telegram_id,
            )
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            return

        if "orders_count" in pos_data:
            profile.cached_orders_count = int(pos_data["orders_count"])
        if "ltv" in pos_data:
            profile.cached_ltv = float(pos_data["ltv"])
        if "avg_check" in pos_data:
            profile.cached_avg_check = float(pos_data["avg_check"])
        if "last_order" in pos_data and pos_data["last_order"]:
            lo = pos_data["last_order"]
            profile.cached_last_order = lo if isinstance(lo, datetime) else datetime.fromisoformat(lo)
        if "data_source" in pos_data:
            profile.data_source = pos_data["data_source"]

        profile.updated_at = datetime.now(UTC)

    # track_funnel

    async def track_funnel(self, profile_id: int, step: str) -> None:
        """
        Записать текущий шаг воронки: catalog / product_view / cart / checkout / payment / completed.
        Если step='completed' — сбрасывает last_funnel_step (заказ оформлен).
        """
        result = await self._db.execute(
            select(TelegramProfile).where(TelegramProfile.id == profile_id)
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            return

        now = datetime.now(UTC)
        if step == "completed":
            profile.last_funnel_step = None
            profile.last_funnel_at = None
        else:
            profile.last_funnel_step = step
            profile.last_funnel_at = now

        profile.last_activity_date = now
        profile.updated_at = now

    # update_rating

    async def update_rating(self, profile_id: int, rating: int) -> None:
        """
        Добавить оценку (1–5) и пересчитать avg_rating.
        """
        if rating < 1 or rating > 5:
            raise ValueError(f"Рейтинг должен быть от 1 до 5, получено: {rating}")

        result = await self._db.execute(
            select(TelegramProfile).where(TelegramProfile.id == profile_id)
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            return

        old_avg = float(profile.avg_rating or 0)
        old_count = profile.total_ratings

        new_count = old_count + 1
        new_avg = (old_avg * old_count + rating) / new_count

        profile.avg_rating = round(new_avg, 2)
        profile.total_ratings = new_count
        profile.updated_at = datetime.now(UTC)

    # track_activity

    async def track_activity(self, profile_id: int) -> None:
        """Обновить last_activity_date."""
        result = await self._db.execute(
            select(TelegramProfile).where(TelegramProfile.id == profile_id)
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            return

        now = datetime.now(UTC)
        profile.last_activity_date = now
        profile.updated_at = now
