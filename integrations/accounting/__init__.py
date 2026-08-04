import logging
import os

from integrations.accounting.base import AccountingProvider

log = logging.getLogger(__name__)

# Determine the mode from environment variables
ACCOUNTING_MODE = os.getenv("ACCOUNTING_MODE", "mock").lower()

def get_accounting_provider() -> AccountingProvider:
    """
    Фабрика для получения провайдера интеграции с бухгалтерией (AZMA).
    Возвращает Mock или Live версию в зависимости от ACCOUNTING_MODE в .env
    """
    if ACCOUNTING_MODE == "live":
        from integrations.accounting.azma_live import AzmaLiveProvider
        log.info("Используется боевой провайдер AZMA (Live Mode)")
        return AzmaLiveProvider()
    else:
        from integrations.accounting.azma_mock import MockAccountingProvider
        log.info("Используется тестовый провайдер AZMA (Mock Mode)")
        return MockAccountingProvider()

# Глобальный инстанс провайдера для использования в приложении
accounting_provider = get_accounting_provider()
