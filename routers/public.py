"""
Public Router — Shop storefront API (no authentication required).
Extracted from main.py STAGE_05.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

# Короткий публичный кэш витринных данных. Правки каталога из кабинета видны
# на витрине в пределах max-age (≤30 с) — приемлемый компромисс.
_PUBLIC_CACHE = "public, max-age=30, stale-while-revalidate=120"
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import BotUser, Category, Customer, Order, OrderItem, Product, Promocode, Shop
from payments.factory import get_gateway
from payments.models import Payment
from routers.helpers import get_shop_or_404, parse_discount
from schemas import OrderCreate, TurnkeyRequestIn
from services.platform_bot_notify import send_owner_telegram
from services.profile_service import ProfileService
from utils.i18n import detect_language, get_text
from utils.order_status_token import make_order_status_token, verify_order_status_token
from utils.shop_config import public_shop_config
from utils.telegram_auth import extract_user_data, verify_telegram_web_app_data

log = logging.getLogger(__name__)

router = APIRouter(tags=["public"])


# Helpers

def _dropship_lines(order: Order) -> str:
    """
    Форматирует строки про предоплату/остаток для дропшип-заказов.
    Возвращает готовый блок с \n в начале, либо "" если заказ не дропшип-частичный.
    """
    if order.prepaid_amount is None:
        return ""
    total = int(order.total_amount)
    prepaid = int(order.prepaid_amount)
    if prepaid >= total:
        return ""
    remaining = total - prepaid
    pct = None
    try:
        pct = int(order.meta.get("prepayment_percent")) if order.meta else None
    except (TypeError, ValueError):
        pct = None
    pct_label = f" ({pct}%)" if pct else ""
    return (
        f"\n💳 <b>Предоплата{pct_label}:</b> {prepaid:,} сум"
        f"\n🕐 <b>Остаток при получении:</b> {remaining:,} сум"
    )


async def _get_shop_admin_ids(shop_id: int, owner_tg_id: int | None, db: AsyncSession) -> list[int]:
    from models import BotAdmin
    recipients = [owner_tg_id] if owner_tg_id else []
    bot_admins_result = await db.execute(
        select(BotAdmin.telegram_id).where(
            BotAdmin.shop_id == shop_id,
            BotAdmin.is_active == True,
            BotAdmin.invite_used == True,
            BotAdmin.telegram_id > 0
        )
    )
    recipients.extend(bot_admins_result.scalars().all())
    return list(set(recipients))


async def _notify_owner_via_platform_bot(shop: Shop, text: str, db: AsyncSession) -> None:
    """Фоллбэк: в боте магазина нет получателей (нет owner_tg_id и не назначен BotAdmin) →
    шлём уведомление о заказе владельцу через официальный бот Grammerce по его
    User.telegram_id (аккаунт, которым он регистрировался). Без инлайн-кнопок — их
    обрабатывает бот магазина, а не официальный бот."""
    from models import User
    if not shop.owner_id:
        return
    res = await db.execute(select(User.telegram_id).where(User.id == shop.owner_id))
    owner_tg = res.scalar_one_or_none()
    if not owner_tg:
        log.info("Shop %s: owner has no platform telegram_id — order notification skipped", shop.id)
        return
    hint = "\n\nℹ️ Назначьте администратора в боте магазина, чтобы управлять заказами прямо там."
    await send_owner_telegram(owner_tg, text + hint)


async def notify_shop_admin(shop_id: int, order: Order, db: AsyncSession) -> None:
    """Send notification to shop owner about new order"""
    try:
        result = await db.execute(select(Shop).where(Shop.id == shop_id))
        shop = result.scalar_one_or_none()

        if not shop or not shop.bot_token:
            log.warning("Shop %s not found or has no bot token", shop_id)
            return

        items_text = "\n".join([
            f"• {item.product.name if item.product else 'Unknown'} x{item.quantity} = {int(item.price_at_moment * item.quantity):,} сум"
            for item in order.items
        ])

        discount_percent = order.meta.get('discount_percent', 0) if order.meta else 0
        discount_line = ""
        if discount_percent and float(discount_percent) > 0 and order.discount:
            original = int(order.original_total) if order.original_total else int(order.total_amount)
            pct_display = int(discount_percent) if float(discount_percent) == int(discount_percent) else discount_percent
            discount_line = (
                f"\n🎁 <b>Скидка {pct_display}%:</b> −{int(order.discount):,} сум"
                f"\n💵 <b>Сумма без скидки:</b> {original:,} сум"
            )

        payment = order.meta.get('payment_method', 'cash') if order.meta else 'cash'
        payment_labels = {'cash': 'Наличными', 'click': 'Click', 'payme': 'PayMe', 'uzum': 'Uzum Bank'}
        payment_text = payment_labels.get(payment, payment)

        text = f"""
🛒 <b>Новый заказ #{order.number or order.id}</b>

{items_text}
{discount_line}
💰 <b>Итого:</b> {int(order.total_amount):,} сум{_dropship_lines(order)}
💳 <b>Оплата:</b> {payment_text}

