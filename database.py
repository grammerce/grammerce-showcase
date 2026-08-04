from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Централизованный конфиг (config/settings.py делает load_dotenv).
# Fallback на SQLite сохраняется — задан дефолтом в settings.database_url.
from config.settings import settings

DATABASE_URL = settings.database_url

# Определяем параметры движка в зависимости от типа БД
is_sqlite = DATABASE_URL.startswith("sqlite")

engine_kwargs = {
    "echo": False,  # Отключить логирование SQL для production
}

# SQLite не поддерживает pool_size
if not is_sqlite:
    engine_kwargs.update({
        "pool_size": 30,        # для 100+ магазинов (было: 15)
        "max_overflow": 70,     # итого максимум 100 соединений (было: 25)
        "pool_timeout": 10,     # fail fast при исчерпании пула (секунды)
        "pool_pre_ping": True,  # проверка соединения перед использованием
        "pool_recycle": 3600,   # пересоздавать соединения каждый час
    })

# --- ENGINE ---
engine = create_async_engine(DATABASE_URL, **engine_kwargs)

# --- SESSION FACTORY ---
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# --- BASE MODEL ---
class Base(DeclarativeBase):
    pass


# --- DEPENDENCY ---
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
