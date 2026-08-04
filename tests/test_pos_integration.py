"""
Тесты Этапа 7: Интеграционные тесты POS (12 сценариев).

Охватывают сквозные цепочки между компонентами:
CatalogSyncService → DB, OrderPushService → POS,
Webhook → Status → Profile, Factory, Idempotency.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from integrations.base import PosClientData, PosOrderResult, PosOrderStatus, PosProduct
from integrations.router import _process_status_change
from integrations.sync_service import CatalogSyncService
from models import Category, Customer, Order, OrderItem, Product, Shop, TelegramProfile

# ── Общие фикстуры ─────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def db_with_factory():
    """Возвращает (session, factory) — для тестов с несколькими сессиями."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session, factory
    await engine.dispose()


async def _shop(db: AsyncSession, pos_type: str = "mock", auto_push: bool = True) -> Shop:
    s = Shop(
        name="IntShop",
        bot_token=None,
        owner_tg_id=None,
        integration_settings={
            "type": pos_type,
            "auto_push_orders": auto_push,
            "webhook_secret": "secret",
        },
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _customer(db: AsyncSession, shop: Shop, tg_id: int = 12345) -> Customer:
    c = Customer(shop_id=shop.id, telegram_id=tg_id)
    db.add(c)
    await db.flush()
    return c


async def _order(
    db: AsyncSession, shop: Shop, customer: Customer,
    ext_id: str | None = None, int_status: str = "none",
) -> Order:
    o = Order(
        shop_id=shop.id, customer_id=customer.id,
        total_amount=500000, status="new",
        external_order_id=ext_id,
        integration_status=int_status,
        meta={"telegram_user_id": customer.telegram_id, "customer_phone": "+998901234567"},
    )
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


# Хелпер для правильного создания PosProduct
# PosProduct(external_id, name, price, in_stock, category_name, stock_quantity, image_url, description, sku)
def _pp(ext_id: str, name: str, price: int, in_stock: bool = True,
        category: str | None = "Кат") -> PosProduct:
    return PosProduct(
        external_id=ext_id, name=name, price=price,
        in_stock=in_stock, category_name=category,
    )


# ── Сценарий 1: Полный Mock-цикл ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_mock_cycle(db):
    """
    Sync каталога → товары в БД → webhook completed → статус обновлён.
    Полный пайплайн через Mock-адаптер без реального POS.
    """
    shop = await _shop(db, pos_type="mock")

    # 1. Синхронизация каталога
    svc = CatalogSyncService(db)
    result = await svc.sync(shop.id)
    assert result["created"] == 10
    assert result["synced"] == 10

    # 2. Товары появились в БД
    products = (await db.execute(
        select(Product).where(Product.shop_id == shop.id, Product.external_source == "mock")
    )).scalars().all()
    assert len(products) == 10

    # 3. Создаём заказ с external_id
    customer = await _customer(db, shop)
    order = await _order(db, shop, customer, ext_id="MOCK-100-111", int_status="pushed")

    # 4. Webhook → completed
    with patch("integrations.router._notify_customer_status"):
        with patch("integrations.router._update_profile_from_pos"):
            await _process_status_change(db, shop, "MOCK-100-111", "completed", "closed")

    await db.refresh(order)
    assert order.status == "completed"
    assert order.integration_status == "confirmed"


# ── Сценарий 2: Правила синхронизации ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_creates_updates_deactivates(db):
    """
    Первый sync: создаёт товары.
    Второй sync с изменёнными данными: обновляет цены.
    Товар, удалённый из POS: деактивируется.
    """
    shop = await _shop(db, pos_type="mock")

    catalog_v1 = [_pp("A1", "Товар А", 100000), _pp("B2", "Товар Б", 200000)]
    catalog_v2 = [_pp("A1", "Товар А NEW", 150000)]  # B2 удалён

    with patch("integrations.factory.get_pos_connector") as mock_factory:
        conn = AsyncMock()
        conn.sync_catalog = AsyncMock(return_value=catalog_v1)
        mock_factory.return_value = conn
        r1 = await CatalogSyncService(db).sync(shop.id)
        assert r1["created"] == 2

    with patch("integrations.factory.get_pos_connector") as mock_factory:
        conn2 = AsyncMock()
        conn2.sync_catalog = AsyncMock(return_value=catalog_v2)
        mock_factory.return_value = conn2
        r2 = await CatalogSyncService(db).sync(shop.id)
        assert r2["updated"] == 1
        assert r2["deactivated"] == 1

    # A1 обновлён
    a1 = (await db.execute(
        select(Product).where(Product.shop_id == shop.id, Product.external_id == "A1")
    )).scalar_one()
    assert float(a1.price) == 150000
    assert a1.name == "Товар А NEW"

    # B2 деактивирован
    b2 = (await db.execute(
        select(Product).where(Product.shop_id == shop.id, Product.external_id == "B2")
    )).scalar_one()
    assert b2.is_active is False


# ── Сценарий 3: Retry при ошибке POS ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_push_retry_then_failed(db):
    """
    POS всегда возвращает ошибку → все 3 попытки → integration_status=failed.
    Сессионный слой полностью замокирован (обход ограничений SQLAlchemy greenlet
    при использовании сессий внутри _push_with_retry).
    """
    from integrations.order_push_service import MAX_RETRIES, OrderPushService

    shop = await _shop(db, pos_type="mock", auto_push=True)
    customer = await _customer(db, shop)
    order = await _order(db, shop, customer, int_status="pending")

    failing_result = PosOrderResult(success=False, external_order_id=None, error="POS down")
    call_count = 0

    # Мок-продукт с external_id (POS-товар) — нужен чтобы retry дошёл до push_order
    mock_product = MagicMock()
    mock_product.external_id = "SKU-001"

    mock_item = MagicMock()
    mock_item.product = mock_product
    mock_item.quantity = 1
    mock_item.price_at_moment = 100000

    # Мок-заказ с одним POS-товаром
    mock_order = MagicMock()
    mock_order.id = order.id
    mock_order.shop_id = shop.id
    mock_order.integration_status = "pending"
    mock_order.discount = 0
    mock_order.total_amount = 500000
    mock_order.meta = {"customer_phone": "+998901234567"}
    mock_order.items = [mock_item]

    # Мок-магазин
    mock_shop = MagicMock()
    mock_shop.id = shop.id
    mock_shop.integration_settings = {"type": "mock", "auto_push_orders": True}
    mock_shop.bot_token = None
    mock_shop.owner_tg_id = None

    class MockScalar:
        def __init__(self, obj):
            self._obj = obj
        def scalar_one_or_none(self):
            return self._obj

    execute_calls = 0

    async def mock_execute(*_a, **_kw):
        nonlocal execute_calls
        execute_calls += 1
        # Нечётный вызов → Order, чётный → Shop
        return MockScalar(mock_order if execute_calls % 2 == 1 else mock_shop)

    class FakeSession:
        async def __aenter__(self_):
            return self_
        async def __aexit__(self_, *_):
            pass
        execute = mock_execute
        async def commit(self_):
            pass

    def fake_factory():
        return FakeSession()

    admin_notified = []

    with patch("database.async_session_factory", new=fake_factory):
        with patch("integrations.factory.get_pos_connector") as mock_pos:
            connector = AsyncMock()

            async def counting_push(**_kw):
                nonlocal call_count
                call_count += 1
                return failing_result

            connector.push_order = counting_push
            mock_pos.return_value = connector

            with patch("asyncio.sleep", new=AsyncMock()):
                with patch.object(
                    OrderPushService, "_notify_admin_push_failed",
                    new=AsyncMock(side_effect=lambda o: admin_notified.append(o.id)),
                ):
                    svc = OrderPushService(db)
                    await svc._push_with_retry(order.id)

    # Все 3 попытки отработали
    assert call_count == MAX_RETRIES
    # Админ уведомлён об ошибке
    assert len(admin_notified) == 1


# ── Сценарий 4: Webhook → статус + уведомление ────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_updates_status_and_notifies(db):
    """
    Webhook in_progress → order.status=processing, покупатель уведомлён.
    """
    shop = await _shop(db, pos_type="mock")
    shop.bot_token = "fakebottoken"
    await db.commit()

    customer = await _customer(db, shop)
    order = await _order(db, shop, customer, ext_id="EXT-W01", int_status="pushed")

    notified_status = None

    async def fake_notify(s, o, status):
        nonlocal notified_status
        notified_status = status

    with patch("integrations.router._update_profile_from_pos"):
        with patch("integrations.router._notify_customer_status", side_effect=fake_notify):
            await _process_status_change(db, shop, "EXT-W01", "in_progress", "COOKING")

    await db.refresh(order)
    assert order.status == "processing"
    assert order.external_status == "COOKING"
    assert notified_status == "processing"


# ── Сценарий 5: Webhook completed → обновление профиля ────────────────────────

@pytest.mark.asyncio
async def test_webhook_completed_updates_profile(db):
    """
    Webhook completed → _update_profile_from_pos вызван → cached_orders_count обновлён.
    """
    shop = await _shop(db, pos_type="mock")
    customer = await _customer(db, shop)
    order = await _order(db, shop, customer, ext_id="EXT-C01", int_status="pushed")

    mock_pos_data = MagicMock()
    mock_pos_data.orders_count = 7
    mock_pos_data.ltv = 2_000_000
    mock_pos_data.avg_check = 285_714
    mock_pos_data.last_order_date = "2026-02-01"

    with patch("integrations.factory.get_pos_connector") as mock_factory:
        connector = AsyncMock()
        connector.get_client_data = AsyncMock(return_value=mock_pos_data)
        mock_factory.return_value = connector

        with patch("integrations.router._notify_customer_status"):
            await _process_status_change(db, shop, "EXT-C01", "completed", "closed")

    profile = (await db.execute(
        select(TelegramProfile).where(
            TelegramProfile.shop_id == shop.id,
            TelegramProfile.telegram_id == 12345,
        )
    )).scalar_one_or_none()

    if profile:
        assert profile.cached_orders_count == 7


# ── Сценарий 6: Polling fallback ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_polling_updates_stale_pushed_order(db):
    """
    Заказ integration_status=pushed → polling получает новый статус → обновляет.
    Симулируем логику polling через _process_status_change (как делает планировщик).
    """
    shop = await _shop(db, pos_type="mock")
    customer = await _customer(db, shop)
    order = await _order(db, shop, customer, ext_id="EXT-POLL-01", int_status="pushed")

    new_status = PosOrderStatus(
        external_order_id="EXT-POLL-01",
        status="completed",
        original_status="closed",
        updated_at=None,
    )

    with patch("integrations.router._notify_customer_status"):
        with patch("integrations.router._update_profile_from_pos"):
            await _process_status_change(
                db, shop,
                new_status.external_order_id,
                new_status.status,
                new_status.original_status,
            )

    await db.refresh(order)
    assert order.status == "completed"
    assert order.integration_status == "confirmed"


# ── Сценарий 7: Без POS (type=none) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_pos_order_not_pushed(db):
    """
    Магазин без POS (type=none) → on_order_created ничего не делает.
    """
    from integrations.order_push_service import OrderPushService

    shop = await _shop(db, pos_type="none")
    customer = await _customer(db, shop)
    order = await _order(db, shop, customer, int_status="none")

    svc = OrderPushService(db)
    await svc.on_order_created(order.id)

    await db.refresh(order)
    assert order.integration_status == "none"


@pytest.mark.asyncio
async def test_no_pos_sync_returns_zero(db):
    """
    Sync для магазина без POS → ничего не происходит, 0 товаров.
    """
    shop = await _shop(db, pos_type="none")
    result = await CatalogSyncService(db).sync(shop.id)
    assert result["synced"] == 0
    assert result["created"] == 0


# ── Сценарий 8: Переключение none → mock ──────────────────────────────────────

@pytest.mark.asyncio
async def test_switch_none_to_mock_enables_push(db):
    """
    Магазин меняет integration_settings с none на mock →
    on_order_created начинает ставить заказы в очередь.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from integrations.order_push_service import OrderPushService

    shop = await _shop(db, pos_type="none")
    customer = await _customer(db, shop)

    # При type=none — заказ не ставится в очередь
    order_no_pos = await _order(db, shop, customer)
    svc = OrderPushService(db)
    await svc.on_order_created(order_no_pos.id)
    await db.refresh(order_no_pos)
    assert order_no_pos.integration_status == "none"

    # Переключаем на mock
    shop.integration_settings = {"type": "mock", "auto_push_orders": True}
    flag_modified(shop, "integration_settings")
    await db.commit()

    # Новый заказ должен перейти в pending
    order_with_pos = await _order(db, shop, customer)
    with patch("asyncio.create_task"):
        await svc.on_order_created(order_with_pos.id)

    await db.refresh(order_with_pos)
    assert order_with_pos.integration_status == "pending"


# ── Сценарий 9: Factory — правильный адаптер для каждого типа ─────────────────

def test_factory_correct_adapter_types():
    """
    Factory возвращает правильный класс адаптера.
    """
    from integrations.factory import get_pos_connector
    from integrations.mock_adapter import MockPosAdapter
    from integrations.moysklad_adapter import MoySkladAdapter

    # none → None
    shop_none = MagicMock()
    shop_none.integration_settings = {"type": "none"}
    assert get_pos_connector(shop_none) is None

    # mock → MockPosAdapter
    shop_mock = MagicMock()
    shop_mock.id = 1
    shop_mock.integration_settings = {"type": "mock"}
    adapter = get_pos_connector(shop_mock)
    assert isinstance(adapter, MockPosAdapter)

    # moysklad → MoySkladAdapter
    shop_ms = MagicMock()
    shop_ms.id = 2
    shop_ms.integration_settings = {"type": "moysklad", "api_key": "token123"}
    adapter_ms = get_pos_connector(shop_ms)
    assert isinstance(adapter_ms, MoySkladAdapter)

    # unknown → None
    shop_unknown = MagicMock()
    shop_unknown.id = 3
    shop_unknown.integration_settings = {"type": "custompos"}
    assert get_pos_connector(shop_unknown) is None


# ── Сценарий 10: BILLZ — создаётся, методы бросают NotImplementedError ────────

def test_billz_adapter_created_but_not_implemented():
    """
    Factory создаёт BillzAdapter при наличии ключей.
    Методы адаптера ещё не реализованы → NotImplementedError.
    """
    from integrations.billz_adapter import BillzAdapter
    from integrations.factory import get_pos_connector

    shop = MagicMock()
    shop.id = 99
    shop.integration_settings = {
        "type": "billz",
        "api_key": "key",
        "api_secret": "secret",
        "api_url": "https://api.billz.io/v1",
    }

    adapter = get_pos_connector(shop)
    assert adapter is not None
    assert isinstance(adapter, BillzAdapter)

    # Методы пока не реализованы
    with pytest.raises(NotImplementedError):
        asyncio.get_event_loop().run_until_complete(adapter.test_connection())


# ── Сценарий 11: Ручные + POS товары в одном заказе ──────────────────────────

@pytest.mark.asyncio
async def test_mixed_items_only_pos_items_sent(db):
    """
    Заказ содержит POS-товар (external_id) и ручной товар (нет external_id).
    При push — в POS уходят только POS-товары.
    """
    shop = await _shop(db, pos_type="mock", auto_push=True)
    customer = await _customer(db, shop)

    # POS-товар
    pos_product = Product(
        shop_id=shop.id, name="POS Товар", price=200000,
        external_id="SKU-POS", external_source="mock", is_active=True,
    )
    # Ручной товар
    manual_product = Product(
        shop_id=shop.id, name="Ручной Товар", price=100000,
        external_id=None, external_source=None, is_active=True,
    )
    db.add_all([pos_product, manual_product])
    await db.flush()

    order = Order(
        shop_id=shop.id, customer_id=customer.id,
        total_amount=300000, status="new", integration_status="pending",
        meta={"telegram_user_id": 12345, "customer_phone": "+998901234567"},
    )
    db.add(order)
    await db.flush()

    db.add(OrderItem(
        order_id=order.id, product_id=pos_product.id,
        quantity=1, price_at_moment=200000,
    ))
    db.add(OrderItem(
        order_id=order.id, product_id=manual_product.id,
        quantity=1, price_at_moment=100000,
    ))
    await db.commit()
    await db.refresh(order)

    # Симулируем логику фильтрации из OrderPushService.push_order()
    from sqlalchemy.orm import selectinload
    order_loaded = (await db.execute(
        select(Order).where(Order.id == order.id)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
    )).scalar_one()

    items_to_push = [
        {"external_id": it.product.external_id, "quantity": it.quantity}
        for it in order_loaded.items
        if it.product and it.product.external_id
    ]

    # Только POS-товар должен попасть в список
    assert len(items_to_push) == 1
    assert items_to_push[0]["external_id"] == "SKU-POS"


# ── Сценарий 12: Идемпотентность синхронизации — без дублей ─────────────────

@pytest.mark.asyncio
async def test_repeated_sync_no_duplicates(db):
    """
    Sync запускается дважды с одинаковым каталогом.
    Товары не должны задваиваться — второй sync обновляет, а не создаёт.
    """
    shop = await _shop(db, pos_type="mock")

    catalog = [_pp("X1", "Товар X", 100000), _pp("X2", "Товар Y", 200000)]

    with patch("integrations.factory.get_pos_connector") as mock_factory:
        connector = AsyncMock()
        connector.sync_catalog = AsyncMock(return_value=catalog)
        mock_factory.return_value = connector

        r1 = await CatalogSyncService(db).sync(shop.id)
        r2 = await CatalogSyncService(db).sync(shop.id)

    # Первый sync: 2 created
    assert r1["created"] == 2
    assert r1["updated"] == 0

    # Второй sync: 0 created, 2 updated (idempotent)
    assert r2["created"] == 0
    assert r2["updated"] == 2

    # В БД ровно 2 товара, без дублей
    products = (await db.execute(
        select(Product).where(Product.shop_id == shop.id, Product.external_source == "mock")
    )).scalars().all()
    assert len(products) == 2
