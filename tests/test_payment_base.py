"""
Этап 1 — Тесты абстрактного слоя платежей и Mock-провайдера.

Покрывает:
- MockGateway: create_payment → получаем URL
- MockGateway: simulate_pay → статус = paid
- MockGateway: simulate_decline → статус = failed
- MockGateway: cancel_payment → статус = cancelled
- MockGateway: get_payment_status — корректный статус
- Factory: fallback на MockGateway при отсутствии ключей
- Factory: правильный выбор gateway при наличии ключей (mock-env)
- PaymentResult: dataclass корректно создаётся
- utils: verify_click_sign — валидная и невалидная подписи
- utils: uzs_to_tiyin / tiyin_to_uzs
"""
import asyncio
import hashlib
import os

import pytest

from payments.base import PaymentGateway, PaymentResult
from payments.factory import get_gateway
from payments.mock_gateway import _MOCK_STORE, MockGateway
from payments.utils import tiyin_to_uzs, uzs_to_tiyin, verify_click_sign

# Фикстура: очищать store перед каждым тестом

@pytest.fixture(autouse=True)
def clear_mock_store():
    MockGateway.clear_store()
    yield
    MockGateway.clear_store()


# PaymentResult

class TestPaymentResult:
    def test_success_result(self):
        r = PaymentResult(success=True, payment_url="http://example.com/pay", payment_id="abc123")
        assert r.success is True
        assert r.payment_url == "http://example.com/pay"
        assert r.payment_id == "abc123"
        assert r.error is None

    def test_error_result(self):
        r = PaymentResult(success=False, payment_url=None, payment_id=None, error="Connection failed")
        assert r.success is False
        assert r.error == "Connection failed"


# MockGateway — create_payment

class TestMockGatewayCreate:
    def test_create_payment_returns_url(self):
        gw = MockGateway(provider_name="click", base_url="http://localhost:8000")
        result = asyncio.get_event_loop().run_until_complete(
            gw.create_payment(order_id=42, amount=80000, description="Заказ #42",
                              return_url="http://localhost:8000/status/42")
        )
        assert result.success is True
        assert result.payment_url is not None
        assert "/payments/mock/checkout/" in result.payment_url
        assert result.payment_id is not None
        assert result.error is None

    def test_create_payment_stores_entry(self):
        gw = MockGateway(provider_name="payme")
        result = asyncio.get_event_loop().run_until_complete(
            gw.create_payment(order_id=7, amount=15000, description="Test",
                              return_url="http://localhost/ret")
        )
        entry = MockGateway.get_entry(result.payment_id)
        assert entry is not None
        assert entry["order_id"] == 7
        assert entry["amount"] == 15000
        assert entry["status"] == "pending"
        assert entry["provider"] == "payme"

    def test_create_payment_initial_status_pending(self):
        gw = MockGateway()
        result = asyncio.get_event_loop().run_until_complete(
            gw.create_payment(1, 5000, "d", "http://r")
        )
        status = asyncio.get_event_loop().run_until_complete(
            gw.get_payment_status(result.payment_id)
        )
        assert status == "pending"

    def test_payment_ids_are_unique(self):
        gw = MockGateway()
        ids = set()
        for i in range(10):
            r = asyncio.get_event_loop().run_until_complete(
                gw.create_payment(i, 1000, "d", "http://r")
            )
            ids.add(r.payment_id)
        assert len(ids) == 10


# MockGateway — simulate_pay

class TestMockGatewayPay:
    def test_simulate_pay_changes_status(self):
        gw = MockGateway()
        r = asyncio.get_event_loop().run_until_complete(
            gw.create_payment(1, 1000, "d", "http://r")
        )
        ok = MockGateway.simulate_pay(r.payment_id)
        assert ok is True
        status = asyncio.get_event_loop().run_until_complete(
            gw.get_payment_status(r.payment_id)
        )
        assert status == "paid"

    def test_simulate_pay_unknown_id(self):
        ok = MockGateway.simulate_pay("nonexistent_id")
        assert ok is False

    def test_simulate_pay_already_paid_is_idempotent(self):
        gw = MockGateway()
        r = asyncio.get_event_loop().run_until_complete(
            gw.create_payment(1, 1000, "d", "http://r")
        )
        MockGateway.simulate_pay(r.payment_id)
        # Повторная оплата — уже не pending, должна вернуть False
        ok2 = MockGateway.simulate_pay(r.payment_id)
        assert ok2 is False


# MockGateway — simulate_decline

class TestMockGatewayDecline:
    def test_simulate_decline_changes_status(self):
        gw = MockGateway()
        r = asyncio.get_event_loop().run_until_complete(
            gw.create_payment(2, 2000, "d", "http://r")
        )
        ok = MockGateway.simulate_decline(r.payment_id)
        assert ok is True
        status = asyncio.get_event_loop().run_until_complete(
            gw.get_payment_status(r.payment_id)
        )
        assert status == "failed"

    def test_simulate_decline_unknown_id(self):
        ok = MockGateway.simulate_decline("no_such_id")
        assert ok is False


