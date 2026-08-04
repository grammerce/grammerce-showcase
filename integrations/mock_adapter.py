"""
Mock POS-адаптер — полная имитация POS-системы для разработки и тестов.

- sync_catalog()      → фейковый каталог 10 товаров
- push_order()        → сохраняет в памяти, возвращает mock external_id
- get_order_status()  → "completed" через 30 сек после пуша
- get_client_data()   → фейковые данные клиента
"""
from __future__ import annotations

import time

from .base import PosClientData, PosConnector, PosOrderResult, PosOrderStatus, PosProduct


class MockPosAdapter(PosConnector):
    """Полная имитация POS-системы. Используется в разработке и тестах."""

    def __init__(self, shop_id: int):
        self.shop_id = shop_id
        self._orders: dict[str, dict] = {}   # {external_id: {status, pushed_at, ...}}
        self._catalog = self._generate_mock_catalog()

    # ── Catalog ──────────────────────────────────────────────────────────────

    def _generate_mock_catalog(self) -> list[PosProduct]:
        items = [
            ("SKU-001", "Фитнес Футболка",      245000, "Одежда",   50),
            ("SKU-002", "Леггинсы Pro",          480000, "Одежда",   30),
            ("SKU-003", "Худи Urban",            890000, "Одежда",   20),
            ("SKU-004", "Шорты Run",             160000, "Одежда",   60),
            ("SKU-005", "Кроссовки Air",         950000, "Обувь",    15),
            ("SKU-006", "Топ Бра",               320000, "Одежда",   40),
            ("SKU-007", "Ветровка Lite",         710000, "Одежда",   25),
            ("SKU-008", "Джоггеры Cotton",       410000, "Одежда",   35),
            ("SKU-009", "Перчатки Fit",          155000, "Аксессуары", 55),
            ("SKU-010", "Кепка Sport",           190000, "Аксессуары", 45),
        ]
        return [
            PosProduct(
                external_id=sku,
                name=name,
                price=price,
                in_stock=True,
                stock_quantity=stock,
                category_name=cat,
                description=f"Описание товара {name}",
            )
            for sku, name, price, cat, stock in items
        ]

    async def test_connection(self) -> bool:
        return True

    async def sync_catalog(self) -> list[PosProduct]:
        return list(self._catalog)

    # ── Orders ───────────────────────────────────────────────────────────────

    async def push_order(
        self,
        order_id: int,
        items: list[dict],
        total_amount: int,
        discount_amount: int,
        customer_name: str | None,
        customer_phone: str | None,
        payment_method: str,
        delivery_type: str | None = None,
    ) -> PosOrderResult:
        ext_id = f"MOCK-{order_id}-{int(time.time())}"
        self._orders[ext_id] = {
            "status": "new",
            "pushed_at": time.time(),
            "order_id": order_id,
        }
        return PosOrderResult(success=True, external_order_id=ext_id)

    async def get_order_status(self, external_order_id: str) -> PosOrderStatus:
        order = self._orders.get(external_order_id)
        if order is None:
            return PosOrderStatus(
                external_order_id=external_order_id,
                status="unknown",
                original_status="unknown",
            )
        # Автоматически "завершается" через 30 сек
        if time.time() - order["pushed_at"] > 30:
            order["status"] = "completed"
        return PosOrderStatus(
            external_order_id=external_order_id,
            status=order["status"],
            original_status=order["status"],
            updated_at=None,
        )

    # ── Client data ──────────────────────────────────────────────────────────

    async def get_client_data(
        self,
        phone: str | None = None,
        external_id: str | None = None,
    ) -> PosClientData | None:
        if not phone and not external_id:
            return None
        # Фейковые данные для любого клиента
        return PosClientData(
            external_id=f"MOCK-CLIENT-{phone or external_id}",
            orders_count=3,
            ltv=1_200_000,
            avg_check=400_000,
            last_order_date="2026-01-15",
        )
