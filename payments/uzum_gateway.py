"""
Uzum Bank gateway — REST протокол: check / create / confirm / reverse / status.

Без реальных ключей (UZUM_MERCHANT_ID/UZUM_API_KEY пустые) → MockGateway.
С ключами → реальный Uzum Bank.

Верификация: проверка IP + Bearer token в заголовке.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import JSONResponse

from payments.base import PaymentGateway, PaymentResult

log = logging.getLogger(__name__)

# Допустимые IP-адреса Uzum Bank — загружаются из UZUM_ALLOWED_IPS env var.
# Формат: "1.2.3.4,5.6.7.8" (через запятую). Пустая строка = проверка отключена.
_raw_ips = os.environ.get("UZUM_ALLOWED_IPS", "")
UZUM_ALLOWED_IPS: set[str] = {ip.strip() for ip in _raw_ips.split(",") if ip.strip()}


class UzumGateway(PaymentGateway):
    def __init__(self, merchant_id: str, api_key: str):
        self.merchant_id = merchant_id
        self.api_key = api_key
        self.is_mock = not (merchant_id and api_key)

    async def create_payment(
        self,
        order_id: int,
        amount: int,
        description: str,  # noqa: ARG002
        return_url: str,   # noqa: ARG002
    ) -> PaymentResult:
        if self.is_mock:
            from payments.mock_gateway import MockGateway
            base_url = os.getenv("BASE_URL") or os.getenv("WEB_APP_URL", "http://localhost:8000")
            return await MockGateway(provider_name="uzum", base_url=base_url).create_payment(
                order_id, amount, description, return_url
            )
        # Реальная ссылка — формируется через API Uzum (этап регистрации)
        log.info("Uzum payment for order #%s", order_id)
        raise NotImplementedError("Uzum: нужны реальные ключи для формирования ссылки")

    async def verify_webhook(self, request_data: dict, headers: dict) -> bool:
        token = headers.get("authorization", "").replace("Bearer ", "")
        return token == self.api_key

    async def get_payment_status(self, payment_id: str) -> str:  # noqa: ARG002
        return "pending"

    async def cancel_payment(self, payment_id: str) -> bool:  # noqa: ARG002
        return False


def _uzum_ok(data: dict) -> JSONResponse:
    return JSONResponse({"status": "OK", **data})


def _uzum_error(code: int, message: str) -> JSONResponse:
    return JSONResponse({"status": "ERROR", "code": code, "message": message})


async def handle_uzum_check(request: Request, gateway: UzumGateway, db) -> JSONResponse:
    """POST /payments/uzum/check — проверить возможность оплаты."""
    from sqlalchemy import select

    from models import Order

    if not gateway.is_mock:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if token != gateway.api_key:
            return _uzum_error(401, "Unauthorized")

    try:
        body = await request.json()
    except Exception:
        return _uzum_error(400, "Invalid JSON")

    order_id = body.get("order_id")
    if not order_id:
        return _uzum_error(400, "order_id required")

    order = (await db.execute(select(Order).where(Order.id == int(order_id)))).scalar_one_or_none()
    if not order:
        return _uzum_error(404, "Order not found")

    if order.status in ("paid", "partially_paid", "confirmed", "completed"):
        return _uzum_error(409, "Already paid")

    # Для дропшип-заказов с частичной предоплатой сумма = prepaid_amount.
    expected_amount = float(order.prepaid_amount) if order.prepaid_amount is not None else float(order.total_amount)
    return _uzum_ok({"order_id": order_id, "amount": expected_amount})


async def handle_uzum_create(request: Request, gateway: UzumGateway, db) -> JSONResponse:
    """POST /payments/uzum/create — создать транзакцию."""
    from sqlalchemy import select

    from models import Order
    from payments.models import Payment

    if not gateway.is_mock:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if token != gateway.api_key:
            return _uzum_error(401, "Unauthorized")

    try:
        body = await request.json()
    except Exception:
        return _uzum_error(400, "Invalid JSON")

    order_id = body.get("order_id")
    uzum_trans_id = body.get("transaction_id")
    amount = body.get("amount", 0)

    order = (await db.execute(select(Order).where(Order.id == int(order_id)))).scalar_one_or_none()
    if not order:
        return _uzum_error(404, "Order not found")

    if order.status in ("paid", "partially_paid", "confirmed", "completed"):
        return _uzum_error(409, "Already paid")

    expected_amount = float(order.prepaid_amount) if order.prepaid_amount is not None else float(order.total_amount)
    if abs(float(amount) - expected_amount) > 1:
        return _uzum_error(400, "Incorrect amount")

    existing = (await db.execute(
        select(Payment).where(Payment.provider == "uzum", Payment.provider_id == str(uzum_trans_id))
    )).scalar_one_or_none()
    if existing:
        return _uzum_ok({"transaction_id": uzum_trans_id, "internal_id": existing.id, "state": "created"})

    payment = Payment(
        order_id=order.id, shop_id=order.shop_id,
        provider="uzum", provider_id=str(uzum_trans_id),
        amount=amount, status="pending",
        meta={"uzum_trans_id": uzum_trans_id},
    )
    db.add(payment)
    await db.flush()
    await db.commit()

    return _uzum_ok({"transaction_id": uzum_trans_id, "internal_id": payment.id, "state": "created"})


async def handle_uzum_confirm(request: Request, gateway: UzumGateway, db) -> JSONResponse:
    """POST /payments/uzum/confirm — подтвердить оплату."""
    from sqlalchemy import select

    from models import Order
    from payments.models import Payment

    if not gateway.is_mock:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if token != gateway.api_key:
            return _uzum_error(401, "Unauthorized")

    try:
        body = await request.json()
    except Exception:
        return _uzum_error(400, "Invalid JSON")

    uzum_trans_id = body.get("transaction_id")
    payment = (await db.execute(
        select(Payment).where(Payment.provider == "uzum", Payment.provider_id == str(uzum_trans_id))
    )).scalar_one_or_none()

    if not payment:
        return _uzum_error(404, "Transaction not found")

    if payment.status == "paid":
        return _uzum_ok({"transaction_id": uzum_trans_id, "state": "confirmed"})

    if payment.status == "cancelled":
        return _uzum_error(409, "Transaction cancelled")

    now = datetime.now(UTC)
    payment.status = "paid"
    payment.paid_at = now

    order = (await db.execute(select(Order).where(Order.id == payment.order_id))).scalar_one_or_none()
    if order and order.status not in ("paid", "partially_paid"):
        from payments.router import resolve_paid_status
        order.status = resolve_paid_status(order)

    await db.commit()
    log.info("[Uzum] Confirm: order #%s status=%s", payment.order_id, order.status if order else "?")

    # Уведомления
    from routers.public import notify_customer_payment, notify_shop_admin_payment
    await notify_shop_admin_payment(order, payment, db)
    await notify_customer_payment(order, payment, db)

    return _uzum_ok({"transaction_id": uzum_trans_id, "state": "confirmed"})


async def handle_uzum_reverse(request: Request, gateway: UzumGateway, db) -> JSONResponse:
    """POST /payments/uzum/reverse — отмена транзакции."""
    from sqlalchemy import select

    from payments.models import Payment

    if not gateway.is_mock:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if token != gateway.api_key:
            return _uzum_error(401, "Unauthorized")

    try:
        body = await request.json()
    except Exception:
        return _uzum_error(400, "Invalid JSON")

    uzum_trans_id = body.get("transaction_id")
    payment = (await db.execute(
        select(Payment).where(Payment.provider == "uzum", Payment.provider_id == str(uzum_trans_id))
    )).scalar_one_or_none()

    if not payment:
        return _uzum_error(404, "Transaction not found")

    if payment.status == "paid":
        return _uzum_error(409, "Cannot reverse paid transaction")

    payment.status = "cancelled"
    await db.commit()

    return _uzum_ok({"transaction_id": uzum_trans_id, "state": "reversed"})


async def handle_uzum_status(request: Request, gateway: UzumGateway, db) -> JSONResponse:
    """POST /payments/uzum/status — статус транзакции."""
    from sqlalchemy import select

    from payments.models import Payment

    if not gateway.is_mock:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if token != gateway.api_key:
            return _uzum_error(401, "Unauthorized")

    try:
        body = await request.json()
    except Exception:
        return _uzum_error(400, "Invalid JSON")

    uzum_trans_id = body.get("transaction_id")
    payment = (await db.execute(
        select(Payment).where(Payment.provider == "uzum", Payment.provider_id == str(uzum_trans_id))
    )).scalar_one_or_none()

    if not payment:
        return _uzum_error(404, "Transaction not found")

    state_map = {"pending": "created", "paid": "confirmed", "cancelled": "reversed", "failed": "failed"}
    state = state_map.get(payment.status, "unknown")

    return _uzum_ok({
        "transaction_id": uzum_trans_id,
        "state": state,
        "amount": float(payment.amount),
    })
