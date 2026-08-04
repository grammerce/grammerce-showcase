"""Тесты DidoxService — интеграция с оператором ЭСФ Didox.

Предыдущая версия этого файла тестировала интерфейс, которого в сервисе больше
нет: синхронные create_invoice_factura/check_status и конструктор, принимавший
сессию БД. Сервис с тех пор переписан на async с httpx и другим набором методов
(draft → sign → send), поэтому все 13 тестов падали на этапе setup.

Здесь тесты приведены к фактическому интерфейсу services/didox_service.py.
Сеть не задействована: httpx.AsyncClient подменяется целиком, БД не нужна.
"""
from __future__ import annotations

from datetime import UTC, datetime, timezone
from types import FunctionType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.didox_service import DidoxError, DidoxService

# ─── Инфраструктура моков ────────────────────────────────────────────────────

def _response(status_code: int, payload: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload if payload is not None else {}
    resp.text = text
    return resp


def _patch_client(post=None, get=None):
    """Подменить httpx.AsyncClient, используемый внутри сервиса.

    Сервис создаёт клиент через `async with httpx.AsyncClient(...)`, поэтому
    мок должен поддерживать протокол асинхронного контекстного менеджера.
    """
    # Различать «готовый ответ» и «функция, выбирающая ответ по URL» через
    # callable() нельзя: MagicMock сам по себе callable, и ответ-заглушка ушла бы
    # в side_effect. Поэтому проверяем именно обычную функцию.
    def _as_async_mock(value):
        if isinstance(value, FunctionType):
            return AsyncMock(side_effect=value)
        return AsyncMock(return_value=value)

    client = MagicMock()
    client.post = _as_async_mock(post)
    client.get = _as_async_mock(get)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)

    return patch("services.didox_service.httpx.AsyncClient", return_value=ctx), client


@pytest.fixture
def svc() -> DidoxService:
    return DidoxService()


@pytest.fixture
def authed_svc() -> DidoxService:
    """Сервис с уже полученным токеном — чтобы не мокать авторизацию каждый раз."""
    s = DidoxService()
    s._token = "test-token"
    return s


# ─── Авторизация ─────────────────────────────────────────────────────────────

class TestAuthenticate:

    @pytest.mark.asyncio
    async def test_returns_and_stores_token(self, svc):
        patcher, client = _patch_client(post=_response(200, {"token": "tok-123"}))
        with patcher:
            token = await svc.authenticate(tax_id="123456789", password="secret")

        assert token == "tok-123"
        assert svc._token == "tok-123"
        # Пароль уходит в теле запроса, а не в URL.
        _, kwargs = client.post.call_args
        assert kwargs["json"] == {"password": "secret"}

    @pytest.mark.asyncio
    async def test_raises_on_non_200(self, svc):
        patcher, _ = _patch_client(post=_response(401, text="unauthorized"))
        with patcher, pytest.raises(DidoxError, match="auth failed"):
            await svc.authenticate(tax_id="123456789", password="wrong")

    @pytest.mark.asyncio
    async def test_raises_when_token_missing_in_response(self, svc):
        patcher, _ = _patch_client(post=_response(200, {"unexpected": "shape"}))
        with patcher, pytest.raises(DidoxError, match="no token"):
            await svc.authenticate(tax_id="123456789", password="secret")


class TestHeaders:

    def test_no_authorization_before_authentication(self, svc):
        assert "Authorization" not in svc._headers()

    def test_bearer_added_after_authentication(self, authed_svc):
        assert authed_svc._headers()["Authorization"] == "Bearer test-token"


# ─── Черновик документа ──────────────────────────────────────────────────────

class TestCreateInvoiceDraft:

    @pytest.mark.asyncio
    async def test_returns_document_id(self, authed_svc):
        patcher, client = _patch_client(post=_response(201, {"documentId": "DOC-42"}))
        with patcher:
            doc_id = await authed_svc.create_invoice_draft({"tin": "1"}, {"tin": "2"}, [])

        assert doc_id == "DOC-42"
        _, kwargs = client.post.call_args
        assert kwargs["json"]["documentType"] == "INVOICE"

    @pytest.mark.asyncio
    async def test_accepts_id_instead_of_document_id(self, authed_svc):
        """Didox отдаёт то documentId, то id — сервис поддерживает оба ключа."""
        patcher, _ = _patch_client(post=_response(200, {"id": 77}))
        with patcher:
            assert await authed_svc.create_invoice_draft({}, {}, []) == "77"

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, authed_svc):
        patcher, _ = _patch_client(post=_response(500, text="boom"))
        with patcher, pytest.raises(DidoxError, match="create draft failed"):
            await authed_svc.create_invoice_draft({}, {}, [])

    @pytest.mark.asyncio
    async def test_raises_when_no_document_id(self, authed_svc):
        patcher, _ = _patch_client(post=_response(200, {"status": "ok"}))
        with patcher, pytest.raises(DidoxError, match="no documentId"):
            await authed_svc.create_invoice_draft({}, {}, [])

    @pytest.mark.asyncio
    async def test_authenticates_lazily_when_token_missing(self, svc):
        """Без токена сервис сам сходит за авторизацией перед созданием черновика."""
        def post_side_effect(url, *a, **kw):
            if "/auth/" in url:
                return _response(200, {"token": "lazy-token"})
            return _response(201, {"documentId": "DOC-1"})

        patcher, _ = _patch_client(post=post_side_effect)
        with patcher:
            doc_id = await svc.create_invoice_draft({}, {}, [])

        assert doc_id == "DOC-1"
        assert svc._token == "lazy-token"


