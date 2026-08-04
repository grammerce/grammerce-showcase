"""
Root conftest — sets env vars BEFORE any test imports.
This file is loaded by pytest before tests/conftest.py.
"""
import os

# Set required environment variables for test environment
os.environ.setdefault("BOT_TOKEN", "0000000000:AAHfakeTokenForTestingOnly123456789")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_async.db")
os.environ.setdefault("WEB_APP_URL", "http://localhost:8000/")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")
