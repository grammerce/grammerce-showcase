"""
SQLAlchemy модель Payment — отдельная таблица для всех платежей.
Изолирована от основного models.py для чистоты архитектуры.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    # "click" / "payme" / "uzum" / "mock" / "cash"
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    # ID транзакции в системе провайдера
    provider_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # pending / paid / failed / cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Доп. данные от провайдера (prepare_id, webhook payload, etc.)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
