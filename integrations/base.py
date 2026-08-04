"""
Абстрактный интерфейс POS-коннектора.
Каждая POS-система реализует этот интерфейс.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PosProduct:
    """Товар из POS-системы"""
    external_id: str
    name: str
    price: int                      # в UZS (целое число)
    in_stock: bool
    category_name: str | None = None
    stock_quantity: int | None = None
    image_url: str | None = None
    description: str | None = None
    sku: str | None = None


@dataclass
class PosOrderResult:
    """Результат отправки заказа в POS"""
    success: bool
    external_order_id: str | None = None
    error: str | None = None


@dataclass
class PosOrderStatus:
    """Статус заказа из POS"""
    external_order_id: str
    # Нормализованный статус: new | in_progress | ready | completed | cancelled
    status: str
    original_status: str            # оригинальный статус POS-системы
    updated_at: str | None = None


@dataclass
class PosClientData:
    """Данные клиента из POS для кеширования в TelegramProfile"""
    orders_count: int
    ltv: int                        # в UZS
    avg_check: int                  # в UZS
    external_id: str | None = None
    last_order_date: str | None = None


class PosConnector(ABC):
    """
    Абстрактный адаптер POS-системы.
    Реализации: MockPosAdapter, MoySkladAdapter, BillzAdapter, JowiAdapter.
    """

    @abstractmethod
    async def test_connection(self) -> bool:
        """Проверить что API доступен и ключи валидны"""
        ...

    @abstractmethod
    async def sync_catalog(self) -> list[PosProduct]:
        """
        Забрать все товары из POS.
        Возвращает список PosProduct.
        Маппинг на нашу БД выполняется в CatalogSyncService по external_id.
        """
        ...

    @abstractmethod
    async def push_order(
        self,
        order_id: int,
        items: list[dict],          # [{"external_id": ..., "quantity": N, "price": N}]
        total_amount: int,
        discount_amount: int,
        customer_name: str | None,
        customer_phone: str | None,
        payment_method: str,        # "cash" | "click" | "payme" | "uzum"
        delivery_type: str | None = None,  # "pickup" | "delivery" (для Jowi)
    ) -> PosOrderResult:
        """
        Отправить заказ в POS-систему.
        Возвращает external_order_id при успехе.
        """
        ...

    @abstractmethod
    async def get_order_status(self, external_order_id: str) -> PosOrderStatus:
        """Проверить статус заказа (fallback если нет webhook)"""
        ...

    @abstractmethod
    async def get_client_data(
        self,
        phone: str | None = None,
        external_id: str | None = None,
    ) -> PosClientData | None:
        """Получить данные клиента из POS для кеширования в TelegramProfile"""
        ...