# ─── Подписание и отправка ───────────────────────────────────────────────────

class TestSignAndSend:

    @pytest.mark.asyncio
    async def test_sign_returns_status(self, authed_svc):
        patcher, _ = _patch_client(post=_response(200, {"status": "SIGNED"}))
        with patcher:
            assert await authed_svc.sign_document("DOC-42") == "SIGNED"

    @pytest.mark.asyncio
    async def test_sign_defaults_status_when_absent(self, authed_svc):
        patcher, _ = _patch_client(post=_response(200, {}))
        with patcher:
            assert await authed_svc.sign_document("DOC-42") == "SIGNED"

    @pytest.mark.asyncio
    async def test_sign_raises_on_error(self, authed_svc):
        patcher, _ = _patch_client(post=_response(400, text="cannot sign"))
        with patcher, pytest.raises(DidoxError, match="sign failed"):
            await authed_svc.sign_document("DOC-42")

    @pytest.mark.asyncio
    async def test_send_returns_status(self, authed_svc):
        patcher, _ = _patch_client(post=_response(200, {"status": "SENT"}))
        with patcher:
            assert await authed_svc.send_document("DOC-42") == "SENT"

    @pytest.mark.asyncio
    async def test_send_raises_on_error(self, authed_svc):
        patcher, _ = _patch_client(post=_response(503, text="unavailable"))
        with patcher, pytest.raises(DidoxError, match="send failed"):
            await authed_svc.send_document("DOC-42")


# ─── Получение документа ─────────────────────────────────────────────────────

class TestGetDocument:

    @pytest.mark.asyncio
    async def test_returns_payload(self, authed_svc):
        patcher, _ = _patch_client(get=_response(200, {"documentId": "DOC-42", "status": "SENT"}))
        with patcher:
            doc = await authed_svc.get_document("DOC-42")

        assert doc["status"] == "SENT"

    @pytest.mark.asyncio
    async def test_404_reported_as_not_found(self, authed_svc):
        patcher, _ = _patch_client(get=_response(404))
        with patcher, pytest.raises(DidoxError, match="not found"):
            await authed_svc.get_document("NOPE")


# ─── Полный цикл ─────────────────────────────────────────────────────────────

class TestCreateAndSendInvoice:

    @pytest.mark.asyncio
    async def test_runs_draft_sign_send_in_order(self, authed_svc):
        calls = []

        def post_side_effect(url, *a, **kw):
            calls.append(url)
            if url.endswith("/draft"):
                return _response(201, {"documentId": "DOC-9"})
            return _response(200, {"status": "OK"})

        patcher, _ = _patch_client(post=post_side_effect)
        with patcher:
            doc_id = await authed_svc.create_and_send_invoice({}, {}, [])

        assert doc_id == "DOC-9"
        assert [c.rsplit("/", 1)[-1] for c in calls] == ["draft", "sign", "send"]

    @pytest.mark.asyncio
    async def test_stops_when_draft_fails(self, authed_svc):
        patcher, client = _patch_client(post=_response(500, text="boom"))
        with patcher, pytest.raises(DidoxError):
            await authed_svc.create_and_send_invoice({}, {}, [])

        # Подписания и отправки быть не должно — только неудачный draft.
        assert client.post.await_count == 1


# ─── Хелперы формирования данных ─────────────────────────────────────────────

class TestBuilders:

    def test_seller_data_filled_from_config(self):
        data = DidoxService.build_seller_data()
        assert data["tin"] == data["taxId"]
        assert data["name"]

    def test_buyer_data_from_shop(self):
        shop = SimpleNamespace(
            company_inn="305123456",
            company_name='ООО "Клиент"',
            company_account="20208000000000000002",
            company_bank_name="Банк",
            company_bank_mfo="00123",
            company_director="Петров П.П.",
            company_address="г. Ташкент",
        )
        data = DidoxService.build_buyer_data(shop)
        assert data["tin"] == "305123456"
        assert data["taxId"] == "305123456"
        assert data["name"] == 'ООО "Клиент"'

    def test_buyer_data_tolerates_missing_company_fields(self):
        """Магазин мог не заполнить реквизиты — билдер не должен падать."""
        data = DidoxService.build_buyer_data(SimpleNamespace())
        assert data["tin"] == ""
        assert data["name"] == ""

    def test_invoice_items_carry_amounts_and_catalog_code(self):
        invoice = SimpleNamespace(
            description="Подписка Business, август",
            amount=300_000,
            vat_amount=36_000,
            total_amount=336_000,
        )
        items = DidoxService.build_invoice_items(invoice)

        assert len(items) == 1
        item = items[0]
        assert item["name"] == "Подписка Business, август"
        assert item["price"] == 300_000
        assert item["nds"] == 36_000
        assert item["total"] == 336_000
        # ИКПУ для ИТ-услуг обязателен для ЭСФ.
        assert item["catalogCode"]

    def test_invoice_items_default_description(self):
        invoice = SimpleNamespace(description=None, amount=1, vat_amount=0, total_amount=1)
        assert DidoxService.build_invoice_items(invoice)[0]["name"] == "Подписка SaaS"

    def test_contract_info_uses_acceptance_year(self):
        accepted = datetime(2026, 3, 15, tzinfo=UTC)
        info = DidoxService.build_contract_info(user_id=42, accepted_at=accepted)

        assert info["contractNumber"] == "GRM-42-2026"
        assert info["contractDate"] == "2026-03-15"
