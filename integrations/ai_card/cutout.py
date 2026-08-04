"""
Вырезание фона товара (md_s/grammerce_ai_card_spec.md, раздел 3.2.1, шаг «чистка
белого фона»). Локально, бесплатно, без внешних API — через rembg (модель u2net).

Опционально: включается флагом AI_CARD_REMOVE_BG. Ленивый импорт rembg — модуль
остаётся импортируемым без пакета (моки, тесты, локальный запуск). При любой
проблеме (нет пакета/модели/ошибка инференса) — graceful fallback: возвращает
исходное изображение, пайплайн продолжает работу (белый фон даст normalize).
"""
from __future__ import annotations

import io
import logging
import os

from PIL import Image

from integrations.ai_card.settings import (
    AI_CARD_REMOVE_BG,
    CARD_BG_COLOR,
    JPEG_QUALITY,
    NUMBA_CACHE_DIR,
    U2NET_HOME,
)

log = logging.getLogger(__name__)

# Сессия rembg переиспользуется — модель грузится в память один раз
_session = None


def _remove_sync(image_bytes: bytes) -> bytes:
    """Вырезать фон и положить товар на белый фон. Синхронно (CPU-инференс)."""
    global _session
    # Пути задаём ДО импорта rembg (rembg → pymatting → numba читают их на импорте):
    #  - U2NET_HOME: куда качать модель;
    #  - NUMBA_CACHE_DIR: писабельный кэш JIT (иначе numba пишет в read-only
    #    site-packages под юзером app и падает с "no locator available").
    os.makedirs(U2NET_HOME, exist_ok=True)
    os.makedirs(NUMBA_CACHE_DIR, exist_ok=True)
    os.environ.setdefault("U2NET_HOME", U2NET_HOME)
    os.environ.setdefault("NUMBA_CACHE_DIR", NUMBA_CACHE_DIR)
    from rembg import new_session, remove

    if _session is None:
        _session = new_session()  # u2net по умолчанию

    cut_png = remove(image_bytes, session=_session)  # PNG RGBA с прозрачным фоном
    img = Image.open(io.BytesIO(cut_png)).convert("RGBA")
    bg = Image.new("RGB", img.size, CARD_BG_COLOR)
    bg.paste(img, mask=img.split()[-1])  # альфа как маска → товар на белом
    out = io.BytesIO()
    bg.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def remove_background(image_bytes: bytes) -> tuple[bytes, bool]:
    """Вырезать фон → (bytes, applied). При выключенном флаге или ошибке —
    возвращает исходные bytes и applied=False (без вырезания)."""
    if not AI_CARD_REMOVE_BG:
        return image_bytes, False
    try:
        return _remove_sync(image_bytes), True
    except Exception as exc:  # нет пакета/модели/ошибка инференса — не роняем
        log.warning("rembg cutout недоступен (%s) — пропускаю вырезание фона", exc)
        return image_bytes, False