# MockGateway — cancel_payment

class TestMockGatewayCancel:
    def test_cancel_changes_status(self):
        gw = MockGateway()
        r = asyncio.get_event_loop().run_until_complete(
            gw.create_payment(3, 3000, "d", "http://r")
        )
        ok = asyncio.get_event_loop().run_until_complete(gw.cancel_payment(r.payment_id))
        assert ok is True
        status = asyncio.get_event_loop().run_until_complete(
            gw.get_payment_status(r.payment_id)
        )
        assert status == "cancelled"

    def test_cancel_unknown_id(self):
        gw = MockGateway()
        ok = asyncio.get_event_loop().run_until_complete(gw.cancel_payment("no_id"))
        assert ok is False


# MockGateway — verify_webhook

class TestMockGatewayWebhook:
    def test_verify_webhook_valid_secret(self):
        gw = MockGateway()
        ok = asyncio.get_event_loop().run_until_complete(
            gw.verify_webhook({"_mock_secret": "mock_ok"}, {})
        )
        assert ok is True

    def test_verify_webhook_invalid_secret(self):
        gw = MockGateway()
        ok = asyncio.get_event_loop().run_until_complete(
            gw.verify_webhook({"_mock_secret": "wrong"}, {})
        )
        assert ok is False


# Factory

class TestFactory:
    def test_factory_returns_mock_when_no_keys(self):
        gw = get_gateway("click", shop_config={})
        assert isinstance(gw, MockGateway)

    def test_factory_returns_mock_for_payme_no_keys(self):
        gw = get_gateway("payme", shop_config={})
        assert isinstance(gw, MockGateway)

    def test_factory_returns_mock_for_uzum_no_keys(self):
        gw = get_gateway("uzum", shop_config={})
        assert isinstance(gw, MockGateway)

    def test_factory_mock_provider_name_preserved(self):
        gw = get_gateway("payme", shop_config={})
        assert isinstance(gw, MockGateway)
        assert gw.provider_name == "payme"

    def test_factory_returns_click_gateway_when_keys_set(self, monkeypatch):
        monkeypatch.setenv("CLICK_SERVICE_ID", "123")
        monkeypatch.setenv("CLICK_MERCHANT_ID", "456")
        monkeypatch.setenv("CLICK_MERCHANT_USER_ID", "789")
        monkeypatch.setenv("CLICK_SECRET_KEY", "secret")
        from payments.click_gateway import ClickGateway
        gw = get_gateway("click")
        assert isinstance(gw, ClickGateway)

    def test_factory_returns_payme_gateway_when_keys_set(self, monkeypatch):
        monkeypatch.setenv("PAYME_ID", "payme123")
        monkeypatch.setenv("PAYME_KEY", "paymekey")
        from payments.payme_gateway import PaymeGateway
        gw = get_gateway("payme")
        assert isinstance(gw, PaymeGateway)

    def test_factory_returns_uzum_gateway_when_keys_set(self, monkeypatch):
        monkeypatch.setenv("UZUM_MERCHANT_ID", "uzum123")
        monkeypatch.setenv("UZUM_API_KEY", "uzumkey")
        from payments.uzum_gateway import UzumGateway
        gw = get_gateway("uzum")
        assert isinstance(gw, UzumGateway)

    def test_factory_shop_config_overrides_env(self, monkeypatch):
        # Нет env ключей → должен быть Mock
        monkeypatch.delenv("CLICK_SERVICE_ID", raising=False)
        gw = get_gateway("click", shop_config={})
        assert isinstance(gw, MockGateway)

    def test_factory_unknown_provider_returns_mock(self):
        gw = get_gateway("unknown_provider", shop_config={})
        assert isinstance(gw, MockGateway)

    # ─── enabled=False должен выдавать Mock даже с заполненными ключами ───

    def test_factory_click_disabled_by_owner_returns_mock(self, monkeypatch):
        """enabled=False в кабинете → Mock, даже если env-ключи заданы."""
        monkeypatch.setenv("CLICK_SERVICE_ID", "123")
        monkeypatch.setenv("CLICK_MERCHANT_ID", "456")
        monkeypatch.setenv("CLICK_MERCHANT_USER_ID", "789")
        monkeypatch.setenv("CLICK_SECRET_KEY", "secret")
        shop_config = {
            "cart": {
                "payments": {
                    "click": {
                        "enabled": False,
                        "service_id": "abc",
                        "merchant_id": "def",
                        "merchant_user_id": "ghi",
                        "secret_key": "xyz",
                    }
                }
            }
        }
        gw = get_gateway("click", shop_config=shop_config)
        assert isinstance(gw, MockGateway)

    def test_factory_click_enabled_true_with_keys_returns_real(self):
        from payments.click_gateway import ClickGateway
        shop_config = {
            "cart": {
                "payments": {
                    "click": {
                        "enabled": True,
                        "service_id": "abc",
                        "merchant_id": "def",
                        "merchant_user_id": "ghi",
                        "secret_key": "xyz",
                    }
                }
            }
        }
        gw = get_gateway("click", shop_config=shop_config)
        assert isinstance(gw, ClickGateway)

    def test_factory_click_no_enabled_field_fallbacks_to_keys(self, monkeypatch):
        """Если поле enabled вообще отсутствует — решение по ключам (env-сценарий)."""
        monkeypatch.setenv("CLICK_SERVICE_ID", "123")
        monkeypatch.setenv("CLICK_MERCHANT_ID", "456")
        monkeypatch.setenv("CLICK_MERCHANT_USER_ID", "789")
        monkeypatch.setenv("CLICK_SECRET_KEY", "secret")
        from payments.click_gateway import ClickGateway
        gw = get_gateway("click", shop_config={})
        assert isinstance(gw, ClickGateway)

    def test_factory_payme_disabled_by_owner_returns_mock(self, monkeypatch):
        monkeypatch.setenv("PAYME_ID", "p123")
        monkeypatch.setenv("PAYME_KEY", "pkey")
        shop_config = {
            "cart": {
                "payments": {
                    "payme": {"enabled": False, "merchant_id": "abc", "key": "xyz"}
                }
            }
        }
        gw = get_gateway("payme", shop_config=shop_config)
        assert isinstance(gw, MockGateway)

    def test_factory_uzum_disabled_by_owner_returns_mock(self, monkeypatch):
        monkeypatch.setenv("UZUM_MERCHANT_ID", "u123")
        monkeypatch.setenv("UZUM_API_KEY", "ukey")
        shop_config = {
            "cart": {
                "payments": {
                    "uzum": {"enabled": False, "merchant_id": "abc", "api_key": "xyz"}
                }
            }
        }
        gw = get_gateway("uzum", shop_config=shop_config)
        assert isinstance(gw, MockGateway)


