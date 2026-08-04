"""Подписанный токен для страницы «статус заказа» после возврата с оплаты.

Зачем. Статус заказа нельзя отдавать анониму: order_id — сквозной автоинкремент,
перебором вычитываются номера, суммы и статусы оплаты чужих заказов, а значит и
оборот любого магазина. Но и потребовать подписанный initData нельзя: платёжный
провайдер редиректит покупателя на return_url обычной навигацией, и в этот момент
контекста Telegram Mini App уже нет — initData пуст.

Решение. В return_url кладём HMAC-подпись пары (shop_id, order_id). Она
неугадываема, привязана к конкретному заказу и не даёт ничего, кроме чтения
статуса именно этого заказа. Схему БД менять не нужно — токен вычисляемый.

Ключ — SECRET_KEY из окружения (в проекте он уже пробрасывается через
docker-compose, но до сих пор ничего не подписывал). Без него подпись не
выдаётся и не принимается: тогда единственным способом остаётся initData —
fail-closed, а не «принимаем любой токен».
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

log = logging.getLogger(__name__)

_TOKEN_LEN = 32  # хватает: 128 бит от hexdigest


def _secret() -> bytes | None:
    raw = (os.getenv("SECRET_KEY") or "").strip()
    if not raw:
        return None
    return raw.encode()


def make_order_status_token(shop_id: int, order_id: int) -> str | None:
    """Подпись для return_url. None, если SECRET_KEY не задан."""
    key = _secret()
    if not key:
        log.warning("SECRET_KEY не задан — токен статуса заказа не выдан")
        return None
    msg = f"order-status:{int(shop_id)}:{int(order_id)}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:_TOKEN_LEN]


def verify_order_status_token(shop_id: int, order_id: int, token: str | None) -> bool:
    """Проверить подпись. Сравнение — constant-time."""
    if not token:
        return False
    expected = make_order_status_token(shop_id, order_id)
    if not expected:
        return False
    return hmac.compare_digest(expected, token)
