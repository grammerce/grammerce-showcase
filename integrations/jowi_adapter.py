"""
Адаптер Jowi (Этап 5 — заготовка).

Jowi API — https://docs.jowi.club
Специфика HoReCa: модификаторы блюд, тип заказа (на вынос/доставка/в зале).
"""
from __future__ import annotations

from .base import PosClientData, PosConnector, PosOrderResult, PosOrderStatus, PosProduct


class JowiAdapter(PosConnector):
    """
    Jowi — POS для HoReCa.
    Статус: нужен аккаунт ресторана для реальных тестов.
    """

    BASE_URL = "https://api.jowi.club/v1"

    def __init__(self, api_key: str, api_secret: str, restaurant_id: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.restaurant_id = restaurant_id

    async def test_connection(self) -> bool:
        raise NotImplementedError("Jowi adapter: нужен аккаунт ресторана")

    async def sync_catalog(self) -> list[PosProduct]:
        # GET /restaurants/{id}/menu — учесть модификаторы
        raise NotImplementedError("Jowi adapter: sync_catalog не реализован")

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
        # POST /restaurants/{id}/orders — delivery_type обязателен для Jowi
        raise NotImplementedError("Jowi adapter: push_order не реализован")

    async def get_order_status(self, external_order_id: str) -> PosOrderStatus:
        raise NotImplementedError("Jowi adapter: get_order_status не реализован")

    async def get_client_data(
        self,
        phone: str | None = None,
        external_id: str | None = None,
    ) -> PosClientData | None:
        raise NotImplementedError("Jowi adapter: get_client_data не реализован")
