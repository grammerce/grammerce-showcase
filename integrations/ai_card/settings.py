"""
Настройки модуля «AI Карточка товара» — все из env, без хардкода по месту
(md_s/grammerce_ai_card_spec.md, разделы 0.4 и 3.1).
"""
from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# Мок-режим по умолчанию: все внешние AI-вызовы — заглушки
AI_CARD_MOCK_MODE: bool = _env_bool("AI_CARD_MOCK_MODE", True)
# Задержка мока, имитирующая работу AI (в тестах ставится 0)
AI_CARD_MOCK_DELAY: float = _env_float("AI_CARD_MOCK_DELAY", 2.0)

# Целевой формат карточки маркетплейса (Uzum: белый фон, 3:4)
CARD_BG_COLOR: str = os.getenv("AI_CARD_BG_COLOR", "#FFFFFF")
TARGET_W: int = _env_int("AI_CARD_TARGET_W", 1080)
TARGET_H: int = _env_int("AI_CARD_TARGET_H", 1440)
MIN_W: int = _env_int("AI_CARD_MIN_W", 750)
MIN_H: int = _env_int("AI_CARD_MIN_H", 1000)
JPEG_QUALITY: int = _env_int("AI_CARD_JPEG_QUALITY", 90)

# Стратегия приведения к 3:4: fit (вписать + белые поля) | crop (обрезать)
FIT_STRATEGY: str = os.getenv("AI_CARD_FIT_STRATEGY", "fit")

# Ограничения генерации текста
TITLE_MAX_LEN: int = _env_int("AI_CARD_TITLE_MAX_LEN", 100)
FEW_SHOT_K: int = _env_int("AI_CARD_FEW_SHOT_K", 3)

# Автопополнение библиотеки примеров принятыми без правок генерациями
AI_CARD_AUTO_APPROVE: bool = _env_bool("AI_CARD_AUTO_APPROVE", False)

# Провайдер генерации текста: "" (по MOCK_MODE) | "mock" | "gemini".
# Отдельно от MOCK_MODE — чтобы включить реальный Gemini для текста,
# оставив обработку фото на бесплатном локальном пайплайне (Pillow/rembg).
AI_CARD_TEXT_PROVIDER: str = os.getenv("AI_CARD_TEXT_PROVIDER", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Вырезание фона (rembg) → товар на чистом белом. Локально, бесплатно, но
# тяжёлая зависимость. По умолчанию выключено; включается env-флагом.
AI_CARD_REMOVE_BG: bool = _env_bool("AI_CARD_REMOVE_BG", False)
# Куда rembg кладёт скачанную модель (u2net ~176 МБ). Персистентный путь,
# чтобы не качать при каждом рестарте контейнера.
U2NET_HOME: str = os.getenv("U2NET_HOME", "/app/data/.u2net")
# Куда numba (зависимость pymatting внутри rembg) пишет JIT-кэш. По умолчанию
# он пишет рядом с site-packages — read-only под юзером app → падение. Указываем
# писабельную персистентную папку.
NUMBA_CACHE_DIR: str = os.getenv("NUMBA_CACHE_DIR", "/app/data/.numba_cache")
