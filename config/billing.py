"""
Биллинг-конфигурация: реквизиты нашей компании-продавца.
Все значения берутся из .env; здесь — безопасные дефолты для разработки.

Числа читаются через _int_env/_float_env из config.settings, а не через
int(os.getenv(...)): заданная, но пустая переменная (`TRIAL_DAYS=`) вернула бы ""
и уронила импорт модуля вместе со всем приложением.
"""
import os

from config.settings import _float_env, _int_env

# ─── Реквизиты поставщика (наша компания) ────────────────────────────────────
SELLER_COMPANY_NAME = os.getenv("SELLER_COMPANY_NAME", 'ООО "Your SaaS Company"')
SELLER_INN          = os.getenv("SELLER_INN",          "123456789")
SELLER_ACCOUNT      = os.getenv("SELLER_ACCOUNT",      "20208000000000000001")
SELLER_BANK_NAME    = os.getenv("SELLER_BANK_NAME",    "АКБ Kapitalbank")
SELLER_BANK_MFO     = os.getenv("SELLER_BANK_MFO",     "01057")
SELLER_DIRECTOR     = os.getenv("SELLER_DIRECTOR",     "Иванов И.И.")
SELLER_ADDRESS      = os.getenv("SELLER_ADDRESS",      "г. Ташкент, ул. Примерная, 1")
SELLER_PHONE        = os.getenv("SELLER_PHONE",        "+998901234567")

def get_seller_details() -> dict:
    """Вернуть словарь реквизитов продавца (копируется в Invoice.seller_details)."""
    return {
        "company_name": SELLER_COMPANY_NAME,
        "inn":          SELLER_INN,
        "account":      SELLER_ACCOUNT,
        "bank_name":    SELLER_BANK_NAME,
        "bank_mfo":     SELLER_BANK_MFO,
        "director":     SELLER_DIRECTOR,
        "address":      SELLER_ADDRESS,
        "phone":        SELLER_PHONE,
    }

# ─── НДС ─────────────────────────────────────────────────────────────────────
# Мы не плательщики НДС → 0. При переходе на общую систему → 0.12.
VAT_RATE = _float_env("VAT_RATE", 0)

# ─── Сроки (дни) ──────────────────────────────────────────────────────────────
TRIAL_DAYS          = _int_env("TRIAL_DAYS", 7)
INVOICE_DUE_DAYS    = _int_env("INVOICE_DUE_DAYS", 7)   # срок оплаты
GRACE_SUSPEND_DAYS  = _int_env("GRACE_SUSPEND_DAYS", 14)  # →suspended
GRACE_CANCEL_DAYS   = _int_env("GRACE_CANCEL_DAYS", 30)  # →cancelled

# ─── Напоминания онбординга: тест-режим ──────────────────────────────────────
# В проде окна «день 2 / день 5» считаются в ДНЯХ (крон раз в сутки 09:00).
# Для проверки в текущей сессии включаем ONBOARDING_REMINDER_TEST_MODE=1 — тогда
# окна считаются в МИНУТАХ, а планировщик крутит interval-джобу каждые TICK минут.
# Прод-логика (дни) остаётся дефолтом — ничего не ломаем.
def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")

ONBOARDING_REMINDER_TEST_MODE       = _env_bool("ONBOARDING_REMINDER_TEST_MODE", "0")
ONBOARDING_REMINDER_DAY2_MINUTES    = _int_env("ONBOARDING_REMINDER_DAY2_MINUTES", 2)
ONBOARDING_REMINDER_DAY5_MINUTES    = _int_env("ONBOARDING_REMINDER_DAY5_MINUTES", 5)
ONBOARDING_REMINDER_TEST_TICK_MINUTES = _int_env("ONBOARDING_REMINDER_TEST_TICK_MINUTES", 1)
# «Застрял на шаге»: если владелец N минут не двигается по текущему шагу — напоминание
# именно про этот шаг (инструкция + поддержка). Одно на шаг; таймер считается от последнего
# действия (метки onboarding_events), правки БД для теста не нужны.
ONBOARDING_STUCK_STEP_MINUTES       = _int_env("ONBOARDING_STUCK_STEP_MINUTES", 5)

# ─── Трек D: напоминания об оплате триала + блокировка/удаление ───────────────
# Отсчёт от конца триала T0 = trial_started_at + TRIAL_DAYS.
# Прод: D1 −3д, D2 −2д, D3 −1д, D4 в конце; блок доступа через N дней после T0,
# удаление — через M дней после T0.
TRIAL_BLOCK_AFTER_DAYS   = _int_env("TRIAL_BLOCK_AFTER_DAYS", 3)   # доступ отключаем (is_active=False)
TRIAL_DELETE_AFTER_DAYS  = _int_env("TRIAL_DELETE_AFTER_DAYS", 14)  # удаляем магазин
# Реальное удаление магазина НЕОБРАТИМО — по умолчанию ВЫКЛ. Сообщения и блокировка
# работают всегда; фактическое удаление на T0+delete происходит только при флаге.
ONBOARDING_AUTODELETE_ENABLED = _env_bool("ONBOARDING_AUTODELETE_ENABLED", "0")
# Тест-режим (минуты): триал считается коротким, смещения — в минутах.
ONBOARDING_TEST_TRIAL_MINUTES = _int_env("ONBOARDING_TEST_TRIAL_MINUTES", 5)
TRIAL_BLOCK_AFTER_MINUTES     = _int_env("TRIAL_BLOCK_AFTER_MINUTES", 2)  # блок через N мин после конца
TRIAL_DELETE_AFTER_MINUTES    = _int_env("TRIAL_DELETE_AFTER_MINUTES", 4)  # удаление через M мин после конца

# ─── Didox ───────────────────────────────────────────────────────────────────
# DIDOX_MODE: mock | staging | production
DIDOX_MODE          = os.getenv("DIDOX_MODE", "mock")
DIDOX_PARTNER_TOKEN = os.getenv("DIDOX_PARTNER_TOKEN", "")
DIDOX_LOGIN         = os.getenv("DIDOX_LOGIN",    "")
DIDOX_PASSWORD      = os.getenv("DIDOX_PASSWORD", "")
DIDOX_TIN           = os.getenv("DIDOX_TIN",      SELLER_INN)

# Base URL вычисляется из MODE
_DIDOX_URLS = {
    "mock":       os.getenv("DIDOX_MOCK_URL", "http://localhost:8000/mock/didox"),
    "staging":    os.getenv("DIDOX_API_URL",  "https://stage.goodsign.biz"),
    "production": os.getenv("DIDOX_API_URL",  "https://api-partners.didox.uz"),
}
DIDOX_BASE_URL = _DIDOX_URLS.get(DIDOX_MODE, _DIDOX_URLS["mock"])

# ─── PDF хранилище ────────────────────────────────────────────────────────────
INVOICES_DIR   = os.getenv("INVOICES_DIR",   "media/invoices")
CONTRACTS_DIR  = os.getenv("CONTRACTS_DIR",  "media/contracts")
