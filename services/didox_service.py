"""
DidoxService — единая точка взаимодействия с Didox API.

Переключение mock/staging/production через env DIDOX_MODE.
Все методы async, используют httpx.AsyncClient.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from config.billing import (
    DIDOX_BASE_URL,
    DIDOX_MODE,
    DIDOX_PARTNER_TOKEN,
    DIDOX_PASSWORD,
    DIDOX_TIN,
    SELLER_ACCOUNT,
    SELLER_ADDRESS,
    SELLER_BANK_MFO,
    SELLER_BANK_NAME,
    SELLER_COMPANY_NAME,
    SELLER_DIRECTOR,
    SELLER_INN,
    SELLER_PHONE,
)

log = logging.getLogger(__name__)

# ИКПУ для ИТ-услуг
DIDOX_CATALOG_CODE = "10523001001000000"


class DidoxError(Exception):
    """Ошибки интеграции с Didox."""


class DidoxService:
    """
    Async-сервис для работы с Didox API.

    Методы:
        authenticate     — получить токен
        create_invoice_draft — создать черновик документа
        sign_document    — подписать документ
        send_document    — отправить документ
        get_document     — получить документ по ID
        create_and_send_invoice — композитный: draft → sign → send
    """

    def __init__(self) -> None:
        self._base_url = DIDOX_BASE_URL.rstrip("/")
        self._token: str | None = None
        log.info("DidoxService init: mode=%s, base_url=%s", DIDOX_MODE, self._base_url)

    # ── Авторизация ──────────────────────────────────────────────────────────

    async def authenticate(self, tax_id: str | None = None, password: str | None = None) -> str:
        """Получить токен авторизации от Didox API."""
        tax_id = tax_id or DIDOX_TIN
        password = password or DIDOX_PASSWORD

        url = f"{self._base_url}/v1/auth/{tax_id}/password/ru"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json={"password": password})

        if resp.status_code != 200:
            raise DidoxError(f"Didox auth failed: {resp.status_code} {resp.text[:200]}")

        data = resp.json()
        token = data.get("token")
        if not token:
            raise DidoxError(f"Didox: no token in response: {data}")

        self._token = token
        log.info("Didox authenticated: taxId=%s", tax_id)
        return token

    def _headers(self) -> dict:
        """Заголовки для запросов к Didox."""
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if DIDOX_PARTNER_TOKEN:
            headers["X-Partner-Token"] = DIDOX_PARTNER_TOKEN
        return headers

    # ── Черновик документа ────────────────────────────────────────────────────

    async def create_invoice_draft(
        self,
        seller_data: dict,
        buyer_data: dict,
        items: list[dict],
        contract_info: dict | None = None,
    ) -> str:
        """Создаёт черновик документа, возвращает documentId."""
        if not self._token:
            await self.authenticate()

        url = f"{self._base_url}/v1/documents/draft"
        body = {
            "documentType": "INVOICE",
            "sellerData": seller_data,
            "buyerData": buyer_data,
            "items": items,
            "contractInfo": contract_info or {},
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=body, headers=self._headers())

        if resp.status_code not in (200, 201):
            raise DidoxError(f"Didox create draft failed: {resp.status_code} {resp.text[:300]}")

        data = resp.json()
        doc_id = data.get("documentId") or data.get("id")
        if not doc_id:
            raise DidoxError(f"Didox: no documentId in response: {data}")

        log.info("Didox draft created: %s", doc_id)
        return str(doc_id)

    # ── Подписание ───────────────────────────────────────────────────────────

    async def sign_document(self, document_id: str) -> str:
        """Подписать документ, возвращает статус."""
        if not self._token:
            await self.authenticate()

        url = f"{self._base_url}/v1/documents/{document_id}/sign"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=self._headers())

        if resp.status_code not in (200, 201):
            raise DidoxError(f"Didox sign failed: {resp.status_code} {resp.text[:300]}")

        data = resp.json()
        log.info("Didox signed: %s → %s", document_id, data.get("status"))
        return data.get("status", "SIGNED")

    # ── Отправка ─────────────────────────────────────────────────────────────

    async def send_document(self, document_id: str) -> str:
        """Отправить документ, возвращает статус."""
        if not self._token:
            await self.authenticate()

        url = f"{self._base_url}/v1/documents/{document_id}/send"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=self._headers())

        if resp.status_code not in (200, 201):
            raise DidoxError(f"Didox send failed: {resp.status_code} {resp.text[:300]}")

        data = resp.json()
        log.info("Didox sent: %s → %s", document_id, data.get("status"))
        return data.get("status", "SENT")

    # ── Получение документа ──────────────────────────────────────────────────

    async def get_document(self, document_id: str) -> dict:
        """Получить документ по ID."""
        if not self._token:
            await self.authenticate()

        url = f"{self._base_url}/v1/documents/{document_id}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=self._headers())

        if resp.status_code == 404:
            raise DidoxError(f"Document {document_id} not found")
        if resp.status_code != 200:
            raise DidoxError(f"Didox get failed: {resp.status_code}")

        return resp.json()

    # ── Композитный метод: draft → sign → send ──────────────────────────────

    async def create_and_send_invoice(
        self,
        seller_data: dict,
        buyer_data: dict,
        items: list[dict],
        contract_info: dict | None = None,
    ) -> str:
        """
        Полный цикл: создать черновик → подписать → отправить.
        Возвращает documentId.
        """
        doc_id = await self.create_invoice_draft(seller_data, buyer_data, items, contract_info)
        await self.sign_document(doc_id)
        await self.send_document(doc_id)
        log.info("Didox full cycle complete: %s (SENT)", doc_id)
        return doc_id

    # ── Хелперы для формирования данных ──────────────────────────────────────

    @staticmethod
    def build_seller_data() -> dict:
        """Формирует данные продавца из env-конфигурации."""
        return {
            "tin": SELLER_INN,
            "taxId": SELLER_INN,
            "name": SELLER_COMPANY_NAME,
            "account": SELLER_ACCOUNT,
            "bankName": SELLER_BANK_NAME,
            "bankMfo": SELLER_BANK_MFO,
            "director": SELLER_DIRECTOR,
            "address": SELLER_ADDRESS,
            "phone": SELLER_PHONE,
        }

    @staticmethod
    def build_buyer_data(shop) -> dict:
        """Формирует данные покупателя из модели Shop."""
        return {
            "tin": getattr(shop, "company_inn", "") or "",
            "taxId": getattr(shop, "company_inn", "") or "",
            "name": getattr(shop, "company_name", "") or "",
            "account": getattr(shop, "company_account", "") or "",
            "bankName": getattr(shop, "company_bank_name", "") or "",
            "bankMfo": getattr(shop, "company_bank_mfo", "") or "",
            "director": getattr(shop, "company_director", "") or "",
            "address": getattr(shop, "company_address", "") or "",
        }

    @staticmethod
    def build_invoice_items(invoice) -> list[dict]:
        """Формирует список товаров/услуг для ЭСФ из Invoice."""
        return [
            {
                "ordNo": 1,
                "name": invoice.description or "Подписка SaaS",
                "catalogCode": DIDOX_CATALOG_CODE,
                "count": 1,
                "countmeasure": "услуга",
                "price": invoice.amount,
                "summa": invoice.amount,
                "ndsRate": 12,
                "nds": invoice.vat_amount,
                "total": invoice.total_amount,
            }
        ]

    @staticmethod
    def build_contract_info(user_id: int, accepted_at: datetime | None = None) -> dict:
        """Формирует информацию о договоре."""
        year = (accepted_at or datetime.now(UTC)).year
        return {
            "contractNumber": f"GRM-{user_id}-{year}",
            "contractDate": (accepted_at or datetime.now(UTC)).strftime("%Y-%m-%d"),
        }
