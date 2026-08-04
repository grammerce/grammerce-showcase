"""
CatalogSyncService — синхронизация каталога из POS в нашу БД.

Правила:
- POS = источник правды по ценам, остаткам, названиям
- Товары без external_id (добавленные вручную) — не трогаем
- Удалённые в POS товары → is_active = False (не удаляем)
- Если POS недоступен — логируем ошибку, работаем с последним кешем
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Category, Product, Shop

log = logging.getLogger(__name__)


class CatalogSyncService:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync(self, shop_id: int) -> dict:
        """
        Синхронизировать каталог для магазина.
        Возвращает {"synced": N, "created": N, "updated": N, "deactivated": N}.
        """
        from integrations.factory import get_pos_connector

        shop_result = await self.db.execute(select(Shop).where(Shop.id == shop_id))
        shop = shop_result.scalar_one_or_none()
        if not shop:
            raise ValueError(f"Shop {shop_id} not found")

        connector = get_pos_connector(shop)
        if connector is None:
            log.info("Shop %s: no POS integration configured, skipping sync", shop_id)
            return {"synced": 0, "created": 0, "updated": 0, "deactivated": 0}

        pos_source = shop.integration_settings.get("type", "none")

        try:
            pos_products = await connector.sync_catalog()
        except Exception as e:
            log.error("Shop %s: POS sync_catalog failed: %r", shop_id, e)
            return {"synced": 0, "created": 0, "updated": 0, "deactivated": 0, "error": str(e)}

        # Загружаем все текущие POS-товары магазина (только с external_id)
        existing_result = await self.db.execute(
            select(Product).where(
                Product.shop_id == shop_id,
                Product.external_source == pos_source,
            )
        )
        existing_products = {p.external_id: p for p in existing_result.scalars().all()}

        now = datetime.now(UTC)
        created = updated = deactivated = 0
        seen_external_ids: set[str] = set()

        for pos_p in pos_products:
            seen_external_ids.add(pos_p.external_id)

            # Найти или создать категорию
            category_id = await self._get_or_create_category(
                shop_id=shop_id,
                name=pos_p.category_name,
                external_id=f"{pos_source}:{pos_p.category_name}" if pos_p.category_name else None,
            )

            if pos_p.external_id in existing_products:
                # Обновляем существующий
                product = existing_products[pos_p.external_id]
                product.name = pos_p.name
                product.price = pos_p.price
                product.is_active = pos_p.in_stock
                product.last_synced_at = now
                if pos_p.stock_quantity is not None:
                    product.stock = pos_p.stock_quantity
                if pos_p.description:
                    product.description = pos_p.description
                if pos_p.image_url:
                    product.image_url = pos_p.image_url
                if category_id:
                    product.category_id = category_id
                updated += 1
            else:
                # Создаём новый
                product = Product(
                    shop_id=shop_id,
                    name=pos_p.name,
                    price=pos_p.price,
                    description=pos_p.description or "",
                    image_url=pos_p.image_url,
                    sku=pos_p.sku,
                    stock=pos_p.stock_quantity or 0,
                    is_active=pos_p.in_stock,
                    external_id=pos_p.external_id,
                    external_source=pos_source,
                    last_synced_at=now,
                    category_id=category_id,
                )
                self.db.add(product)
                created += 1

        # Деактивируем товары, которых больше нет в POS
        for ext_id, product in existing_products.items():
            if ext_id not in seen_external_ids and product.is_active:
                product.is_active = False
                deactivated += 1

        await self.db.commit()

        total = len(pos_products)
        log.info(
            "Shop %s sync complete: %d POS products → created=%d, updated=%d, deactivated=%d",
            shop_id, total, created, updated, deactivated,
        )
        return {"synced": total, "created": created, "updated": updated, "deactivated": deactivated}

    async def _get_or_create_category(
        self,
        shop_id: int,
        name: str | None,
        external_id: str | None,
    ) -> int | None:
        """Найти или создать категорию. Возвращает category.id или None."""
        if not name:
            return None

        result = await self.db.execute(
            select(Category).where(
                Category.shop_id == shop_id,
                Category.name == name,
            )
        )
        cat = result.scalar_one_or_none()
        if cat:
            return cat.id

        cat = Category(
            shop_id=shop_id,
            name=name,
            external_id=external_id,
        )
        self.db.add(cat)
        await self.db.flush()
        return cat.id
