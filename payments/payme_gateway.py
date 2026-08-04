"""
Payme gateway — JSON-RPC протокол.

Без реальных ключей (PAYME_ID/PAYME_KEY пустые) → MockGateway.
С ключами → реальный Payme через checkout.paycom.uz.

Суммы: Payme работает в тийинах (1 сум = 100 тийин).
Auth: Basic Auth header — Base64(merchant_id:key).
"""
from __future__ import annotations

import base64
import logging
import os
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import JSONResponse

from payments.base import PaymentGateway, PaymentResult
from payments.utils import tiyin_to_uzs, uzs_to_tiyin

log = logging.getLogger(__name__)

PAYME_METHODS = {
    "CheckPerformTransaction",
    "CreateTransaction",
    "PerformTransaction",
    "CancelTransaction",
    "CheckTransaction",
}


class PaymeGateway(PaymentGateway):
    def __init__(self, merchant_id: str, key: str):
        self.merchant_id = merchant_id
        self.key = key
        self.is_mock = not (merchant_id and key)

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
            return await MockGateway(provider_name="payme", base_url=base_url).create_payment(
                order_id, amount, description, return_url
            )
        # Реальная ссылка: Base64(m=MERCHANT_ID;ac.order_id=42;a=<тийины>)
        amount_tiyin = uzs_to_tiyin(amount)
        payload = f"m={self.merchant_id};ac.order_id={order_id};a={amount_tiyin}"
        encoded = base64.b64encode(payload.encode()).decode()
        payment_url = f"https://checkout.paycom.uz/{encoded}"
        log.info("Payme payment URL for order #%s", order_id)
        return PaymentResult(success=True, payment_url=payment_url, payment_id=None)

    def verify_basic_auth(self, authorization: str) -> bool:
        try:
            if not authorization.startswith("Basic "):
                return False
            decoded = base64.b64decode(authorization[6:]).decode()
            import hmac
            return hmac.compare_digest(decoded, f"{self.merchant_id}:{self.key}")
        except Exception:
            return False

    async def verify_webhook(self, request_data: dict, headers: dict) -> bool:
        auth = headers.get("authorization") or headers.get("Authorization", "")
        return self.verify_basic_auth(auth)

    async def get_payment_status(self, payment_id: str) -> str:  # noqa: ARG002
        return "pending"

    async def cancel_payment(self, payment_id: str) -> bool:  # noqa: ARG002
        return False

    @staticmethod
    def rpc_error(code: int, message: str, request_id=None) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": {"ru": message, "uz": message, "en": message}},
        }

    @staticmethod
    def rpc_result(result: dict, request_id=None) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


