"""
payments/router.py — Webhook и mock-эндпоинты.

Подключается в main.py:
    from payments.router import router as payments_router
    app.include_router(payments_router)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from database import get_db
from models import Invoice, Order, Shop
from payments.click_gateway import ClickGateway
from payments.mock_gateway import MockGateway
from payments.models import Payment
from payments.payme_gateway import PaymeGateway, handle_payme_rpc
from payments.utils import verify_click_sign
from payments.uzum_gateway import (
    UzumGateway,
    handle_uzum_check,
    handle_uzum_confirm,
    handle_uzum_create,
    handle_uzum_reverse,
    handle_uzum_status,
)
from services.funnel_sync import sync_owner_funnel_state

log = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])

_ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")
_MOCK_ENABLED = _ENVIRONMENT != "production"


def _guard_unconfigured_provider(is_mock: bool) -> None:
    """В продакшне запрещаем обработку вебхуков провайдера без ключей (is_mock).

    Иначе неподключённый Payme/Uzum остаётся публичным эндпоинтом «пометить
    заказ оплаченным» без аутентификации. Вне прода (dev/test) — mock разрешён.
    """
    if is_mock and not _MOCK_ENABLED:
        raise HTTPException(404, "Payment provider not configured")

# Mock checkout страница (только вне продакшна)

MOCK_CHECKOUT_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mock Payment Gateway</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #f0f2f5;
           display: flex; align-items: center; justify-content: center;
           min-height: 100vh; margin: 0; }}
    .card {{ background: white; border-radius: 16px; padding: 32px 40px;
             box-shadow: 0 4px 24px rgba(0,0,0,.12); max-width: 360px; width: 100%; }}
    h2 {{ margin: 0 0 8px; font-size: 20px; color: #1a1a2e; }}
    .badge {{ display: inline-block; background: #fff3cd; color: #856404;
              border-radius: 8px; padding: 2px 10px; font-size: 13px; margin-bottom: 20px; }}
    .row {{ display: flex; justify-content: space-between; margin: 8px 0;
            font-size: 15px; color: #444; }}
    .amount {{ font-size: 28px; font-weight: 700; color: #1a1a2e; margin: 16px 0; }}
    .btn {{ width: 100%; padding: 14px; border: none; border-radius: 10px;
            font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 10px;
            transition: opacity .2s; }}
    .btn:hover {{ opacity: .85; }}
    .btn-pay  {{ background: #2ecc71; color: white; }}
    .btn-fail {{ background: #e74c3c; color: white; }}
    form {{ margin: 0; }}
  </style>
</head>
<body>
<div class="card">
  <div class="badge">⚙️ Mock Payment Gateway</div>
  <h2>Оплата заказа</h2>
  <div class="row"><span>Заказ #</span><span>{order_id}</span></div>
  <div class="row"><span>Провайдер</span><span>{provider}</span></div>
  <div class="row"><span>Описание</span><span>{description}</span></div>
  <div class="amount">{amount:,} сум</div>

  <form method="POST" action="/payments/mock/complete/{payment_id}">
    <input type="hidden" name="action" value="pay">
    <button class="btn btn-pay" type="submit">✅ Оплатить</button>
  </form>
  <form method="POST" action="/payments/mock/complete/{payment_id}">
    <input type="hidden" name="action" value="decline">
    <button class="btn btn-fail" type="submit">❌ Отклонить</button>
  </form>
</div>
</body>
</html>"""


@router.get("/mock/checkout/{payment_id}", response_class=HTMLResponse)
async def mock_checkout(payment_id: str) -> HTMLResponse:
    """Mock-страница оплаты — показывает детали и кнопки Оплатить/Отклонить."""
    if not _MOCK_ENABLED:
        raise HTTPException(404, "Not found")
    entry = MockGateway.get_entry(payment_id)
    if not entry:
        raise HTTPException(404, f"Payment {payment_id} not found")

    html = MOCK_CHECKOUT_HTML.format(
        payment_id=payment_id,
        order_id=entry["order_id"],
        provider=entry["provider"].upper(),
        description=entry.get("description", "—"),
        amount=int(entry["amount"]),
    )
    return HTMLResponse(content=html)


