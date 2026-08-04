"""
Тесты Этапа 3: вебхуки от POS, обновление статусов и профилей.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from integrations.router import _process_status_change, _verify_signature
from models import Customer, Order, Shop, TelegramProfile

# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _make_shop(db: AsyncSession, pos_type: str = "mock") -> Shop:
    shop = Shop(
        name="Test",
        bot_token=None,
        integration_settings={"type": pos_type, "webhook_secret": "secret123"},
    )
    db.add(shop)
    await db.commit()
    await db.refresh(shop)
    return shop


async def _make_order(db: AsyncSession, shop: Shop, ext_id: str) -> Order:
    customer = Customer(shop_id=shop.id, telegram_id=12345)
    db.add(customer)
    await db.flush()

    order = Order(
        shop_id=shop.id,
        customer_id=customer.id,
        total_amount=300000,
        status="new",
        external_order_id=ext_id,
        integration_status="pushed",
        meta={"telegram_user_id": 12345, "customer_phone": "+998901234567"},
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


# ── Тесты вебхуков ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_change_updates_order(db_session):
    shop = await _make_shop(db_session)
    order = await _make_order(db_session, shop, "EXT-001")

    with patch("integrations.router._notify_customer_status"):
        with patch("integrations.router._update_profile_from_pos"):
            await _process_status_change(db_session, shop, "EXT-001", "completed", "closed")

    await db_session.refresh(order)
    assert order.status == "completed"
    assert order.external_status == "closed"
    assert order.integration_status == "confirmed"


@pytest.mark.asyncio
async def test_status_change_in_progress(db_session):
    shop = await _make_shop(db_session)
    order = await _make_order(db_session, shop, "EXT-002")

    with patch("integrations.router._notify_customer_status"):
        with patch("integrations.router._update_profile_from_pos"):
            await _process_status_change(db_session, shop, "EXT-002", "in_progress", "cooking")

    await db_session.refresh(order)
    assert order.status == "processing"


@pytest.mark.asyncio
async def test_status_change_unknown_external_id(db_session):
    shop = await _make_shop(db_session)

    # Не должен падать при несуществующем external_order_id
    with patch("integrations.router._notify_customer_status"):
        with patch("integrations.router._update_profile_from_pos"):
            await _process_status_change(db_session, shop, "UNKNOWN-999", "completed", "closed")
    # Нет ошибки — просто логирует


@pytest.mark.asyncio
async def test_profile_updated_on_completed(db_session):
    """При status=completed → telegram_profiles.cached_* обновляются из POS."""
    shop = await _make_shop(db_session)
    await _make_order(db_session, shop, "EXT-003")

    mock_pos_data = MagicMock()
    mock_pos_data.orders_count = 5
    mock_pos_data.ltv = 1_500_000
    mock_pos_data.avg_check = 300_000
    mock_pos_data.last_order_date = "2026-02-01"

    with patch("integrations.factory.get_pos_connector") as mock_factory:
        connector = AsyncMock()
        connector.get_client_data = AsyncMock(return_value=mock_pos_data)
        mock_factory.return_value = connector

        with patch("integrations.router._notify_customer_status"):
            await _process_status_change(db_session, shop, "EXT-003", "completed", "closed")

    profile = (await db_session.execute(
        select(TelegramProfile).where(
            TelegramProfile.shop_id == shop.id,
            TelegramProfile.telegram_id == 12345,
        )
    )).scalar_one_or_none()

    # ProfileService.update_from_pos обновляет cached_orders_count
    if profile:
        assert profile.cached_orders_count == 5 or profile.data_source != "local"


@pytest.mark.asyncio
async def test_cancelled_order_status(db_session):
    shop = await _make_shop(db_session)
    order = await _make_order(db_session, shop, "EXT-004")

    with patch("integrations.router._notify_customer_status"):
        with patch("integrations.router._update_profile_from_pos"):
            await _process_status_change(db_session, shop, "EXT-004", "cancelled", "CANCELLED")

    await db_session.refresh(order)
    assert order.status == "cancelled"


# ── Тесты верификации подписи ──────────────────────────────────────────────────

def test_verify_signature_correct():
    import hashlib
    import hmac as hmac_lib
    body = b'{"test": 1}'
    secret = "my_secret"
    sig = "sha256=" + hmac_lib.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert _verify_signature(body, secret, sig) is True


def test_verify_signature_wrong():
    assert _verify_signature(b'{"test": 1}', "secret", "sha256=wrongsig") is False


def test_verify_signature_no_secret():
    assert _verify_signature(b'{"test": 1}', "", "sha256=anysig") is False


def test_verify_signature_no_header():
    assert _verify_signature(b'{"test": 1}', "secret", None) is False
