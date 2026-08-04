import asyncio
import logging
from typing import Any

from integrations.accounting.base import AccountingProvider
from models import Order, OrderItem

log = logging.getLogger(__name__)

class MockAccountingProvider(AccountingProvider):
    """
    Mock-провайдер для тестирования интеграции с AZMA.
    Имитирует сетевую задержку, успешную генерацию чека
    и отправку сводного отчета.
    """

    async def sync_order(self, order: Order, items: list[OrderItem]) -> dict[str, Any]:
        """
        Имитируем запрос к API AZMA для генерации фискального чека.
        """
        log.info(f"[AZMA Mock] Начинаем синхронизацию заказа #{order.id} (UUID: {order.order_uuid})")
        
        # Имитируем задержку сети 2 секунды
        await asyncio.sleep(2)
        
        # Имитируем успешный ответ
        external_id = f"azma-mock-{order.id}-{order.order_uuid[:8]}"
        fiscal_url = f"https://soliq.uz/test/receipt/{external_id}"
        
        log.info(f"[AZMA Mock] Заказ #{order.id} успешно синхронизирован. URL: {fiscal_url}")
        
        return {
            "ok": True,
            "external_id": external_id,
            "fiscal_url": fiscal_url
        }

    async def send_report(self, shop_id: int, period: str) -> dict[str, Any]:
        """
        Имитируем отправку сводного Z-отчета или декларации.
        """
        log.info(f"[AZMA Mock] Отправка сводного отчета для shop_id={shop_id}, период={period}")
        
        await asyncio.sleep(1)
        
        return {
            "ok": True,
            "message": "Отчет успешно принят в песочнице"
        }

# Глобальный инстанс для использования
mock_accounting = MockAccountingProvider()
