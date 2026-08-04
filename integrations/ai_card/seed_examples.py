"""
Первичное наполнение библиотеки примеров ai_card_examples (спека 3.3 п.3).

Идемпотентно: пример добавляется только если пары (category, title.ru) ещё нет.
Запуск: docker compose exec app python -m integrations.ai_card.seed_examples
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from models import AICardExample

log = logging.getLogger(__name__)

# Глобальные seed-примеры (shop_id=NULL) по нишам проекта
SEED_EXAMPLES: list[dict] = [
    {
        "category": "flowers",
        "title": {"ru": "Букет красных роз в крафте", "uz": "Kraft o'ramdagi qizil atirgullar guldastasi"},
        "description": "Букет свежих красных роз в стильной крафтовой упаковке. Отличный подарок к любому празднику.",
        "bullets": ["Свежие розы", "Крафтовая упаковка", "Открытка в подарок"],
    },
    {
        "category": "flowers",
        "title": {"ru": "Композиция в шляпной коробке", "uz": "Shlyapa qutisidagi gul kompozitsiyasi"},
        "description": "Нежная композиция из хризантем и эвкалипта в шляпной коробке. Не требует вазы.",
        "bullets": ["Готова к вручению", "Стойкие цветы", "Шляпная коробка"],
    },
    {
        "category": "cosmetics",
        "title": {"ru": "Питательный крем для лица 50 мл", "uz": "Yuz uchun oziqlantiruvchi krem 50 ml"},
        "description": "Питательный крем для ежедневного ухода за кожей лица. Лёгкая текстура, быстро впитывается.",
        "bullets": ["Глубокое питание", "Лёгкая текстура", "Для всех типов кожи"],
    },
    {
        "category": "clothing",
        "title": {"ru": "Базовая футболка из хлопка", "uz": "Paxtadan tikilgan bazaviy futbolka"},
        "description": "Хлопковая футболка унисекс на каждый день. Мягкая ткань, аккуратные швы.",
        "bullets": ["100% хлопок", "Унисекс", "На каждый день"],
    },
    {
        "category": "_default",
        "title": {"ru": "Шампунь L'Oréal Liss Unlimited", "uz": "L'Oréal Liss Unlimited shampuni"},
        "description": "Профессиональный шампунь для интенсивного разглаживания и смягчения непослушных волос.",
        "bullets": ["Глубокое разглаживание", "Для непослушных волос", "Профессиональная серия"],
    },
]


async def seed_examples(db) -> int:
    """Засеять недостающие примеры. Возвращает число добавленных строк."""
    added = 0
    for item in SEED_EXAMPLES:
        result = await db.execute(
            select(AICardExample.id).where(
                AICardExample.category == item["category"],
                AICardExample.shop_id.is_(None),
            )
        )
        existing_ids = result.scalars().all()
        # Проверка по title.ru среди существующих строк категории (JSON-поле —
        # сравниваем в Python, чтобы не зависеть от диалекта БД)
        exists = False
        if existing_ids:
            rows = await db.execute(
                select(AICardExample).where(AICardExample.id.in_(existing_ids))
            )
            exists = any(
                (row.title or {}).get("ru") == item["title"]["ru"]
                for row in rows.scalars().all()
            )
        if exists:
            continue
        db.add(AICardExample(shop_id=None, source="seed", **item))
        added += 1
    await db.commit()
    log.info("ai-card seed: добавлено %d примеров", added)
    return added


async def _main() -> None:
    from database import async_session_factory

    async with async_session_factory() as db:
        added = await seed_examples(db)
        log.info("seed_examples: добавлено %s", added)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
