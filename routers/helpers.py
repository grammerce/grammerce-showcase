"""
Общие вспомогательные утилиты для роутеров.
"""
import logging

from fastapi import HTTPException, Request

from auth_utils import get_current_user_from_token

log = logging.getLogger(__name__)


async def get_shop_or_404(shop_id: int, db):
    """Загрузить Shop по id или 404. Единый помощник вместо повторов
    select(Shop)→scalar_one_or_none()→raise 404 по всем роутерам."""
    from sqlalchemy import select

    from models import Shop

    result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = result.scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


def parse_discount(raw: str | None, subtotal: float) -> tuple[float, float]:
    """Разобрать строку скидки «"20%"» или «"15000 сум"/"15000"».

    Возвращает (percent, amount): для процентной — (проценты, сумма от subtotal),
    для фиксированной — (0.0, сумма). Невалидные значения → (0.0, 0.0) с логом
    (раньше молча глотались через `except: pass` — латентный источник ошибок).
    """
    if not raw:
        return 0.0, 0.0
    raw = raw.strip()
    try:
        if raw.endswith("%"):
            percent = float(raw.replace("%", "").strip())
            return percent, round(subtotal * percent / 100, 2)
        amount = float(raw.replace("сум", "").replace(" ", "").strip())
        return 0.0, amount
    except (ValueError, AttributeError):
        log.warning("parse_discount: не удалось разобрать скидку %r", raw)
        return 0.0, 0.0


def get_shop_id_from_request(request: Request) -> int:
    """Get shop_id из заголовка X-Shop-Id, query-параметра shop_id или Bearer-токена.

    Раньше fallback был на shop_id=1 (PunkShop), что приводило к подмене магазина
    у новых юзеров. Теперь — fallback на shop_id из Bearer-токена пользователя,
    либо 400 если идентифицировать магазин не удалось.
    """
    sid = request.headers.get("X-Shop-Id")
    if sid:
        return int(sid)
    sid = request.query_params.get("shop_id")
    if sid:
        return int(sid)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        user = get_current_user_from_token(auth[7:])
        if user and user.get("shop_id"):
            return int(user["shop_id"])
    raise HTTPException(status_code=400, detail="Shop ID required (X-Shop-Id header)")
