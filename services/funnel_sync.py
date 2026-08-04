"""Отправка состояния воронки владельца магазина в Telegram-бот Grammerce.

Бот сам решает кому/когда слать активационные пуши (тайминг, тихие часы, дедуп,
язык). Платформа отдаёт ему ТОЛЬКО факты о воронке владельца:
  product_count, training_completed, trial_ends_at, plan_paid, store_created_at.

Контракт: POST {BOT_BASE_URL}/api/bot/funnel-state, заголовок
X-Bot-Secret: PLATFORM_BOT_SHARED_SECRET. Успех — только HTTP 200.

Подход — идемпотентный «слать полное текущее состояние»: любой триггер вызывает
`sync_owner_funnel_state(shop_id)`, который пересчитывает все поля и шлёт их.
Повтор безопасен. Fire-and-forget: ошибки не поднимаются, основной поток не блокируется.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select

from config.billing import TRIAL_DAYS
from config.oauth import oauth_settings
from database import async_session_factory
from models import Product, Shop, Subscription, User

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 5.0
_MAX_ATTEMPTS = 3            # всего попыток при таймауте/сетевой ошибке
_RETRY_BASE_DELAY = 0.5      # сек; экспоненциально: 0.5 → 1.0 → 2.0


def _to_iso_utc(dt: datetime) -> str:
    """datetime → ISO-8601 в UTC (tz-aware). Наивные даты считаем уже UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _configured() -> bool:
    return bool(oauth_settings.bot_base_url and oauth_settings.platform_bot_shared_secret)


