"""Общие фикстуры тестов.

Отличие от версии в основном репозитории: здесь нет фикстуры `client`.

Она поднимала `TestClient(app)` и потому импортировала `main` — сборку
FastAPI-приложения, которая в эту выборку модулей не входит (см. README).
Тесты в этом репозитории работают со слоем данных и доменной логикой напрямую
и используют только `db_session`, поэтому удаление `client` ничего не
подменяет и ничего не скрывает: сами модули перенесены без изменений.
"""
import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import payments.models  # noqa: F401 — регистрирует Payment в Base.metadata
from models import Base, Category, Product, Shop

# Test database (SQLite for testing)
TEST_DATABASE_URL = "sqlite:///./test.db"

engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db_session():
    """
    Creates a clean database for each test
    """
    # Create tables
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    # Create test shop
    shop = Shop(
        id=1,
        name="Test Shop",
        bot_token="test_token_123",
        owner_tg_id=123456789,
        config={
            "type": "fashion",
            "currency": "UZS",
            "min_order": 50000,
            "colors": {"primary": "#000000"},
            "features": {
                "variants": True,
                "modifiers": False,
                "gift_message": False
            }
        }
    )
    session.add(shop)

    # Create test category
    category = Category(
        id=1,
        shop_id=1,
        name="Test Category",
        description="Category for testing"
    )
    session.add(category)

    # Create test products
    for i in range(1, 6):
        product = Product(
            id=i,
            shop_id=1,
            category_id=1,
            name=f"Test Product {i}",
            description=f"Description for product {i}",
            price=100000 * i,
            stock=10,
            sold=0,
            image_url=f"/img/test_{i}.png",
            variants=[],
            is_active=True
        )
        session.add(product)

    session.commit()

    yield session

    # Cleanup
    session.close()
    Base.metadata.drop_all(bind=engine)
