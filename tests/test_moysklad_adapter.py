"""
Unit-тесты для MoySkladAdapter.
Мокируем httpx.AsyncClient чтобы не ходить в реальное API.
Проверяем: test_connection, sync_catalog, push_order, get_order_status, get_client_data.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.base import PosClientData, PosOrderResult, PosOrderStatus, PosProduct
from integrations.moysklad_adapter import MoySkladAdapter

# ── Фикстуры ─────────────────────────────────────────────────────────────────

@pytest.fixture
def adapter():
    return MoySkladAdapter(api_token="test-token-123", organization_id="org-uuid-456")


@pytest.fixture
def adapter_no_org():
    """Адаптер без organization_id"""
    return MoySkladAdapter(api_token="test-token-123")


def _mock_response(status_code: int = 200, json_data: dict | None = None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


# ── test_connection ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_connection_success(adapter):
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200, {"rows": [{"id": "org-1"}]})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.test_connection()

    assert result is True
    mock_client.get.assert_called_once()
    call_args = mock_client.get.call_args
    assert "/entity/organization" in call_args[0][0]
    assert call_args[1]["headers"]["Authorization"] == "Bearer test-token-123"


@pytest.mark.asyncio
async def test_connection_failure(adapter):
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(401)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.test_connection()

    assert result is False


# ── sync_catalog ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_catalog_single_page(adapter):
    """Один запрос — менее 1000 товаров, пагинация не нужна"""
    api_products = [
        {
            "id": "prod-uuid-1",
            "name": "Роза красная",
            "salePrices": [{"value": 1500000}],  # 15000 UZS в копейках
            "archived": False,
            "productFolder": {"name": "Цветы"},
            "description": "Свежие розы",
            "article": "ROSE-001",
        },
        {
            "id": "prod-uuid-2",
            "name": "Тюльпан жёлтый",
            "salePrices": [{"value": 800000}],  # 8000 UZS
            "archived": False,
            "productFolder": {"name": "Цветы"},
            "description": None,
            "article": "TULIP-001",
        },
    ]

    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200, {"rows": api_products})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        products = await adapter.sync_catalog()

    assert len(products) == 2
    assert all(isinstance(p, PosProduct) for p in products)

    # Проверяем первый товар
    rose = products[0]
    assert rose.external_id == "prod-uuid-1"
    assert rose.name == "Роза красная"
    assert rose.price == 15000  # копейки / 100
    assert rose.in_stock is True
    assert rose.category_name == "Цветы"
    assert rose.description == "Свежие розы"
    assert rose.sku == "ROSE-001"

    # Проверяем второй товар
    tulip = products[1]
    assert tulip.external_id == "prod-uuid-2"
    assert tulip.price == 8000


@pytest.mark.asyncio
async def test_sync_catalog_archived_product(adapter):
    """Архивированный товар → in_stock = False"""
    api_products = [
        {
            "id": "prod-archived",
            "name": "Старые лилии",
            "salePrices": [{"value": 500000}],
            "archived": True,
            "productFolder": {},
        },
    ]

    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200, {"rows": api_products})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        products = await adapter.sync_catalog()

    assert len(products) == 1
    assert products[0].in_stock is False


@pytest.mark.asyncio
async def test_sync_catalog_no_sale_prices(adapter):
    """Товар без salePrices → цена 0"""
    api_products = [
        {
            "id": "prod-no-price",
            "name": "Без цены",
            "salePrices": [],
            "archived": False,
        },
    ]

    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200, {"rows": api_products})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        products = await adapter.sync_catalog()

    assert products[0].price == 0


@pytest.mark.asyncio
async def test_sync_catalog_pagination(adapter):
    """Два запроса: первый на 1000 товаров, второй на 500"""
    page1 = [{"id": f"p-{i}", "name": f"Item {i}", "salePrices": [{"value": 100000}],
              "archived": False} for i in range(1000)]
    page2 = [{"id": f"p-{i}", "name": f"Item {i}", "salePrices": [{"value": 100000}],
              "archived": False} for i in range(1000, 1500)]

    call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_response(200, {"rows": page1})
        elif call_count == 2:
            return _mock_response(200, {"rows": page2})
        return _mock_response(200, {"rows": []})

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        products = await adapter.sync_catalog()

    assert len(products) == 1500
    assert call_count == 2  # ровно 2 запроса (500 < 1000, цикл прерывается)


@pytest.mark.asyncio
async def test_sync_catalog_empty(adapter):
    """Пустой каталог → пустой список"""
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200, {"rows": []})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        products = await adapter.sync_catalog()

    assert products == []


@pytest.mark.asyncio
async def test_sync_catalog_no_category(adapter):
    """Товар без productFolder → category_name = None"""
    api_products = [
        {
            "id": "prod-no-cat",
            "name": "Без категории",
            "salePrices": [{"value": 200000}],
            "archived": False,
        },
    ]

    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200, {"rows": api_products})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        products = await adapter.sync_catalog()

    assert products[0].category_name is None


# ── push_order ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_push_order_success(adapter):
    """Успешное создание заказа в МойСклад"""
    org_response = _mock_response(200, {
        "rows": [{"meta": {"href": "https://api.moysklad.ru/api/remap/1.2/entity/organization/org-1",
                           "type": "organization", "mediaType": "application/json"}}]
    })
    counterparty_response = _mock_response(200, {
        "rows": [{"meta": {"href": "https://...", "type": "counterparty", "mediaType": "application/json"}}]
    })
    order_response = _mock_response(201, {"id": "new-order-uuid-123"})

    call_log = []

    async def mock_get(url, **kwargs):
        call_log.append(("GET", url))
        if "/entity/organization" in url:
            return org_response
        if "/entity/counterparty" in url:
            return counterparty_response
        return _mock_response(404)

    async def mock_post(url, **kwargs):
        call_log.append(("POST", url))
        if "/entity/customerorder" in url:
            # Проверяем payload
            payload = kwargs.get("json", {})
            assert "organization" in payload
            assert "agent" in payload
            assert "positions" in payload
            assert len(payload["positions"]) == 1
            # Цена в копейках (245000 * 100)
            assert payload["positions"][0]["price"] == 245000 * 100
            return order_response
        return _mock_response(404)

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.push_order(
            order_id=42,
            items=[{"external_id": "prod-uuid-1", "quantity": 2, "price": 245000}],
            total_amount=490000,
            discount_amount=0,
            customer_name="Алиса",
            customer_phone="+998901234567",
            payment_method="cash",
        )

    assert isinstance(result, PosOrderResult)
    assert result.success is True
    assert result.external_order_id == "new-order-uuid-123"


@pytest.mark.asyncio
async def test_push_order_skips_items_without_external_id(adapter):
    """Позиции без external_id не попадают в заказ МойСклад"""
    org_response = _mock_response(200, {
        "rows": [{"meta": {"href": "...", "type": "organization", "mediaType": "application/json"}}]
    })
    counterparty_response = _mock_response(200, {
        "rows": [{"meta": {"href": "...", "type": "counterparty", "mediaType": "application/json"}}]
    })
    captured_payload = {}

    async def mock_get(url, **kwargs):
        if "/entity/organization" in url:
            return org_response
        if "/entity/counterparty" in url:
            return counterparty_response
        return _mock_response(404)

    async def mock_post(url, **kwargs):
        if "/entity/customerorder" in url:
            captured_payload.update(kwargs.get("json", {}))
            return _mock_response(201, {"id": "order-999"})
        return _mock_response(404)

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.push_order(
            order_id=99,
            items=[
                {"external_id": "prod-1", "quantity": 1, "price": 100000},
                {"quantity": 2, "price": 50000},  # без external_id — ручной товар
                {"external_id": None, "quantity": 1, "price": 30000},  # explicit None
            ],
            total_amount=230000,
            discount_amount=0,
            customer_name="Тест",
            customer_phone="+998900000000",
            payment_method="click",
        )

    assert result.success is True
    # Только 1 позиция с external_id попала в заказ
    assert len(captured_payload["positions"]) == 1
    assert captured_payload["positions"][0]["quantity"] == 1


@pytest.mark.asyncio
async def test_push_order_http_error(adapter):
    """Ошибка HTTP при создании заказа"""
    org_response = _mock_response(200, {
        "rows": [{"meta": {"href": "...", "type": "organization", "mediaType": "application/json"}}]
    })
    counterparty_response = _mock_response(200, {
        "rows": [{"meta": {"href": "...", "type": "counterparty", "mediaType": "application/json"}}]
    })

    async def mock_get(url, **kwargs):
        if "/entity/organization" in url:
            return org_response
        if "/entity/counterparty" in url:
            return counterparty_response
        return _mock_response(404)

    async def mock_post(url, **kwargs):
        if "/entity/customerorder" in url:
            return _mock_response(500, text="Internal Server Error")
        return _mock_response(404)

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.push_order(
            order_id=100,
            items=[{"external_id": "prod-1", "quantity": 1, "price": 100000}],
            total_amount=100000,
            discount_amount=0,
            customer_name="Ошибка",
            customer_phone="+998900000001",
            payment_method="cash",
        )

    assert result.success is False
    assert "500" in result.error


@pytest.mark.asyncio
async def test_push_order_creates_new_counterparty(adapter):
    """Если контрагент не найден по телефону — создаётся новый"""
    org_response = _mock_response(200, {
        "rows": [{"meta": {"href": "...", "type": "organization", "mediaType": "application/json"}}]
    })

    post_calls = []

    async def mock_get(url, **kwargs):
        if "/entity/organization" in url:
            return org_response
        if "/entity/counterparty" in url:
            # Не найдён по телефону
            return _mock_response(200, {"rows": []})
        return _mock_response(404)

    async def mock_post(url, **kwargs):
        post_calls.append((url, kwargs.get("json")))
        if "/entity/counterparty" in url:
            return _mock_response(200, {
                "meta": {"href": "...", "type": "counterparty", "mediaType": "application/json"}
            })
        if "/entity/customerorder" in url:
            return _mock_response(201, {"id": "new-order"})
        return _mock_response(404)

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.push_order(
            order_id=200,
            items=[{"external_id": "p1", "quantity": 1, "price": 50000}],
            total_amount=50000,
            discount_amount=0,
            customer_name="Новый клиент",
            customer_phone="+998911111111",
            payment_method="payme",
        )

    assert result.success is True
    # Проверяем что был POST на /entity/counterparty с данными клиента
    cp_call = [c for c in post_calls if "/entity/counterparty" in c[0]]
    assert len(cp_call) == 1
    assert cp_call[0][1]["name"] == "Новый клиент"
    assert cp_call[0][1]["phone"] == "+998911111111"


# ── get_order_status ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("state_name,expected_status", [
    ("New", "new"),
    ("InProcess", "in_progress"),
    ("Assembled", "ready"),
    ("Delivered", "completed"),
    ("Closed", "completed"),
    ("Cancelled", "cancelled"),
    ("UnknownState", "new"),  # неизвестный → fallback "new"
])
async def test_get_order_status_mapping(adapter, state_name, expected_status):
    """Проверяем маппинг всех статусов МойСклад → наши статусы"""
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200, {
        "id": "order-123",
        "state": {"name": state_name},
    })
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        status = await adapter.get_order_status("order-123")

    assert isinstance(status, PosOrderStatus)
    assert status.external_order_id == "order-123"
    assert status.status == expected_status
    assert status.original_status == state_name


@pytest.mark.asyncio
async def test_get_order_status_no_state(adapter):
    """Заказ без поля state → fallback "new" """
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200, {"id": "order-456"})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        status = await adapter.get_order_status("order-456")

    assert status.status == "new"
    assert status.original_status == "Unknown"


# ── get_client_data ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_client_data_by_phone(adapter):
    """Получение данных клиента по номеру телефона"""
    counterparty_resp = _mock_response(200, {
        "rows": [{"id": "cp-uuid-1", "name": "Алиса"}]
    })
    orders_resp = _mock_response(200, {
        "rows": [
            {"id": "o1", "sum": 5000000, "updated": "2026-04-10 12:00:00"},
            {"id": "o2", "sum": 3000000, "updated": "2026-04-01 10:00:00"},
        ]
    })

    async def mock_get(url, **kwargs):
        if "/entity/counterparty" in url:
            return counterparty_resp
        if "/entity/customerorder" in url:
            return orders_resp
        return _mock_response(404)

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        data = await adapter.get_client_data(phone="+998901234567")

    assert isinstance(data, PosClientData)
    assert data.external_id == "cp-uuid-1"
    assert data.orders_count == 2
    assert data.ltv == 80000  # (5000000 + 3000000) / 100
    assert data.avg_check == 40000  # 80000 / 2
    assert data.last_order_date == "2026-04-10 12:00:00"


@pytest.mark.asyncio
async def test_get_client_data_by_external_id(adapter):
    """Получение данных клиента по external_id (без поиска по телефону)"""
    orders_resp = _mock_response(200, {
        "rows": [
            {"id": "o1", "sum": 2000000, "updated": "2026-03-15"},
        ]
    })

    async def mock_get(url, **kwargs):
        if "/entity/customerorder" in url:
            return orders_resp
        return _mock_response(404)

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        data = await adapter.get_client_data(external_id="cp-uuid-direct")

    assert data.external_id == "cp-uuid-direct"
    assert data.orders_count == 1
    assert data.ltv == 20000


@pytest.mark.asyncio
async def test_get_client_data_no_params(adapter):
    """Без phone и external_id → None"""
    result = await adapter.get_client_data()
    assert result is None


@pytest.mark.asyncio
async def test_get_client_data_phone_not_found(adapter):
    """Контрагент не найден по телефону → None"""
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200, {"rows": []})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        data = await adapter.get_client_data(phone="+998999999999")

    assert data is None


@pytest.mark.asyncio
async def test_get_client_data_no_orders(adapter):
    """Клиент без заказов → нулевые метрики"""
    counterparty_resp = _mock_response(200, {
        "rows": [{"id": "cp-new", "name": "Новенький"}]
    })
    orders_resp = _mock_response(200, {"rows": []})

    async def mock_get(url, **kwargs):
        if "/entity/counterparty" in url:
            return counterparty_resp
        if "/entity/customerorder" in url:
            return orders_resp
        return _mock_response(404)

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        data = await adapter.get_client_data(phone="+998900000000")

    assert data.orders_count == 0
    assert data.ltv == 0
    assert data.avg_check == 0
    assert data.last_order_date is None


@pytest.mark.asyncio
async def test_get_client_data_api_error(adapter):
    """Ошибка API при поиске контрагента → None"""
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(500)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        data = await adapter.get_client_data(phone="+998900000000")

    assert data is None


# ── Конструктор и заголовки ──────────────────────────────────────────────────

def test_adapter_init():
    a = MoySkladAdapter(api_token="my-token", organization_id="my-org")
    assert a.api_token == "my-token"
    assert a.organization_id == "my-org"
    assert a._headers == {"Authorization": "Bearer my-token"}


def test_adapter_base_url():
    assert MoySkladAdapter.BASE_URL == "https://api.moysklad.ru/api/remap/1.2"


def test_adapter_without_organization_id():
    a = MoySkladAdapter(api_token="tok")
    assert a.organization_id is None


# ── Factory интеграция ───────────────────────────────────────────────────────

def test_factory_creates_moysklad_adapter():
    from integrations.factory import get_pos_connector
    shop = MagicMock()
    shop.id = 1
    shop.integration_settings = {
        "type": "moysklad",
        "api_key": "real-token",
        "organization_id": "org-123",
    }
    adapter = get_pos_connector(shop)
    assert isinstance(adapter, MoySkladAdapter)
    assert adapter.api_token == "real-token"
    assert adapter.organization_id == "org-123"


def test_factory_moysklad_without_api_key():
    from integrations.factory import get_pos_connector
    shop = MagicMock()
    shop.id = 2
    shop.integration_settings = {"type": "moysklad"}
    adapter = get_pos_connector(shop)
    assert adapter is None


def test_factory_moysklad_without_org_id():
    """api_key есть, organization_id нет — адаптер создаётся (org опционален)"""
    from integrations.factory import get_pos_connector
    shop = MagicMock()
    shop.id = 3
    shop.integration_settings = {"type": "moysklad", "api_key": "tok-123"}
    adapter = get_pos_connector(shop)
    assert isinstance(adapter, MoySkladAdapter)
    assert adapter.organization_id is None


# ── Конвертация цен ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_price_conversion_kopecks_to_uzs(adapter):
    """МойСклад хранит цены в копейках, наша система в UZS целых"""
    api_products = [
        {
            "id": "price-test",
            "name": "Тестовый",
            "salePrices": [{"value": 24500000}],  # 245000 UZS
            "archived": False,
        },
    ]

    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200, {"rows": api_products})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        products = await adapter.sync_catalog()

    assert products[0].price == 245000


@pytest.mark.asyncio
async def test_push_order_price_uzs_to_kopecks(adapter):
    """При пуше заказа цена конвертируется обратно: UZS → копейки"""
    org_resp = _mock_response(200, {
        "rows": [{"meta": {"href": "...", "type": "organization", "mediaType": "application/json"}}]
    })
    cp_resp = _mock_response(200, {
        "rows": [{"meta": {"href": "...", "type": "counterparty", "mediaType": "application/json"}}]
    })

    captured = {}

    async def mock_get(url, **kwargs):
        if "/entity/organization" in url:
            return org_resp
        if "/entity/counterparty" in url:
            return cp_resp
        return _mock_response(404)

    async def mock_post(url, **kwargs):
        if "/entity/customerorder" in url:
            captured["payload"] = kwargs.get("json", {})
            return _mock_response(201, {"id": "order-price-test"})
        return _mock_response(404)

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await adapter.push_order(
            order_id=1,
            items=[{"external_id": "p1", "quantity": 3, "price": 150000}],  # UZS
            total_amount=450000,
            discount_amount=0,
            customer_name="Тест",
            customer_phone="+998900000000",
            payment_method="cash",
        )

    # Цена в payload должна быть 150000 * 100 = 15000000 копеек
    assert captured["payload"]["positions"][0]["price"] == 15000000
