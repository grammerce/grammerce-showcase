"""
Хранилище контура улучшения AI-карточки (md_s/grammerce_ai_card_spec.md, 3.3):
библиотека примеров (few-shot/RAG) + журнал генераций (фидбэк пользователя).

Repository-паттерн: бизнес-логика работает с интерфейсом AICardRepository,
реализация — на основной БД проекта (Postgres, SQLAlchemy async; в тестах
прозрачно работает на SQLite через тот же интерфейс).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AICardExample, AICardGeneration

log = logging.getLogger(__name__)


class AICardRepository(ABC):
    """Интерфейс хранилища примеров и генераций."""

    @abstractmethod
    async def list_examples(
        self, category: str, k: int, shop_id: int | None = None
    ) -> list[AICardExample]:
        """Top-k примеров категории: глобальные seed + одобренные этого магазина."""
        raise NotImplementedError

    @abstractmethod
    async def add_example(
        self,
        category: str,
        title: dict,
        description: str,
        bullets: list,
        source: str = "seed",
        shop_id: int | None = None,
    ) -> int:
        raise NotImplementedError

    @abstractmethod
    async def save_generation(self, shop_id: int, gen: dict) -> int:
        """Записать журнал генерации; gen — поля AICardGeneration без id/shop_id."""
        raise NotImplementedError


class DBAICardRepository(AICardRepository):
    """Реализация на основной БД проекта (SQLAlchemy 2.0 async)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_examples(
        self, category: str, k: int, shop_id: int | None = None
    ) -> list[AICardExample]:
        query = select(AICardExample).where(AICardExample.category == category)
        if shop_id is None:
            query = query.where(AICardExample.shop_id.is_(None))
        else:
            # Глобальные seed-примеры + одобренные примеры этого магазина,
            # НИКОГДА не примеры чужих магазинов (изоляция по shop_id)
            query = query.where(
                (AICardExample.shop_id.is_(None)) | (AICardExample.shop_id == shop_id)
            )
        query = query.order_by(AICardExample.id.desc()).limit(k)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def add_example(
        self,
        category: str,
        title: dict,
        description: str,
        bullets: list,
        source: str = "seed",
        shop_id: int | None = None,
    ) -> int:
        example = AICardExample(
            shop_id=shop_id,
            category=category,
            title=title,
            description=description,
            bullets=bullets,
            source=source,
        )
        self.db.add(example)
        await self.db.commit()
        await self.db.refresh(example)
        return example.id

    async def save_generation(self, shop_id: int, gen: dict) -> int:
        generation = AICardGeneration(shop_id=shop_id, **gen)
        self.db.add(generation)
        await self.db.commit()
        await self.db.refresh(generation)
        return generation.id
