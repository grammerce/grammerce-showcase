"""
Промокоды и акции с поддержкой настроек из конструктора.
"""
from __future__ import annotations

import re
from datetime import UTC
from typing import Any

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from sqlalchemy import select

from bot.config import WEB_APP_URL, async_session_factory
from bot.i18n.bot_texts import t
from bot.services.settings import DEFAULT_PROMO_SETTINGS, get_button_settings
from models import BotUser, Customer, Promocode

from .keyboards import DynamicButtonFilter, get_dynamic_menu

router = Router(name="promo")


class PromoStates(StatesGroup):
    """FSM состояния для промокодов"""
    waiting_code = State()


# Хелперы

async def _get_settings(shop_id: int = 1) -> dict[str, Any]:
    """Получить настройки акций"""
    return await get_button_settings(shop_id, "promo", DEFAULT_PROMO_SETTINGS)


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


async def _get_customer(user_id: int, shop_id: int = 1) -> Customer | None:
    """Получить покупателя"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Customer).where(
                Customer.telegram_id == user_id,
                Customer.shop_id == shop_id
            )
        )
        return result.scalar_one_or_none()


async def _get_bot_user(user_id: int, shop_id: int = 1) -> BotUser | None:
    """Получить зарегистрированного пользователя бота (для чтения скидок)"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(BotUser).where(
                BotUser.telegram_id == user_id,
                BotUser.shop_id == shop_id,
                BotUser.is_registered == True,
            )
        )
        return result.scalar_one_or_none()


async def _get_promocode(code: str, shop_id: int = 1) -> Promocode | None:
    """Получить промокод по коду"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Promocode).where(
                Promocode.code == code.upper(),
                Promocode.shop_id == shop_id
            )
        )
        return result.scalar_one_or_none()


async def _use_promocode(code: str, user_id: int, shop_id: int = 1) -> bool:
    """Использовать промокод и сохранить скидку в BotUser."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Promocode).where(
                Promocode.code == code.upper(),
                Promocode.shop_id == shop_id
            )
        )
        promo = result.scalar_one_or_none()

        if not promo:
            return False

        if promo.max_usage is not None:
            promo.is_active = False
            promo.used_by = user_id
        else:
            promo.usage_count = (promo.usage_count or 0) + 1

        # Сохраняем скидку в профиль пользователя (BotUser)
        bu_result = await session.execute(
            select(BotUser).where(
                BotUser.telegram_id == user_id,
                BotUser.shop_id == shop_id,
                BotUser.is_registered == True,
            )
        )
        bot_user = bu_result.scalar_one_or_none()
        if bot_user:
            # Формируем строку скидки для отображения
            if promo.discount_type == "percent":
                bot_user.discount_promo = f"{promo.value}%"
            else:
                bot_user.discount_promo = f"{promo.value} сум"
            bot_user.promo_code = code.upper()

        await session.commit()
        return True


def _cancel_kb(cancel_text: str = "❌ Отмена") -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cancel_text)]],
        resize_keyboard=True,
    )


def _format_discount(value: int, discount_type: str, settings: dict[str, Any]) -> str:
    """Форматирование скидки"""
    if discount_type == "percent":
        return settings.get("percent_format", "-{value}%").format(value=value)
    else:
        return settings.get("discount_format", "-{value} сум").format(value=f"{value:,}")


# Начало ввода промокода

