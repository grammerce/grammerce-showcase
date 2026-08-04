"""
Главное меню и Личный кабинет с поддержкой настроек из конструктора.
"""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy import select

from bot.config import async_session_factory
from bot.i18n.bot_texts import t
from bot.services.settings import (
    DEFAULT_PROFILE_SETTINGS,
    DEFAULT_SHOP_WEBAPP_SETTINGS,
    get_button_settings,
)
from models import BotUser

from .keyboards import DynamicButtonFilter, get_dynamic_menu
from .registration import _get_shop_languages, _language_kb, start_registration

router = Router(name="menu")


# Кастомные фильтры для динамических кнопок

# DynamicButtonFilter defined in keyboards.py and re-exported here for handlers.

# Хелперы

async def _get_profile_settings(shop_id: int = 1) -> dict[str, Any]:
    """Получить настройки личного кабинета"""
    return await get_button_settings(shop_id, "profile", DEFAULT_PROFILE_SETTINGS)


async def _get_user_lang(telegram_id: int, shop_id: int = 1) -> str:
    """Получить язык пользователя из bot_users. Fallback: 'ru'."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(BotUser).where(
                BotUser.telegram_id == telegram_id,
                BotUser.shop_id == shop_id
            )
        )
        bot_user = result.scalar_one_or_none()
        return (bot_user.language or "ru") if bot_user else "ru"


async def _load_bot_user(telegram_id: int, shop_id: int = 1) -> BotUser | None:
    """Загрузить профиль покупателя из bot_users"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(BotUser).where(
                BotUser.telegram_id == telegram_id,
                BotUser.shop_id == shop_id
            )
        )
        return result.scalar_one_or_none()