👤 <b>Клиент:</b> {html_lib.escape(str(order.meta.get('customer_name') or 'Не указано'))}
📞 <b>Телефон:</b> {html_lib.escape(str(order.meta.get('customer_phone') or 'Не указано'))}
📍 <b>Адрес:</b> {html_lib.escape(str(order.meta.get('delivery_address') or 'Не указано'))}
"""

        if order.meta.get('gift_message'):
            text += f"\n💌 <b>Открытка:</b> {html_lib.escape(str(order.meta['gift_message']))}"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📞 Связаться",
                    callback_data=f"contact_client:{order.id}"
                ),
                InlineKeyboardButton(
                    text="✅ Заказ принят",
                    callback_data=f"accept_order:{order.id}"
                ),
            ]
        ])

        recipients = await _get_shop_admin_ids(shop_id, shop.owner_tg_id, db)
        if not recipients:
            await _notify_owner_via_platform_bot(shop, text, db)
            return

        # Точка доставки с карты витрины — до-отправляем гео отдельным сообщением (venue),
        # чтобы владелец видел место на карте, а не только текстовый адрес.
        delivery_lat = order.meta.get('delivery_lat')
        delivery_lon = order.meta.get('delivery_lon')

        admin_bot = Bot(token=shop.bot_token, parse_mode="HTML")
        try:
            for tg_id in recipients:
                try:
                    await admin_bot.send_message(tg_id, text, reply_markup=keyboard)
                except Exception as e:
                    log.warning("Failed to send order notification to admin %s: %r", tg_id, e)
                    continue
                if delivery_lat is not None and delivery_lon is not None:
                    try:
                        await admin_bot.send_venue(
                            chat_id=tg_id,
                            latitude=float(delivery_lat),
                            longitude=float(delivery_lon),
                            title="📍 Адрес доставки",
                            address=str(order.meta.get('delivery_address') or ""),
                        )
                    except Exception as e:
                        # Гео не должно ронять уведомление — текст уже ушёл
                        log.warning("Failed to send order location to admin %s: %r", tg_id, e)
        finally:
            await admin_bot.session.close()

    except Exception as e:
        log.error("Failed to notify admin for shop %s: %r", shop_id, e)


async def notify_shop_admin_payment(order: Order, payment: Payment, db: AsyncSession) -> None:
    """Уведомить админа об успешной онлайн-оплате заказа."""
    try:
        result = await db.execute(select(Shop).where(Shop.id == order.shop_id))
        shop = result.scalar_one_or_none()
        if not shop or not shop.bot_token:
            return

        provider_labels = {"click": "Click", "payme": "PayMe", "uzum": "Uzum Bank", "mock": "Mock"}
        provider_text = provider_labels.get(payment.provider, payment.provider.upper())

        ds_block = _dropship_lines(order)
        sum_line = (
            f"💰 <b>Оплачено:</b> {int(payment.amount):,} сум{ds_block}"
            if ds_block
            else f"💰 <b>Сумма:</b> {int(order.total_amount):,} сум"
        )
        text = (
            f"✅ <b>Оплата получена — Заказ #{order.number or order.id}</b>\n\n"
            f"💳 <b>Способ:</b> {provider_text}\n"
            f"{sum_line}\n"
            f"🕐 <b>Время:</b> {payment.paid_at.strftime('%d.%m.%Y %H:%M') if payment.paid_at else '—'}\n\n"
            f"👤 <b>Клиент:</b> {html_lib.escape(str(order.meta.get('customer_name') or '—'))}\n"
            f"📞 {html_lib.escape(str(order.meta.get('customer_phone') or '—'))}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📞 Связаться",
                    callback_data=f"contact_client:{order.id}"
                ),
                InlineKeyboardButton(
                    text="✅ Заказ принят",
                    callback_data=f"accept_order:{order.id}"
                ),
            ]
        ])

        recipients = await _get_shop_admin_ids(order.shop_id, shop.owner_tg_id, db)
        if not recipients:
            await _notify_owner_via_platform_bot(shop, text, db)
            return

        admin_bot = Bot(token=shop.bot_token, parse_mode="HTML")
        try:
            for tg_id in recipients:
                try:
                    await admin_bot.send_message(tg_id, text, reply_markup=keyboard)
                except Exception as e:
                    log.warning("Failed to notify admin %s about payment: %r", tg_id, e)
        finally:
            await admin_bot.session.close()
    except Exception as e:
        log.error("Failed to notify admin about payment for order %s: %r", order.id, e)


async def notify_customer_payment(order: Order, payment: Payment, db: AsyncSession) -> None:
    """Уведомить покупателя об успешной оплате через бота."""
    try:
        result = await db.execute(select(Shop).where(Shop.id == order.shop_id))
        shop = result.scalar_one_or_none()
        if not shop or not shop.bot_token:
            return

        customer_tg_id = order.meta.get("telegram_user_id") if order.meta else None
        if not customer_tg_id:
            return

        provider_labels = {"click": "Click", "payme": "PayMe", "uzum": "Uzum Bank", "mock": "Mock"}
        provider_text = provider_labels.get(payment.provider, payment.provider.upper())

        ds_block = _dropship_lines(order)
        if ds_block:
            text = (
                f"✅ <b>Предоплата получена!</b>\n\n"
                f"Заказ #{order.number or order.id} — {int(order.total_amount):,} сум\n"
                f"Способ: {provider_text}"
                f"{ds_block}\n\n"
                f"Товар заказан у поставщика. Мы свяжемся с вами, когда он будет готов к выдаче — остаток оплатите при получении."
            )
        else:
            text = (
                f"✅ <b>Оплата прошла успешно!</b>\n\n"
                f"Заказ #{order.number or order.id} — {int(order.total_amount):,} сум\n"
                f"Способ: {provider_text}\n\n"
                f"Спасибо за покупку! Ваш заказ обрабатывается."
            )
        customer_bot = Bot(token=shop.bot_token, parse_mode="HTML")
        try:
            await customer_bot.send_message(int(customer_tg_id), text)
        finally:
            await customer_bot.session.close()
    except Exception as e:
        log.error("Failed to notify customer about payment for order %s: %r", order.id, e)


async def _notify_customer_cash_order(
    shop_id: int,
    order: Order,
    customer_tg_id: int,
    db: AsyncSession,
) -> None:
    """Уведомить покупателя об успешном оформлении заказа (наличные)."""
    try:
        result = await db.execute(select(Shop).where(Shop.id == shop_id))
        shop = result.scalar_one_or_none()
        if not shop or not shop.bot_token:
            return

        discount_line = ""
        if order.discount and float(order.discount) > 0:
            discount_line = f"\n🎁 Скидка: −{int(order.discount):,} сум"

        ds_block = _dropship_lines(order)
        if ds_block:
            text = (
                f"✅ <b>Заказ #{order.number or order.id} принят!</b>\n\n"
                f"💰 Итого: {int(order.total_amount):,} сум{discount_line}"
                f"{ds_block}\n"
                f"💳 Оплата: Наличными\n\n"
                f"Товар приходит под заказ. Менеджер свяжется для согласования предоплаты — остаток оплатите при получении."
            )
        else:
            text = (
                f"✅ <b>Заказ #{order.number or order.id} принят!</b>\n\n"
                f"💰 Сумма к оплате: {int(order.total_amount):,} сум{discount_line}\n"
                f"💳 Оплата: Наличными\n\n"
                f"Менеджер свяжется с вами для подтверждения."
            )
        customer_bot = Bot(token=shop.bot_token, parse_mode="HTML")
        try:
            await customer_bot.send_message(customer_tg_id, text)
        finally:
            await customer_bot.session.close()
    except Exception as e:
        log.error("Failed to notify customer for cash order #%s: %r", order.id, e)


# Тексты уведомлений покупателю при смене статуса заказа владельцем в кабинете.
# Уведомляем только на ключевых переходах — промежуточные/технические статусы молчат.
_STATUS_CUSTOMER_MESSAGES = {
    "accepted": "✅ <b>Заказ #{num} принят!</b>\n\nМы приступили к его обработке.",
    "ready": "📦 <b>Заказ #{num} собран!</b>\n\nГотов к выдаче — скоро с вами свяжутся.",
    "delivered": "🎉 <b>Заказ #{num} выдан.</b>\n\nСпасибо за покупку!",
    "cancelled": "❌ <b>Заказ #{num} отменён.</b>\n\nЕсли это ошибка — свяжитесь с магазином.",
}


async def notify_customer_status_change(order: Order, new_status: str, db: AsyncSession) -> None:
    """Уведомить покупателя в бота о смене статуса заказа.

    Отправляет сообщение только для ключевых статусов (accepted/ready/delivered/
    cancelled). Ошибки не пробрасываются — уведомление не должно ломать смену статуса.
    """
    try:
        template = _STATUS_CUSTOMER_MESSAGES.get(new_status)
        if not template:
            return

        customer_tg_id = order.meta.get("telegram_user_id") if order.meta else None
        if not customer_tg_id:
            return

        result = await db.execute(select(Shop).where(Shop.id == order.shop_id))
        shop = result.scalar_one_or_none()
        if not shop or not shop.bot_token:
            return

        text = template.format(num=order.number or order.id)
        customer_bot = Bot(token=shop.bot_token, parse_mode="HTML")
        try:
            await customer_bot.send_message(int(customer_tg_id), text)
        finally:
            await customer_bot.session.close()
    except Exception as e:
        log.error("Failed to notify customer about status %s for order %s: %r", new_status, order.id, e)


# Public Shops List

@router.get("/api/public/shops", response_class=JSONResponse)
async def get_public_shops(db: AsyncSession = Depends(get_db)) -> dict:
    """Get all public shops for examples page (no authentication required)"""
    from sqlalchemy import func as sql_func

    # Подзапрос: количество товаров по магазинам
    products_sq = (
        select(Product.shop_id, sql_func.count(Product.id).label("cnt"))
        .group_by(Product.shop_id)
        .subquery()
    )
    # Подзапрос: количество клиентов по магазинам
    customers_sq = (
        select(Customer.shop_id, sql_func.count(Customer.id).label("cnt"))
        .group_by(Customer.shop_id)
        .subquery()
    )

    result = await db.execute(
        select(
            Shop,
            sql_func.coalesce(products_sq.c.cnt, 0).label("products_count"),
            sql_func.coalesce(customers_sq.c.cnt, 0).label("customers_count"),
        )
        .outerjoin(products_sq, Shop.id == products_sq.c.shop_id)
        .outerjoin(customers_sq, Shop.id == customers_sq.c.shop_id)
        .where(Shop.is_active == True)
        .order_by(Shop.id.desc())
    )
    rows = result.all()

    shops_data = [
        {
            "id": row.Shop.id,
            "name": row.Shop.name,
            "category": row.Shop.category,
            "products_count": row.products_count,
            "customers_count": row.customers_count,
        }
        for row in rows
    ]

    return {"shops": shops_data, "total": len(shops_data)}


@router.get("/api/public/showcase", response_class=JSONResponse)
async def get_showcase_shops(db: AsyncSession = Depends(get_db)) -> dict:
    """Витрины-кейсы для страницы /examples.

    Возвращает ТОЛЬКО магазины, помеченные флагом config.showcase.enabled=true
    (чужие клиентские магазины сюда не попадают). На каждый магазин — данные
    для живого iframe витрины и deep-link в Telegram-бота.
    """
    from sqlalchemy import func as sql_func

    products_sq = (
        select(Product.shop_id, sql_func.count(Product.id).label("cnt"))
        .group_by(Product.shop_id)
        .subquery()
    )

    result = await db.execute(
        select(
            Shop,
            sql_func.coalesce(products_sq.c.cnt, 0).label("products_count"),
        )
        .outerjoin(products_sq, Shop.id == products_sq.c.shop_id)
        .where(Shop.is_active == True)
    )
    rows = result.all()

    shops_data = []
    for row in rows:
        shop = row.Shop
        config = shop.config or {}
        showcase = config.get("showcase") or {}
        if not (isinstance(showcase, dict) and showcase.get("enabled")):
            continue

        bot_username = config.get("bot_username")
        bot_link = (
            f"https://t.me/{bot_username}?start=shop_{shop.id}"
            if bot_username else None
        )
        shops_data.append({
            "id": shop.id,
            "name": shop.name,
            "label": showcase.get("label") or shop.category or shop.name,
            "icon": showcase.get("icon") or "",
            "order": showcase.get("order") or 0,
            "products_count": row.products_count,
            "bot_username": bot_username,
            "bot_link": bot_link,
            "storefront_url": f"/shop/?shop_id={shop.id}",
        })

    shops_data.sort(key=lambda s: (s["order"], s["id"]))
    return {"shops": shops_data, "total": len(shops_data)}


@router.post("/api/public/turnkey-request", response_class=JSONResponse)
async def submit_turnkey_request(
    payload: TurnkeyRequestIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Заявка «под ключ» с публичного лендинга (без авторизации).

    Доставка в два места, как в кабинете (см. routers/onboarding.py:submit_delegated_request):
      1) Telegram менеджеру/суперадмину через send_manager_telegram (PLATFORM_MANAGER_CHAT_ID);
      2) запись PlatformSupportMessage в инбокс суперадмина под СИСТЕМНЫМ скрытым магазином
         «Заявки с лендинга» (config.system == 'landing_leads', is_active=false) — без изменения
         схемы (shop_id остаётся NOT NULL).
    Fire-and-forget: ошибка доставки не валит ответ клиенту.
    """
    import asyncio

    from models import PlatformSupportMessage
    from services.platform_bot_notify import send_manager_telegram

    lead_parts = [f"Имя: {payload.name}", f"Телефон: {payload.phone}"]
    if payload.contact_link:
        lead_parts.append(f"Instagram/Telegram: {payload.contact_link}")

    # 1) Инбокс суперадмина — под системным магазином-приёмником.
    try:
        result = await db.execute(
            select(Shop).where(Shop.is_active == False).order_by(Shop.id)
        )
        system_shop = next(
            (s for s in result.scalars().all()
             if isinstance(s.config, dict) and (s.config or {}).get("system") == "landing_leads"),
            None,
        )
        if system_shop is not None:
            db.add(PlatformSupportMessage(
                shop_id=system_shop.id,
                sender="owner",
                sender_user_id=None,
                text="\n".join(["[Заявка с лендинга — под ключ]", *lead_parts]),
                is_read=False,
            ))
            await db.commit()
        else:
            log.warning(
                "turnkey-request: системный магазин landing_leads не найден "
                "(нужна migration 043) — заявка уйдёт только в Telegram"
            )
    except Exception as exc:
        log.warning("turnkey-request: не удалось записать в инбокс суперадмина: %r", exc)

    # 2) Telegram менеджеру/суперадмину.
    tg_text = (
        f"<b>📩 Заявка с лендинга — под ключ</b>\n\n"
        f"Имя: {payload.name}\n"
        f"Телефон: {payload.phone}"
    )
    if payload.contact_link:
        tg_text += f"\nInstagram/Telegram: {payload.contact_link}"
    asyncio.create_task(send_manager_telegram(tg_text))

    log.info("turnkey-request: name=%s phone=%s", payload.name, payload.phone)
    return {"status": "ok"}


