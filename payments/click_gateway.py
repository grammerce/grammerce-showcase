"""
Click gateway — реальная интеграция через Click Shop API.

Click использует двухстадийный протокол:
  1. Prepare (action=0) — Click проверяет заказ на нашем сервере
  2. Complete (action=1) — Click подтверждает оплату

Документация: https://docs.click.uz/
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from payments.base import PaymentGateway, PaymentResult
from payments.utils import verify_click_sign

log = logging.getLogger(__name__)

CLICK_PAY_URL = "https://my.click.uz/services/pay"


class ClickGateway(PaymentGateway):
    def __init__(
        self,
        service_id: str,
        merchant_id: str,
        merchant_user_id: str,
        secret_key: str,
    ):
        self.service_id = service_id
        self.merchant_id = merchant_id
        self.merchant_user_id = merchant_user_id
        self.secret_key = secret_key

    async def create_payment(
        self,
        order_id: int,
        amount: int,
        description: str,  # noqa: ARG002 — не используется Click'ом (передаётся через transaction_param)
        return_url: str,
    ) -> PaymentResult:
        """
        Формирует URL для оплаты через Click.
        Click не создаёт транзакцию заранее — URL сам по себе является ссылкой.
        payment_id будет получен из Prepare webhook (Click шлёт его первым).
        """
        params = {
            "service_id": self.service_id,
            "merchant_id": self.merchant_id,
            "amount": amount,
            "transaction_param": order_id,   # Click передаст это обратно как merchant_trans_id
            "return_url": return_url,
        }
        payment_url = f"{CLICK_PAY_URL}?{urlencode(params)}"
        log.info("Click payment URL for order #%s: %s", order_id, payment_url)
        # payment_id будет создан в prepare-webhook (Payment.id из нашей БД)
        return PaymentResult(success=True, payment_url=payment_url, payment_id=None)

    async def verify_webhook(self, request_data: dict, headers: dict) -> bool:  # noqa: ARG002
        """Проверяет MD5-подпись от Click."""
        return verify_click_sign(request_data, self.secret_key)

    async def get_payment_status(self, payment_id: str) -> str:  # noqa: ARG002
        """
        Для Click нет прямого API статуса по нашему payment_id.
        Статус хранится в нашей БД — вернуть 'pending' как заглушку.
        Реальный статус читается из БД в роутере.
        """
        return "pending"

    async def cancel_payment(self, payment_id: str) -> bool:
        """
        Click не поддерживает отмену через API из нашей стороны.
        Отмена возможна только через кабинет Click.
        """
        log.warning("Click cancel_payment called for %s — not supported via API", payment_id)
        return False

    # Helpers для формирования ответов на Prepare / Complete

    @staticmethod
    def success_prepare(click_trans_id: int, merchant_trans_id: str, prepare_id: int) -> dict:
        return {
            "click_trans_id": click_trans_id,
            "merchant_trans_id": merchant_trans_id,
            "merchant_prepare_id": prepare_id,
            "error": 0,
            "error_note": "Success",
        }

    @staticmethod
    def success_complete(click_trans_id: int, merchant_trans_id: str, confirm_id: int) -> dict:
        return {
            "click_trans_id": click_trans_id,
            "merchant_trans_id": merchant_trans_id,
            "merchant_confirm_id": confirm_id,
            "error": 0,
            "error_note": "Success",
        }

    @staticmethod
    def error_response(click_trans_id: int, merchant_trans_id: str, code: int, note: str) -> dict:
        """
        Click error codes:
          -1  SIGN CHECK FAILED
          -2  Incorrect parameter amount
          -4  Already paid
          -5  Order not found
          -6  Transaction does not exist
          -9  Transaction cancelled
        """
        return {
            "click_trans_id": click_trans_id,
            "merchant_trans_id": merchant_trans_id,
            "error": code,
            "error_note": note,
        }
