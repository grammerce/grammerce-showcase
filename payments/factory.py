"""
Фабрика платёжных шлюзов.

Выбирает реальный провайдер (если есть ключи) или возвращает MockGateway.
Конфигурация берётся из переменных окружения или shop_config (JSON из БД).
"""
import logging
import os

from payments.base import PaymentGateway
from payments.mock_gateway import MockGateway

log = logging.getLogger(__name__)


def get_gateway(
    provider: str,
    shop_config: dict | None = None,
    base_url: str | None = None,
) -> PaymentGateway:
    """
    Возвращает нужный gateway по имени провайдера.
    Приоритет ключей: shop_config → env.
    Если для провайдера нет реальных ключей — возвращает MockGateway.

    :param provider:    "click" / "payme" / "uzum" / "cash" / "mock"
    :param shop_config: dict из shop.config (может содержать ключи провайдера)
    :param base_url:    Базовый URL сервера (для mock checkout URL)
    """
    config = shop_config or {}
    _base = base_url or os.getenv("BASE_URL") or os.getenv("WEB_APP_URL", "http://localhost:8000")

    # Ключи могут лежать в двух местах:
    # 1. shop.config.cart.payments.<provider>.* — формат кабинета владельца (EditorSidebar)
    # 2. shop.config.CLICK_SERVICE_ID и т.д. — плоский формат / env-переменные
    _payments_cfg = (config.get("cart") or {}).get("payments") or {}

    if provider == "click":
        _click = _payments_cfg.get("click") or {}
        # Явный off в кабинете владельца → сразу MockGateway,
        # даже если ключи заполнены. None/отсутствие = не блокирует (env-сценарий).
        if _click.get("enabled") is False:
            log.info("[MOCK] Click disabled by shop owner → MockGateway")
            return MockGateway(provider_name=provider, base_url=_base)

        service_id = _click.get("service_id") or config.get("CLICK_SERVICE_ID") or os.getenv("CLICK_SERVICE_ID", "")
        merchant_id = _click.get("merchant_id") or config.get("CLICK_MERCHANT_ID") or os.getenv("CLICK_MERCHANT_ID", "")
        merchant_user_id = _click.get("merchant_user_id") or config.get("CLICK_MERCHANT_USER_ID") or os.getenv("CLICK_MERCHANT_USER_ID", "")
        secret_key = _click.get("secret_key") or config.get("CLICK_SECRET_KEY") or os.getenv("CLICK_SECRET_KEY", "")

        if service_id and merchant_id and secret_key:
            from payments.click_gateway import ClickGateway
            log.info("Using real ClickGateway for provider '%s'", provider)
            return ClickGateway(service_id, merchant_id, merchant_user_id, secret_key)

    elif provider == "payme":
        _payme = _payments_cfg.get("payme") or {}
        if _payme.get("enabled") is False:
            log.info("[MOCK] Payme disabled by shop owner → MockGateway")
            return MockGateway(provider_name=provider, base_url=_base)

        merchant_id = _payme.get("merchant_id") or config.get("PAYME_ID") or os.getenv("PAYME_ID", "")
        key = _payme.get("key") or config.get("PAYME_KEY") or os.getenv("PAYME_KEY", "")

        if merchant_id and key:
            from payments.payme_gateway import PaymeGateway
            log.info("Using real PaymeGateway for provider '%s'", provider)
            return PaymeGateway(merchant_id, key)

    elif provider == "uzum":
        _uzum = _payments_cfg.get("uzum") or {}
        if _uzum.get("enabled") is False:
            log.info("[MOCK] Uzum disabled by shop owner → MockGateway")
            return MockGateway(provider_name=provider, base_url=_base)

        merchant_id = _uzum.get("merchant_id") or config.get("UZUM_MERCHANT_ID") or os.getenv("UZUM_MERCHANT_ID", "")
        api_key = _uzum.get("api_key") or config.get("UZUM_API_KEY") or os.getenv("UZUM_API_KEY", "")

        if merchant_id and api_key:
            from payments.uzum_gateway import UzumGateway
            log.info("Using real UzumGateway for provider '%s'", provider)
            return UzumGateway(merchant_id, api_key)

    # Мок как запасной вариант нужен, чтобы магазин без подключённой платёжки
    # мог принимать заказы, а не падал на оформлении. Но в production это
    # означает, что «оплата» проходит понарошку, — сообщаем об этом громко,
    # иначе такое легко не заметить.
    from config.settings import settings as _cfg
    if _cfg.is_production:
        log.warning(
            "[MOCK] В production у провайдера '%s' нет реальных ключей — заказы "
            "будут оплачиваться фиктивным MockGateway. Подключите ключи магазину.",
            provider,
        )
    else:
        log.info("[MOCK] No real keys for '%s' → using MockGateway", provider)
    return MockGateway(provider_name=provider, base_url=_base)