async def handle_payme_rpc(request: Request, gateway: PaymeGateway, db) -> JSONResponse:
    """Центральный обработчик Payme JSON-RPC webhook."""
    from sqlalchemy import select

    from models import Order
    from payments.models import Payment

    auth = request.headers.get("Authorization", "")
    if not gateway.is_mock and not gateway.verify_basic_auth(auth):
        return JSONResponse(PaymeGateway.rpc_error(-32504, "Unauthorized"))

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(PaymeGateway.rpc_error(-32700, "Parse error"))

    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    if method not in PAYME_METHODS:
        return JSONResponse(PaymeGateway.rpc_error(-32601, f"Method not found: {method}", req_id))

    account = params.get("account", {})
    order_id_raw = account.get("order_id")

    if method == "CheckPerformTransaction":
        if not order_id_raw:
            return JSONResponse(PaymeGateway.rpc_error(-31050, "order_id not provided", req_id))
        order = (await db.execute(select(Order).where(Order.id == int(order_id_raw)))).scalar_one_or_none()
        if not order:
            return JSONResponse(PaymeGateway.rpc_error(-31050, "Order not found", req_id))
        amount_tiyin = params.get("amount", 0)
        # Для дропшип-заказов с частичной предоплатой ожидаемая сумма = prepaid_amount.
        expected_uzs = int(order.prepaid_amount) if order.prepaid_amount is not None else int(order.total_amount)
        expected_tiyin = uzs_to_tiyin(expected_uzs)
        if abs(amount_tiyin - expected_tiyin) > 100:
            return JSONResponse(PaymeGateway.rpc_error(-31001, "Incorrect amount", req_id))
        if order.status in ("paid", "partially_paid", "confirmed", "completed"):
            return JSONResponse(PaymeGateway.rpc_error(-31099, "Already paid", req_id))
        return JSONResponse(PaymeGateway.rpc_result({"allow": True}, req_id))

    elif method == "CreateTransaction":
        payme_trans_id = params.get("id")
        amount_tiyin = params.get("amount", 0)
        if not order_id_raw:
            return JSONResponse(PaymeGateway.rpc_error(-31050, "order_id not provided", req_id))
        order = (await db.execute(select(Order).where(Order.id == int(order_id_raw)))).scalar_one_or_none()
        if not order:
            return JSONResponse(PaymeGateway.rpc_error(-31050, "Order not found", req_id))
        if order.status in ("paid", "partially_paid", "confirmed", "completed"):
            return JSONResponse(PaymeGateway.rpc_error(-31099, "Already paid", req_id))
        existing = (await db.execute(
            select(Payment).where(Payment.provider == "payme", Payment.provider_id == payme_trans_id)
        )).scalar_one_or_none()
        if existing:
            return JSONResponse(PaymeGateway.rpc_result({
                "create_time": int(existing.created_at.timestamp() * 1000),
                "transaction": str(existing.id),
                "state": 1,
            }, req_id))
        payment = Payment(
            order_id=order.id, shop_id=order.shop_id, provider="payme",
            provider_id=payme_trans_id, amount=tiyin_to_uzs(amount_tiyin),
            status="pending", meta={"payme_trans_id": payme_trans_id},
        )
        db.add(payment)
        await db.flush()
        await db.commit()
        return JSONResponse(PaymeGateway.rpc_result({
            "create_time": int(payment.created_at.timestamp() * 1000),
            "transaction": str(payment.id),
            "state": 1,
        }, req_id))

    elif method == "PerformTransaction":
        payme_trans_id = params.get("id")
        payment = (await db.execute(
            select(Payment).where(Payment.provider == "payme", Payment.provider_id == payme_trans_id)
        )).scalar_one_or_none()
        if not payment:
            return JSONResponse(PaymeGateway.rpc_error(-31003, "Transaction not found", req_id))
        if payment.status == "paid":
            perform_ms = int(payment.paid_at.timestamp() * 1000) if payment.paid_at else 0
            return JSONResponse(PaymeGateway.rpc_result({
                "transaction": str(payment.id), "perform_time": perform_ms, "state": 2,
            }, req_id))
        if payment.status == "cancelled":
            return JSONResponse(PaymeGateway.rpc_error(-31008, "Transaction cancelled", req_id))
        now = datetime.now(UTC)
        payment.status = "paid"
        payment.paid_at = now
        order = (await db.execute(select(Order).where(Order.id == payment.order_id))).scalar_one_or_none()
        if order and order.status not in ("paid", "partially_paid"):
            from payments.router import resolve_paid_status
            order.status = resolve_paid_status(order)
        await db.commit()
        log.info("[Payme] PerformTransaction: order #%s status=%s", payment.order_id, order.status if order else "?")
        
        # Уведомления
        from routers.public import notify_customer_payment, notify_shop_admin_payment
        await notify_shop_admin_payment(order, payment, db)
        await notify_customer_payment(order, payment, db)

        return JSONResponse(PaymeGateway.rpc_result({
            "transaction": str(payment.id),
            "perform_time": int(now.timestamp() * 1000),
            "state": 2,
        }, req_id))

    elif method == "CancelTransaction":
        payme_trans_id = params.get("id")
        reason = params.get("reason", 0)
        payment = (await db.execute(
            select(Payment).where(Payment.provider == "payme", Payment.provider_id == payme_trans_id)
        )).scalar_one_or_none()
        if not payment:
            return JSONResponse(PaymeGateway.rpc_error(-31003, "Transaction not found", req_id))
        if payment.status == "paid":
            return JSONResponse(PaymeGateway.rpc_error(-31007, "Cannot cancel paid transaction", req_id))
        payment.status = "cancelled"
        payment.meta = {**payment.meta, "cancel_reason": reason}
        await db.commit()
        return JSONResponse(PaymeGateway.rpc_result({
            "transaction": str(payment.id),
            "cancel_time": int(datetime.now(UTC).timestamp() * 1000),
            "state": -1,
        }, req_id))

    elif method == "CheckTransaction":
        payme_trans_id = params.get("id")
        payment = (await db.execute(
            select(Payment).where(Payment.provider == "payme", Payment.provider_id == payme_trans_id)
        )).scalar_one_or_none()
        if not payment:
            return JSONResponse(PaymeGateway.rpc_error(-31003, "Transaction not found", req_id))
        state_map = {"pending": 1, "paid": 2, "cancelled": -1, "failed": -2}
        state = state_map.get(payment.status, 0)
        return JSONResponse(PaymeGateway.rpc_result({
            "create_time": int(payment.created_at.timestamp() * 1000),
            "perform_time": int(payment.paid_at.timestamp() * 1000) if payment.paid_at else 0,
            "cancel_time": 0,
            "transaction": str(payment.id),
            "state": state,
            "reason": None,
        }, req_id))

    return JSONResponse(PaymeGateway.rpc_error(-32601, "Method not implemented", req_id))
