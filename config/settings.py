"""
Центральный конфиг приложения (R2).

Сводит воедино разрозненные os.getenv для общих настроек (БД, Redis, URL-ы,
окружение, лимиты). Единый load_dotenv. Специфические группы (OAuth, billing)
остаются в своих модулях (config/oauth.py, config/billing.py).

Принцип безопасности: ВСЕ поля строковые с дефолтами, числа парсятся отдельными
свойствами с защитой от пустой строки — импорт не падает ни при каком .env
(в отличие от строгих типизированных BaseSettings, которые роняли всё приложение
на пустом PLATFORM_MANAGER_CHAT_ID).

Старые os.getenv в коде продолжают работать — миграция call-sites инкрементальная.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _int_env(name: str, default: int) -> int:
    """int из env с защитой от пустой строки/мусора.

    os.getenv(name, default) здесь не годится: дефолт применяется, только если
    переменная не задана вовсе. Заданная, но пустая (`TRIAL_DAYS=` в .env) вернёт
    "", и int("") уронит импорт модуля — то есть всё приложение.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("config: %s=%r не число, использую %s", name, raw, default)
        return default


def _float_env(name: str, default: float) -> float:
    """float из env с той же защитой, что и _int_env."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("config: %s=%r не число, использую %s", name, raw, default)
        return default


def _optional_int_env(name: str, default: int | None = None) -> int | None:
    """int из env, где пустое значение — легальное «функция отключена»."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("config: %s=%r не число, использую %s", name, raw, default)
        return default


_KNOWN_ENVIRONMENTS = ("production", "staging", "development")


def _validated_environment(raw: str | None) -> str:
    """Нормализовать ENVIRONMENT и не дать опечатке молча включить dev-режим.

    От этого значения зависят гейты mock-платежей, mock-Didox, mock POS-вебхука,
    закрытие /docs и HSTS. Любое неизвестное значение (`prod`, `Production`,
    опечатка) при наивном сравнении `== "production"` означало бы «не production»,
    то есть тихо открывало бы mock-эндпоинты на боевом сервере. Поэтому:
    регистр не важен, а неизвестное непустое значение — падение на старте.
    """
    value = (raw or "").strip().lower()
    if not value:
        return "development"
    if value not in _KNOWN_ENVIRONMENTS:
        raise RuntimeError(
            f"ENVIRONMENT={raw!r} не распознан. Допустимо: "
            f"{', '.join(_KNOWN_ENVIRONMENTS)}. "
            "Неизвестное значение трактовалось бы как НЕ production и включило бы "
            "mock-эндпоинты (оплата, Didox, POS-вебхук) на боевом сервере."
        )
    return value


class AppSettings:
    """Ленивое чтение общих настроек из окружения (значения фиксируются при импорте)."""

    def __init__(self) -> None:
        self.database_url: str = (os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///./local.db").strip()
        self.redis_url: str = (os.getenv("REDIS_URL") or "").strip()
        self.web_app_url: str = (os.getenv("WEB_APP_URL") or "http://localhost:8000/").strip()
        self.frontend_url: str = (os.getenv("FRONTEND_URL") or "").strip()
        self.backend_url: str = (os.getenv("BACKEND_URL") or "").strip()
        self.environment: str = _validated_environment(os.getenv("ENVIRONMENT"))
        self.token_ttl_seconds: int = _int_env("TOKEN_TTL_SECONDS", 86400)
        self.max_body_bytes: int = _int_env("MAX_BODY_BYTES", 20 * 1024 * 1024)
        # Импорт каталога архивом — единственная ручка, куда законно льют сотни МБ
        # (zip с картинками товаров). Общий лимит на неё не распространяется.
        self.max_archive_bytes: int = _int_env("MAX_ARCHIVE_BYTES", 500 * 1024 * 1024)
        # Telegram-аккаунты, которым разрешено быть суперадмином платформы.
        # Список chat_id через запятую. Пустое значение = вход суперадмина закрыт
        # (fail-closed: лучше лок-аут с break-glass-скриптом, чем открытая дверь).
        self.superadmin_telegram_ids_raw: str = (os.getenv("SUPERADMIN_TELEGRAM_IDS") or "").strip()

    @property
    def superadmin_telegram_ids(self) -> frozenset[str]:
        """Разрешённые Telegram chat_id суперадминов (строки, как приходят из Telegram).

        Это ПЕРВАЯ из двух обязательных проверок: вторая — запись в platform_users
        (role='superadmin', is_active). Одной записи в БД недостаточно, поэтому
        компрометации только базы для захвата суперадмина не хватит.
        """
        return frozenset(
            part.strip()
            for part in self.superadmin_telegram_ids_raw.split(",")
            if part.strip()
        )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def has_redis(self) -> bool:
        return bool(self.redis_url)

    def require_redis_in_prod(self) -> None:
        """В production Redis обязателен: без него сессии/FSM живут в памяти процесса
        и теряются при рестарте (все владельцы разлогиниваются). Падаем громко."""
        if self.is_production and not self.has_redis:
            raise RuntimeError(
                "REDIS_URL обязателен в production (ENVIRONMENT=production): "
                "иначе токены сессий и FSM бота хранятся в памяти и теряются при рестарте."
            )


settings = AppSettings()

__all__ = [
    "settings",
    "AppSettings",
    "BASE_DIR",
    "_int_env",
    "_float_env",
    "_optional_int_env",
]
