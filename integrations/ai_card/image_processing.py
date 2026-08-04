"""
Нормализация фото товара к формату карточки маркетплейса
(md_s/grammerce_ai_card_spec.md, раздел 3.1).

Работает по-настоящему даже в мок-режиме (Pillow, локально, без токенов):
  1) приведение к соотношению 3:4 — fit (вписать + белые поля) или crop;
  2) апскейл до >= MIN_W×MIN_H с целью TARGET_W×TARGET_H;
  3) вывод JPEG q=JPEG_QUALITY (не PNG: base64-PNG в БД слишком тяжёлый).
"""
from __future__ import annotations

import base64
import io
import logging

from PIL import Image

from integrations.ai_card.settings import (
    CARD_BG_COLOR,
    FIT_STRATEGY,
    JPEG_QUALITY,
    MIN_H,
    MIN_W,
    TARGET_H,
    TARGET_W,
)

log = logging.getLogger(__name__)

# Целевое соотношение сторон карточки: ширина/высота = 3/4
_ASPECT_W, _ASPECT_H = 3, 4


def data_url_to_bytes(data_url: str) -> bytes:
    """data-URL (data:image/...;base64,xxx) или голый base64 → bytes."""
    payload = data_url
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    return base64.b64decode(payload)


def bytes_to_data_url(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """bytes → data-URL для хранения/передачи по проектной конвенции."""
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _to_canvas_3x4(img: Image.Image, strategy: str) -> tuple[Image.Image, list[str]]:
    """Привести изображение к соотношению 3:4 (fit — белые поля, crop — обрезка)."""
    w, h = img.size
    steps: list[str] = []
    # Уже 3:4 (с точностью до пикселя от целочисленного округления)
    if w * _ASPECT_H == h * _ASPECT_W:
        return img, steps

    if strategy == "crop":
        # Центрированная обрезка до 3:4
        if w * _ASPECT_H > h * _ASPECT_W:  # слишком широкое — режем бока
            new_w = h * _ASPECT_W // _ASPECT_H
            left = (w - new_w) // 2
            img = img.crop((left, 0, left + new_w, h))
        else:  # слишком высокое — режем верх/низ
            new_h = w * _ASPECT_H // _ASPECT_W
            top = (h - new_h) // 2
            img = img.crop((0, top, w, top + new_h))
        steps.append("crop_3x4")
        return img, steps

    # fit (по умолчанию): вписать в холст 3:4, добить фон белым, товар не растягивать
    if w * _ASPECT_H > h * _ASPECT_W:  # ограничивает ширина
        canvas_w, canvas_h = w, w * _ASPECT_H // _ASPECT_W
    else:  # ограничивает высота
        canvas_w, canvas_h = h * _ASPECT_W // _ASPECT_H, h
    canvas = Image.new("RGB", (canvas_w, canvas_h), CARD_BG_COLOR)
    canvas.paste(img, ((canvas_w - w) // 2, (canvas_h - h) // 2))
    steps.append("white_bg")
    return canvas, steps


def normalize(image_bytes: bytes, strategy: str | None = None) -> tuple[bytes, int, int, list[str]]:
    """Нормализовать фото: 3:4 + белый фон + апскейл. → (bytes, w, h, applied_steps)."""
    strategy = strategy or FIT_STRATEGY
    applied_steps: list[str] = []

    img = Image.open(io.BytesIO(image_bytes))
    # RGBA/P (прозрачность) кладём на белую подложку — карточке нужен белый фон
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, CARD_BG_COLOR)
        bg.paste(img, mask=img.split()[-1])
        img = bg
        applied_steps.append("flatten_alpha")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    img, steps = _to_canvas_3x4(img, strategy)
    applied_steps.extend(steps)

    # Апскейл до минимума маркетплейса, целясь в целевое разрешение
    w, h = img.size
    if w < MIN_W or h < MIN_H:
        img = img.resize((TARGET_W, TARGET_H), Image.LANCZOS)
        applied_steps.append("upscale")
    elif w > TARGET_W or h > TARGET_H:
        img = img.resize((TARGET_W, TARGET_H), Image.LANCZOS)
        applied_steps.append("downscale")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY)
    applied_steps.append("normalize_3x4")
    return out.getvalue(), img.size[0], img.size[1], applied_steps
