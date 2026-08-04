"""
Тесты формата скидки за регистрацию (тип percent|fixed + значение).
format_registration_discount превращает настройки кнопки в строку, которая
пишется в bot_user.discount_registration и парсится на кассе.
"""
from bot.services.settings import format_registration_discount


class TestFormatRegistrationDiscount:
    def test_percent(self):
        assert format_registration_discount(
            {"registration_discount_type": "percent", "registration_discount_value": 20}
        ) == "20%"

    def test_percent_non_integer(self):
        assert format_registration_discount(
            {"registration_discount_type": "percent", "registration_discount_value": 12.5}
        ) == "12.5%"

    def test_fixed_sum(self):
        assert format_registration_discount(
            {"registration_discount_type": "fixed", "registration_discount_value": 15000}
        ) == "15000 сум"

    def test_legacy_fraction_percent(self):
        # Старое значение доли 0.20 → 20%
        assert format_registration_discount(
            {"registration_discount_type": "percent", "registration_discount_value": 0.20}
        ) == "20%"

    def test_fallback_to_legacy_string(self):
        # Нет type/value → берём legacy-строку registration_discount
        assert format_registration_discount({"registration_discount": "30%"}) == "30%"

    def test_fallback_default(self):
        assert format_registration_discount({}) == "20%"

    def test_bad_value_falls_back(self):
        assert format_registration_discount(
            {"registration_discount_type": "percent",
             "registration_discount_value": "abc",
             "registration_discount": "25%"}
        ) == "25%"

    def test_disabled_returns_zero(self):
        # Тумблер выключен → скидка не начисляется (0%), даже если значение задано
        assert format_registration_discount(
            {"registration_discount_enabled": False,
             "registration_discount_type": "percent",
             "registration_discount_value": 20}
        ) == "0%"

    def test_disabled_ignores_fixed_value(self):
        assert format_registration_discount(
            {"registration_discount_enabled": False,
             "registration_discount_type": "fixed",
             "registration_discount_value": 15000}
        ) == "0%"

    def test_enabled_true_keeps_discount(self):
        assert format_registration_discount(
            {"registration_discount_enabled": True,
             "registration_discount_type": "percent",
             "registration_discount_value": 20}
        ) == "20%"

    def test_missing_enabled_key_is_legacy_on(self):
        # Старые настройки без ключа enabled → скидка работает как раньше
        assert format_registration_discount(
            {"registration_discount_type": "percent",
             "registration_discount_value": 20}
        ) == "20%"


class TestCashierParsing:
    """Парсинг строки скидки на кассе (как в routers/public.py): percent vs fixed."""

    @staticmethod
    def _apply(raw: str, subtotal: float) -> float:
        raw = raw.strip()
        if raw.endswith("%"):
            pct = float(raw.replace("%", "").strip())
            return round(subtotal * pct / 100, 2)
        return float(raw.replace("сум", "").replace(" ", "").strip())

    def test_percent_applied(self):
        assert self._apply("20%", 100000) == 20000

    def test_fixed_applied(self):
        assert self._apply("15000 сум", 100000) == 15000

    def test_fixed_no_suffix(self):
        assert self._apply("15000", 100000) == 15000
