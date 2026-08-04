"""
Пайплайн улучшения фото товара (md_s/grammerce_ai_card_spec.md, раздел 3.2.1).

Целевой пайплайн (каждый шаг — отключаемый):
  шумоподавление → баланс белого / авто-уровни → апскейл (Real-ESRGAN) →
  опц. релайт → чистка/добивка белого фона.

Mock-first по паттерну integrations/ai_product.py: MockEnhancer имитирует
задержку AI и выполняет дешёвые реальные Pillow-операции (autocontrast +
sharpen) — фото пользователя не подменяется статикой, applied_steps честный.
RealEnhancer (Real-ESRGAN / внешний API) — точка расширения этапа 5.
"""
from __future__ import annotations

import asyncio
import io
import logging
from abc import ABC, abstractmethod

from PIL import Image, ImageFilter, ImageOps

from integrations.ai_card.settings import (
    AI_CARD_MOCK_DELAY,
    AI_CARD_MOCK_MODE,
    JPEG_QUALITY,
)

log = logging.getLogger(__name__)


class EnhancerAdapter(ABC):
    """Абстрактный интерфейс улучшения фото: bytes → (bytes, applied_steps)."""

    @abstractmethod
    async def enhance(self, image_bytes: bytes, category: str | None = None) -> tuple[bytes, list[str]]:
        raise NotImplementedError


class MockEnhancer(EnhancerAdapter):
    """Mock: задержка AI + реальные дешёвые Pillow-шаги (без внешних токенов)."""

    async def enhance(self, image_bytes: bytes, category: str | None = None) -> tuple[bytes, list[str]]:
        await asyncio.sleep(AI_CARD_MOCK_DELAY)
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img = ImageOps.autocontrast(img, cutoff=1)   # авто-уровни / баланс
        img = img.filter(ImageFilter.SHARPEN)         # лёгкая резкость
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=JPEG_QUALITY)
        log.info("MockEnhancer: autocontrast+sharpen, category=%s", category)
        return out.getvalue(), ["auto_levels", "sharpen"]


class RealEnhancer(EnhancerAdapter):
    """
    Заглушка реального пайплайна (denoise → white_balance → Real-ESRGAN upscale
    → relight → white_bg). Реализуется на этапе 5 (реальные API) без изменения
    сигнатур. Сейчас сигнализирует о необходимости fallback на Mock.
    """

    async def enhance(self, image_bytes: bytes, category: str | None = None) -> tuple[bytes, list[str]]:
        raise NotImplementedError("Реальный enhancer ещё не подключён — используется Mock")


def get_enhancer() -> EnhancerAdapter:
    """Фабрика по образцу get_ai_product_adapter(): mock-first через env-флаг."""
    if AI_CARD_MOCK_MODE:
        return MockEnhancer()
    return RealEnhancer()


async def enhance_image(image_bytes: bytes, category: str | None = None) -> tuple[bytes, list[str]]:
    """Высокоуровневая обёртка с авто-fallback на Mock при нереализованном адаптере."""
    adapter = get_enhancer()
    try:
        return await adapter.enhance(image_bytes, category)
    except NotImplementedError:
        log.warning("Enhancer не реализован — fallback на Mock")
        return await MockEnhancer().enhance(image_bytes, category)
