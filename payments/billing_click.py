"""
Click-оплата счёта за тариф (биллинг платформы).

Переиспользует тот же CLICK_PAY_URL что и ClickGateway для заказов магазина.
transaction_param = "billing_{inv.id}" — отличается от order_id чтобы Click webhook
мог различить платёж за тариф и платёж за заказ магазина.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

CLICK_PAY_URL = "https://my.click.uz/services/pay"


def build_invoice_checkout_url(inv) -> str:
    """Формирует URL для оплаты счёта за тариф через Click."""
    service_id  = os.getenv("CLICK_SERVICE_ID", "")
    merchant_id = os.getenv("CLICK_MERCHANT_ID", "")
    base_url    = os.getenv("BASE_URL", "")
    return_url  = os.getenv("CLICK_BILLING_RETURN_URL", f"{base_url}/cabinet/tariffs")

    params = {
        "service_id":        service_id,
        "merchant_id":       merchant_id,
        "amount":            inv.total_amount,
        "transaction_param": f"billing_{inv.id}",
        "return_url":        return_url,
    }
    return f"{CLICK_PAY_URL}?{urlencode(params)}"