@router.post("/mock/complete/{payment_id}")
async def mock_complete(
    payment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """
    Обрабатывает нажатие кнопки на mock-странице.
    action=pay     → simulate_pay → статус paid
    action=decline → simulate_decline → статус failed
    """
    if not _MOCK_ENABLED:
        raise HTTPException(404, "Not found")
    import traceback

    status_text = "⚠️ Неизвестная ошибка"
    status_color = "#e74c3c"
    return_url = "/"

    try:
        form = await request.form()
        action = form.get("action", "pay")

        entry = MockGateway.get_entry(payment_id)
        if not entry:
            raise HTTPException(404, f"Payment {payment_id} not found")

        return_url = entry.get("return_url", "/")

        log.info("[MOCK WEBHOOK] payment_id=%s order_id=%s action=%s",
                 payment_id, entry["order_id"], action)

        # Найти запись Payment
        payment = None
        try:
            payment = (await db.execute(
                select(Payment).where(
                    Payment.provider_id == payment_id,
                    Payment.provider.in_(["mock", "click", "payme", "uzum"]),
                )
            )).scalar_one_or_none()
        except Exception as e:
            log.warning("[MOCK] Payment query by provider_id failed: %r", e)

        if not payment:
            try:
                payment = (await db.execute(
                    select(Payment)
                    .where(Payment.order_id == entry["order_id"])
                    .order_by(Payment.id.desc())
                )).scalar_one_or_none()
            except Exception as e:
                log.warning("[MOCK] Payment query by order_id failed: %r", e)

        order = None
        try:
            order = (await db.execute(
                select(Order).where(Order.id == entry["order_id"])
            )).scalar_one_or_none()
        except Exception as e:
            log.warning("[MOCK] Order query failed: %r", e)

        if action == "pay":
            MockGateway.simulate_pay(payment_id)
            if payment:
                payment.status = "paid"
                payment.paid_at = datetime.now(UTC)
            if order and order.status not in ("paid", "partially_paid"):
                order.status = resolve_paid_status(order)
            try:
                await db.commit()
            except Exception as e:
                log.error("[MOCK] DB commit failed: %r", e)
                await db.rollback()
            log.info("[MOCK] Order #%s marked as PAID", entry["order_id"])

            # Telegram-уведомления об оплате
            if order and payment:
                try:
                    from routers.public import notify_customer_payment, notify_shop_admin_payment
                    await notify_shop_admin_payment(order, payment, db)
                    if order.meta and order.meta.get("telegram_user_id"):
                        await notify_customer_payment(order, payment, db)
                except Exception as e:
                    log.error("[MOCK] Notification failed: %r", e)

            status_text = "✅ Оплата прошла успешно!"
            status_color = "#2ecc71"
        else:
            MockGateway.simulate_decline(payment_id)
            if payment:
                payment.status = "failed"
            if order:
                order.status = "payment_failed"
            try:
                await db.commit()
            except Exception as e:
                log.error("[MOCK] DB commit failed: %r", e)
                await db.rollback()
            log.info("[MOCK] Order #%s marked as payment_failed", entry["order_id"])

            status_text = "❌ Оплата отклонена"
            status_color = "#e74c3c"

    except HTTPException:
        raise
    except Exception as e:
        log.error("[MOCK WEBHOOK] Unhandled error: %s\n%s", e, traceback.format_exc())
        status_text = "⚠️ Ошибка обработки платежа"
        status_color = "#e74c3c"

    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<meta http-equiv="refresh" content="2;url={return_url}">
<style>body{{font-family:system-ui;display:flex;align-items:center;
justify-content:center;min-height:100vh;margin:0;background:#f0f2f5;}}
.msg{{text-align:center;font-size:22px;font-weight:600;color:{status_color};}}</style>
</head><body><div class="msg">{status_text}<br><small>Перенаправление...</small></div></body></html>"""
    return HTMLResponse(content=html)


# Click Prepare / Complete

def _click_error(click_trans_id: int, merchant_trans_id: str, code: int, note: str) -> JSONResponse:
    return JSONResponse({
        "click_trans_id": click_trans_id,
        "merchant_trans_id": merchant_trans_id,
        "error": code,
        "error_note": note,
    })


def resolve_paid_status(order: Order) -> str:
    """
    Вернуть корректный статус заказа после успешной предоплаты.
    Для дропшип-заказа с частичной предоплатой → "partially_paid",
    иначе → "paid". Используется во всех gateway-хендлерах.
    """
    if order.prepaid_amount is not None and float(order.prepaid_amount) < float(order.total_amount):
        return "partially_paid"
    return "paid"


async def _resolve_click_secret(db: AsyncSession, shop_id: int | None) -> str:
    """
    Multi-tenant: вернуть secret_key для магазина.
    Приоритет: shop.config.cart.payments.click.secret_key → env CLICK_SECRET_KEY.

    Если shop_id не задан или магазин не найден — fallback на env.
    Это соответствует приоритетам в payments.factory.get_gateway().
    """
    if shop_id is not None:
        shop = (await db.execute(select(Shop).where(Shop.id == shop_id))).scalar_one_or_none()
        if shop and shop.config:
            _payments_cfg = (shop.config.get("cart") or {}).get("payments") or {}
            _click = _payments_cfg.get("click") or {}
            key = _click.get("secret_key") or shop.config.get("CLICK_SECRET_KEY")
            if key:
                log.debug("Click secret_key resolved from shop.config (shop_id=%s)", shop_id)
                return key
    env_key = os.getenv("CLICK_SECRET_KEY", "")
    log.debug("Click secret_key resolved from env (shop_id=%s, key_set=%s)", shop_id, bool(env_key))
    return env_key


@router.get("/click/prepare", response_class=JSONResponse)
@router.get("/click/complete", response_class=JSONResponse)
async def click_health():
    """GET-ответ для валидации URL в кабинете Click."""
    return JSONResponse({"status": "ok"})


@router.post("/click/prepare", response_class=JSONResponse)
async def click_prepare(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """
    Click Prepare (action=0).
    Click проверяет: существует ли заказ, верна ли сумма.
    Мы создаём запись Payment и возвращаем merchant_prepare_id.
    """
    form = await request.form()
    data = dict(form)

    # Безопасный лог входящего запроса (без sign_string)
    log.info(
        "Click Prepare incoming: service_id=%s merchant_trans_id=%s amount=%s action=%s",
        data.get("service_id"), data.get("merchant_trans_id"),
        data.get("amount"), data.get("action"),
    )

    # 0. Парсинг базовых полей
    try:
        click_trans_id = int(data.get("click_trans_id", 0))
        merchant_trans_id = str(data.get("merchant_trans_id", ""))
    except (ValueError, TypeError) as exc:
        log.error("Click Prepare: bad input fields: %r", exc)
        return JSONResponse({"click_trans_id": 0, "merchant_trans_id": "", "error": -5, "error_note": "Bad request"})

    def _err(code: int, note: str) -> JSONResponse:
        log.warning("Click Prepare ERROR %s (%s): click_trans_id=%s merchant_trans_id=%s",
                    code, note, click_trans_id, merchant_trans_id)
        return _click_error(click_trans_id, merchant_trans_id, code, note)

    try:
        # === Billing invoice payment (transaction_param = "billing_{inv.id}") ===
        if merchant_trans_id.startswith("billing_"):
            try:
                invoice_id = int(merchant_trans_id.split("_", 1)[1])
            except (ValueError, TypeError):
                return _err(-5, "Invoice not found")

            secret_key = os.getenv("CLICK_SECRET_KEY", "")
            if not verify_click_sign(data, secret_key):
                return _err(-1, "SIGN CHECK FAILED")

            inv = (await db.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            )).scalar_one_or_none()
            if not inv:
                return _err(-5, "Invoice not found")
            if inv.status == "paid":
                return _err(-4, "Already paid")
            if inv.status == "cancelled":
                return _err(-9, "Transaction cancelled")

            try:
                click_amount = float(data.get("amount", 0))
            except (ValueError, TypeError):
                return _err(-2, "Incorrect parameter amount")

            if abs(click_amount - float(inv.total_amount)) > 1:
                return _err(-2, "Incorrect parameter amount")

            log.info("Click Prepare (billing) SUCCESS: invoice #%s click_trans_id=%s",
                     invoice_id, click_trans_id)
            return JSONResponse(ClickGateway.success_prepare(
                click_trans_id=click_trans_id,
                merchant_trans_id=merchant_trans_id,
                prepare_id=invoice_id,
            ))

        # 1. Найти заказ — нужен раньше подписи, чтобы достать shop.config.secret_key
        try:
            order_id = int(merchant_trans_id)
        except (ValueError, TypeError):
            return _err(-5, "Order not found")

        order = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
        if not order:
            # Нет заказа — нет shop.config, подпись проверяем по env-ключу.
            secret_key = await _resolve_click_secret(db, None)
            if not verify_click_sign(data, secret_key):
                return _err(-1, "SIGN CHECK FAILED")
            return _err(-5, "Order not found")

        # 2. Multi-tenant: secret_key из shop.config этого магазина (fallback на env)
        secret_key = await _resolve_click_secret(db, order.shop_id)
        if not verify_click_sign(data, secret_key):
            return _err(-1, "SIGN CHECK FAILED")

        # 3. Проверить, не оплачен ли уже заказ
        if order.status in ("paid", "partially_paid", "confirmed", "completed"):
            return _err(-4, "Already paid")

        if order.status == "cancelled":
            return _err(-9, "Transaction cancelled")

        # 4. Проверить сумму (±1 сум допуск для float).
        # Для дропшип-заказов с частичной предоплатой ожидаемая сумма = prepaid_amount,
        # для обычных заказов = total_amount.
        try:
            click_amount = float(data.get("amount", 0))
        except (ValueError, TypeError):
            return _err(-2, "Incorrect parameter amount")

        expected_amount = float(order.prepaid_amount) if order.prepaid_amount is not None else float(order.total_amount)
        if abs(click_amount - expected_amount) > 1:
            log.warning(
                "Click Prepare amount mismatch: click=%.2f expected=%.2f order_id=%s",
                click_amount, expected_amount, order_id,
            )
            return _err(-2, "Incorrect parameter amount")

        # 5. Создать Payment запись (или найти существующую pending)
        existing = (await db.execute(
            select(Payment).where(
                Payment.order_id == order_id,
                Payment.provider == "click",
                Payment.status == "pending",
            )
        )).scalar_one_or_none()

        if existing:
            payment = existing
        else:
            payment = Payment(
                order_id=order_id,
                shop_id=order.shop_id,
                provider="click",
                provider_id=str(click_trans_id),
                amount=expected_amount,
                status="pending",
                meta={"click_trans_id": click_trans_id},
            )
            db.add(payment)
            await db.flush()

        await db.commit()

        log.info("Click Prepare SUCCESS: order #%s click_trans_id=%s payment_id=%s",
                 order_id, click_trans_id, payment.id)

        return JSONResponse(ClickGateway.success_prepare(
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
            prepare_id=payment.id,
        ))

    except Exception as exc:
        log.exception("Click Prepare UNHANDLED EXCEPTION: click_trans_id=%s merchant_trans_id=%s: %r",
                      click_trans_id, merchant_trans_id, exc)
        return _click_error(click_trans_id, merchant_trans_id, -5, "Internal error")


@router.post("/click/complete", response_class=JSONResponse)
async def click_complete(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """
    Click Complete (action=1).
    Click подтверждает оплату. Обновляем Payment + Order, шлём уведомления.
    """
    form = await request.form()
    data = dict(form)

    # Безопасный лог входящего запроса (без sign_string)
    log.info(
        "Click Complete incoming: service_id=%s merchant_trans_id=%s merchant_prepare_id=%s amount=%s",
        data.get("service_id"), data.get("merchant_trans_id"),
        data.get("merchant_prepare_id"), data.get("amount"),
    )

    # 0. Парсинг базовых полей
    try:
        click_trans_id = int(data.get("click_trans_id", 0))
        merchant_trans_id = str(data.get("merchant_trans_id", ""))
    except (ValueError, TypeError) as exc:
        log.error("Click Complete: bad input fields: %r", exc)
        return JSONResponse({"click_trans_id": 0, "merchant_trans_id": "", "error": -6, "error_note": "Bad request"})

    def _err(code: int, note: str) -> JSONResponse:
        log.warning("Click Complete ERROR %s (%s): click_trans_id=%s merchant_trans_id=%s",
                    code, note, click_trans_id, merchant_trans_id)
        return _click_error(click_trans_id, merchant_trans_id, code, note)

    try:
        # === Billing invoice payment (transaction_param = "billing_{inv.id}") ===
        if merchant_trans_id.startswith("billing_"):
            try:
                invoice_id = int(merchant_trans_id.split("_", 1)[1])
            except (ValueError, TypeError):
                return _err(-6, "Transaction does not exist")

            secret_key = os.getenv("CLICK_SECRET_KEY", "")
            if not verify_click_sign(data, secret_key):
                return _err(-1, "SIGN CHECK FAILED")

            inv = (await db.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            )).scalar_one_or_none()
            if not inv:
                return _err(-6, "Transaction does not exist")

            if inv.status == "paid":
                return JSONResponse(ClickGateway.success_complete(
                    click_trans_id=click_trans_id,
                    merchant_trans_id=merchant_trans_id,
                    confirm_id=invoice_id,
                ))
            if inv.status == "cancelled":
                return _err(-9, "Transaction cancelled")

            try:
                click_amount = float(data.get("amount", 0))
            except (ValueError, TypeError):
                return _err(-2, "Incorrect parameter amount")

            if abs(click_amount - float(inv.total_amount)) > 1:
                return _err(-2, "Incorrect parameter amount")

            # Пометить инвойс как оплаченный через sync InvoiceService
            db_url = os.getenv("DATABASE_URL", "sqlite:///./local.db")
            sync_url = (db_url
                        .replace("sqlite+aiosqlite", "sqlite")
                        .replace("postgresql+asyncpg", "postgresql"))
            from services.invoice_service import InvoiceService

            eng = create_engine(sync_url)
            SM = sessionmaker(bind=eng)
            with SM() as sess:
                svc = InvoiceService(sess)
                svc.mark_as_paid(invoice_id)

            shop_id = inv.shop_id
            asyncio.create_task(sync_owner_funnel_state(shop_id))

            log.info("Click Complete (billing) SUCCESS: invoice #%s PAID via Click (click_trans_id=%s)",
                     invoice_id, click_trans_id)
            return JSONResponse(ClickGateway.success_complete(
                click_trans_id=click_trans_id,
                merchant_trans_id=merchant_trans_id,
                confirm_id=invoice_id,
            ))

        # 1. Найти Payment по merchant_prepare_id — нужен раньше подписи, чтобы
        # достать shop.config.secret_key этого магазина (multi-tenant).
        try:
            prepare_id = int(data.get("merchant_prepare_id", 0))
        except (ValueError, TypeError):
            return _err(-6, "Transaction does not exist")

        payment = (await db.execute(
            select(Payment).where(Payment.id == prepare_id)
        )).scalar_one_or_none()

        if not payment:
            # Нет payment — нет shop.config, подпись проверяем по env-ключу.
            secret_key = await _resolve_click_secret(db, None)
            if not verify_click_sign(data, secret_key):
                return _err(-1, "SIGN CHECK FAILED")
            return _err(-6, "Transaction does not exist")

        # 2. Multi-tenant: secret_key из shop.config (fallback на env)
        secret_key = await _resolve_click_secret(db, payment.shop_id)
        if not verify_click_sign(data, secret_key):
            return _err(-1, "SIGN CHECK FAILED")

        # 3. Идемпотентность: уже оплачен — OK
        if payment.status == "paid":
            log.info("Click Complete idempotent: payment #%s already paid", payment.id)
            return JSONResponse(ClickGateway.success_complete(
                click_trans_id=click_trans_id,
                merchant_trans_id=merchant_trans_id,
                confirm_id=payment.id,
            ))

        # 4. Отменён — ошибка
        if payment.status == "cancelled":
            return _err(-9, "Transaction cancelled")

        # 5. Найти заказ
        order = (await db.execute(
            select(Order).where(Order.id == payment.order_id)
        )).scalar_one_or_none()

        if not order:
            return _err(-5, "Order not found")

        # 6. Проверить сумму
        try:
            click_amount = float(data.get("amount", 0))
        except (ValueError, TypeError):
            return _err(-2, "Incorrect parameter amount")

        if abs(click_amount - float(payment.amount)) > 1:
            log.warning(
                "Click Complete amount mismatch: click=%.2f payment=%.2f payment_id=%s",
                click_amount, float(payment.amount), payment.id,
            )
            return _err(-2, "Incorrect parameter amount")

        # 7. Отметить как оплаченный
        payment.status = "paid"
        payment.paid_at = datetime.now(UTC)
        payment.provider_id = str(click_trans_id)
        payment.meta = {**payment.meta, "complete_data": data}

        if order.status not in ("paid", "partially_paid"):
            order.status = resolve_paid_status(order)
        await db.commit()

        log.info("Click Complete SUCCESS: order #%s PAID via Click (click_trans_id=%s payment_id=%s)",
                 order.id, click_trans_id, payment.id)

        # 8. Уведомления — не даём им сломать ответ Click
        try:
            from routers.public import notify_customer_payment, notify_shop_admin_payment
            await notify_shop_admin_payment(order, payment, db)
            await notify_customer_payment(order, payment, db)
        except Exception as notify_exc:
            log.error("Click Complete: notification failed (order #%s): %r", order.id, notify_exc)

        return JSONResponse(ClickGateway.success_complete(
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
            confirm_id=payment.id,
        ))

    except Exception as exc:
        log.exception("Click Complete UNHANDLED EXCEPTION: click_trans_id=%s merchant_trans_id=%s: %r",
                      click_trans_id, merchant_trans_id, exc)
        return _click_error(click_trans_id, merchant_trans_id, -6, "Internal error")


# Payme JSON-RPC webhook

def _get_payme_gateway() -> PaymeGateway:
    return PaymeGateway(
        merchant_id=os.getenv("PAYME_ID", ""),
        key=os.getenv("PAYME_KEY", ""),
    )


@router.post("/payme/webhook", response_class=JSONResponse)
async def payme_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """
    Payme JSON-RPC endpoint.
    Обрабатывает: CheckPerformTransaction, CreateTransaction,
    PerformTransaction, CancelTransaction, CheckTransaction.
    Без ключей (PAYME_ID пустой) — принимает mock-запросы.
    """
    gw = _get_payme_gateway()
    _guard_unconfigured_provider(gw.is_mock)
    return await handle_payme_rpc(request, gw, db)


# Uzum Bank webhooks

def _get_uzum_gateway() -> UzumGateway:
    return UzumGateway(
        merchant_id=os.getenv("UZUM_MERCHANT_ID", ""),
        api_key=os.getenv("UZUM_API_KEY", ""),
    )


def _check_uzum_ip(request: Request) -> None:
    """Блокировать запросы не с разрешённых IP Uzum Bank.
    Проверка активна только если UZUM_ALLOWED_IPS непустой."""
    from payments.uzum_gateway import UZUM_ALLOWED_IPS
    if not UZUM_ALLOWED_IPS:
        return  # IP-фильтрация отключена (dev/не настроено)
    client_ip = request.client.host if request.client else ""
    if client_ip not in UZUM_ALLOWED_IPS:
        log.warning("Uzum webhook blocked: IP %s not in whitelist", client_ip)
        raise HTTPException(403, "Forbidden")


def _uzum_guarded(request: Request) -> UzumGateway:
    """Гейт для Uzum-вебхуков: в проде без ключей — 404, плюс IP-фильтр."""
    gw = _get_uzum_gateway()
    _guard_unconfigured_provider(gw.is_mock)
    _check_uzum_ip(request)
    return gw


@router.post("/uzum/check", response_class=JSONResponse)
async def uzum_check(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Uzum: проверить возможность оплаты."""
    return await handle_uzum_check(request, _uzum_guarded(request), db)


@router.post("/uzum/create", response_class=JSONResponse)
async def uzum_create(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Uzum: создать транзакцию."""
    return await handle_uzum_create(request, _uzum_guarded(request), db)


@router.post("/uzum/confirm", response_class=JSONResponse)
async def uzum_confirm(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Uzum: подтвердить оплату."""
    return await handle_uzum_confirm(request, _uzum_guarded(request), db)


@router.post("/uzum/reverse", response_class=JSONResponse)
async def uzum_reverse(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Uzum: отменить транзакцию."""
    return await handle_uzum_reverse(request, _uzum_guarded(request), db)


@router.post("/uzum/status", response_class=JSONResponse)
async def uzum_status(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Uzum: статус транзакции."""
    return await handle_uzum_status(request, _uzum_guarded(request), db)
