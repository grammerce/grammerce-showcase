"""
Тесты Этапа 1: PosConnector + Mock-адаптер + Factory.
Используют только в-памяти объекты (без БД).
"""
from unittest.mock import MagicMock

import pytest

from integrations.base import PosClientData, PosOrderResult, PosOrderStatus, PosProduct
from integrations.factory import get_pos_connector
from integrations.mock_adapter import MockPosAdapter

# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_adapter():
    return MockPosAdapter(shop_id=1)


def _make_shop(pos_type: str, **extra):
    shop = MagicMock()
    shop.id = 1
    settings = {"type": pos_type}
    settings.update(extra)
    shop.integration_settings = settings
    return shop


# ── MockPosAdapter ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mock_test_connection(mock_adapter):
    assert await mock_adapter.test_connection() is True


@pytest.mark.asyncio
async def test_mock_sync_catalog_returns_10_products(mock_adapter):
    products = await mock_adapter.sync_catalog()
    assert len(products) == 10
    assert all(isinstance(p, PosProduct) for p in products)


@pytest.mark.asyncio
async def test_mock_sync_catalog_fields(mock_adapter):
    products = await mock_adapter.sync_catalog()
    p = products[0]
    assert p.external_id.startswith("SKU-")
    assert p.price > 0
    assert p.in_stock is True
    assert p.category_name is not None


@pytest.mark.asyncio
async def test_mock_push_order_returns_external_id(mock_adapter):
    result = await mock_adapter.push_order(
        order_id=42,
        items=[{"external_id": "SKU-001", "quantity": 2, "price": 245000}],
        total_amount=490000,
        discount_amount=0,
        customer_name="Тест",
        customer_phone="+998901234567",
        payment_method="cash",
    )
    assert isinstance(result, PosOrderResult)
    assert result.success is True
    assert result.external_order_id is not None
    assert "42" in result.external_order_id


@pytest.mark.asyncio
async def test_mock_get_order_status_new(mock_adapter):
    push = await mock_adapter.push_order(
        order_id=99,
        items=[],
        total_amount=100000,
        discount_amount=0,
        customer_name=None,
        customer_phone=None,
        payment_method="cash",
    )
    status = await mock_adapter.get_order_status(push.external_order_id)
    assert isinstance(status, PosOrderStatus)
    assert status.external_order_id == push.external_order_id
    assert status.status == "new"


@pytest.mark.asyncio
async def test_mock_get_order_status_unknown(mock_adapter):
    status = await mock_adapter.get_order_status("UNKNOWN-ID")
    assert status.status == "unknown"


@pytest.mark.asyncio
async def test_mock_get_client_data(mock_adapter):
    data = await mock_adapter.get_client_data(phone="+998901234567")
    assert isinstance(data, PosClientData)
    assert data.orders_count > 0
    assert data.ltv > 0
    assert data.avg_check > 0


@pytest.mark.asyncio
async def test_mock_get_client_data_no_params(mock_adapter):
    data = await mock_adapter.get_client_data()
    assert data is None


# ── Factory ────────────────────────────────────────────────────────────────────

def test_factory_type_none_returns_none():
    shop = _make_shop("none")
    assert get_pos_connector(shop) is None


def test_factory_type_mock_returns_mock():
    shop = _make_shop("mock")
    adapter = get_pos_connector(shop)
    assert isinstance(adapter, MockPosAdapter)


def test_factory_missing_settings_returns_none():
    shop = MagicMock()
    shop.id = 1
    shop.integration_settings = None
    assert get_pos_connector(shop) is None


def test_factory_unknown_type_returns_none():
    shop = _make_shop("unknown_pos_system")
    assert get_pos_connector(shop) is None


def test_factory_billz_missing_keys_returns_none():
    shop = _make_shop("billz")  # без api_key/api_secret
    result = get_pos_connector(shop)
    # Должен вернуть None из-за KeyError
    assert result is None


def test_factory_moysklad_missing_api_key_returns_none():
    shop = _make_shop("moysklad")  # без api_key
    result = get_pos_connector(shop)
    assert result is None


@pytest.mark.asyncio
async def test_mock_sync_catalog_independent_copies(mock_adapter):
    """Два вызова sync_catalog возвращают независимые списки"""
    first = await mock_adapter.sync_catalog()
    second = await mock_adapter.sync_catalog()
    assert first is not second
    assert len(first) == len(second)
