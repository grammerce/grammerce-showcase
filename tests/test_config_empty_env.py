"""Пустая переменная окружения не должна ронять приложение на импорте.

История бага: .env и .env.example штатно содержат `PLATFORM_MANAGER_CHAT_ID=`
без значения (менеджер не назначен). Поле объявлено как Optional[int], но pydantic
получал пустую СТРОКУ, а не None, и падал с ValidationError прямо на импорте
config.oauth. Импорт этого модуля лежит в цепочке main -> bot.handlers ->
registration -> config.oauth, поэтому не поднималось вообще ничего: ни сервер,
ни сбор тестов.

Тот же класс ошибки был в config/billing.py: int(os.getenv("TRIAL_DAYS", "7"))
возвращает дефолт, только если переменная НЕ ЗАДАНА. Заданная пустая даёт "",
и int("") — ValueError на импорте.
"""
import importlib

import pytest

from config.settings import _float_env, _int_env, _optional_int_env

# ─── Хелперы чтения чисел из окружения ───────────────────────────────────────

@pytest.mark.parametrize("raw", ["", "   ", "не число", "12.5abc"])
def test_int_env_falls_back_on_garbage(monkeypatch, raw):
    monkeypatch.setenv("GRAMMERCE_TEST_INT", raw)
    assert _int_env("GRAMMERCE_TEST_INT", 7) == 7


def test_int_env_reads_real_value(monkeypatch):
    monkeypatch.setenv("GRAMMERCE_TEST_INT", "42")
    assert _int_env("GRAMMERCE_TEST_INT", 7) == 42


def test_int_env_unset_uses_default(monkeypatch):
    monkeypatch.delenv("GRAMMERCE_TEST_INT", raising=False)
    assert _int_env("GRAMMERCE_TEST_INT", 7) == 7


@pytest.mark.parametrize("raw", ["", "  ", "abc"])
def test_float_env_falls_back_on_garbage(monkeypatch, raw):
    monkeypatch.setenv("GRAMMERCE_TEST_FLOAT", raw)
    assert _float_env("GRAMMERCE_TEST_FLOAT", 0.12) == 0.12


def test_float_env_reads_real_value(monkeypatch):
    monkeypatch.setenv("GRAMMERCE_TEST_FLOAT", "0.12")
    assert _float_env("GRAMMERCE_TEST_FLOAT", 0.0) == 0.12


def test_optional_int_env_empty_means_none(monkeypatch):
    monkeypatch.setenv("GRAMMERCE_TEST_OPT", "")
    assert _optional_int_env("GRAMMERCE_TEST_OPT") is None


# ─── OAuthSettings: собственно упавшее место ─────────────────────────────────

@pytest.mark.parametrize("raw", ["", "   "])
def test_oauth_settings_survives_empty_manager_chat_id(monkeypatch, raw):
    monkeypatch.setenv("PLATFORM_MANAGER_CHAT_ID", raw)
    from config.oauth import OAuthSettings

    assert OAuthSettings().platform_manager_chat_id is None


def test_oauth_settings_reads_real_manager_chat_id(monkeypatch):
    monkeypatch.setenv("PLATFORM_MANAGER_CHAT_ID", "123456789")
    from config.oauth import OAuthSettings

    assert OAuthSettings().platform_manager_chat_id == 123456789


def test_config_oauth_imports_with_empty_env(monkeypatch):
    """Импорт модуля целиком, а не только конструктор класса.

    Падал именно импорт: oauth_settings = OAuthSettings() на уровне модуля.
    """
    monkeypatch.setenv("PLATFORM_MANAGER_CHAT_ID", "")
    import config.oauth

    importlib.reload(config.oauth)
    assert config.oauth.oauth_settings.platform_manager_chat_id is None


# ─── billing: числа с пустыми переменными ────────────────────────────────────

def test_billing_imports_with_empty_numeric_env(monkeypatch):
    """Все числовые переменные биллинга пустые — модуль обязан импортироваться."""
    for name in (
        "VAT_RATE",
        "TRIAL_DAYS",
        "INVOICE_DUE_DAYS",
        "GRACE_SUSPEND_DAYS",
        "GRACE_CANCEL_DAYS",
        "ONBOARDING_STUCK_STEP_MINUTES",
        "TRIAL_BLOCK_AFTER_DAYS",
        "TRIAL_DELETE_AFTER_DAYS",
    ):
        monkeypatch.setenv(name, "")

    import config.billing

    importlib.reload(config.billing)

    assert config.billing.TRIAL_DAYS == 7
    assert config.billing.VAT_RATE == 0
    assert config.billing.GRACE_CANCEL_DAYS == 30

    # Вернуть модуль в состояние, ожидаемое остальными тестами.
    for name in (
        "VAT_RATE",
        "TRIAL_DAYS",
        "INVOICE_DUE_DAYS",
        "GRACE_SUSPEND_DAYS",
        "GRACE_CANCEL_DAYS",
        "ONBOARDING_STUCK_STEP_MINUTES",
        "TRIAL_BLOCK_AFTER_DAYS",
        "TRIAL_DELETE_AFTER_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)
    importlib.reload(config.billing)


def test_no_unguarded_int_getenv_in_config():
    """Регрессия: в config/ не должно появиться новых int(os.getenv(...)).

    Дефолт второго аргумента os.getenv срабатывает только для НЕЗАДАННОЙ
    переменной, поэтому такая конструкция всегда уязвима к пустому значению.
    """
    import io
    import re
    import tokenize
    from pathlib import Path

    config_dir = Path(__file__).resolve().parent.parent / "config"
    pattern = re.compile(r"(?<!_)\b(?:int|float)\(os\.getenv")

    offenders = []
    for path in config_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        # Комментарии и docstring вырезаем: они описывают антипаттерн, а не
        # содержат его. Иначе тест ловит собственное объяснение в шапке модуля.
        code_only = []
        readline = io.StringIO(source).readline
        for tok in tokenize.generate_tokens(readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            code_only.append((tok.start[0], tok.line))

        for lineno, line in code_only:
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not offenders, "Небезопасный разбор числа из env:\n" + "\n".join(sorted(set(offenders)))