async def send_funnel_state(
    telegram_id: Any,
    *,
    product_count: int | None = None,
    training_completed: bool | None = None,
    trial_ends_at: datetime | None = None,
    plan_paid: bool | None = None,
    store_created_at: datetime | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bool:
    """POST состояния воронки в бот. Шлёт только не-None поля (+ обязательный telegram_id).

    Ретраи с экспоненциальным backoff только на таймаут/сетевую ошибку. Успех — только
    HTTP 200. На 4xx/прочий не-200 — без ретраев. Никогда не рейзит.
    """
    base_url = oauth_settings.bot_base_url
    secret = oauth_settings.platform_bot_shared_secret

    if not base_url:
        log.warning("[funnel_sync] BOT_BASE_URL not set — skip telegram_id=%s", telegram_id)
        return False
    if not secret:
        log.warning("[funnel_sync] PLATFORM_BOT_SHARED_SECRET not set — skip telegram_id=%s", telegram_id)
        return False
    if not telegram_id:
        log.info("[funnel_sync] no telegram_id — skip send")
        return False

    body: dict[str, Any] = {"telegram_id": str(telegram_id)}
    if product_count is not None:
        body["product_count"] = int(product_count)
    if training_completed is not None:
        body["training_completed"] = bool(training_completed)
    if trial_ends_at is not None:
        body["trial_ends_at"] = _to_iso_utc(trial_ends_at)
    if plan_paid is not None:
        body["plan_paid"] = bool(plan_paid)
    if store_created_at is not None:
        body["store_created_at"] = _to_iso_utc(store_created_at)

    url = f"{base_url.rstrip('/')}/api/bot/funnel-state"
    headers = {"X-Bot-Secret": secret, "Content-Type": "application/json"}

    delay = _RETRY_BASE_DELAY
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            log.warning(
                "[funnel_sync] telegram_id=%s network error (attempt %s/%s): %r",
                telegram_id, attempt, _MAX_ATTEMPTS, e,
            )
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return False
        except Exception as e:  # noqa: BLE001 — fire-and-forget, не поднимаем
            log.warning("[funnel_sync] telegram_id=%s unexpected error: %r", telegram_id, e)
            return False

        if resp.status_code == 200:
            log.info("[funnel_sync] telegram_id=%s delivered (200)", telegram_id)
            return True
        # 401/400/прочее — не ретраим (повтор не поможет)
        log.warning(
            "[funnel_sync] telegram_id=%s non-200 %s: %s",
            telegram_id, resp.status_code, (resp.text or "")[:200],
        )
        return False

    return False


async def _compute_state(db, shop: Shop) -> dict[str, Any]:
    """Пересчитать факты воронки владельца по данным БД."""
    product_count = await db.scalar(
        select(func.count(Product.id)).where(Product.shop_id == shop.id)
    ) or 0

    setup_fee_paid = False
    if shop.owner_id is not None:
        setup_fee_paid = bool(await db.scalar(
            select(User.setup_fee_paid).where(User.id == shop.owner_id)
        ))

    sub = (await db.execute(
        select(Subscription).where(Subscription.shop_id == shop.id)
    )).scalar_one_or_none()

    plan_paid = setup_fee_paid or (sub is not None and sub.status == "active")

    training_completed = (
        shop.onboarding_completed_at is not None
        or shop.onboarding_tour_completed_at is not None
    )

    if sub is not None and sub.trial_ends_at is not None:
        trial_ends_at = sub.trial_ends_at
    elif shop.trial_started_at is not None:
        trial_ends_at = shop.trial_started_at + timedelta(days=TRIAL_DAYS)
    else:
        trial_ends_at = None

    return {
        "product_count": int(product_count),
        "training_completed": training_completed,
        "trial_ends_at": trial_ends_at,
        "plan_paid": plan_paid,
        "store_created_at": shop.created_at,
    }


async def _resolve_owner_telegram_id(db, shop: Shop) -> str | None:
    """telegram_id владельца: User.telegram_id (Telegram-логин) → fallback Shop.owner_tg_id."""
    tg = None
    if shop.owner_id is not None:
        tg = await db.scalar(select(User.telegram_id).where(User.id == shop.owner_id))
    if not tg and shop.owner_tg_id:
        tg = shop.owner_tg_id
    return str(tg) if tg else None


async def sync_owner_funnel_state(shop_id: int) -> bool:
    """Пересчитать и отправить полное состояние воронки владельца магазина.

    Открывает свою сессию (безопасно для fire-and-forget через asyncio.create_task).
    Идемпотентно. Используется триггерами, разовым бэкафиллом и ежедневным батчем.
    """
    if not _configured():
        return False
    try:
        async with async_session_factory() as db:
            shop = (await db.execute(
                select(Shop).where(Shop.id == shop_id)
            )).scalar_one_or_none()
            if shop is None:
                log.info("[funnel_sync] shop_id=%s not found — skip", shop_id)
                return False
            telegram_id = await _resolve_owner_telegram_id(db, shop)
            if not telegram_id:
                log.info("[funnel_sync] shop_id=%s: owner has no telegram_id — skip", shop_id)
                return False
            state = await _compute_state(db, shop)
        # Сеть — уже вне сессии БД
        return await send_funnel_state(telegram_id, **state)
    except Exception as e:  # noqa: BLE001 — fire-and-forget
        log.warning("[funnel_sync] sync_owner_funnel_state shop_id=%s failed: %r", shop_id, e)
        return False


async def backfill_all_owners() -> int:
    """Разовый прогон: отправить актуальное состояние по ВСЕМ магазинам."""
    if not _configured():
        log.warning("[funnel_sync] backfill skipped — BOT_BASE_URL/secret не заданы")
        return 0
    async with async_session_factory() as db:
        shop_ids = list((await db.execute(select(Shop.id))).scalars().all())

    sent = 0
    for shop_id in shop_ids:
        if await sync_owner_funnel_state(shop_id):
            sent += 1
    log.info("[funnel_sync] backfill done: %s/%s sent", sent, len(shop_ids))
    return sent


async def daily_funnel_sync() -> int:
    """Ежедневный батч: синк состояния по всем владельцам с plan_paid=false (страховка)."""
    if not _configured():
        return 0
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Shop.id, User.setup_fee_paid, Subscription.status)
            .select_from(Shop)
            .outerjoin(User, User.id == Shop.owner_id)
            .outerjoin(Subscription, Subscription.shop_id == Shop.id)
        )).all()

    candidates = [
        shop_id
        for shop_id, setup_fee_paid, sub_status in rows
        if not (bool(setup_fee_paid) or sub_status == "active")
    ]

    sent = 0
    for shop_id in candidates:
        if await sync_owner_funnel_state(shop_id):
            sent += 1
    log.info("[funnel_sync] daily sync done: %s/%s sent", sent, len(candidates))
    return sent