# utils — verify_click_sign

class TestClickSign:
    def _make_sign(self, data: dict, secret: str, action: str) -> str:
        parts = [
            str(data["click_trans_id"]),
            str(data["service_id"]),
            secret,
            str(data["merchant_trans_id"]),
        ]
        if action == "1":
            parts.append(str(data.get("merchant_prepare_id", "")))
        parts += [str(data["amount"]), action, str(data["sign_time"])]
        return hashlib.md5("".join(parts).encode()).hexdigest()

    def test_valid_sign_prepare(self):
        data = {
            "click_trans_id": 111,
            "service_id": 99,
            "merchant_trans_id": "42",
            "amount": 80000,
            "action": "0",
            "sign_time": "2026-02-22 14:00:00",
        }
        data["sign_string"] = self._make_sign(data, "mysecret", "0")
        assert verify_click_sign(data, "mysecret") is True

    def test_valid_sign_complete(self):
        data = {
            "click_trans_id": 111,
            "service_id": 99,
            "merchant_trans_id": "42",
            "merchant_prepare_id": 1,
            "amount": 80000,
            "action": "1",
            "sign_time": "2026-02-22 14:00:00",
        }
        data["sign_string"] = self._make_sign(data, "mysecret", "1")
        assert verify_click_sign(data, "mysecret") is True

    def test_invalid_sign(self):
        data = {
            "click_trans_id": 111,
            "service_id": 99,
            "merchant_trans_id": "42",
            "amount": 80000,
            "action": "0",
            "sign_time": "2026-02-22",
            "sign_string": "invalid_hash",
        }
        assert verify_click_sign(data, "mysecret") is False

    def test_tampered_amount(self):
        data = {
            "click_trans_id": 111,
            "service_id": 99,
            "merchant_trans_id": "42",
            "amount": 80000,
            "action": "0",
            "sign_time": "2026-02-22",
        }
        data["sign_string"] = self._make_sign(data, "mysecret", "0")
        data["amount"] = 1  # Подмена суммы
        assert verify_click_sign(data, "mysecret") is False


# utils — uzs_to_tiyin / tiyin_to_uzs

class TestCurrencyConversion:
    def test_uzs_to_tiyin(self):
        assert uzs_to_tiyin(80000) == 8_000_000
        assert uzs_to_tiyin(1) == 100
        assert uzs_to_tiyin(0) == 0

    def test_tiyin_to_uzs(self):
        assert tiyin_to_uzs(8_000_000) == 80000
        assert tiyin_to_uzs(100) == 1
        assert tiyin_to_uzs(0) == 0

    def test_round_trip(self):
        original = 150000
        assert tiyin_to_uzs(uzs_to_tiyin(original)) == original
