"""
Утилиты для платёжных шлюзов: верификация подписей, хелперы.
"""
import hashlib
import hmac


def verify_click_sign(data: dict, secret_key: str) -> bool:
    """
    Верификация подписи Click.

    sign_string = md5(
        click_trans_id + service_id + secret_key +
        merchant_trans_id + (merchant_prepare_id если action=1) +
        amount + action + sign_time
    )
    """
    try:
        action = str(data.get("action", ""))
        parts = [
            str(data.get("click_trans_id", "")),
            str(data.get("service_id", "")),
            secret_key,
            str(data.get("merchant_trans_id", "")),
        ]
        if action == "1":
            parts.append(str(data.get("merchant_prepare_id", "")))
        parts += [
            str(data.get("amount", "")),
            action,
            str(data.get("sign_time", "")),
        ]
        # NOTE: Click API (Uzbekistan) mandates MD5 in its official webhook spec.
        # Cannot switch to SHA-256 without a protocol change on Click's side.
        # Timing-attack risk is mitigated by using hmac.compare_digest below.
        expected = hashlib.md5("".join(parts).encode()).hexdigest()  # nosec B324
        return hmac.compare_digest(expected, str(data.get("sign_string", "")))
    except Exception:
        return False


def uzs_to_tiyin(amount_uzs: int) -> int:
    """Конвертация суммы из UZS в тийины (для Payme: 1 сум = 100 тийин)."""
    return amount_uzs * 100


def tiyin_to_uzs(amount_tiyin: int) -> int:
    """Конвертация тийинов обратно в UZS."""
    return amount_tiyin // 100
