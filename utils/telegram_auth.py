"""
Telegram WebApp authentication utilities.

Validates initData from Telegram Mini Apps to ensure requests come from legitimate
Telegram users and haven't been tampered with.

Documentation: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import logging
from urllib.parse import parse_qsl

log = logging.getLogger(__name__)


# Максимальный возраст initData (защита от replay перехваченной строки).
# Telegram рекомендует отвергать устаревшие auth_date. 24 часа — компромисс между
# безопасностью и тем, что WebApp может оставаться открытым долго.
INIT_DATA_MAX_AGE_SECONDS = 86400


def verify_telegram_web_app_data(
    init_data: str, bot_token: str, max_age_seconds: int = INIT_DATA_MAX_AGE_SECONDS
) -> bool:
    """
    Verify that initData received from Telegram WebApp is authentic.

    Args:
        init_data: The initData string from window.Telegram.WebApp.initData
        bot_token: The bot token for this shop
        max_age_seconds: reject initData older than this (0/None — не проверять)

    Returns:
        True if the data is valid, False otherwise

    Example:
        >>> verify_telegram_web_app_data(init_data, "123456:ABC-DEF...")
        True
    """
    if not init_data or not bot_token:
        return False

    try:
        # Parse the init_data string into key-value pairs
        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))

        # Extract the hash that Telegram sent
        received_hash = parsed_data.pop("hash", None)
        if not received_hash:
            return False

        # Sort the remaining parameters alphabetically
        data_check_arr = [f"{k}={v}" for k, v in sorted(parsed_data.items())]
        data_check_string = "\n".join(data_check_arr)

        # Create secret key from bot token
        secret_key = hmac.new(
            key=b"WebAppData", msg=bot_token.encode(), digestmod=hashlib.sha256
        ).digest()

        # Calculate the hash
        calculated_hash = hmac.new(
            key=secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256
        ).hexdigest()

        # Compare hashes
        if not hmac.compare_digest(calculated_hash, received_hash):
            return False

        # Проверка свежести auth_date (anti-replay)
        if max_age_seconds:
            import time
            try:
                auth_date = int(parsed_data.get("auth_date", "0"))
            except (TypeError, ValueError):
                return False
            if auth_date <= 0 or (time.time() - auth_date) > max_age_seconds:
                return False

        return True

    except Exception as e:
        log.warning("Не удалось проверить подпись Telegram WebApp: %r", e)
        return False


def parse_init_data(init_data: str) -> dict[str, str]:
    """
    Parse initData string into a dictionary.

    Args:
        init_data: The initData string from Telegram WebApp

    Returns:
        Dictionary with parsed parameters

    Example:
        >>> parse_init_data("user=%7B%22id%22%3A123...&hash=abc...")
        {'user': '{"id":123,...}', 'hash': 'abc...', ...}
    """
    if not init_data:
        return {}

    try:
        return dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return {}


def extract_user_id(init_data: str) -> int | None:
    """
    Extract Telegram user ID from initData.

    Args:
        init_data: The initData string from Telegram WebApp

    Returns:
        User ID as integer, or None if not found

    Example:
        >>> extract_user_id(init_data)
        123456789
    """
    import json

    try:
        parsed = parse_init_data(init_data)
        user_json = parsed.get("user", "")

        if user_json:
            user_data = json.loads(user_json)
            return user_data.get("id")

        return None
    except Exception:
        return None


def extract_user_data(init_data: str) -> dict | None:
    """
    Extract full user data from initData.

    Args:
        init_data: The initData string from Telegram WebApp

    Returns:
        Dictionary with user data (id, first_name, last_name, username, language_code)
        or None if not found

    Example:
        >>> extract_user_data(init_data)
        {'id': 123, 'first_name': 'John', 'username': 'john_doe', ...}
    """
    import json

    try:
        parsed = parse_init_data(init_data)
        user_json = parsed.get("user", "")

        if user_json:
            return json.loads(user_json)

        return None
    except Exception:
        return None


# Example usage for FastAPI dependency injection
async def verify_telegram_request(
    x_telegram_init_data: str, bot_token: str
) -> dict[str, any]:
    """
    FastAPI dependency to verify Telegram WebApp requests.

    Usage in FastAPI:
        @app.post("/api/v1/shop/{shop_id}/orders")
        async def create_order(
            shop_id: int,
            x_telegram_init_data: str = Header(None),
            db: AsyncSession = Depends(get_db)
        ):
            # Get shop and verify
            shop = await db.get(Shop, shop_id)
            if not verify_telegram_web_app_data(x_telegram_init_data, shop.bot_token):
                raise HTTPException(401, "Invalid Telegram data")

            user_data = extract_user_data(x_telegram_init_data)
            # ... process order
    """
    if not verify_telegram_web_app_data(x_telegram_init_data, bot_token):
        return {"valid": False, "user": None}

    user_data = extract_user_data(x_telegram_init_data)
    return {"valid": True, "user": user_data}
