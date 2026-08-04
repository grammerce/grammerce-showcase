"""HTTP-нотификатор маркет-бота Grammerce (отдельный проект).

Платформа вызывает `notify_platform_bot(event, payload)` — и POST-ит на
`PLATFORM_BOT_NOTIFY_URL` с заголовком `X-Bot-Secret: <PLATFORM_BOT_SHARED_SECRET>`.
Бот на своей стороне аутентифицирует секретом и пересылает сообщение владельцу
в Telegram.

Использование fire-and-forget: если HTTP-эндпоинт не настроен или упал —
платформа не блокируется, заявка остаётся в support_messages (фоллбэк).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from config.oauth import oauth_settings

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 5.0


async def notify_platform_bot(
    event: str,
    payload: dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bool:
    """Отправить событие в grammerce-бот.

    Args:
        event: тип события (`delegated_request` и т.д.).
        payload: произвольный dict-payload, сериализуется в JSON.
        timeout: таймаут HTTP-запроса, сек.

    Returns:
        True если бот вернул 2xx, иначе False. Не рейзит.
    """
    url: str | None = oauth_settings.platform_bot_notify_url
    secret: str | None = oauth_settings.platform_bot_shared_secret

    if not url:
        log.warning("[platform_bot_notify] PLATFORM_BOT_NOTIFY_URL not set — skip event=%s", event)
        return False
    if not secret:
        log.warning("[platform_bot_notify] PLATFORM_BOT_SHARED_SECRET not set — skip event=%s", event)
        return False

    body = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
        **payload,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers={
                    "X-Bot-Secret": secret,
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if 200 <= resp.status_code < 300:
            log.info("[platform_bot_notify] event=%s delivered (%s)", event, resp.status_code)
            return True
        log.warning(
            "[platform_bot_notify] event=%s non-2xx %s: %s",
            event, resp.status_code, (resp.text or "")[:200],
        )
        return False
    except Exception as e:  # noqa: BLE001 — fire-and-forget, не поднимаем
        log.warning("[platform_bot_notify] event=%s failed: %r", event, e)
        return False


async def _send_platform_message(
    chat_id, text: str, *, reply_markup: dict | None = None, timeout: float = _DEFAULT_TIMEOUT
) -> bool:
    """Прямой sendMessage через главный бот Grammerce (`TELEGRAM_BOT_TOKEN`).

    Токен берётся ТОЛЬКО из `telegram_bot_token` — это главный маркетинг-бот Grammerce.
    Намеренно НЕ падаем на `BOT_TOKEN`: он принадлежит шоп-боту, и сообщения уходили бы
    «не от того бота». Self-contained: не зависит от внешнего notify-сервиса.
    reply_markup — опциональная inline-клавиатура (dict Telegram API).
    Fire-and-forget — не рейзит, возвращает True при 2xx.
    """
    token: str | None = oauth_settings.telegram_bot_token

    if not token:
        log.warning("[platform_tg] TELEGRAM_BOT_TOKEN не задан — пропуск")
        return False
    if not chat_id:
        log.warning("[platform_tg] chat_id не задан — пропуск")
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
            )
        if 200 <= resp.status_code < 300:
            log.info("[platform_tg] доставлено chat_id=%s (%s)", chat_id, resp.status_code)
            return True
        log.warning(
            "[platform_tg] non-2xx %s: %s", resp.status_code, (resp.text or "")[:200]
        )
        return False
    except Exception as e:  # noqa: BLE001 — fire-and-forget, не поднимаем
        log.warning("[platform_tg] send failed: %r", e)
        return False


async def send_manager_telegram(text: str, *, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    """Прямой sendMessage менеджеру Grammerce через главный бот Grammerce.

    Получатель — `platform_manager_chat_id`. Используется для заявок «Свяжемся с вами»
    (Setup Fee) и «под ключ». Fire-and-forget — не рейзит, возвращает True при 2xx.
    """
    chat_id = oauth_settings.platform_manager_chat_id
    if not chat_id:
        log.warning("[manager_tg] PLATFORM_MANAGER_CHAT_ID не задан — пропуск")
        return False
    return await _send_platform_message(chat_id, text, timeout=timeout)


async def send_owner_telegram(
    chat_id, text: str, *, reply_markup: dict | None = None, timeout: float = _DEFAULT_TIMEOUT
) -> bool:
    """Прямой sendMessage владельцу магазина через главный бот Grammerce.

    Фоллбэк-канал: когда в боте магазина ещё нет получателей (не задан `owner_tg_id`
    и не назначен `BotAdmin`), уведомления о заказах шлём владельцу по его
    `User.telegram_id` — тому аккаунту, которым он регистрировался через @Grammerce_bot.
    reply_markup — опциональная inline-клавиатура (dict Telegram API).
    Fire-and-forget — не рейзит, возвращает True при 2xx.
    """
    return await _send_platform_message(chat_id, text, reply_markup=reply_markup, timeout=timeout)
