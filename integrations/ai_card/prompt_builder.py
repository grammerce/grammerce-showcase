"""
Сборка промпта генерации текста (md_s/grammerce_ai_card_spec.md, раздел 7):
system + few_shot(seed из prompts/few_shot/) + few_shot(RAG из библиотеки) + user.

Промпт собирается и в мок-режиме (логируется) — чтобы пайплайн few-shot/RAG
отлаживался без реальных токенов.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from integrations.ai_card.settings import TITLE_MAX_LEN
from models import AICardExample

log = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_FEW_SHOT_DIR = _PROMPTS_DIR / "few_shot"


def _load_system_prompt() -> str:
    try:
        text = (_PROMPTS_DIR / "system_text.md").read_text(encoding="utf-8")
    except OSError:
        log.warning("ai-card: system_text.md не найден — используется минимальный system")
        text = "Ты — копирайтер карточек товаров маркетплейса. Отвечай строго JSON GeneratedText."
    return text.replace("{TITLE_MAX_LEN}", str(TITLE_MAX_LEN))


def _load_seed_few_shot(category: str | None) -> list[dict]:
    """Эталонные примеры категории из prompts/few_shot/<category>.json, иначе _default."""
    candidates = []
    if category:
        # Имя файла — только из безопасных символов (категория приходит от пользователя)
        safe = "".join(c for c in category.lower() if c.isalnum() or c in "_-")
        if safe:
            candidates.append(_FEW_SHOT_DIR / f"{safe}.json")
    candidates.append(_FEW_SHOT_DIR / "_default.json")

    for path in candidates:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        except json.JSONDecodeError:
            log.warning("ai-card: битый few-shot файл %s — пропускаю", path.name)
    return []


def _example_to_pair(example: AICardExample) -> dict:
    """AICardExample (БД) → пара input/output в формате few-shot."""
    return {
        "input": {"vision_desc": "", "category": example.category},
        "output": {
            "title": example.title,
            "description": example.description,
            "bullets": example.bullets,
            "detected_category": example.category,
            "used_examples": [],
        },
    }


def build_prompt(
    category: str | None,
    vision_desc: str = "",
    rag_examples: list[AICardExample] | None = None,
) -> str:
    """Финальный промпт: system + few_shot(seed) + few_shot(RAG) + user."""
    parts = [_load_system_prompt()]

    seed_pairs = _load_seed_few_shot(category)
    if seed_pairs:
        parts.append("## Примеры (эталонные)\n" + json.dumps(seed_pairs, ensure_ascii=False, indent=2))

    rag_pairs = [_example_to_pair(e) for e in (rag_examples or [])]
    if rag_pairs:
        parts.append("## Примеры (одобренные)\n" + json.dumps(rag_pairs, ensure_ascii=False, indent=2))

    user_block = json.dumps(
        {"vision_desc": vision_desc, "category": category or "_default"},
        ensure_ascii=False,
    )
    parts.append("## Задача\n" + user_block)

    prompt = "\n\n".join(parts)
    log.info(
        "ai-card build_prompt: category=%s, seed=%d, rag=%d, len=%d",
        category, len(seed_pairs), len(rag_pairs), len(prompt),
    )
    return prompt
