from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Используем локальный фронт по умолчанию; при деплое можно переопределить через ENV.
WEB_APP_URL = os.getenv("WEB_APP_URL", "http://localhost:8000/")


def _parse_admin_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            continue
    return result


@dataclass
class Settings:
    bot_token: str
    admin_ids: list[int]
    database_url: str
    static_dir: Path


def load_settings() -> Settings:
    bot_token = (os.getenv("BOT_TOKEN") or "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN environment variable is required.")

    # Prefer env, but fall back to SQLite for local development.
    database_url = (
        os.getenv("DATABASE_URL")
        or "sqlite+aiosqlite:///./local.db"
    ).strip()

    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID"))
    static_dir = Path(os.getenv("STATIC_DIR", BASE_DIR / "dist")).resolve()

    return Settings(
        bot_token=bot_token,
        admin_ids=admin_ids,
        database_url=database_url,
        static_dir=static_dir,
    )


settings = load_settings()

# Единый пул соединений из database.py (pool_size=10, pool_recycle=3600)
# Не создаём отдельный engine — бот и FastAPI используют одни и те же соединения
from database import async_session_factory  # noqa: E402

__all__ = ["settings", "async_session_factory", "WEB_APP_URL", "BASE_DIR"]
