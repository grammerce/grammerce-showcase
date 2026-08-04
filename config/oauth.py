"""
OAuth Configuration
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings


class OAuthSettings(BaseSettings):
    """Настройки внешних входов и связи с платформенным ботом.

    Все поля опциональны: незаполненная интеграция должна означать «выключена»,
    а не «приложение не стартует». Отсюда валидатор пустой строки ниже — .env
    и .env.example штатно содержат `PLATFORM_MANAGER_CHAT_ID=` без значения.
    """

    # Google
    google_client_id: str | None = None
    google_client_secret: str | None = None

    # Apple
    apple_client_id: str | None = None
    apple_team_id: str | None = None
    apple_key_id: str | None = None
    apple_private_key: str | None = None

    # Telegram — токен ГЛАВНОГО маркетинг-бота Grammerce (@Grammerce, тот же, что
    # для deeplink-логина «Создать магазин»). Через него платформа шлёт менеджеру
    # заявки Setup Fee (send_manager_telegram). НЕ путать с BOT_TOKEN (шоп-бот).
    telegram_bot_token: str | None = None
    telegram_bot_username: str | None = None

    # Секрет для Telegram-deeplink регистрации из маркетингового бота Grammerce.
    # Бот подписывает им POST /api/auth/telegram/issue в заголовке X-Bot-Secret.
    platform_bot_shared_secret: str | None = None

    # URL эндпоинта /notify маркет-бота Grammerce — сюда платформа шлёт
    # уведомления (delegated_request и т.д.) с тем же shared_secret.
    platform_bot_notify_url: str | None = None

    # Базовый URL Telegram-бота Grammerce для отправки состояния воронки владельца
    # (services/funnel_sync.py → POST {bot_base_url}/api/bot/funnel-state). Секрет —
    # тот же platform_bot_shared_secret в заголовке X-Bot-Secret. Если пуст — отправка
    # пропускается (fire-and-forget).
    bot_base_url: str | None = None

    # Numeric Telegram chat_id менеджера Grammerce, которому уходят заявки
    # «Свяжемся с вами» (Setup Fee). Платформа шлёт sendMessage напрямую через
    # telegram_bot_token. Менеджер должен был хоть раз написать grammerce-боту.
    platform_manager_chat_id: int | None = None

    # URLs
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"

    @field_validator("platform_manager_chat_id", mode="before")
    @classmethod
    def _empty_string_means_unset(cls, v):
        """Пустая переменная окружения — это «не задано», а не «невалидное число».

        Без этого `PLATFORM_MANAGER_CHAT_ID=` (штатное состояние .env.example и
        любого стенда без менеджера) валит ValidationError прямо на импорте
        config.oauth — то есть приложение не поднимается вообще. Тот же класс
        ошибки описан в config/settings.py, где числа читаются через _int_env.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.backend_url}/api/auth/google/callback"

    @property
    def apple_redirect_uri(self) -> str:
        return f"{self.backend_url}/api/auth/apple/callback"

    @property
    def telegram_redirect_uri(self) -> str:
        return f"{self.backend_url}/api/auth/telegram/callback"

    class Config:
        env_file = ".env"
        extra = "ignore"


oauth_settings = OAuthSettings()
