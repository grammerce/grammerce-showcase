"""
Фабрика POS-коннекторов.
Возвращает нужный адаптер по настройкам магазина (shop.integration_settings).
"""
from __future__ import annotations

import logging

from .base import PosConnector
from .mock_adapter import MockPosAdapter

log = logging.getLogger(__name__)


def get_pos_connector(shop) -> PosConnector | None:
    """
    Возвращает адаптер по shop.integration_settings.
    Если type == "none" → возвращает None (POS не подключён).

    Автоматический fallback на MockPosAdapter если реальный адаптер
    бросает NotImplementedError (BILLZ/Jowi ждут API-доступа).
    """
    settings: dict = shop.integration_settings or {"type": "none"}
    pos_type: str = settings.get("type", "none")

    if pos_type == "none":
        return None

    if pos_type == "mock":
        # Мок выбирается настройкой магазина в БД, а не окружением, поэтому
        # доступен и в production. Это осознанно (демо и отладка на живом
        # сервере), но синхронизация с ним фиктивна — предупреждаем.
        from config.settings import settings as _cfg
        if _cfg.is_production:
            log.warning(
                "Магазин %s использует mock POS-адаптер в production: "
                "синхронизация каталога и остатков фиктивная.", shop.id
            )
        return MockPosAdapter(shop.id)

    if pos_type == "moysklad":
        try:
            from .moysklad_adapter import MoySkladAdapter
            return MoySkladAdapter(
                api_token=settings["api_key"],
                organization_id=settings.get("organization_id"),
            )
        except KeyError as e:
            log.error("MoySklad adapter: missing config key %s for shop %s", e, shop.id)
            return None

    if pos_type == "billz":
        try:
            from .billz_adapter import BillzAdapter
            adapter = BillzAdapter(
                api_key=settings["api_key"],
                api_secret=settings["api_secret"],
                api_url=settings.get("api_url", "https://api.billz.io/v1"),
            )
            return adapter
        except KeyError as e:
            log.error("BILLZ adapter: missing config key %s for shop %s", e, shop.id)
            return None
        except NotImplementedError:
            log.warning(
                "BILLZ adapter not ready, falling back to mock for shop %s — "
                "остатки и каталог не синхронизируются с реальной кассой", shop.id
            )
            return MockPosAdapter(shop.id)

    if pos_type == "jowi":
        try:
            from .jowi_adapter import JowiAdapter
            adapter = JowiAdapter(
                api_key=settings["api_key"],
                api_secret=settings["api_secret"],
                restaurant_id=settings["restaurant_id"],
            )
            return adapter
        except KeyError as e:
            log.error("Jowi adapter: missing config key %s for shop %s", e, shop.id)
            return None
        except NotImplementedError:
            log.warning(
                "Jowi adapter not ready, falling back to mock for shop %s — "
                "остатки и каталог не синхронизируются с реальной кассой", shop.id
            )
            return MockPosAdapter(shop.id)

    log.warning("Unknown POS type '%s' for shop %s", pos_type, shop.id)
    return None
