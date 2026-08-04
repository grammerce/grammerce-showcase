"""
payments/ — Абстрактный платёжный слой.
Поддерживает: Click, Payme, Uzum, Mock.
"""
from payments.base import PaymentGateway, PaymentResult
from payments.factory import get_gateway

__all__ = ["PaymentGateway", "PaymentResult", "get_gateway"]