async def _profile_kb(shop_id: int = 1, lang: str = "ru") -> InlineKeyboardMarkup:
    """Клавиатура редактирования профиля"""
    buttons = [
        [InlineKeyboardButton(text=t(lang, "btn_edit_name"), callback_data="edit_name")],
        [InlineKeyboardButton(text=t(lang, "btn_edit_phone"), callback_data="edit_phone")],
        [InlineKeyboardButton(text=t(lang, "btn_edit_location"), callback_data="edit_location")],
    ]
    # Показываем кнопку смены языка если у магазина >1 языков
    available_langs, _ = await _get_shop_languages(shop_id)
    if len(available_langs) > 1:
        buttons.append([InlineKeyboardButton(text=t(lang, "btn_change_language"), callback_data="change_language")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Личный кабинет (динамический фильтр)

@router.message(DynamicButtonFilter("profile"))
async def show_profile(message: Message, state: FSMContext, shop_id: int = 1) -> None:
    """Показать личный кабинет. shop_id injected by ShopContextMiddleware."""
    lang = await _get_user_lang(message.from_user.id, shop_id)
    settings = await _get_profile_settings(shop_id)

    # Проверяем, включена ли кнопка
    if not settings.get("is_enabled", True):
        await message.answer(t(lang, "profile_disabled"))
        return

    # Загружаем профиль из bot_users
    bot_user = await _load_bot_user(message.from_user.id, shop_id)

    if not bot_user or not bot_user.is_registered:
        # Пользователь не зарегистрирован
        await message.answer(t(lang, "not_registered"))

        # Предложить регистрацию + смену языка
        buttons = [
            [InlineKeyboardButton(text=t(lang, "btn_register"), callback_data="start_registration")]
        ]
        available_langs, _ = await _get_shop_languages(shop_id)
        if len(available_langs) > 1:
            buttons.append([InlineKeyboardButton(text=t(lang, "btn_change_language"), callback_data="change_language")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(t(lang, "want_to_register"), reply_markup=kb)
        return

    # Формируем сообщение профиля
    title = settings.get("title", "Ваш профиль")
    lines = [f"<b>{title}</b>", ""]

    # Имя
    if settings.get("show_name", True) and bot_user.name:
        name_label = settings.get("name_label", "Имя")
        lines.append(f"👤 {name_label}: {bot_user.name}")

    # Телефон
    if settings.get("show_phone", True) and bot_user.phone:
        phone_label = settings.get("phone_label", "Телефон")
        lines.append(f"📱 {phone_label}: {bot_user.phone}")

    # Геолокация
    if settings.get("show_location", True):
        location_label = settings.get("location_label", "Адрес доставки")
        if bot_user.location_lat and bot_user.location_lon:
            geo_str = f"{float(bot_user.location_lat):.5f}, {float(bot_user.location_lon):.5f}"
        else:
            geo_str = t(lang, "location_not_set")
        lines.append(f"📍 {location_label}: {geo_str}")

    # Скидки
    if settings.get("show_discounts", True):
        lines.append("")
        discounts_title = settings.get("discounts_title", "Ваши скидки")
        lines.append(f"<b>{discounts_title}</b>")

        if bot_user.discount_registration:
            reg_label = settings.get("registration_discount_label", "Регистрационная")
            lines.append(f"• {reg_label}: {bot_user.discount_registration}")
        else:
            no_discounts = settings.get("no_discounts_text", "У вас пока нет активных скидок.")
            lines.append(no_discounts)

    # Количество заказов
    if settings.get("show_orders_count", True):
        orders_label = settings.get("orders_label", "Заказов")
        lines.append(f"\n📦 {orders_label}: 0")

    # Бонусный баланс
    if settings.get("show_bonus_balance", False):
        bonus_label = settings.get("bonus_label", "Бонусный баланс")
        lines.append(f"💰 {bonus_label}: 0 сум")

    menu = await get_dynamic_menu(shop_id, user_id=message.from_user.id, lang=lang)
    await message.answer(
        "\n".join(lines),
        reply_markup=menu,
        parse_mode="HTML"
    )

    # Кнопки редактирования
    await message.answer(t(lang, "update_data"), reply_markup=await _profile_kb(shop_id, lang))


@router.callback_query(F.data == "change_language")
async def cb_change_language(callback, state: FSMContext, shop_id: int = 1) -> None:
    """Сменить язык из личного кабинета"""
    available_langs, _ = await _get_shop_languages(shop_id)
    lang = await _get_user_lang(callback.from_user.id, shop_id)
    if len(available_langs) < 2:
        await callback.answer(t(lang, "one_language_only"))
        return
    await callback.answer()
    await callback.message.answer(
        t(lang, "choose_language"),
        reply_markup=_language_kb(available_langs),
    )


@router.callback_query(F.data == "start_registration")
async def cb_start_registration(callback, state: FSMContext) -> None:
    """Начать регистрацию из личного кабинета"""
    await callback.answer()
    await start_registration(callback.message, state)


# Магазин (динамический фильтр)

@router.message(DynamicButtonFilter("shop"))
async def open_shop(message: Message, shop_id: int = 1) -> None:
    """Открытие магазина через inline-кнопку WebApp. shop_id injected by ShopContextMiddleware."""
    from bot.config import WEB_APP_URL
    from utils.i18n import get_text

    lang = await _get_user_lang(message.from_user.id, shop_id)
    settings = await get_button_settings(shop_id, "shop_webapp", DEFAULT_SHOP_WEBAPP_SETTINGS)

    # Welcome с поддержкой i18n → fallback на welcome_message → fallback на bot_texts
    welcome_i18n = settings.get("welcome_message_i18n")
    if welcome_i18n:
        welcome = get_text(welcome_i18n, lang, default=t(lang, "open_shop"))
    else:
        welcome = settings.get("welcome_message") or t(lang, "open_shop")

    # Кнопка "Открыть магазин" с i18n
    open_label_i18n = settings.get("open_button_label_i18n")
    if open_label_i18n:
        open_label = get_text(open_label_i18n, lang, default=t(lang, "open_shop_btn"))
    else:
        open_label = t(lang, "open_shop_btn")

    shop_url = WEB_APP_URL.rstrip("/") + f"/shop?shop_id={shop_id}&lang={lang}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=open_label,
            web_app=WebAppInfo(url=shop_url),
        )]
    ])
    await message.answer(welcome, reply_markup=kb)


# Контакты обрабатывает contact.py (contact_router регистрируется раньше menu_router).
# Прежний дубль show_contacts здесь был недостижим и удалён (R0).


# Меню

@router.message(F.text.in_({"/menu", "Меню", "меню"}))
async def send_menu(message: Message, shop_id: int = 1) -> None:
    """Показать главное меню. shop_id injected by ShopContextMiddleware."""
    lang = await _get_user_lang(message.from_user.id, shop_id)
    menu = await get_dynamic_menu(shop_id, user_id=message.from_user.id, lang=lang)
    await message.answer(t(lang, "main_menu"), reply_markup=menu)
