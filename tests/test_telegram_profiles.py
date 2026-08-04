"""
Тесты TelegramProfile + ProfileService.
Используют SQLite in-memory (без PostgreSQL).
"""
from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import Shop
from services.profile_service import ProfileService

# Fixtures

@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """In-memory SQLite async session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def shop(db: AsyncSession) -> Shop:
    """Создаём тестовый магазин."""
    s = Shop(name="Test Shop", owner_tg_id=123456)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


@pytest_asyncio.fixture
async def svc(db: AsyncSession) -> ProfileService:
    return ProfileService(db)


# Тест 1: Создание профиля

@pytest.mark.asyncio
async def test_get_or_create_new_profile(svc: ProfileService, db: AsyncSession, shop: Shop):
    profile = await svc.get_or_create(
        shop_id=shop.id,
        telegram_id=111,
        username="alice",
        name="Алиса",
        phone="+998901234567",
    )
    await db.commit()

    assert profile.id is not None
    assert profile.telegram_id == 111
    assert profile.shop_id == shop.id
    assert profile.username == "alice"
    assert profile.name == "Алиса"
    assert profile.data_source == "local"
    assert profile.cached_orders_count == 0
    assert float(profile.cached_ltv) == 0.0
    assert profile.is_unsubscribed is False


# Тест 2: Уникальность telegram_id + shop_id

@pytest.mark.asyncio
async def test_get_or_create_returns_existing(svc: ProfileService, db: AsyncSession, shop: Shop):
    p1 = await svc.get_or_create(shop_id=shop.id, telegram_id=222)
    await db.commit()
    p2 = await svc.get_or_create(shop_id=shop.id, telegram_id=222)
    await db.commit()

    assert p1.id == p2.id


# Тест 3: update_after_order — mode=local, cached_* обновляются

@pytest.mark.asyncio
async def test_update_after_order_local(svc: ProfileService, db: AsyncSession, shop: Shop):
    profile = await svc.get_or_create(shop_id=shop.id, telegram_id=333)
    await db.commit()

    await svc.update_after_order(profile.id, order_amount=100_000)
    await db.commit()

    await db.refresh(profile)
    assert profile.cached_orders_count == 1
    assert float(profile.cached_ltv) == 100_000.0
    assert float(profile.cached_avg_check) == 100_000.0

    await svc.update_after_order(profile.id, order_amount=200_000)
    await db.commit()
    await db.refresh(profile)

    assert profile.cached_orders_count == 2
    assert float(profile.cached_ltv) == 300_000.0
    assert float(profile.cached_avg_check) == 150_000.0


# Тест 4: update_after_order — mode=billz, cached_* НЕ обновляются

@pytest.mark.asyncio
async def test_update_after_order_pos_skipped(svc: ProfileService, db: AsyncSession, shop: Shop):
    profile = await svc.get_or_create(shop_id=shop.id, telegram_id=444)
    profile.data_source = "billz"
    await db.commit()

    await svc.update_after_order(profile.id, order_amount=50_000)
    await db.commit()
    await db.refresh(profile)

    assert profile.cached_orders_count == 0  # не изменилось
    assert float(profile.cached_ltv) == 0.0


# Тест 5: update_from_pos — кешированные данные записываются

@pytest.mark.asyncio
async def test_update_from_pos(svc: ProfileService, db: AsyncSession, shop: Shop):
    profile = await svc.get_or_create(shop_id=shop.id, telegram_id=555)
    await db.commit()

    last_order_dt = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    await svc.update_from_pos(
        shop_id=shop.id,
        telegram_id=555,
        pos_data={
            "orders_count": 7,
            "ltv": 980_000,
            "avg_check": 140_000,
            "last_order": last_order_dt,
            "data_source": "billz",
        },
    )
    await db.commit()
    await db.refresh(profile)

    assert profile.cached_orders_count == 7
    assert float(profile.cached_ltv) == 980_000.0
    assert float(profile.cached_avg_check) == 140_000.0
    # SQLite возвращает naive datetime — сравниваем без timezone
    stored = profile.cached_last_order
    if stored is not None and stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert stored == last_order_dt
    assert profile.data_source == "billz"


# Тест 6: track_funnel — step и timestamp сохраняются

@pytest.mark.asyncio
async def test_track_funnel_step(svc: ProfileService, db: AsyncSession, shop: Shop):
    profile = await svc.get_or_create(shop_id=shop.id, telegram_id=666)
    await db.commit()

    await svc.track_funnel(profile.id, "cart")
    await db.commit()
    await db.refresh(profile)

    assert profile.last_funnel_step == "cart"
    assert profile.last_funnel_at is not None


@pytest.mark.asyncio
async def test_track_funnel_completed_resets_step(svc: ProfileService, db: AsyncSession, shop: Shop):
    profile = await svc.get_or_create(shop_id=shop.id, telegram_id=667)
    await db.commit()

    await svc.track_funnel(profile.id, "checkout")
    await db.commit()
    await svc.track_funnel(profile.id, "completed")
    await db.commit()
    await db.refresh(profile)

    assert profile.last_funnel_step is None
    assert profile.last_funnel_at is None


# Тест 7: update_rating — avg пересчитывается корректно

@pytest.mark.asyncio
async def test_update_rating_average(svc: ProfileService, db: AsyncSession, shop: Shop):
    profile = await svc.get_or_create(shop_id=shop.id, telegram_id=777)
    await db.commit()

    await svc.update_rating(profile.id, 5)
    await db.commit()
    await db.refresh(profile)
    assert float(profile.avg_rating) == 5.0
    assert profile.total_ratings == 1

    await svc.update_rating(profile.id, 3)
    await db.commit()
    await db.refresh(profile)
    assert float(profile.avg_rating) == 4.0
    assert profile.total_ratings == 2

    await svc.update_rating(profile.id, 4)
    await db.commit()
    await db.refresh(profile)
    # (5+3+4)/3 = 4.0
    assert float(profile.avg_rating) == 4.0
    assert profile.total_ratings == 3


@pytest.mark.asyncio
async def test_update_rating_invalid_raises(svc: ProfileService, db: AsyncSession, shop: Shop):
    profile = await svc.get_or_create(shop_id=shop.id, telegram_id=778)
    await db.commit()

    with pytest.raises(ValueError, match="от 1 до 5"):
        await svc.update_rating(profile.id, 6)

    with pytest.raises(ValueError, match="от 1 до 5"):
        await svc.update_rating(profile.id, 0)


# Тест 8: is_unsubscribed флаг

@pytest.mark.asyncio
async def test_unsubscribed_flag(svc: ProfileService, db: AsyncSession, shop: Shop):
    profile = await svc.get_or_create(shop_id=shop.id, telegram_id=888)
    await db.commit()

    assert profile.is_unsubscribed is False

    profile.is_unsubscribed = True
    await db.commit()
    await db.refresh(profile)

    assert profile.is_unsubscribed is True


# Тест 9: track_activity

@pytest.mark.asyncio
async def test_track_activity(svc: ProfileService, db: AsyncSession, shop: Shop):
    profile = await svc.get_or_create(shop_id=shop.id, telegram_id=999)
    await db.commit()
    await db.refresh(profile)

    # Фиксируем время ДО — после refresh SQLite возвращает naive datetime
    old_activity = profile.last_activity_date

    import asyncio
    await asyncio.sleep(0.05)

    await svc.track_activity(profile.id)
    await db.commit()
    await db.refresh(profile)

    new_activity = profile.last_activity_date
    # Нормализуем в naive для сравнения (SQLite strips timezone)
    if hasattr(old_activity, 'tzinfo') and old_activity.tzinfo is not None:
        old_activity = old_activity.replace(tzinfo=None)
    if hasattr(new_activity, 'tzinfo') and new_activity.tzinfo is not None:
        new_activity = new_activity.replace(tzinfo=None)

    assert new_activity >= old_activity
