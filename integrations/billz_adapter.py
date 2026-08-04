"""
Адаптер BILLZ (Этап 5 — заготовка).

BILLZ API — https://billz.io/for-developers
API-доступ запрашивается у developers@billz.io.
До получения доступа всё поднимает NotImplementedError → фабрика переключается на Mock.
"""
from __future__ import annotations

from .base import PosClientData, PosConnector, PosOrderResult, PosOrderStatus, PosProduct


class BillzAdapter(PosConnector):
    """
    BILLZ — главная POS-система для Узбекистана.
    Статус: ожидает API-доступ от developers@billz.io.
    """

    def __init__(self, api_key: str, api_secret: str, api_url: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_url = api_url.rstrip("/")

    async def test_connection(self) -> bool:
        raise NotImplementedError("BILLZ API-доступ ещё не получен. Запросить у developers@billz.io")

    async def sync_catalog(self) -> list[PosProduct]:
        # Endpoint: GET /products (предполагаемый, уточнить по документации)
        raise NotImplementedError("BILLZ adapter: sync_catalog не реализован")

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
        # Endpoint: POST /sales или POST /orders (уточнить по документации)
        raise NotImplementedError("BILLZ adapter: push_order не реализован")

    async def get_order_status(self, external_order_id: str) -> PosOrderStatus:
        raise NotImplementedError("BILLZ adapter: get_order_status не реализован")

    async def get_client_data(
        self,
        phone: str | None = None,
        external_id: str | None = None,
    ) -> PosClientData | None:
        # Endpoint: GET /clients?phone=... (уточнить по документации)
        raise NotImplementedError("BILLZ adapter: get_client_data не реализован")
