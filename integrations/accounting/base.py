# Accounting Integrations
from abc import ABC, abstractmethod
from typing import Any

from models import Order, OrderItem


class AccountingProvider(ABC):
    """
    Абстрактный интерфейс провайдера бухгалтерии/фискализации (Паттерн 'Адаптер').
    """

    @abstractmethod
    async def sync_order(self, order: Order, items: list[OrderItem]) -> dict[str, Any]:
        """
        Синхронизация заказа. Срабатывает при переводе в статус 'Принят'.
        Должен возвращать словарь с ключами:
        - ok: bool
        - external_id: str (Опционально)
        - fiscal_url: str (Опционально)
        - error: str (Опционально, если ok=False)
        """
        pass

    @abstractmethod
    async def send_report(self, shop_id: int, period: str) -> dict[str, Any]:
        """
        Отправка сводного отчета по продажам за период (Аналитика).
        Возвращает:
        - ok: bool
        - error: str (если ok=False)
        """
        pass
