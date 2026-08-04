"""Подготовка shop.config к отдаче наружу.

`shop.config` — свободный JSON, который владелец редактирует из кабинета. Там
лежат и настройки витрины (тема, тексты, доставка), и мерчант-креды платёжных
шлюзов: `payments/factory.py` читает оттуда `cart.payments.<provider>.secret_key`,
`.key`, `.api_key`, а также плоские `CLICK_SECRET_KEY` / `PAYME_KEY` / `UZUM_API_KEY`.

Публичный эндпоинт витрины отдавал этот объект целиком, поэтому ключ Click любого
магазина забирался анонимно одним GET. Этим ключом подписываются вебхуки
(`payments/utils.py`), то есть его утечка = возможность подделать «оплату».

Витрине из платёжного блока нужен ровно один флаг — `enabled` (см.
`src/components/OrderModal.jsx:226`), поэтому здесь применяется whitelist, а не
попытка перечислить все секретные имена.
"""
from __future__ import annotations

from typing import Any

# Что витрина реально читает из cart.payments.<provider>.
_PAYMENT_PUBLIC_FIELDS = frozenset({"enabled"})

# Страховка для остального дерева: если владелец (или будущая фича) положит
# секрет мимо cart.payments, он всё равно не уедет в публичный ответ.
_SECRET_SUBSTRINGS = (
    "secret", "password", "passwd", "token", "apikey", "api_key",
    "private", "credential", "signature",
)


def _is_secret_name(name: str) -> bool:
    n = str(name).lower()
    if any(h in n for h in _SECRET_SUBSTRINGS):
        return True
    # CLICK_SECRET_KEY ловится выше; PAYME_KEY / *_KEY — здесь.
    return n == "key" or n.endswith("_key")


def _strip_secrets(value: Any) -> Any:
    """Рекурсивно выбрасывает поля с «секретными» именами."""
    if isinstance(value, dict):
        return {
            k: _strip_secrets(v)
            for k, v in value.items()
            if not _is_secret_name(k)
        }
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


def public_shop_config(config: dict | None) -> dict:
    """Вернуть копию config, безопасную для анонимной отдачи.

    Мутации исходного объекта не происходит — важно, т.к. тот же экземпляр
    остаётся привязанным к SQLAlchemy-сессии.
    """
    if not isinstance(config, dict):
        return {}

    safe = _strip_secrets(config)

    cart = safe.get("cart")
    if isinstance(cart, dict):
        payments = cart.get("payments")
        if isinstance(payments, dict):
            cart["payments"] = {
                provider: {
                    k: v for k, v in settings.items()
                    if k in _PAYMENT_PUBLIC_FIELDS
                }
                for provider, settings in payments.items()
                if isinstance(settings, dict)
            }

    return safe
