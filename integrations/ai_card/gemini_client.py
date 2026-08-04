"""
Генерация текста карточки товара (md_s/grammerce_ai_card_spec.md, раздел 3.2.2).

Вход: фото (data-URL/base64) + категория. Выход: GeneratedText (структурный,
валидируется Pydantic) — title {ru, uz}, description, bullets, detected_category.

Mock-first: MockTextGen отдаёт детерминированный черновик по категории/нише.
Промпт собирается и логируется даже в мок-режиме (требование раздела 7 спеки) —
чтобы пайплайн few-shot/RAG отлаживался без реальных токенов.
GeminiTextGen (Gemini 2.5 Flash) — точка расширения этапа 5.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod

from integrations.ai_card.settings import (
    AI_CARD_MOCK_DELAY,
    AI_CARD_MOCK_MODE,
    AI_CARD_TEXT_PROVIDER,
    GEMINI_MODEL,
    TITLE_MAX_LEN,
)
from schemas import GeneratedText, LocalizedText

log = logging.getLogger(__name__)


class AICardTextUnavailable(Exception):
    """Реальный провайдер текста недоступен (лимит/сеть/парсинг). В отличие от
    мок-режима, НЕ подменяем демо-текстом — пробрасываем, чтобы UI показал ошибку."""


def _text_provider() -> str:
    """Эффективный провайдер текста: явный флаг или производный от MOCK_MODE."""
    return (AI_CARD_TEXT_PROVIDER or ("mock" if AI_CARD_MOCK_MODE else "gemini")).lower()


class TextGenAdapter(ABC):
    """Абстрактный интерфейс генерации текста карточки."""

    @abstractmethod
    async def generate_text(
        self,
        image_data: str | None,
        category: str | None,
        prompt: str,
    ) -> GeneratedText:
        raise NotImplementedError


# Детерминированные черновики по нише/категории (стиль _NICHE_DRAFTS из
# integrations/ai_product.py, расширенный до двуязычного GeneratedText).
_MOCK_TEXTS: dict[str, dict] = {
    "flowers": {
        "title": {"ru": "Букет роз «Классика»", "uz": "«Klassika» atirgul guldastasi"},
        "description": "Свежий букет из отборных роз с упаковкой и открыткой. Идеален для подарка.",
        "bullets": ["Свежие розы", "Стильная упаковка", "Открытка в подарок"],
        "detected_category": "Цветы",
    },
    "clothing": {
        "title": {"ru": "Базовая футболка из хлопка", "uz": "Paxtadan tikilgan bazaviy futbolka"},
        "description": "Хлопковая футболка унисекс на каждый день. Мягкая ткань, аккуратные швы.",
        "bullets": ["100% хлопок", "Унисекс", "На каждый день"],
        "detected_category": "Одежда",
    },
    "cosmetics": {
        "title": {"ru": "Питательный крем для лица", "uz": "Yuz uchun oziqlantiruvchi krem"},
        "description": "Питательный крем для ежедневного ухода за кожей лица, 50 мл.",
        "bullets": ["Глубокое питание", "Для всех типов кожи", "50 мл"],
        "detected_category": "Косметика",
    },
    "electronics": {
        "title": {"ru": "Портативный гаджет", "uz": "Portativ gadjet"},
        "description": "Компактное электронное устройство для повседневных задач.",
        "bullets": ["Компактный корпус", "Простое подключение", "Гарантия качества"],
        "detected_category": "Электроника",
    },
}

# Дефолт — пример из раздела 5.2 спеки
_DEFAULT_TEXT: dict = {
    "title": {"ru": "Шампунь L'Oréal Liss Unlimited", "uz": "L'Oréal Liss Unlimited shampuni"},
    "description": "Профессиональный шампунь L'Oréal Liss Unlimited для интенсивного разглаживания и смягчения непослушных волос.",
    "bullets": ["Глубокое разглаживание", "Для непослушных волос", "Профессиональная серия"],
    "detected_category": "Уход за волосами",
}


class MockTextGen(TextGenAdapter):
    """Mock: задержка AI + детерминированный GeneratedText по категории."""

    async def generate_text(
        self,
        image_data: str | None,
        category: str | None,
        prompt: str,
    ) -> GeneratedText:
        await asyncio.sleep(AI_CARD_MOCK_DELAY)
        # Промпт собран выше по пайплайну — логируем для отладки few-shot/RAG
        log.info("MockTextGen: category=%s, prompt_len=%d", category, len(prompt))
        key = (category or "").strip().lower()
        payload = _MOCK_TEXTS.get(key, _DEFAULT_TEXT)
        return GeneratedText(
            title=LocalizedText(**payload["title"]),
            description=payload["description"],
            bullets=list(payload["bullets"]),
            detected_category=payload["detected_category"],
            used_examples=[],
        )


def _parse_data_url(image_data: str | None) -> tuple[bytes, str] | None:
    """data-URL → (bytes, mime). Возвращает None, если это не data-URL."""
    if not image_data or not image_data.startswith("data:"):
        return None
    try:
        import base64

        header, _, payload = image_data.partition(",")
        mime = header[5:header.find(";")] or "image/jpeg"
        return base64.b64decode(payload), mime
    except Exception:
        return None


def _json_from_text(raw: str) -> dict:
    """Достать JSON-объект из ответа модели (снять ```json-обёртку при наличии)."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    text = text.strip()
    # На случай преамбулы — берём подстроку от первой { до последней }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


class GeminiTextGen(TextGenAdapter):
    """
    Реальный Gemini (vision → structured JSON по схеме GeneratedText).
    SDK импортируется лениво внутри метода — модуль остаётся импортируемым
    без установленного google-genai (моки, тесты, локальный запуск).
    """

    def __init__(self, api_key: str, model: str = GEMINI_MODEL):
        self.api_key = api_key
        self.model = model

    async def generate_text(
        self,
        image_data: str | None,
        category: str | None,
        prompt: str,
    ) -> GeneratedText:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)

        contents: list = []
        parsed = _parse_data_url(image_data)
        if parsed:
            image_bytes, mime = parsed
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime))
        contents.append(prompt)

        # Синхронный SDK-вызов уводим в тред, чтобы не блокировать event loop
        def _call():
            return client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.7,
                    max_output_tokens=1024,
                ),
            )

        response = await asyncio.to_thread(_call)
        data = _json_from_text(getattr(response, "text", "") or "")

        title = data.get("title") or {}
        ru = (title.get("ru") or data.get("title_ru") or "").strip()
        uz = (title.get("uz") or data.get("title_uz") or "").strip()
        # Заголовок не длиннее лимита (страховка поверх промпта)
        ru, uz = ru[:TITLE_MAX_LEN], uz[:TITLE_MAX_LEN]
        return GeneratedText(
            title=LocalizedText(ru=ru or uz, uz=uz or ru),
            description=(data.get("description") or "").strip(),
            bullets=[str(b) for b in (data.get("bullets") or [])][:5],
            detected_category=(data.get("detected_category") or category or "").strip(),
            used_examples=[],
        )


def get_text_gen() -> TextGenAdapter:
    """Фабрика провайдера текста. По умолчанию — по MOCK_MODE; можно явно
    выбрать через AI_CARD_TEXT_PROVIDER. Реальный Gemini — только при ключе."""
    if _text_provider() == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            return GeminiTextGen(api_key=api_key)
        log.warning("AI_CARD_TEXT_PROVIDER=gemini, но GEMINI_API_KEY не задан — fallback на Mock")
    return MockTextGen()


async def generate_card_text(
    image_data: str | None,
    category: str | None,
    prompt: str,
    retries: int = 2,
) -> GeneratedText:
    """Высокоуровневая обёртка.

    Мок-режим — всегда возвращает демо-черновик (без ошибок).
    Реальный провайдер — с ретраями на транзиентные сбои (429/таймаут). Если
    после ретраев не вышло — НЕ подменяем демо-текстом, а поднимаем
    AICardTextUnavailable, чтобы UI показал честную ошибку вместо «шампуня»."""
    adapter = get_text_gen()
    if isinstance(adapter, MockTextGen):
        return await adapter.generate_text(image_data, category, prompt)

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await adapter.generate_text(image_data, category, prompt)
        except Exception as exc:  # сеть/лимит/парсинг
            last_exc = exc
            log.warning(
                "Gemini generate_text попытка %d/%d не удалась: %s",
                attempt + 1, retries + 1, exc,
            )
            if attempt < retries:
                await asyncio.sleep(2 * (attempt + 1))  # 2с, затем 4с
    raise AICardTextUnavailable(str(last_exc) if last_exc else "unknown")
