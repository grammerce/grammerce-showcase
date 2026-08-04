"""
Тесты Этапа 2a: синхронизация каталога (CatalogSyncService).
Используют SQLite in-memory + MockPosAdapter.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from integrations.base import PosProduct
from integrations.sync_service import CatalogSyncService
from models import Category, Product, Shop

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


async def _create_shop(db: AsyncSession, pos_type: str = "mock") -> Shop:
    shop = Shop(
        name="Test",
        integration_settings={"type": pos_type, "auto_push_orders": True},
    )
    db.add(shop)
    await db.commit()
    await db.refresh(shop)
    return shop


def _mock_catalog(n: int = 5) -> list[PosProduct]:
    return [
        PosProduct(
            external_id=f"SKU-{i:03d}",
            name=f"Товар {i}",
            price=100000 * i,
            in_stock=True,
            stock_quantity=10,
            category_name="Тест",
        )
        for i in range(1, n + 1)
    ]


# ── Тесты CatalogSyncService ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_creates_products(db_session):
    shop = await _create_shop(db_session)
    mock_products = _mock_catalog(5)

    with patch("integrations.factory.get_pos_connector") as mock_factory:
        connector = AsyncMock()
        connector.sync_catalog = AsyncMock(return_value=mock_products)
        mock_factory.return_value = connector

        svc = CatalogSyncService(db_session)
        result = await svc.sync(shop.id)

    assert result["created"] == 5
    assert result["updated"] == 0
    assert result["synced"] == 5

    products = (await db_session.execute(
        select(Product).where(Product.shop_id == shop.id)
    )).scalars().all()
    assert len(products) == 5
    assert all(p.external_id is not None for p in products)


@pytest.mark.asyncio
async def test_sync_updates_existing_products(db_session):
    shop = await _create_shop(db_session)
    mock_products = _mock_catalog(3)

    with patch("integrations.factory.get_pos_connector") as mock_factory:
        connector = AsyncMock()
        connector.sync_catalog = AsyncMock(return_value=mock_products)
        mock_factory.return_value = connector

        svc = CatalogSyncService(db_session)
        await svc.sync(shop.id)

        # Меняем цену в POS и синхронизируем повторно
        mock_products[0] = PosProduct(
            external_id="SKU-001",
            name="Товар 1 НОВЫЙ",
            price=999000,
            in_stock=True,
            stock_quantity=5,
            category_name="Тест",
        )
        connector.sync_catalog = AsyncMock(return_value=mock_products)
        result = await svc.sync(shop.id)

    assert result["created"] == 0
    assert result["updated"] == 3

    updated = (await db_session.execute(
        select(Product).where(Product.external_id == "SKU-001")
    )).scalar_one_or_none()
    assert updated.name == "Товар 1 НОВЫЙ"
    assert int(updated.price) == 999000


@pytest.mark.asyncio
async def test_sync_deactivates_removed_products(db_session):
    shop = await _create_shop(db_session)
    original_catalog = _mock_catalog(5)

    with patch("integrations.factory.get_pos_connector") as mock_factory:
        connector = AsyncMock()
        connector.sync_catalog = AsyncMock(return_value=original_catalog)
        mock_factory.return_value = connector

        svc = CatalogSyncService(db_session)
        await svc.sync(shop.id)

        # Убираем 2 товара из POS
        reduced_catalog = _mock_catalog(3)
        connector.sync_catalog = AsyncMock(return_value=reduced_catalog)
        result = await svc.sync(shop.id)

    assert result["deactivated"] == 2

    inactive = (await db_session.execute(
        select(Product).where(Product.shop_id == shop.id, Product.is_active == False)
    )).scalars().all()
    assert len(inactive) == 2


@pytest.mark.asyncio
async def test_sync_does_not_touch_manual_products(db_session):
    """Товары без external_id (добавленные вручную) не деактивируются при sync."""
    shop = await _create_shop(db_session)

    # Создаём ручной товар (без external_id)
    manual = Product(
        shop_id=shop.id,
        name="Ручной товар",
        price=50000,
        is_active=True,
    )
    db_session.add(manual)
    await db_session.commit()

    with patch("integrations.factory.get_pos_connector") as mock_factory:
        connector = AsyncMock()
        connector.sync_catalog = AsyncMock(return_value=_mock_catalog(2))
        mock_factory.return_value = connector

        svc = CatalogSyncService(db_session)
        result = await svc.sync(shop.id)

    # Ручной товар должен остаться активным
    manual_after = (await db_session.execute(
        select(Product).where(Product.id == manual.id)
    )).scalar_one()
    assert manual_after.is_active is True


@pytest.mark.asyncio
async def test_sync_no_pos_returns_zeros(db_session):
    shop = await _create_shop(db_session, pos_type="none")
    svc = CatalogSyncService(db_session)
    result = await svc.sync(shop.id)
    assert result["synced"] == 0
    assert result["created"] == 0


@pytest.mark.asyncio
async def test_sync_pos_unavailable_returns_error(db_session):
    shop = await _create_shop(db_session)

    with patch("integrations.factory.get_pos_connector") as mock_factory:
        connector = AsyncMock()
        connector.sync_catalog = AsyncMock(side_effect=ConnectionError("POS unreachable"))
        mock_factory.return_value = connector

        svc = CatalogSyncService(db_session)
        result = await svc.sync(shop.id)

    assert result["synced"] == 0
    assert "error" in result
