"""
RAG-ретривер примеров для few-shot (md_s/grammerce_ai_card_spec.md, 3.3 п.3).

MVP: фильтр по категории с fallback на "_default".
Точка расширения (вне объёма): эмбеддинги + косинусная близость вместо
точного совпадения категории — без изменения сигнатуры retrieve().
"""
from __future__ import annotations

import logging

from integrations.ai_card.repository import AICardRepository
from integrations.ai_card.settings import FEW_SHOT_K
from models import AICardExample

log = logging.getLogger(__name__)

DEFAULT_CATEGORY = "_default"


async def retrieve(
    repo: AICardRepository,
    category: str | None,
    k: int = FEW_SHOT_K,
    shop_id: int | None = None,
) -> list[AICardExample]:
    """Top-k одобренных/seed примеров по категории, fallback на _default."""
    examples: list[AICardExample] = []
    if category:
        examples = await repo.list_examples(category, k, shop_id)
    if not examples:
        examples = await repo.list_examples(DEFAULT_CATEGORY, k, shop_id)
    log.info("ai-card retrieve: category=%s → %d примеров", category, len(examples))
    return examples