# Shop API (v1)

@router.get("/api/v1/shop/{shop_id}", response_class=JSONResponse)
async def get_shop(shop_id: int, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    """Get shop details"""
    shop = await get_shop_or_404(shop_id, db)

    response.headers["Cache-Control"] = _PUBLIC_CACHE
    return {
        "id": shop.id,
        "name": shop.name,
        # НИКОГДА не отдавать shop.config сырым: эндпоинт анонимный, а в config
        # лежат мерчант-креды платёжек (см. utils/shop_config.py).
        "config": public_shop_config(shop.config),
        "available_languages": shop.available_languages or ["ru"],
        "default_language": shop.default_language or "ru",
        "is_dropshipping": bool(shop.is_dropshipping),
        "prepayment_percent": int(shop.prepayment_percent or 100),
    }


@router.get("/api/v1/shop/{shop_id}/products", response_class=JSONResponse)
async def get_products(
    shop_id: int,
    response: Response,
    lang: str | None = None,
    limit: int = 100,
    offset: int = 0,
    category_id: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get products for a specific shop with pagination (default 100, max 500)."""
    from sqlalchemy import func as sa_func

    limit = min(limit, 500)  # cap at 500

    # Determine shop language settings
    shop = await get_shop_or_404(shop_id, db)

    response.headers["Cache-Control"] = _PUBLIC_CACHE

    avail = shop.available_languages or ["ru"]
    default = shop.default_language or "ru"
    resolved_lang = detect_language(lang, avail, default)

    # Base filter
    base_filter = [Product.shop_id == shop_id, Product.is_active == True]
    if category_id is not None:
        base_filter.append(Product.category_id == category_id)

    # Total count (without limit/offset)
    count_result = await db.execute(
        select(sa_func.count(Product.id)).where(*base_filter)
    )
    total = count_result.scalar() or 0

    # Paginated query
    result = await db.execute(
        select(Product)
        .where(*base_filter)
        .options(selectinload(Product.category))
        .order_by(Product.id)
        .limit(limit)
        .offset(offset)
    )
    products = result.scalars().all()

    def _product_name(p):
        if p.name_i18n:
            return get_text(p.name_i18n, resolved_lang, default, p.name)
        return p.name

    def _product_desc(p):
        if p.description_i18n:
            return get_text(p.description_i18n, resolved_lang, default, p.description or "")
        return p.description

    def _category_name(p):
        if p.category and p.category.name_i18n:
            return get_text(p.category.name_i18n, resolved_lang, default, p.category.name)
        return p.category.name if p.category else None

    return {
        "items": [
            {
                "id": p.id,
                "shop_id": p.shop_id,
                "name": _product_name(p),
                "description": _product_desc(p),
                "price": float(p.price),
                "old_price": float(p.old_price) if p.old_price else None,
                "sku": p.sku,
                "image_url": p.image_url,
                "images": p.images or [],
                "video_url": p.video_url,
                "stock": p.stock,
                "sold": p.sold,
                "is_available": p.is_active,
                "category_id": p.category_id,
                "category": _category_name(p),
                "variants": p.variants or [],
                "delivery_time_text": p.delivery_time_text,
                "availability_status": p.availability_status or "in_stock",
            }
            for p in products
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "lang": resolved_lang,
    }


@router.get("/api/v1/shop/{shop_id}/categories", response_class=JSONResponse)
async def get_categories(
    shop_id: int,
    response: Response,
    lang: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get all categories for a specific shop (with optional ?lang= localization)"""
    shop_result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = shop_result.scalar_one_or_none()
    avail = shop.available_languages if shop else ["ru"]
    default = shop.default_language if shop else "ru"
    resolved_lang = detect_language(lang, avail or ["ru"], default or "ru")

    response.headers["Cache-Control"] = _PUBLIC_CACHE

    result = await db.execute(
        select(Category)
        .where(Category.shop_id == shop_id)
        .order_by(Category.id)
    )
    categories = result.scalars().all()

    return {
        "items": [
            {
                "id": c.id,
                "shop_id": c.shop_id,
                "name": get_text(c.name_i18n, resolved_lang, default, c.name) if c.name_i18n else c.name,
                "description": get_text(c.description_i18n, resolved_lang, default, c.description or "") if c.description_i18n else c.description,
                "parent_id": c.parent_id,
            }
            for c in categories
        ],
        "total": len(categories)
    }


@router.post("/api/v1/shop/{shop_id}/orders", response_class=JSONResponse)
async def create_order(
    shop_id: int,
    order_data: OrderCreate,
    db: AsyncSession = Depends(get_db),
    x_telegram_init_data: str | None = Header(None, alias="X-Telegram-Init-Data")
) -> dict:
    """Create a new order for a specific shop"""

    result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = result.scalar_one_or_none()

    if not shop:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    telegram_user_id = None
    telegram_user_data = None

    if x_telegram_init_data:
        if not verify_telegram_web_app_data(x_telegram_init_data, shop.bot_token):
            log.warning("Invalid Telegram WebApp data received for shop %s", shop_id)
        else:
            telegram_user_data = extract_user_data(x_telegram_init_data)
            if telegram_user_data:
                telegram_user_id = telegram_user_data.get('id')
                log.info("Order from Telegram user %s (%s)", telegram_user_id, telegram_user_data.get('first_name'))

    # Нормализуем телефон: убираем пробелы, скобки, дефисы
    clean_phone = re.sub(r'[\s\-\(\)]', '', order_data.customer_phone)
    if not re.match(r'^\+998\d{9}$', clean_phone):
        raise HTTPException(
            status_code=400,
            detail="Неверный формат телефона. Используйте +998XXXXXXXXX"
        )

    # ── Авторитетные цены из БД (защита от подмены цены клиентом) ───────────
    # Цену НИКОГДА не берём из запроса: только из Product.price этого магазина.
    # Варианты товара влияют лишь на размер/остаток, но не на цену (см. витрину),
    # поэтому базовой цены товара достаточно.
    product_ids = [it.product_id for it in order_data.items if it.product_id]
    products_map = {}
    if product_ids:
        prods_result = await db.execute(
            select(Product).where(Product.id.in_(product_ids), Product.shop_id == shop_id)
        )
        products_map = {p.id: p for p in prods_result.scalars().all()}

    # Каждый товар заказа обязан существовать и принадлежать этому магазину.
    for it in order_data.items:
        if it.product_id not in products_map:
            raise HTTPException(status_code=400, detail=f"Товар недоступен (id={it.product_id})")

    def _unit_price(it) -> float:
        prod = products_map.get(it.product_id)
        return float(prod.price) if prod else 0.0

    subtotal = sum(_unit_price(item) * item.qty for item in order_data.items)

    min_order = shop.config.get('min_order', 0) if shop.config else 0
    if subtotal < min_order:
        raise HTTPException(
            status_code=400,
            detail=f"Минимальная сумма заказа: {int(min_order):,} сум"
        )

    customer_phone = clean_phone
    customer_telegram_id = telegram_user_id if telegram_user_id else 0

    # Получить скидку за регистрацию из BotUser.
    # Формат discount_registration: "20%" (процент) или "15000 сум" (фиксированная).
    discount_percent = 0.0          # сохраняем для meta/уведомлений (только для percent)
    reg_discount_amount = 0.0
    bot_user = None
    if customer_telegram_id:
        bu_result = await db.execute(
            select(BotUser).where(
                BotUser.shop_id == shop_id,
                BotUser.telegram_id == customer_telegram_id,
                BotUser.is_registered == True,
            )
        )
        bot_user = bu_result.scalar_one_or_none()
        if bot_user and bot_user.discount_registration:
            # percent сохраняем для meta/уведомлений (только для процентной скидки)
            discount_percent, reg_discount_amount = parse_discount(bot_user.discount_registration, subtotal)

    # Скидка по промокоду из профиля BotUser (активирован через бота)
    promo_user_discount = 0.0
    if bot_user and bot_user.discount_promo and not order_data.promo_code:
        _, promo_user_discount = parse_discount(bot_user.discount_promo, subtotal)

    # Скидка по промокоду (независимо от регистрационной)
    promo_discount_amount = 0.0
    applied_promo_code = None
    promo_applied_pids: list = []
    if order_data.promo_code:
        raw_code = order_data.promo_code.strip().upper()
        promo_result = await db.execute(
            select(Promocode).where(
                Promocode.shop_id == shop_id,
                Promocode.code == raw_code,
            )
        )
        promo = promo_result.scalar_one_or_none()

        promo_error = None
        if not promo:
            promo_error = "Промокод не найден"
        elif not promo.is_active:
            promo_error = "Промокод неактивен"
        elif promo.used_by and promo.used_by != customer_telegram_id:
            # Личный код можно применить только тому, кому он выдан.
            promo_error = "Этот промокод выдан другому клиенту"
        elif promo.expires_at and promo.expires_at < datetime.now(UTC):
            promo_error = "Срок действия промокода истёк"
        elif promo.max_usage is not None and promo.usage_count >= promo.max_usage:
            promo_error = "Промокод исчерпал лимит использований"

        if promo_error:
            raise HTTPException(status_code=400, detail=promo_error)

        # Определяем базу для расчёта скидки
        if promo.apply_to == "selected" and promo.product_ids:
            selected_pids = {int(pid) for pid in promo.product_ids}
            promo_base = sum(
                _unit_price(item) * item.qty
                for item in order_data.items
                if item.product_id in selected_pids
            )
            promo_applied_pids = list(selected_pids & {item.product_id for item in order_data.items})
        else:
            promo_base = subtotal
            promo_applied_pids = []

        if promo.discount_type == "percent":
            promo_discount_amount = round(promo_base * promo.value / 100, 2)
        else:
            promo_discount_amount = float(promo.value)

        applied_promo_code = promo

    total_discount = reg_discount_amount + promo_discount_amount + promo_user_discount
    # Скидка не может превышать сумму заказа
    total_discount = min(total_discount, subtotal)
    discount_amount = total_discount
    total = subtotal - total_discount

    result = await db.execute(
        select(Customer).where(
            Customer.shop_id == shop_id,
            Customer.telegram_id == customer_telegram_id
        ).limit(1)
    )
    customer = result.scalar_one_or_none()

    if not customer:
        customer = Customer(
            shop_id=shop_id,
            telegram_id=customer_telegram_id,
            first_name=order_data.customer_name or (telegram_user_data.get('first_name') if telegram_user_data else None),
            last_name=telegram_user_data.get('last_name') if telegram_user_data else None,
            username=telegram_user_data.get('username') if telegram_user_data else None,
            phone=customer_phone,
        )
        db.add(customer)
        await db.flush()
    elif not customer.phone:
        customer.phone = customer_phone

    # Определяем онлайн-оплату
    import os
    from decimal import ROUND_HALF_UP, Decimal
    online_payment_methods = {"click", "payme", "uzum"}
    payment_method = order_data.payment_method or "cash"
    is_online_payment = payment_method in online_payment_methods
    order_status = "pending_payment" if is_online_payment else "new"

    # Dropshipping: частичная предоплата. Для обычных магазинов prepay_percent=100.
    prepay_percent = int(shop.prepayment_percent or 100) if shop.is_dropshipping else 100
    if not 1 <= prepay_percent <= 100:
        prepay_percent = 100
    total_decimal = Decimal(str(total))
    if shop.is_dropshipping and prepay_percent < 100:
        prepay_amount = (total_decimal * Decimal(prepay_percent) / Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        prepaid_amount_db = prepay_amount
    else:
        prepay_amount = total_decimal
        prepaid_amount_db = None

    # Порядковый номер заказа в пределах магазина (#1, #2, …) для покупателя.
    from sqlalchemy import func as _func
    shop_orders_count = await db.scalar(
        select(_func.count(Order.id)).where(Order.shop_id == shop_id)
    )
    order_number = (shop_orders_count or 0) + 1

    new_order = Order(
        shop_id=shop_id,
        number=order_number,
        customer_id=customer.id,
        total_amount=total,
        original_total=subtotal,
        discount=discount_amount,
        status=order_status,
        prepaid_amount=prepaid_amount_db,
        meta={
            "customer_phone": customer_phone,
            "customer_name": order_data.customer_name,
            "delivery_address": order_data.delivery_address,
            "gift_message": order_data.gift_message,
            "payment_method": payment_method,
            "discount_percent": discount_percent,
            "promo_code": applied_promo_code.code if applied_promo_code else None,
            "promo_discount": float(promo_discount_amount),
            "promo_applied_pids": promo_applied_pids,
            "telegram_user_id": customer_telegram_id or None,
            "prepayment_percent": prepay_percent,
            "delivery_lat": order_data.lat,
            "delivery_lon": order_data.lon,
        }
    )
    db.add(new_order)
    await db.flush()

    # products_map уже загружен выше (авторитетные цены) — переиспользуем его
    # и для проверки остатков, без повторного запроса.

    # Validate stock before creating order
    for item_data in order_data.items:
        prod = products_map.get(item_data.product_id)
        if prod and prod.stock is not None and prod.stock > 0 and item_data.qty > prod.stock:
            raise HTTPException(
                status_code=400,
                detail=f"Товар «{prod.name}»: в наличии {prod.stock} шт., запрошено {item_data.qty}"
            )

    for item_data in order_data.items:
        order_item = OrderItem(
            order_id=new_order.id,
            product_id=item_data.product_id,
            quantity=item_data.qty,
            price_at_moment=_unit_price(item_data),  # снимок цены из БД, не из запроса
            selected_options=item_data.selected_options,
        )
        db.add(order_item)

        # Списываем остаток товара (из pre-loaded map, без дополнительных запросов)
        prod = products_map.get(item_data.product_id)
        if prod and prod.stock > 0:
            prod.stock = max(0, prod.stock - item_data.qty)
            prod.sold = (prod.sold or 0) + item_data.qty

    if applied_promo_code:
        applied_promo_code.usage_count += 1

    # Сжигаем одноразовую скидку за регистрацию после использования
    if reg_discount_amount > 0 and bot_user:
        bot_user.discount_registration = None
        log.info("Registration discount consumed for BotUser id=%s (shop=%s)", bot_user.id, shop_id)

    # Автосохраняем данные покупателя в BotUser чтобы профиль WebApp подтянулся после первого заказа
    if customer_telegram_id and not bot_user:
        try:
            bu_check = await db.execute(
                select(BotUser).where(
                    BotUser.shop_id == shop_id,
                    BotUser.telegram_id == customer_telegram_id,
                )
            )
            existing_bu = bu_check.scalar_one_or_none()
            if existing_bu:
                if not existing_bu.name and order_data.customer_name:
                    existing_bu.name = order_data.customer_name
                if not existing_bu.phone and customer_phone:
                    existing_bu.phone = customer_phone
                existing_bu.is_registered = True
            else:
                db.add(BotUser(
                    shop_id=shop_id,
                    telegram_id=customer_telegram_id,
                    name=order_data.customer_name or (telegram_user_data.get('first_name') if telegram_user_data else None),
                    phone=customer_phone,
                    is_registered=True,
                ))
        except Exception as _bu_err:
            log.warning("Auto-register BotUser failed for telegram_id=%s: %r", customer_telegram_id, _bu_err)

    await db.commit()

    # Загружаем заказ со связями (нужно для notify_shop_admin)
    order_result = await db.execute(
        select(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
        .where(Order.id == new_order.id)
    )
    new_order = order_result.scalar_one()

    # ── Обновить TelegramProfile (маркетинговый слой) ──────────────────────
    if customer_telegram_id:
        try:
            svc = ProfileService(db)
            name = order_data.customer_name or (telegram_user_data.get('first_name') if telegram_user_data else None)
            username = telegram_user_data.get('username') if telegram_user_data else None
            profile = await svc.get_or_create(
                shop_id=shop_id,
                telegram_id=customer_telegram_id,
                name=name,
                username=username,
            )
            await svc.update_after_order(profile.id, float(total))
            await svc.track_funnel(profile.id, "completed")
            await db.commit()
        except Exception as e:
            log.warning("ProfileService update failed for order #%s: %r", new_order.id, e)

    # ── Онлайн-оплата: генерируем payment_url ──────────────────────────────
    payment_url = None
    if is_online_payment:
        try:
            base_url = os.getenv("BASE_URL") or os.getenv("WEB_APP_URL", "http://localhost:8000")
            # Подпись пары (shop_id, order_id): после редиректа с оплаты контекста
            # Telegram Mini App нет, initData пуст — витрина опрашивает статус
            # по этому токену (см. utils/order_status_token.py).
            status_token = make_order_status_token(shop_id, new_order.id)
            return_url = f"{base_url}/shop/?shop_id={shop_id}&paid={new_order.id}"
            if status_token:
                return_url += f"&t={status_token}"
            gateway = get_gateway(payment_method, shop.config or {}, base_url=base_url)
            result_pay = await gateway.create_payment(
                order_id=new_order.id,
                amount=int(prepay_amount),
                description=f"Заказ #{new_order.number or new_order.id}",
                return_url=return_url,
            )
            if result_pay.success:
                payment_url = result_pay.payment_url
                # Создаём запись Payment (сумма = prepay, для обычных магазинов = total)
                payment_rec = Payment(
                    order_id=new_order.id,
                    shop_id=shop_id,
                    provider=payment_method,
                    provider_id=result_pay.payment_id,
                    amount=prepay_amount,
                    status="pending",
                    meta={"prepayment_percent": prepay_percent},
                )
                db.add(payment_rec)
                await db.commit()
                log.info("Online payment created for order #%s via %s: %s",
                         new_order.id, payment_method, payment_url)
            else:
                log.error("Payment creation failed for order #%s: %s",
                          new_order.id, result_pay.error)
        except Exception as e:
            log.error("Payment gateway error for order #%s: %r", new_order.id, e)
    else:
        # Наличные — уведомить владельца и покупателя
        await notify_shop_admin(shop_id, new_order, db)
        if customer_telegram_id:
            await _notify_customer_cash_order(shop_id, new_order, customer_telegram_id, db)

    # ── POS-интеграция: поставить заказ в очередь на пуш (fire-and-forget) ──
    try:
        from integrations.order_push_service import OrderPushService
        push_svc = OrderPushService(db)
        await push_svc.on_order_created(new_order.id)
    except Exception as e:
        log.warning("OrderPushService.on_order_created failed for #%s: %r", new_order.id, e)

    return {
        "status": "ok",
        "order_id": new_order.id,
        "order_number": new_order.number,
        "subtotal": float(subtotal),
        "discount_percent": discount_percent,
        "promo_code": applied_promo_code.code if applied_promo_code else None,
        "promo_discount": float(promo_discount_amount),
        "discount_amount": float(discount_amount),
        "total": float(total),
        "payment_url": payment_url,
        "message": "Заказ успешно создан" if not is_online_payment else "Перейдите к оплате",
    }


@router.get("/api/v1/shop/{shop_id}/orders/{order_id}/status", response_class=JSONResponse)
async def get_order_status(
    shop_id: int,
    order_id: int,
    request: Request,
    t: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get order payment status (polled by frontend after redirect).

    order_id — сквозной автоинкремент, поэтому анониму этот эндпоинт отдавать
    нельзя: перебором вычитываются номера, суммы и статусы оплаты чужих заказов,
    а значит и оборот любого магазина.

    Доступ подтверждается одним из двух способов:
      1. Подписанный initData — обычный опрос из Mini App (как в my-orders);
      2. `?t=` — HMAC-подпись пары (shop_id, order_id) из return_url. Нужна
         потому, что платёжный провайдер возвращает покупателя обычным
         редиректом, и контекста Mini App в этот момент уже нет — initData пуст.
    """
    # Токен из return_url проверяем первым: он не требует обращений к БД.
    if verify_order_status_token(shop_id, order_id, t):
        result = await db.execute(
            select(Order).where(Order.id == order_id, Order.shop_id == shop_id)
        )
        order = result.scalar_one_or_none()
        if not order:
            raise HTTPException(404, "Order not found")
    else:
        shop_row = await db.execute(select(Shop.bot_token).where(Shop.id == shop_id))
        bot_token = shop_row.scalar_one_or_none()
        init_data_header = request.headers.get("X-Telegram-Init-Data", "")

        telegram_id = None
        if init_data_header and bot_token and verify_telegram_web_app_data(init_data_header, bot_token):
            from utils.telegram_auth import extract_user_id
            telegram_id = extract_user_id(init_data_header)
        if not telegram_id:
            raise HTTPException(401, "Missing Telegram init data")

        customer_result = await db.execute(
            select(Customer.id).where(
                Customer.shop_id == shop_id,
                Customer.telegram_id == telegram_id,
            )
        )
        customer_id = customer_result.scalar_one_or_none()
        if not customer_id:
            raise HTTPException(404, "Order not found")

        result = await db.execute(
            select(Order).where(
                Order.id == order_id,
                Order.shop_id == shop_id,
                Order.customer_id == customer_id,
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            raise HTTPException(404, "Order not found")

    # Latest payment for this order
    pay_result = await db.execute(
        select(Payment)
        .where(Payment.order_id == order_id)
        .order_by(Payment.id.desc())
        .limit(1)
    )
    payment = pay_result.scalar_one_or_none()

    return {
        "order_id": order_id,
        "order_number": order.number,
        "status": order.status,
        "payment_status": payment.status if payment else None,
        "total_amount": float(order.total_amount),
        "payment_method": order.meta.get("payment_method") if order.meta else None,
    }


@router.get("/api/v1/shop/{shop_id}/profile", response_class=JSONResponse)
async def get_bot_user_profile(
    shop_id: int,
    request: Request,
    telegram_id: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get registered bot user profile by telegram_id.
    Falls back to extracting user_id from X-Telegram-Init-Data header."""

    # PII (телефон, геолокация, скидки) отдаём ТОЛЬКО владельцу профиля.
    # telegram_id берём ИСКЛЮЧИТЕЛЬНО из подписанного initData, а не из query-
    # параметра — иначе любой аноним перебором telegram_id выкачивает ПДн.
    shop_row = await db.execute(select(Shop.bot_token).where(Shop.id == shop_id))
    bot_token = shop_row.scalar_one_or_none()
    init_data_header = request.headers.get("X-Telegram-Init-Data", "")
    verified_id = None
    if init_data_header and bot_token and verify_telegram_web_app_data(init_data_header, bot_token):
        from utils.telegram_auth import extract_user_id
        verified_id = extract_user_id(init_data_header)

    if not verified_id:
        return {
            "is_registered": False,
            "message": "Откройте мини-магазин через бота для подтягивания профиля",
        }
    telegram_id = verified_id

    result = await db.execute(
        select(BotUser).where(
            BotUser.shop_id == shop_id,
            BotUser.telegram_id == telegram_id,
        )
    )
    bot_user = result.scalar_one_or_none()

    if not bot_user or not bot_user.is_registered:
        return {
            "is_registered": False,
            "message": "Откройте бота для регистрации",
        }

    # Parse discount percent from string like "20%"
    discount_percent = 0.0
    if bot_user.discount_registration:
        try:
            discount_percent = float(
                bot_user.discount_registration.replace("%", "").strip()
            )
        except (ValueError, AttributeError):
            pass

    return {
        "is_registered": True,
        "id": bot_user.id,
        "telegram_id": bot_user.telegram_id,
        "name": bot_user.name,
        "phone": bot_user.phone,
        "latitude": float(bot_user.location_lat) if bot_user.location_lat else None,
        "longitude": float(bot_user.location_lon) if bot_user.location_lon else None,
        "discount_registration": bot_user.discount_registration,
        "discount_percent": discount_percent,
        "discount_promo": bot_user.discount_promo,
        "promo_code": bot_user.promo_code,
    }


@router.get("/api/v1/shop/{shop_id}/my-orders", response_class=JSONResponse)
async def get_my_orders(
    shop_id: int,
    request: Request,
    telegram_id: int | None = None,
    lang: str = "ru",
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get order history for a Telegram user in a specific shop.

    История заказов содержит адреса доставки (ПДн), поэтому telegram_id берём
    только из подписанного initData, а не из query-параметра.
    """
    shop_row = await db.execute(select(Shop.bot_token).where(Shop.id == shop_id))
    bot_token = shop_row.scalar_one_or_none()
    init_data_header = request.headers.get("X-Telegram-Init-Data", "")
    telegram_id = None
    if init_data_header and bot_token and verify_telegram_web_app_data(init_data_header, bot_token):
        from utils.telegram_auth import extract_user_id
        telegram_id = extract_user_id(init_data_header)
    if not telegram_id:
        return {"orders": []}

    customer_result = await db.execute(
        select(Customer).where(
            Customer.shop_id == shop_id,
            Customer.telegram_id == telegram_id,
        )
    )
    customer = customer_result.scalar_one_or_none()
    if not customer:
        return {"orders": []}

    orders_result = await db.execute(
        select(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
        .where(Order.shop_id == shop_id, Order.customer_id == customer.id)
        .order_by(Order.created_at.desc())
        .limit(20)
    )
    orders = orders_result.scalars().all()

    _status_labels = {
        "ru": {
            "new": "Новый", "pending_payment": "Ожидает оплаты", "confirmed": "Подтверждён",
            "in_progress": "В обработке", "shipped": "Отправлен", "delivered": "Доставлен",
            "cancelled": "Отменён", "completed": "Завершён",
        },
        "uz": {
            "new": "Yangi", "pending_payment": "To'lov kutilmoqda", "confirmed": "Tasdiqlangan",
            "in_progress": "Ishlanmoqda", "shipped": "Yuborilgan", "delivered": "Yetkazildi",
            "cancelled": "Bekor qilindi", "completed": "Bajarildi",
        },
        "en": {
            "new": "New", "pending_payment": "Pending payment", "confirmed": "Confirmed",
            "in_progress": "In progress", "shipped": "Shipped", "delivered": "Delivered",
            "cancelled": "Cancelled", "completed": "Completed",
        },
    }
    labels = _status_labels.get(lang, _status_labels["ru"])

    result_orders = []
    for order in orders:
        items_data = []
        for item in order.items:
            if item.product and item.product.name_i18n:
                product_name = get_text(item.product.name_i18n, lang, "ru", item.product.name)
            else:
                product_name = (item.product.name if item.product else None) or "Товар"
            opts = item.selected_options or {}
            opts_str = " / ".join(str(v) for v in opts.values()) if opts else ""
            items_data.append({
                "id": item.id,
                "product_name": product_name,
                "options": opts_str,
                "qty": item.quantity,
                "price": int(item.price_at_moment),
                "image_url": item.product.image_url if item.product else None,
            })

        result_orders.append({
            "id": order.id,
            "status": order.status,
            "status_label": labels.get(order.status, order.status),
            "total": int(order.total_amount),
            "items": items_data,
            "delivery_address": order.meta.get("delivery_address") if order.meta else None,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        })

    return {"orders": result_orders}


@router.get("/api/v1/shop/{shop_id}/promo/validate", response_class=JSONResponse)
async def validate_promo_code(
    shop_id: int,
    code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Validate a promo code without applying it (used by WebApp cart).
    Returns discount info on success, raises HTTP 404/400 on failure.
    """
    normalized = code.strip().upper()
    if not normalized:
        raise HTTPException(400, "Введите промокод")

    # Личный промокод (used_by = кому выдан) может применить только владелец.
    # telegram_id берём ИСКЛЮЧИТЕЛЬНО из подписанного initData, а не из query.
    verified_id = None
    shop_row = await db.execute(select(Shop.bot_token).where(Shop.id == shop_id))
    bot_token = shop_row.scalar_one_or_none()
    init_data_header = request.headers.get("X-Telegram-Init-Data", "")
    if init_data_header and bot_token and verify_telegram_web_app_data(init_data_header, bot_token):
        from utils.telegram_auth import extract_user_id
        verified_id = extract_user_id(init_data_header)

    result = await db.execute(
        select(Promocode).where(
            Promocode.code == normalized,
            Promocode.shop_id == shop_id,
        )
    )
    promo = result.scalar_one_or_none()

    if not promo:
        raise HTTPException(404, "Промокод не найден")

    if not promo.is_active:
        raise HTTPException(400, "Промокод неактивен")

    # used_by = кому выдан личный код. Владельцу — можно, чужим — нельзя.
    if promo.used_by and promo.used_by != verified_id:
        raise HTTPException(400, "Этот промокод выдан другому клиенту")

    if promo.max_usage is not None and promo.usage_count >= promo.max_usage:
        raise HTTPException(400, "Лимит использований исчерпан")

    if promo.expires_at and promo.expires_at < datetime.now(UTC):
        raise HTTPException(400, "Срок действия промокода истёк")

    return {
        "code": promo.code,
        "discount_type": promo.discount_type,
        "discount_value": promo.value,
        "is_valid": True,
    }
