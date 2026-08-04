"""
Webhook-эндпоинты от POS-систем.

POST /api/webhooks/pos/{shop_id}       — универсальный приёмник (с верификацией подписи)
POST /api/webhooks/pos/mock/{shop_id}  — для Mock-адаптера (без верификации, только вне production)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings as app_settings
from database import get_db
from models import Order, Shop
from services.profile_service import ProfileService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks/pos", tags=["POS webhooks"])

# Маппинг: нормализованный статус → наш статус заказа
_STATUS_TRANSITIONS = {
    "new": "new",
    "in_progress": "processing",
    "ready": "ready",
    "completed": "completed",
    "cancelled": "cancelled",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _verify_signature(body: bytes, secret: str, signature_header: str | None) -> bool:
    """HMAC-SHA256 верификация подписи от POS."""
    if not secret or not signature_header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


async def _process_status_change(
    db: AsyncSession,
    shop: Shop,
    external_order_id: str,
    new_pos_status: str,
    original_status: str,
) -> None:
    """
    Обновить статус заказа, обновить telegram_profiles.cached_*,
    уведомить покупателя через бота.
    """
    order_result = await db.execute(
        select(Order).where(
            Order.shop_id == shop.id,
            Order.external_order_id == external_order_id,
        )
    )
    order = order_result.scalar_one_or_none()
    if not order:
        log.warning("Webhook: order with external_id=%s not found for shop %s", external_order_id, shop.id)
        return

    our_status = _STATUS_TRANSITIONS.get(new_pos_status, new_pos_status)
    old_status = order.status
    order.external_status = original_status
    order.status = our_status
    order.integration_status = "confirmed"

    if our_status == "completed":
        # Обновить TelegramProfile из POS-данных
        await _update_profile_from_pos(db, shop, order)

    await db.commit()

    # Уведомить покупателя
    if old_status != our_status and shop.bot_token:
        await _notify_customer_status(shop, order, our_status)

    log.info("Webhook: order #%s status %s → %s (POS: %s)", order.id, old_status, our_status, original_status)


async def _update_profile_from_pos(db: AsyncSession, shop: Shop, order: Order) -> None:
    """
    При завершении заказа из POS → обновить cached_* в TelegramProfile.
    Вызывается только если data_source != 'local' (POS = источник правды).
    """
    from integrations.factory import get_pos_connector

    meta = order.meta or {}
    customer_phone = meta.get("customer_phone")
    telegram_user_id = meta.get("telegram_user_id")
    if not telegram_user_id:
        return

    connector = get_pos_connector(shop)
    if connector is None:
        return

    try:
        pos_data = await connector.get_client_data(phone=customer_phone)
        if pos_data:
            svc = ProfileService(db)
            await svc.get_or_create(
                shop_id=shop.id,
                telegram_id=int(telegram_user_id),
            )
            await svc.update_from_pos(shop.id, int(telegram_user_id), {
                "orders_count": pos_data.orders_count,
                "ltv": pos_data.ltv,
                "avg_check": pos_data.avg_check,
                "last_order_date": pos_data.last_order_date,
                "data_source": shop.integration_settings.get("type", "pos"),
            })
    except Exception as e:
        log.warning("Failed to update profile from POS for order #%s: %r", order.id, e)


async def _notify_customer_status(shop: Shop, order: Order, status: str) -> None:
    """Уведомить покупателя об изменении статуса заказа."""
    meta = order.meta or {}
    customer_tg_id = meta.get("telegram_user_id")
    if not customer_tg_id:
        return

    status_texts = {
        "processing": "⚙️ Ваш заказ принят в работу!",
        "ready": "✅ Ваш заказ готов к выдаче!",
        "completed": "🎉 Заказ доставлен. Спасибо за покупку!",
        "cancelled": "❌ К сожалению, ваш заказ был отменён.",
    }
    text = status_texts.get(status)
    if not text:
        return

    bot = Bot(token=shop.bot_token, parse_mode="HTML")
    try:
        await bot.send_message(
            int(customer_tg_id),
            f"{text}\n\nЗаказ #{order.id} — {int(order.total_amount):,} сум",
        )
    except Exception as e:
        log.warning("Failed to notify customer for order #%s: %r", order.id, e)
    finally:
        await bot.session.close()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/mock/{shop_id}")
async def webhook_mock(
    shop_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Mock-вебхук для ручного тестирования.
    Body: {"external_order_id": "MOCK-42-...", "status": "completed"}
    """
    # Аутентификации здесь нет by design, поэтому в production роут обязан
    # отсутствовать: иначе любой аноним меняет статусы чужих заказов и рассылает
    # покупателям Telegram-уведомления. Тот же гейт стоит на mock-платежах
    # (payments/router.py).
    if app_settings.is_production:
        raise HTTPException(404, "Not found")

    body = await request.json()
    external_order_id = body.get("external_order_id")
    status = body.get("status", "completed")

    if not external_order_id:
        raise HTTPException(400, "external_order_id required")

    shop_result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = shop_result.scalar_one_or_none()
    if not shop:
        raise HTTPException(404, "Shop not found")

    await _process_status_change(db, shop, external_order_id, status, status)
    return {"ok": True, "processed": external_order_id}


@router.post("/{shop_id}")
async def webhook_pos(
    shop_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Универсальный вебхук от POS.
    Верифицирует подпись из заголовка X-Webhook-Signature.
    Поддерживает: МойСклад (action=UPDATE, entityType=customerorder).
    """
    raw_body = await request.body()

    shop_result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = shop_result.scalar_one_or_none()
    if not shop:
        raise HTTPException(404, "Shop not found")

    settings = shop.integration_settings or {}
    webhook_secret = settings.get("webhook_secret")
    signature = request.headers.get("X-Webhook-Signature")

    # Fail-closed: раньше при незаполненном webhook_secret проверка подписи
    # пропускалась целиком, и вебхук становился анонимной точкой смены статуса
    # заказов (+ рассылка уведомлений покупателям). Нет секрета — нет приёма.
    if not webhook_secret:
        log.warning("POS webhook rejected: no webhook_secret configured for shop_id=%s", shop_id)
        raise HTTPException(403, "Webhook secret not configured")

    if not _verify_signature(raw_body, webhook_secret, signature):
        raise HTTPException(403, "Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON")

    pos_type = settings.get("type", "none")
    processed = 0

    if pos_type == "moysklad":
        # МойСклад: {"events": [{"meta": {...}, "action": "UPDATE"}]}
        for event in payload.get("events", []):
            if event.get("action") == "UPDATE":
                meta_href = event.get("meta", {}).get("href", "")
                external_id = meta_href.split("/")[-1] if "/" in meta_href else None
                if external_id:
                    # Получить актуальный статус через адаптер
                    from integrations.factory import get_pos_connector
                    connector = get_pos_connector(shop)
                    if connector:
                        try:
                            pos_status = await connector.get_order_status(external_id)
                            await _process_status_change(
                                db, shop,
                                external_id,
                                pos_status.status,
                                pos_status.original_status,
                            )
                            processed += 1
                        except Exception as e:
                            log.error("Webhook MoySklad: error processing event: %r", e)
    else:
        # Общий формат: {"external_order_id": "...", "status": "..."}
        external_order_id = payload.get("external_order_id")
        status = payload.get("status")
        if external_order_id and status:
            await _process_status_change(db, shop, external_order_id, status, status)
            processed += 1

    return {"ok": True, "processed": processed}
