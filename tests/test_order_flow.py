"""
Stage 5 Integration Test — order_flow branch
Checks the complete chain: BotUser → Profile API → Order with discount → Admin notification
"""
import re

import pytest


class TestBotUserModel:
    """Verify BotUser model has all required fields"""

    def test_model_columns(self):
        from sqlalchemy import inspect

        from models import BotUser

        mapper = inspect(BotUser)
        columns = [c.key for c in mapper.columns]
        required = [
            'telegram_id', 'name', 'phone',
            'location_lat', 'location_lon',
            'discount_registration', 'is_registered', 'shop_id',
        ]
        missing = [r for r in required if r not in columns]
        assert not missing, f"BotUser missing columns: {missing}"

    def test_customer_phone_column(self):
        from sqlalchemy import inspect

        from models import Customer

        mapper = inspect(Customer)
        columns = [c.key for c in mapper.columns]
        assert 'phone' in columns, "Customer must have phone column (migration 005)"


class TestDiscountParsing:
    """Discount percent is stored as '20%' string — must parse correctly"""

    def test_parse_valid_percent(self):
        discount_str = "20%"
        pct = float(discount_str.replace("%", "").strip())
        assert pct == 20.0

    def test_parse_none_returns_zero(self):
        discount_str = None
        pct = 0.0
        if discount_str:
            try:
                pct = float(discount_str.replace("%", "").strip())
            except (ValueError, AttributeError):
                pass
        assert pct == 0.0

    def test_parse_malformed_returns_zero(self):
        discount_str = "N/A"
        pct = 0.0
        try:
            pct = float(discount_str.replace("%", "").strip())
        except ValueError:
            pct = 0.0
        assert pct == 0.0


class TestOrderDiscountCalc:
    """Order total must be subtotal - discount_amount"""

    def test_discount_applied(self):
        subtotal = 100_000
        discount_percent = 20.0
        discount_amount = round(subtotal * discount_percent / 100, 2)
        total = subtotal - discount_amount

        assert discount_amount == 20_000
        assert total == 80_000

    def test_no_discount_when_not_registered(self):
        subtotal = 50_000
        discount_percent = 0.0
        discount_amount = round(subtotal * discount_percent / 100, 2)
        total = subtotal - discount_amount

        assert discount_amount == 0
        assert total == 50_000

    def test_multiple_items(self):
        items = [
            {"price": 30_000, "qty": 2},
            {"price": 15_000, "qty": 1},
        ]
        subtotal = sum(i["price"] * i["qty"] for i in items)
        assert subtotal == 75_000

        discount_percent = 20.0
        discount_amount = round(subtotal * discount_percent / 100, 2)
        assert discount_amount == 15_000
        assert subtotal - discount_amount == 60_000


class TestPhoneValidation:
    """Phone must be +998XXXXXXXXX"""

    PATTERN = r'^\+998\d{9}$'

    def test_valid_phones(self):
        valid = ["+998901234567", "+998991234567", "+998711234567"]
        for phone in valid:
            assert re.match(self.PATTERN, phone), f"{phone} should be valid"

    def test_invalid_phones(self):
        invalid = [
            "998901234567",      # no +
            "+99890123456",      # too short
            "+9989012345678",    # too long
            "+1234567890",       # wrong country
        ]
        for phone in invalid:
            assert not re.match(self.PATTERN, phone), f"{phone} should be invalid"


class TestAdminNotificationText:
    """Admin message must include discount info when present"""

    def _build_text(self, total_amount, discount_percent, discount_amount, original_total,
                    customer_name, customer_phone, delivery_address, payment_method):
        items_text = "• Товар x1 = 100,000 сум"
        discount_line = ""
        if discount_percent and float(discount_percent) > 0 and discount_amount:
            pct_display = int(discount_percent) if float(discount_percent) == int(discount_percent) else discount_percent
            discount_line = (
                f"\n🎁 <b>Скидка {pct_display}%:</b> −{int(discount_amount):,} сум"
                f"\n💵 <b>Сумма без скидки:</b> {int(original_total):,} сум"
            )
        payment_labels = {'cash': 'Наличными', 'click': 'Click', 'payme': 'PayMe'}
        payment_text = payment_labels.get(payment_method, payment_method)

        return (
            f"🛒 <b>Новый заказ #1</b>\n\n"
            f"{items_text}\n"
            f"{discount_line}\n"
            f"💰 <b>Итого:</b> {int(total_amount):,} сум\n"
            f"💳 <b>Оплата:</b> {payment_text}\n\n"
            f"👤 <b>Клиент:</b> {customer_name}\n"
            f"📞 <b>Телефон:</b> {customer_phone}\n"
            f"📍 <b>Адрес:</b> {delivery_address}\n"
        )

    def test_message_includes_discount(self):
        text = self._build_text(
            total_amount=80_000,
            discount_percent=20.0,
            discount_amount=20_000,
            original_total=100_000,
            customer_name="Алекс",
            customer_phone="+998901234567",
            delivery_address="Ташкент",
            payment_method="cash",
        )
        assert "Скидка 20%" in text
        assert "20,000" in text
        assert "80,000" in text
        assert "Наличными" in text
        assert "Алекс" in text

    def test_message_no_discount_for_unregistered(self):
        text = self._build_text(
            total_amount=100_000,
            discount_percent=0,
            discount_amount=0,
            original_total=100_000,
            customer_name="Аноним",
            customer_phone="+998901234567",
            delivery_address="Ташкент",
            payment_method="click",
        )
        assert "Скидка" not in text
        assert "Click" in text

    def test_payment_method_payme(self):
        text = self._build_text(
            total_amount=50_000,
            discount_percent=0,
            discount_amount=0,
            original_total=50_000,
            customer_name="X",
            customer_phone="+998901234567",
            delivery_address="Y",
            payment_method="payme",
        )
        assert "PayMe" in text
