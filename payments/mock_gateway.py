"""
Mock-провайдер платежей.

Полностью имитирует платёжный процесс без реальных денег:
- create_payment → возвращает URL на внутреннюю mock-страницу
- GET /payments/mock/checkout/{payment_id} — страница «Оплатить / Отклонить»
- Нажатие кнопки → POST /payments/mock/complete → меняет статус
"""
from __future__ import annotations

import logging
import uuid

from payments.base import PaymentGateway, PaymentResult

log = logging.getLogger(__name__)

# Хранилище mock-платежей в памяти (для тестов — достаточно)
# Формат: {payment_id: {"status": str, "order_id": int, "amount": int, ...}}
_MOCK_STORE: dict[str, dict] = {}


class MockGateway(PaymentGateway):
    """
    Имитирует любой провайдер (Click/Payme/Uzum) для локального тестирования.
    provider_name — строка, которая будет показана на mock-странице.
    base_url — базовый URL сервера (для формирования ссылок).
    """

    def __init__(self, provider_name: str = "mock", base_url: str = "http://localhost:8000"):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")

    async def create_payment(
        self,
        order_id: int,
        amount: int,
        description: str,
        return_url: str,
    ) -> PaymentResult:
        payment_id = f"mock_{uuid.uuid4().hex[:12]}"
        _MOCK_STORE[payment_id] = {
            "status": "pending",
            "order_id": order_id,
            "amount": amount,
            "description": description,
            "return_url": return_url,
            "provider": self.provider_name,
        }
        checkout_url = f"{self.base_url}/payments/mock/checkout/{payment_id}"
        log.info("[MOCK] %s payment created for order #%s → %s", self.provider_name, order_id, checkout_url)
        return PaymentResult(success=True, payment_url=checkout_url, payment_id=payment_id)

    async def verify_webhook(self, request_data: dict, headers: dict) -> bool:
        # Mock всегда доверяет своим собственным запросам
        return request_data.get("_mock_secret") == "mock_ok"

    async def get_payment_status(self, payment_id: str) -> str:
        entry = _MOCK_STORE.get(payment_id)
        if not entry:
            return "not_found"
        return entry["status"]

    async def cancel_payment(self, payment_id: str) -> bool:
        entry = _MOCK_STORE.get(payment_id)
        if not entry:
            return False
        entry["status"] = "cancelled"
        log.info("[MOCK] Payment %s cancelled", payment_id)
        return True

    # Вспомогательные методы для тестов и mock-эндпоинтов

    @staticmethod
    def simulate_pay(payment_id: str) -> bool:
        """Имитирует успешную оплату (вызывается из mock-эндпоинта и тестов)."""
        entry = _MOCK_STORE.get(payment_id)
        if not entry or entry["status"] != "pending":
            return False
        entry["status"] = "paid"
        log.info("[MOCK] Payment %s → paid", payment_id)
        return True

    @staticmethod
    def simulate_decline(payment_id: str) -> bool:
        """Имитирует отклонение оплаты."""
        entry = _MOCK_STORE.get(payment_id)
        if not entry or entry["status"] != "pending":
            return False
        entry["status"] = "failed"
        log.info("[MOCK] Payment %s → failed", payment_id)
        return True

    @staticmethod
    def get_entry(payment_id: str) -> dict | None:
        return _MOCK_STORE.get(payment_id)

    @staticmethod
    def clear_store() -> None:
        """Очистить хранилище (для тестов)."""
        _MOCK_STORE.clear()