@router.message(DynamicButtonFilter("promo"))
async def start_promo(message: Message, state: FSMContext, shop_id: int = 1) -> None:
    """Начать ввод промокода. shop_id injected by ShopContextMiddleware."""
    lang = await _get_user_lang(message.from_user.id, shop_id)
    settings = await _get_settings(shop_id)

    # Проверяем, включена ли кнопка
    if not settings.get("is_enabled", True):
        await message.answer(t(lang, "promo_disabled"))
        return

    # Проверяем регистрацию
    customer = await _get_customer(message.from_user.id, shop_id)
    if not customer or not customer.first_name:
        await message.answer(t(lang, "promo_not_registered"))
        return

    # Сохраняем настройки в state
    await state.update_data(shop_id=shop_id, settings=settings)

    # Показываем текущие скидки если включено
    if settings.get("show_current_discounts", True):
        discounts_title = settings.get("current_discounts_title", "Ваши текущие скидки:")
        lines = [f"<b>{discounts_title}</b>"]

        bot_user = await _get_bot_user(message.from_user.id, shop_id)
        if bot_user and bot_user.discount_registration:
            lines.append(f"• Регистрационная: {bot_user.discount_registration}")
        else:
            no_discounts = settings.get("no_discounts_text", "У вас пока нет активных скидок.")
            lines.append(no_discounts)

        await message.answer("\n".join(lines), parse_mode="HTML")

    # Подсказка с тестовым промокодом (если задан в настройках)
    hint_code = settings.get("hint_code", "")
    if hint_code:
        await message.answer(
            f"🎁 Тестовый промокод: <code>{hint_code}</code>\n"
            "Нажмите на код чтобы скопировать, затем введите ниже.",
            parse_mode="HTML"
        )

    # Кнопка открытия магазина (WebApp)
    shop_url = f"{WEB_APP_URL.rstrip('/')}/shop?shop_id={shop_id}&lang={lang}"
    shop_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "open_shop_btn"), web_app=WebAppInfo(url=shop_url))],
    ])
    await message.answer(t(lang, "promo_enter_code"), reply_markup=shop_kb)

    # Просим ввести промокод
    welcome = settings.get("welcome_message", "🎁 Введите промокод для получения скидки:")
    cancel_text = settings.get("cancel_text", "❌ Отмена")

    await message.answer(welcome, reply_markup=_cancel_kb(cancel_text))
    await state.set_state(PromoStates.waiting_code)


# Обработка промокода

@router.message(PromoStates.waiting_code)
async def process_promo_code(message: Message, state: FSMContext) -> None:
    """Обработка введённого промокода"""
    data = await state.get_data()
    settings = data.get("settings", DEFAULT_PROMO_SETTINGS)
    shop_id = data.get("shop_id", 1)
    cancel_text = settings.get("cancel_text", "❌ Отмена")

    # Проверка на отмену
    if (message.text or "").strip().lower() == cancel_text.lower():
        await state.clear()
        menu = await get_dynamic_menu(shop_id)
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        await message.answer("Главное меню:", reply_markup=menu)
        return

    # Валидация формата кода
    code = (message.text or "").strip().upper()

    # Regex: 4-64 символа, буквы, цифры, дефис, подчеркивание
    if not re.match(r'^[A-Z0-9\-_]{4,64}$', code):
        await message.answer(
            "Неверный формат промокода. Используйте буквы и цифры.",
            reply_markup=_cancel_kb(cancel_text)
        )
        return

    # Ищем промокод в БД
    promocode = await _get_promocode(code, shop_id)

    if not promocode:
        invalid_msg = settings.get("invalid_message", "❌ Неверный промокод. Проверьте правильность ввода.")
        await message.answer(invalid_msg, reply_markup=_cancel_kb(cancel_text))
        return

    # Проверяем активность
    if not promocode.is_active:
        already_used = settings.get("already_used_message", "❌ Этот промокод уже использован.")
        await message.answer(already_used, reply_markup=_cancel_kb(cancel_text))
        return

    # used_by = кому выдан личный код. Владельцу — можно, чужим — «уже использован».
    if promocode.used_by and promocode.used_by != message.from_user.id:
        already_used = settings.get("already_used_message", "❌ Этот промокод уже использован.")
        await message.answer(already_used, reply_markup=_cancel_kb(cancel_text))
        return

    # Проверяем срок действия
    if promocode.expires_at:
        from datetime import datetime
        now = datetime.now(UTC)
        if promocode.expires_at < now:
            expired_msg = settings.get("expired_message", "❌ Срок действия промокода истёк.")
            await message.answer(expired_msg, reply_markup=_cancel_kb(cancel_text))
            return

    # Активируем промокод
    user_id = message.from_user.id

    # Проверяем, не применял ли уже этот пользователь промокод
    bot_user = await _get_bot_user(user_id, shop_id)
    if bot_user and bot_user.promo_code:
        already_used = settings.get("already_used_message", "❌ Этот промокод уже использован.")
        await message.answer(already_used, reply_markup=_cancel_kb(cancel_text))
        return

    await _use_promocode(code, user_id, shop_id)

    # Формируем текст скидки
    discount_text = _format_discount(
        promocode.value,
        promocode.discount_type,
        settings
    )

    # Отправляем сообщение об успехе
    success_msg = settings.get(
        "success_message",
        "✅ Промокод активирован!\n\nВаша скидка: {discount}"
    )
    await message.answer(
        success_msg.format(discount=discount_text),
        reply_markup=ReplyKeyboardRemove()
    )

    await state.clear()
    menu = await get_dynamic_menu(shop_id)
    await message.answer("Главное меню:", reply_markup=menu)
