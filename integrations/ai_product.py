"""
AI-помощник заполнения товара (§ Этап 5).

Adapter + Mock-first по конвенции проекта: структура готова, реальный
Claude Vision подключается позже одним ключом (env AI_PRODUCT_PROVIDER=claude
+ ANTHROPIC_API_KEY). По умолчанию работает Mock — фото превращается в
правдоподобный черновик названия/описания/категории под нишу магазина.

Контракт: фото (data-URL/base64) → ProductDraft (черновик для формы товара).
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)


@dataclass
class ProductDraft:
    """Черновик товара, предложенный AI по фото. Все поля редактируемы пользователем."""
    name: str
    description: str
    category: str | None = None  # подсказка категории/ниши
    confidence: float = 0.0          # 0..1 — насколько модель уверена

    def to_dict(self) -> dict:
        return asdict(self)


class AIProductAdapter(ABC):
    """Абстрактный интерфейс AI-распознавания товара по фото."""

    @abstractmethod
    async def analyze_image(self, image_data: str, niche: str | None = None) -> ProductDraft:
        """Принять фото (data-URL/base64) и вернуть черновик товара."""
        raise NotImplementedError


# Правдоподобные шаблоны черновика под нишу — для Mock (демо без реального API).
_NICHE_DRAFTS = {
    "flowers": ("Букет роз", "Свежий букет из роз с упаковкой и открыткой. Уточните количество и цвет."),
    "clothing": ("Базовая футболка", "Хлопковая футболка унисекс. Укажите размер и цвет."),
    "cosmetics": ("Уходовый крем", "Питательный крем для лица, 50 мл. Дополните составом и эффектом."),
    "electronics": ("Гаджет", "Электронное устройство. Добавьте характеристики и комплектацию."),
    "grocery": ("Продуктовый набор", "Набор продуктов. Уточните состав и вес."),
    "horeca": ("Блюдо дня", "Свежеприготовленное блюдо. Добавьте состав и вес порции."),
    "auto_parts": ("Автозапчасть", "Запчасть для авто. Укажите марку, модель и артикул."),
    "home": ("Товар для дома", "Практичный товар для дома. Добавьте материал и размеры."),
    "beauty": ("Косметический товар", "Средство для красоты и ухода. Дополните описанием."),
    "sport": ("Спортивный товар", "Товар для спорта и активного отдыха. Уточните размер."),
    "pharmacy": ("Товар для здоровья", "Товар из категории здоровья. Добавьте описание и дозировку."),
}
_DEFAULT_DRAFT = ("Новый товар", "Краткое описание товара. Добавьте детали и преимущества.")


class MockAIProductAdapter(AIProductAdapter):
    """Mock: возвращает детерминированный черновик под нишу. Реальный API не вызывает."""

    async def analyze_image(self, image_data: str, niche: str | None = None) -> ProductDraft:
        name, description = _NICHE_DRAFTS.get(niche or "", _DEFAULT_DRAFT)
        log.info("MockAIProductAdapter: draft for niche=%s", niche)
        return ProductDraft(name=name, description=description, category=niche, confidence=0.5)


class ClaudeVisionProductAdapter(AIProductAdapter):
    """
    Заглушка для реального Claude Vision (claude-opus-4-8 / vision).
    Реализуется позже: отправка изображения в Anthropic API и парсинг ответа
    в ProductDraft. Сейчас сигнализирует фабрике о необходимости fallback.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def analyze_image(self, image_data: str, niche: str | None = None) -> ProductDraft:
        raise NotImplementedError("Claude Vision adapter ещё не реализован — используется Mock")


def get_ai_product_adapter() -> AIProductAdapter:
    """
    Фабрика AI-адаптера. Mock-first: по умолчанию Mock.
    Реальный провайдер включается через env AI_PRODUCT_PROVIDER=claude
    при наличии ANTHROPIC_API_KEY. При любой проблеме — fallback на Mock.
    """
    provider = (os.getenv("AI_PRODUCT_PROVIDER") or "mock").lower()
    if provider == "claude":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            return ClaudeVisionProductAdapter(api_key=api_key)
        log.warning("AI_PRODUCT_PROVIDER=claude, но ANTHROPIC_API_KEY не задан — fallback на Mock")
    return MockAIProductAdapter()


async def generate_product_draft(image_data: str, niche: str | None = None) -> ProductDraft:
    """Высокоуровневая обёртка: вернуть черновик, с авто-fallback на Mock при ошибке."""
    adapter = get_ai_product_adapter()
    try:
        return await adapter.analyze_image(image_data, niche)
    except NotImplementedError:
        log.warning("AI adapter не реализован — fallback на Mock")
        return await MockAIProductAdapter().analyze_image(image_data, niche)
