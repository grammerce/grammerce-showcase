"""
Абстрактный базовый класс платёжного шлюза.
Все провайдеры (Click, Payme, Uzum, Mock) реализуют этот интерфейс.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PaymentResult:
    success: bool
    payment_url: str | None   # Ссылка для редиректа клиента
    payment_id: str | None    # ID транзакции в системе провайдера
    error: str | None = None


class PaymentGateway(ABC):
    """Абстрактный платёжный шлюз."""

    @abstractmethod
    async def create_payment(
        self,
        order_id: int,
        amount: int,           # В сумах (UZS), целое число
        description: str,
        return_url: str,       # Куда вернуть клиента после оплаты
    ) -> PaymentResult:
        """Создать платёж и получить ссылку на оплату."""
        ...

    @abstractmethod
    async def verify_webhook(self, request_data: dict, headers: dict) -> bool:
        """Проверить подлинность webhook от провайдера."""
        ...

    @abstractmethod
    async def get_payment_status(self, payment_id: str) -> str:
        """Получить статус платежа: pending / paid / failed / cancelled."""
        ...

    @abstractmethod
    async def cancel_payment(self, payment_id: str) -> bool:
        """Отменить/вернуть платёж."""
        ...
