from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    """Пользователи системы (владельцы магазинов и админы)"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)  # nullable для OAuth users
    role: Mapped[str] = mapped_column(String(20), default="owner")  # admin, owner
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")

    # Профиль
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # OAuth провайдеры
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    apple_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    telegram_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    tg_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(20), default="email")  # email, google, apple, telegram

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Setup Fee
    setup_fee_paid: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    setup_fee_paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relations
    shops: Mapped[list[Shop]] = relationship(back_populates="owner")
    shop_memberships: Mapped[list[ShopMember]] = relationship(
        back_populates="user", foreign_keys="[ShopMember.user_id]"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"


class BotAuthToken(Base):
    """Одноразовый токен для deeplink-регистрации через маркетинговый бот Grammerce."""
    __tablename__ = "bot_auth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    telegram_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tg_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tg_first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tg_last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tg_photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tg_lang: Mapped[str | None] = mapped_column(String(10), nullable=True, server_default="ru")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OfferAcceptance(Base):
    """Записи акцепта публичной оферты и политики конфиденциальности"""
    __tablename__ = "offer_acceptances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    offer_version: Mapped[str] = mapped_column(String(20), nullable=False)
    privacy_version: Mapped[str] = mapped_column(String(20), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    didox_document_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relations
    user: Mapped[User] = relationship("User")


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    bot_token: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    owner_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # Мультиязычность витрины
    available_languages: Mapped[list] = mapped_column(JSON, default=lambda: ["ru"])
    default_language: Mapped[str] = mapped_column(String(5), default="ru", server_default="'ru'")
    # POS-интеграция: {"type":"none"}|{"type":"mock"}|{"type":"moysklad","api_key":...}
    integration_settings: Mapped[dict] = mapped_column(JSON, default=lambda: {"type": "none"})
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    # Dropshipping
    is_dropshipping: Mapped[bool] = mapped_column(default=False, server_default="false")
    prepayment_percent: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Онбординг владельца (см. md_s/CLAUDE_CODE_user_onboarding_checklist.md)
    onboarding_path_chosen: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # "self" | "delegated" | null
    onboarding_dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarding_setup_fee_clicks: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    onboarding_test_purchase_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarding_tour_step: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    onboarding_tour_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )

    # Тип плательщика: 'individual' (физлицо) | 'organization' (юрлицо)
    payer_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Данные физлица (для оплаты тарифа через Click без реквизитов организации)
    individual_first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    individual_last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    individual_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Юридические реквизиты клиента (для выставления счёта)
    company_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    company_inn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    company_account: Mapped[str | None] = mapped_column(String(30), nullable=True)
    company_bank_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company_bank_mfo: Mapped[str | None] = mapped_column(String(10), nullable=True)
    company_director: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    billing_email: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Relations
    owner: Mapped[User | None] = relationship(back_populates="shops")
    categories: Mapped[list[Category]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    products: Mapped[list[Product]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    customers: Mapped[list[Customer]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    orders: Mapped[list[Order]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    bot_users: Mapped[list[BotUser]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    telegram_profiles: Mapped[list[TelegramProfile]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    broadcast_logs: Mapped[list[BroadcastLog]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    bot_settings: Mapped[BotSettings | None] = relationship(
        back_populates="shop", cascade="all, delete-orphan", uselist=False
    )
    promocodes: Mapped[list[Promocode]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    subscription: Mapped[Subscription | None] = relationship(
        back_populates="shop", cascade="all, delete-orphan", uselist=False
    )
    invoices: Mapped[list[Invoice]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    members: Mapped[list[ShopMember]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    approval_requests: Mapped[list[ApprovalRequest]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Shop id={self.id} name={self.name!r} active={self.is_active}>"


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        Index("idx_categories_shop", "shop_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # Мультиязычные поля
    name_i18n: Mapped[dict] = mapped_column(JSON, default=dict)         # {"ru":"...", "uz":"...", "en":"..."}
    description_i18n: Mapped[dict] = mapped_column(JSON, default=dict)
    # POS mapping
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    shop: Mapped[Shop] = relationship(back_populates="categories")
    parent: Mapped[Category | None] = relationship(
        remote_side=[id], back_populates="children"
    )
    children: Mapped[list[Category]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    products: Mapped[list[Product]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"<Category id={self.id} shop_id={self.shop_id} name={self.name!r}>"


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("idx_products_shop_active", "shop_id", "is_active"),
        Index("idx_products_shop_sort", "shop_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # Мультиязычные поля
    name_i18n: Mapped[dict] = mapped_column(JSON, default=dict)         # {"ru":"...", "uz":"...", "en":"..."}
    description_i18n: Mapped[dict] = mapped_column(JSON, default=dict)
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    old_price: Mapped[float | None] = mapped_column(Numeric(10, 2), default=None)
    sku: Mapped[str | None] = mapped_column(String(100), default=None)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    sold: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    image_url: Mapped[str | None] = mapped_column(Text, default=None)
    images: Mapped[list] = mapped_column(JSON, default=list)
    video_url: Mapped[str | None] = mapped_column(Text, default=None)
    variants: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    # Dropshipping-поля (используются только если shop.is_dropshipping=true)
    delivery_time_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    availability_status: Mapped[str] = mapped_column(
        String(16), default="in_stock", server_default="'in_stock'"
    )
    # POS mapping — None для товаров, добавленных вручную (они не трогаются при sync)
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # moysklad|billz|jowi
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    shop: Mapped[Shop] = relationship(back_populates="products")
    category: Mapped[Category | None] = relationship(back_populates="products")
    order_items: Mapped[list[OrderItem]] = relationship(back_populates="product")

    def __repr__(self) -> str:
        return f"<Product id={self.id} shop_id={self.shop_id} name={self.name!r} price={self.price}>"


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("shop_id", "telegram_id", name="uq_customer_shop_tg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), default=None)
    first_name: Mapped[str | None] = mapped_column(String(255), default=None)
    last_name: Mapped[str | None] = mapped_column(String(255), default=None)
    phone: Mapped[str | None] = mapped_column(String(20), default=None)
    bonus_balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    shop: Mapped[Shop] = relationship(back_populates="customers")
    orders: Mapped[list[Order]] = relationship(back_populates="customer")

    def __repr__(self) -> str:
        return f"<Customer id={self.id} shop_id={self.shop_id} tg={self.telegram_id}>"


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_orders_shop_status", "shop_id", "status"),
        Index("idx_orders_shop_created", "shop_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    # Порядковый номер заказа В ПРЕДЕЛАХ магазина (#1, #2, …) — для покупателя.
    # Глобальный id остаётся для внутренних ссылок.
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(50), default="new")
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    original_total: Mapped[float] = mapped_column(Numeric(12, 2), default=0)  # Сумма без скидок
    discount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)       # Скидка
    cost: Mapped[float] = mapped_column(Numeric(12, 2), default=0)           # Себестоимость
    # Dropshipping: сколько оплачено по факту (NULL если магазин не-дропшип или оплата 100%)
    prepaid_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # AZMA Accounting / Учет
    order_uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=lambda: str(uuid.uuid4()))
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fiscal_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="'pending'")
    # POS-интеграция
    external_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    integration_status: Mapped[str] = mapped_column(
        String(30), default="none", server_default="'none'"
    )  # none|pending|pushed|confirmed|failed
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    shop: Mapped[Shop] = relationship(back_populates="orders")
    customer: Mapped[Customer | None] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Order id={self.id} shop_id={self.shop_id} status={self.status!r} total={self.total_amount}>"


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    quantity: Mapped[int] = mapped_column(default=1)
    price_at_moment: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    selected_options: Mapped[dict] = mapped_column(JSON, default=dict)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship(back_populates="order_items")

    def __repr__(self) -> str:
        return f"<OrderItem id={self.id} order_id={self.order_id} product_id={self.product_id} qty={self.quantity}>"


class Promocode(Base):
    """Промокоды"""
    __tablename__ = "promocodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[int] = mapped_column(Integer, default=0)  # Сумма скидки в сумах
    discount_type: Mapped[str] = mapped_column(String(20), default="fixed")  # fixed, percent
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    used_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    max_usage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    apply_to: Mapped[str] = mapped_column(String(10), default="all")   # "all" | "selected"
    product_ids: Mapped[list] = mapped_column(JSON, default=list)       # [1, 2, 5] if selected
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    shop: Mapped[Shop] = relationship(back_populates="promocodes")

    __table_args__ = (
        UniqueConstraint("shop_id", "code", name="uq_promocode_shop_code"),
        Index("idx_promocodes_shop_active", "shop_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Promocode id={self.id} shop_id={self.shop_id} code={self.code!r} active={self.is_active}>"


class BotSettings(Base):
    """Настройки бота из конструктора"""
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # Настройки регистрации
    registration: Mapped[dict] = mapped_column(JSON, default=dict)

    # Настройки личного кабинета
    profile: Mapped[dict] = mapped_column(JSON, default=dict)

    # Настройки акций и скидок
    promo: Mapped[dict] = mapped_column(JSON, default=dict)

    # Настройки контактов
    contact: Mapped[dict] = mapped_column(JSON, default=dict)

    # Настройки магазина (WebApp)
    shop_webapp: Mapped[dict] = mapped_column(JSON, default=dict)

    # Настройки главного меню
    main_menu: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    shop: Mapped[Shop] = relationship(back_populates="bot_settings")

    def __repr__(self) -> str:
        return f"<BotSettings id={self.id} shop_id={self.shop_id}>"


class BotUser(Base):
    """Пользователи Telegram-бота (регистрация, профиль, скидки)"""
    __tablename__ = "bot_users"
    __table_args__ = (
        UniqueConstraint("shop_id", "telegram_id", name="uq_bot_user_shop_tg"),
        Index("idx_bot_users_shop", "shop_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Данные регистрации
    name: Mapped[str | None] = mapped_column(String(255), default=None)
    phone: Mapped[str | None] = mapped_column(String(20), default=None)
    location_lat: Mapped[float | None] = mapped_column(Numeric(10, 8), default=None)
    location_lon: Mapped[float | None] = mapped_column(Numeric(11, 8), default=None)

    # Скидки
    discount_registration: Mapped[str | None] = mapped_column(String(20), default=None)  # "20%"
    discount_promo: Mapped[str | None] = mapped_column(String(50), default=None)  # "15000 сум"
    promo_code: Mapped[str | None] = mapped_column(String(50), default=None)

    # Язык пользователя бота
    language: Mapped[str] = mapped_column(String(5), default="ru", server_default="'ru'")

    # Флаги
    is_registered: Mapped[bool] = mapped_column(default=False)
    is_admin: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    shop: Mapped[Shop] = relationship(back_populates="bot_users")

    def __repr__(self) -> str:
        return f"<BotUser id={self.id} shop_id={self.shop_id} tg={self.telegram_id} registered={self.is_registered}>"


class TelegramProfile(Base):
    """
    Telegram-профили пользователей — маркетинговый слой.

    Хранит данные из регистрации в боте, поведение в WebApp/воронке,
    кешированные бизнес-метрики (из наших заказов или из POS),
    рассылки и оценки.
    Не дублирует POS: cached_* только читаются из POS, если подключён.
    """
    __tablename__ = "telegram_profiles"
    __table_args__ = (
        UniqueConstraint("telegram_id", "shop_id", name="uq_tgprofile_telegram_shop"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Данные из регистрации в боте
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location_lat: Mapped[float | None] = mapped_column(Numeric(10, 8), nullable=True)
    location_lon: Mapped[float | None] = mapped_column(Numeric(11, 8), nullable=True)
    is_registered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    discount_registration: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Кешированные бизнес-метрики
    # data_source='local'  → считаем сами из таблицы orders
    # data_source='billz'  → синхронизированы из BILLZ POS
    # data_source='jowi'   → синхронизированы из Jowi POS
    # data_source='moysklad' → синхронизированы из МойСклад
    cached_orders_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cached_ltv: Mapped[float] = mapped_column(Numeric(14, 2), default=0, server_default="0")
    cached_avg_check: Mapped[float] = mapped_column(Numeric(14, 2), default=0, server_default="0")
    cached_last_order: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_source: Mapped[str] = mapped_column(String(20), default="local", server_default="'local'")

    # Telegram-специфичные данные (наша уникальность — этого нет в POS)
    first_contact_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_activity_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_funnel_step: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_funnel_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Обратная связь
    avg_rating: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    total_ratings: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Рассылки
    broadcasts_received: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_broadcast_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_unsubscribed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Бонусы (из legacy customers)
    bonus_balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    # Relationships
    shop: Mapped[Shop] = relationship(back_populates="telegram_profiles")

    def __repr__(self) -> str:
        return f"<TelegramProfile id={self.id} shop_id={self.shop_id} tg={self.telegram_id}>"


# BILLING — тарифы, подписки, счета

class Plan(Base):
    """Тарифные планы SaaS-платформы."""
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)           # "Start"
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)  # "start"
    price_monthly: Mapped[int] = mapped_column(Integer, nullable=False)     # UZS/мес
    price_yearly: Mapped[int] = mapped_column(Integer, nullable=False)      # UZS/год
    setup_fee_amount: Mapped[int] = mapped_column(Integer, default=3600000, server_default="3600000")  # разовый взнос UZS (300$)
    features: Mapped[dict] = mapped_column(JSON, default=dict)

    # Лимиты
    max_products: Mapped[int] = mapped_column(Integer, default=0)           # 0 = безлимит
    max_broadcasts_month: Mapped[int] = mapped_column(Integer, default=0)  # 0 = безлимит
    pos_integration: Mapped[bool] = mapped_column(Boolean, default=False)
    automation_funnels: Mapped[bool] = mapped_column(Boolean, default=True)
    export_csv: Mapped[bool] = mapped_column(Boolean, default=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="plan")

    def __repr__(self) -> str:
        return f"<Plan id={self.id} slug={self.slug!r} price_monthly={self.price_monthly}>"


class Subscription(Base):
    """Подписка магазина на тарифный план."""
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plans.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="trial")
    # trial | active | past_due | suspended | cancelled
    billing_period: Mapped[str] = mapped_column(String(10), default="monthly")
    # monthly | yearly

    # Период
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Оплата
    last_paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_invoice_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )

    # Grace period
    grace_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    shop: Mapped[Shop] = relationship(back_populates="subscription")
    plan: Mapped[Plan] = relationship(back_populates="subscriptions")
    invoices: Mapped[list[Invoice]] = relationship(
        back_populates="subscription",
        foreign_keys="[Invoice.subscription_id]",
    )

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} shop_id={self.shop_id} status={self.status!r}>"


class Invoice(Base):
    """Счёт на оплату (B2B через банковское платёжное поручение)."""
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscriptions.id"), nullable=False
    )
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    invoice_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    # "INV-2026-000042"

    # Суммы в UZS (целые числа)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    vat_amount: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    # Скидка на счёт (напр. по реферальной ссылке); total = amount - discount + vat(amount-discount)
    discount_amount: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    # Тип: setup_fee (разовый взнос) | subscription (подписка)
    invoice_type: Mapped[str] = mapped_column(
        String(20), default="subscription", server_default="subscription"
    )

    # Описание
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Реквизиты (снимок на момент создания)
    seller_details: Mapped[dict] = mapped_column(JSON, default=dict)
    buyer_details: Mapped[dict] = mapped_column(JSON, default=dict)

    # Статус: draft | sent | paid | overdue | cancelled
    status: Mapped[str] = mapped_column(String(20), default="draft")
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Даты
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Didox / ЭСФ
    didox_document_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    esf_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    esf_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    esf_retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    subscription: Mapped[Subscription] = relationship(
        back_populates="invoices",
        foreign_keys="[Invoice.subscription_id]",
    )
    shop: Mapped[Shop] = relationship(back_populates="invoices")

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} shop_id={self.shop_id} number={self.invoice_number!r} status={self.status!r}>"


class BotAdmin(Base):
    """
    Дополнительные администраторы бота.
    Владелец определяется через shops.owner_tg_id.
    BotAdmin — дополнительные операторы, назначенные через invite-ссылку.
    """
    __tablename__ = "bot_admins"
    __table_args__ = (
        UniqueConstraint("shop_id", "telegram_id", name="uq_bot_admin_shop_tg"),
        Index("idx_bot_admins_shop", "shop_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Invite token (для активации через /start deep link)
    invite_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    invite_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<BotAdmin id={self.id} shop_id={self.shop_id} tg={self.telegram_id}>"


class BroadcastLog(Base):
    """Лог Telegram-рассылок из кабинета."""
    __tablename__ = "broadcast_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    promo_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("promocodes.id", ondelete="SET NULL"), nullable=True
    )
    total_sent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_delivered: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/sending/completed/scheduled
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    shop: Mapped[Shop] = relationship(back_populates="broadcast_logs")

    def __repr__(self) -> str:
        return f"<BroadcastLog id={self.id} shop_id={self.shop_id} status={self.status!r}>"


# RBAC — роли, участники магазина, запросы на одобрение

class ShopMember(Base):
    """Связь пользователя с магазином и его роль."""
    __tablename__ = "shop_members"
    __table_args__ = (
        UniqueConstraint("shop_id", "user_id", name="uq_shop_member_shop_user"),
        Index("idx_shop_members_user", "user_id"),
        Index("idx_shop_members_shop", "shop_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(30), default="owner", nullable=False)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    invited_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    shop: Mapped[Shop] = relationship(back_populates="members")
    user: Mapped[User] = relationship(
        back_populates="shop_memberships", foreign_keys=[user_id]
    )

    def __repr__(self) -> str:
        return f"<ShopMember id={self.id} shop_id={self.shop_id} user_id={self.user_id} role={self.role!r}>"


class ApprovalRequest(Base):
    """Запросы на одобрение критических действий суперадмином."""
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("idx_approval_requests_shop", "shop_id"),
        Index("idx_approval_requests_status", "status"),
        Index("idx_approval_requests_requested_by", "requested_by"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    action_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    reviewed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    shop: Mapped[Shop] = relationship(back_populates="approval_requests")
    requester: Mapped[User] = relationship(foreign_keys=[requested_by])
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])

    def __repr__(self) -> str:
        return f"<ApprovalRequest id={self.id} shop_id={self.shop_id} type={self.action_type!r} status={self.status!r}>"


class SupportMessage(Base):
    """Сообщения чата поддержки (клиент ↔ админ)."""
    __tablename__ = "support_messages"
    __table_args__ = (
        Index("idx_support_msg_shop_customer", "shop_id", "customer_tg_id"),
        Index("idx_support_msg_shop_unread", "shop_id", "is_read", "sender"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    customer_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender: Mapped[str] = mapped_column(String(10), nullable=False)  # "customer" | "admin"
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Структурный маркер типа сообщения: {"type": "delegated_request", ...}
    meta: Mapped[dict] = mapped_column(JSON, default=dict, server_default="'{}'")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    shop: Mapped[Shop] = relationship()

    def __repr__(self) -> str:
        return f"<SupportMessage id={self.id} shop={self.shop_id} tg={self.customer_tg_id} sender={self.sender!r}>"


class OnboardingEvent(Base):
    """События онбординга владельца для аналитики (см. §7 ТЗ)."""
    __tablename__ = "onboarding_events"
    __table_args__ = (
        Index("idx_onboarding_events_shop", "shop_id", "created_at"),
        Index("idx_onboarding_events_name", "event_name", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, server_default="'{}'")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    shop: Mapped[Shop] = relationship()

    def __repr__(self) -> str:
        return f"<OnboardingEvent id={self.id} shop={self.shop_id} event={self.event_name!r}>"


# Платформенные админы — две роли (superadmin/admin) на уровне платформы,
# отдельно от shop_members.

class PlatformUser(Base):
    """Пользователь платформенного уровня (суперадмин или админ).

    Отдельен от users (владельцы магазинов / OAuth). Авторизация — login+password.
    token_version инкрементируется при смене пароля или деактивации, что
    инвалидирует все ранее выданные Bearer-токены (в payload кладётся pwd_v).
    """
    __tablename__ = "platform_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # NULL у суперадмина: он входит только через Telegram, пароля не имеет.
    # Штатные админы (role='admin') пароль сохраняют.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # 'superadmin' | 'admin'
    # Личный Telegram-аккаунт суперадмина. Доступ выдаётся только если этот же id
    # есть и в env SUPERADMIN_TELEGRAM_IDS — см. resolve_superadmin_by_telegram.
    telegram_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("platform_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    admin_shops: Mapped[list[PlatformAdminShop]] = relationship(
        back_populates="admin",
        foreign_keys="[PlatformAdminShop.admin_id]",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<PlatformUser id={self.id} login={self.login!r} role={self.role!r}>"


class PlatformAdminShop(Base):
    """Назначение магазина платформенному админу (M2M)."""
    __tablename__ = "platform_admin_shops"
    __table_args__ = (
        UniqueConstraint("admin_id", "shop_id", name="uq_platform_admin_shop"),
        Index("ix_platform_admin_shops_admin_id", "admin_id"),
        Index("ix_platform_admin_shops_shop_id", "shop_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False
    )
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("platform_users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    admin: Mapped[PlatformUser] = relationship(
        back_populates="admin_shops", foreign_keys=[admin_id]
    )
    shop: Mapped[Shop] = relationship()


class PlatformSupportMessage(Base):
    """Чат "владелец магазина ↔ платформенный админ Grammerce".

    Один thread на shop_id. sender:
      'owner'    — пишет владелец магазина (sender_user_id = users.id)
      'platform' — пишет платформенный админ (sender_user_id = platform_users.id)
    """
    __tablename__ = "platform_support_messages"
    __table_args__ = (
        Index("idx_psm_shop_created", "shop_id", "created_at"),
        Index("idx_psm_shop_unread", "shop_id", "is_read", "sender"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    sender: Mapped[str] = mapped_column(String(16), nullable=False)  # 'owner' | 'platform'
    sender_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    shop: Mapped[Shop] = relationship()

    def __repr__(self) -> str:
        return f"<PlatformSupportMessage id={self.id} shop={self.shop_id} sender={self.sender!r}>"


class AICardExample(Base):
    """Библиотека примеров для few-shot/RAG генерации текста карточки товара.

    shop_id IS NULL — глобальный seed-пример; shop_id задан — одобренный
    пример конкретного магазина (source='approved').
    См. md_s/grammerce_ai_card_spec.md, раздел 3.3.
    """
    __tablename__ = "ai_card_examples"
    __table_args__ = (
        Index("ix_ai_card_examples_category", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[dict] = mapped_column(JSON, nullable=False)            # {"ru": "...", "uz": "..."}
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    bullets: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="seed", nullable=False)  # 'seed' | 'approved'
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<AICardExample id={self.id} category={self.category!r} source={self.source!r}>"


class AICardGeneration(Base):
    """Журнал AI-генераций карточки: что предложил AI и что сохранил пользователь.

    edited — были ли правки; accepted — сохранено без правок (кандидат в examples).
    Изображения храним как sha256-хэши, не base64 — не раздуваем БД.
    """
    __tablename__ = "ai_card_generations"
    __table_args__ = (
        Index("ix_ai_card_generations_shop", "shop_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ai_title: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ai_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_title: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    final_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    image_before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Явная оценка пользователя: +1 лайк / -1 дизлайк / NULL нет оценки (сигнал для обучения)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<AICardGeneration id={self.id} shop={self.shop_id} edited={self.edited} accepted={self.accepted} rating={self.rating}>"


class ReferralLink(Base):
    """Реферальная ссылка на бота платформы (создаёт суперадмин).

    Ссылка вида t.me/<bot>?start=ref_<code>. Пришедший по ней получает скидку на setup_fee.
    """
    __tablename__ = "referral_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), default="", server_default="")
    # fixed — UZS; percent — 0..100
    discount_type: Mapped[str] = mapped_column(String(16), default="fixed", server_default="fixed")
    discount_value: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    max_conversions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<ReferralLink id={self.id} code={self.code!r} active={self.is_active}>"


class ReferralVisit(Base):
    """Заход по реферальной ссылке (уникальная пара link_id+telegram_id).

    converted_shop_id/converted_at заполняются, когда этот telegram_id создал магазин и
    оформил «заявку под ключ» со скидкой.
    """
    __tablename__ = "referral_visits"
    __table_args__ = (
        UniqueConstraint("link_id", "telegram_id", name="uq_referral_visit_link_tg"),
        Index("ix_referral_visits_link", "link_id"),
        Index("ix_referral_visits_tg", "telegram_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    link_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("referral_links.id", ondelete="CASCADE"), nullable=False
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    converted_shop_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("shops.id", ondelete="SET NULL"), nullable=True
    )
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<ReferralVisit id={self.id} link={self.link_id} tg={self.telegram_id} converted={self.converted_shop_id is not None}>"
