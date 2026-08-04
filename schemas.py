"""
Pydantic schemas — consolidated from main.py and original schemas.py.
Single source of truth for all request/response models.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Shop

class ShopBase(BaseModel):
    name: str
    bot_token: str
    owner_tg_id: int
    config: dict[str, Any] | None = {}
    is_dropshipping: bool = False
    prepayment_percent: int = Field(100, ge=1, le=100)

class ShopCreate(ShopBase):
    """Данные для создания магазина"""
    pass

class ShopResponse(ShopBase):
    """Данные, которые мы отдаем фронтенду"""
    id: int
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ShopDropshippingUpdate(BaseModel):
    """PATCH настроек дропшиппинга от админ-кабинета"""
    is_dropshipping: bool | None = None
    prepayment_percent: int | None = Field(None, ge=1, le=100)


# Products

AVAILABILITY_STATUSES = ("in_stock", "preorder", "out_of_stock")


class ProductBase(BaseModel):
    name: str
    description: str | None = None
    price: float
    old_price: float | None = None
    sku: str | None = None
    stock: int = 0
    sort_order: int = 0
    image_url: str | None = None
    images: list[str] = []
    is_active: bool = True
    category_id: int | None = None
    variants: list[dict[str, Any]] = []
    delivery_time_text: str | None = None
    availability_status: str = "in_stock"

class ProductCreate(BaseModel):
    """Used by admin CRUD endpoints (from main.py)"""
    name: str
    description: str = ""
    price: float
    category_id: int | None = None
    image_url: str = ""
    stock: int = 0
    is_active: bool = True
    variants: list = []
    delivery_time_text: str | None = None
    availability_status: str = "in_stock"

class AIProductDraftRequest(BaseModel):
    """Запрос AI-распознавания товара по фото (§ Этап 5)."""
    image_url: str  # data-URL / base64 изображения
    niche: str | None = None  # подсказка ниши (если не передана — берётся из shop.config)

class AIProductDraftResponse(BaseModel):
    name: str
    description: str
    category: str | None = None
    confidence: float = 0.0

class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price: float | None = None
    old_price: float | None = None
    sku: str | None = None
    stock: int | None = None
    sort_order: int | None = None
    image_url: str | None = None
    images: list[str] | None = None
    is_active: bool | None = None
    category_id: int | None = None
    variants: list[dict[str, Any]] | None = None
    delivery_time_text: str | None = None
    availability_status: str | None = None

class ProductResponse(ProductBase):
    id: int
    shop_id: int
    sold: int = 0
    images: list[str] = []
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# Categories

class CategoryCreate(BaseModel):
    name: str
    description: str = ""


# Orders

class OrderItemCreate(BaseModel):
    """Order item from frontend (uses alias id -> product_id)"""
    product_id: int = Field(..., alias="id")
    title: str = Field(..., alias="name")
    qty: int = Field(..., gt=0)
    price: float = Field(..., gt=0)
    selected_options: dict = Field(default_factory=dict)

    class Config:
        populate_by_name = True


class OrderCreate(BaseModel):
    """Order creation payload from frontend"""
    customer_phone: str
    customer_name: str | None = None
    delivery_address: str | None = None
    lat: float | None = None  # координаты точки доставки с карты (для гео в уведомлении)
    lon: float | None = None
    gift_message: str | None = None
    items: list[OrderItemCreate]
    payment_method: str | None = "cash"
    promo_code: str | None = None  # Промокод из витрины

    class Config:
        populate_by_name = True


class OrderItemPayload(BaseModel):
    """Alternative order item schema (legacy)"""
    product_id: int = Field(..., alias="id")
    title: str
    qty: int
    price: float
    selected_options: dict[str, Any] | None = {}


class OrderPayload(BaseModel):
    """Alternative order schema (legacy)"""
    shop_id: int
    items: list[OrderItemPayload]
    total: float
    currency: str = "UZS"
    delivery_info: dict[str, Any] | None = {}
    payment_method: str | None = Field(None, alias="paymentMethod")
    model_config = ConfigDict(populate_by_name=True)


class OrderResponse(BaseModel):
    ok: bool
    order_id: int
    order_number: int | None = None  # порядковый номер в пределах магазина
    message: str


# Auth

class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    shop_name: str
    category: str | None = None
    offer_version: str | None = None
    privacy_version: str | None = None


class SetupShopRequest(BaseModel):
    shop_name: str
    category: str | None = None
    theme: str | None = None
    offer_version: str | None = None
    privacy_version: str | None = None


class TelegramAuthIssueRequest(BaseModel):
    telegram_id: str
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    lang: str | None = "ru"


class TelegramAuthIssueResponse(BaseModel):
    token: str
    consume_url: str
    expires_at: datetime
    has_shop: bool = False
    needs_setup: bool = True


class TelegramWebAppAuthRequest(BaseModel):
    init_data: str


# Support Chat

class SupportMessageSend(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)

class SupportMessageResponse(BaseModel):
    id: int
    sender: str
    text: str
    is_read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class SupportConversationItem(BaseModel):
    customer_tg_id: int
    customer_name: str | None = None
    last_message_text: str
    last_message_at: datetime


# Platform Support Chat (владелец магазина ↔ платформенный админ Grammerce)

class PlatformSupportSend(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)

class PlatformSupportMessageOut(BaseModel):
    id: int
    sender: str  # 'owner' | 'platform'
    text: str
    is_read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class PlatformSupportConversationItem(BaseModel):
    shop_id: int
    shop_name: str
    last_message_text: str
    last_message_at: datetime
    unread_count: int


# Onboarding (см. md_s/CLAUDE_CODE_user_onboarding_checklist.md)

class OnboardingTasks(BaseModel):
    product_added: bool
    branding_set: bool
    bot_connected: bool
    test_purchase_done: bool
    shop_published: bool


class OnboardingStatus(BaseModel):
    path_chosen: str | None = None
    tasks: OnboardingTasks
    progress_percent: int
    dismissed_at: datetime | None = None
    completed_at: datetime | None = None
    show_test_purchase_modal: bool = False
    setup_fee_clicks: int = 0
    trial_started_at: datetime | None = None
    tour_step: int = 0
    tour_completed_at: datetime | None = None


class OnboardingPathIn(BaseModel):
    path: str = Field(..., pattern="^(self|delegated)$")


class OnboardingTourStepIn(BaseModel):
    step: int = Field(..., ge=0, le=5)


class OnboardingTourCompleteIn(BaseModel):
    reason: str = Field("finished", pattern="^(skipped|finished)$")


class DelegatedRequestIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    phone: str = Field(..., min_length=4, max_length=30)
    instagram_url: str | None = Field(None, max_length=300)


class TurnkeyRequestIn(BaseModel):
    """Заявка «под ключ» с публичного лендинга (без авторизации)."""
    name: str = Field(..., min_length=1, max_length=200)
    phone: str = Field(..., min_length=4, max_length=30)
    contact_link: str | None = Field(None, max_length=300)


class OnboardingEventIn(BaseModel):
    event: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, Any] | None = None


# Платформенные админы (платформенный уровень auth)

class PlatformAdminLoginRequest(BaseModel):
    login: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class PlatformAdminLoginResponse(BaseModel):
    token: str
    platform_role: str  # 'superadmin' | 'admin'
    login: str
    is_superadmin: bool
    assigned_shop_ids: list[int]


class PlatformAdminCreate(BaseModel):
    login: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=6, max_length=255)
    shop_ids: list[int] = Field(default_factory=list)


class PlatformAdminUpdate(BaseModel):
    login: str | None = Field(None, min_length=1, max_length=255)
    password: str | None = Field(None, min_length=6, max_length=255)
    is_active: bool | None = None


class PlatformAdminResponse(BaseModel):
    id: int
    login: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    assigned_shop_ids: list[int] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class PlatformAdminShopsUpdate(BaseModel):
    shop_ids: list[int] = Field(default_factory=list)


# AI Карточка товара (md_s/grammerce_ai_card_spec.md, раздел 5.5)

class LocalizedText(BaseModel):
    """Двуязычный текст карточки: ru — кириллица, uz — латиница."""
    ru: str
    uz: str


class GeneratedText(BaseModel):
    """Структурный ответ генерации текста карточки (спека 5.2)."""
    title: LocalizedText
    description: str
    bullets: list[str] = Field(default_factory=list)
    detected_category: str = ""
    used_examples: list[str] = Field(default_factory=list)


class EnhanceResult(BaseModel):
    """Результат улучшения/нормализации фото (спека 5.1).

    enhanced_url — URL или data-URL base64 (в проекте фото ходят как data-URL).
    """
    enhanced_url: str
    width: int
    height: int
    applied_steps: list[str] = Field(default_factory=list)


class AICardEnhanceRequest(BaseModel):
    """Вход /api/v1/ai-card/enhance-image. JSON с data-URL вместо multipart —
    осознанное отклонение от спеки: весь проект хранит фото как base64."""
    image_data: str  # data-URL / base64 изображения
    category: str | None = None
    auto_apply: bool = False
    # enhance=True — полный пайплайн (улучшение + вырез фона + нормализация).
    # enhance=False — только нормализация 3:4 (Фича A, при обрезке фото).
    enhance: bool = True


class AICardGenerateTextRequest(BaseModel):
    """Вход /api/v1/ai-card/generate-text."""
    image_data: str | None = None  # data-URL / base64
    image_url: str | None = None   # альтернатива: ссылка на уже загруженное фото
    category: str | None = None    # название категории/ниша; если нет — из shop.config
    auto_apply: bool = False


class AICardFeedbackIn(BaseModel):
    """Вход /api/v1/ai-card/feedback: что предложил AI и что сохранил пользователь."""
    product_id: int | None = None
    category: str | None = None
    ai_title: LocalizedText | None = None
    ai_description: str | None = None
    final_title: LocalizedText | None = None
    final_description: str | None = None
    image_before_hash: str | None = Field(None, max_length=64)
    image_after_hash: str | None = Field(None, max_length=64)
    # Явная оценка пользователя: 1 лайк / -1 дизлайк / None нет оценки
    rating: int | None = Field(None, ge=-1, le=1)


class ReferralTrackIn(BaseModel):
    """Вход /api/platform/referrals/track (от бота платформы, X-Bot-Secret)."""
    code: str
    telegram_id: int
    username: str | None = None
    first_name: str | None = None


class ReferralLinkCreate(BaseModel):
    """Создание реферальной ссылки (суперадмин)."""
    label: str = ""
    code: str | None = None  # если пусто — сгенерируется
    discount_type: Literal["fixed", "percent"] = "fixed"
    discount_value: int = Field(0, ge=0)
    max_conversions: int | None = Field(None, ge=1)


class ExampleRecord(BaseModel):
    """Пример из библиотеки few-shot/RAG (таблица ai_card_examples)."""
    id: int
    category: str
    title: LocalizedText
    description: str
    bullets: list[str] = Field(default_factory=list)
    source: Literal["seed", "approved"] = "seed"
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)