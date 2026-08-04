"""
Регистрация пользователей с поддержкой настроек из конструктора.

Данные сохраняются в таблицу bot_users (профиль + скидка).
Customer создаётся отдельно при оформлении заказа.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
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
from bot.services.settings import (
    DEFAULT_REGISTRATION_SETTINGS,
    format_registration_discount,
    get_button_settings,
)
from config.oauth import oauth_settings
from models import BotAdmin, BotUser, Shop
from services.profile_service import ProfileService

from .keyboards import get_dynamic_menu, is_user_admin

log = logging.getLogger(__name__)

router = Router(name="registration")

# Константы для callback_data
CALLBACK_EDIT_NAME = "edit_name"
CALLBACK_EDIT_PHONE = "edit_phone"
CALLBACK_EDIT_LOCATION = "edit_location"
CALLBACK_ABOUT = "about"


class RegistrationStates(StatesGroup):
    """FSM состояния регистрации"""
    name = State()
    phone = State()
    location = State()
    edit_field = State()


# Клавиатуры

def _cancel_kb(cancel_text: str = "❌ Отмена") -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cancel_text)]],
        resize_keyboard=True,
    )


def _phone_kb(
    button_text: str = "📱 Отправить контакт",
    cancel_text: str = "❌ Отмена"
) -> ReplyKeyboardMarkup:
    """Клавиатура для отправки телефона"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=button_text, request_contact=True)],
            [KeyboardButton(text=cancel_text)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _location_kb(
    button_text: str = "📍 Отправить местоположение",
    skip_text: str = "⏭ Пропустить",
    cancel_text: str = "❌ Отмена",
    can_skip: bool = True
) -> ReplyKeyboardMarkup:
    """Клавиатура для отправки геолокации"""
    keyboard = [
        [KeyboardButton(text=button_text, request_location=True)],
    ]
    if can_skip:
        keyboard.append([KeyboardButton(text=skip_text)])
    keyboard.append([KeyboardButton(text=cancel_text)])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _webapp_kb(shop_id: int, promo_code: str = "", lang: str = "") -> InlineKeyboardMarkup | None:
    """Инлайн-кнопка 'Открыть магазин' (только для HTTPS).

    Args:
        shop_id: ID магазина
        promo_code: Промокод для O2O-перехода (опционально)
        lang: Язык пользователя для витрины (опционально)
    """
    webapp_url = f"{WEB_APP_URL}/shop?shop_id={shop_id}"
    if lang:
        webapp_url += f"&lang={lang}"
    if promo_code:
        webapp_url += f"&promo={promo_code}"
    if not webapp_url.lower().startswith("https://"):
        return None
    btn_text = t(lang or "ru", "open_shop_btn")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=btn_text,
                web_app=WebAppInfo(url=webapp_url)
            )]
        ]
    )


# Хелперы

_LANG_FLAGS = {"ru": "🇷🇺 Русский", "uz": "🇺🇿 O'zbek", "en": "🇬🇧 English"}


def _language_kb(available: list[str]) -> InlineKeyboardMarkup:
    """Inline-клавиатура выбора языка"""
    buttons = [
        InlineKeyboardButton(
            text=_LANG_FLAGS.get(lang, lang.upper()),
            callback_data=f"select_lang:{lang}"
        )
        for lang in available
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


async def _get_shop_languages(shop_id: int) -> tuple[list[str], str]:
    """Вернуть (available_languages, default_language) магазина"""
    try:
        async with async_session_factory() as session:
            result = await session.execute(select(Shop).where(Shop.id == shop_id))
            shop = result.scalar_one_or_none()
            if shop:
                available = shop.available_languages or ["ru"]
                default = shop.default_language or "ru"
                return available, default
    except Exception as e:
        log.warning("_get_shop_languages error: %r", e)
    return ["ru"], "ru"


async def _get_user_language(user_id: int, shop_id: int) -> str | None:
    """Получить явно установленный язык пользователя из BotUser.
    Возвращает None если язык не был явно выбран (новый или не выбравший пользователь)."""
    bot_user = await _get_bot_user(user_id, shop_id)
    return bot_user.language if (bot_user and bot_user.language) else None


async def _set_user_language(user_id: int, shop_id: int, lang: str) -> None:
    """Сохранить язык пользователя в BotUser"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(BotUser).where(
                BotUser.telegram_id == user_id,
                BotUser.shop_id == shop_id
            )
        )
        bot_user = result.scalar_one_or_none()
        if not bot_user:
            bot_user = BotUser(shop_id=shop_id, telegram_id=user_id)
            session.add(bot_user)
        bot_user.language = lang
        await session.commit()


async def _get_settings(shop_id: int = 1) -> dict[str, Any]:
    """Получить настройки регистрации"""
    return await get_button_settings(shop_id, "registration", DEFAULT_REGISTRATION_SETTINGS)


async def _get_bot_user(user_id: int, shop_id: int = 1) -> BotUser | None:
    """Получить BotUser из БД"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(BotUser).where(
                BotUser.telegram_id == user_id,
                BotUser.shop_id == shop_id
            )
        )
        return result.scalar_one_or_none()


async def _save_bot_user(
    user_id: int,
    shop_id: int,
    name: str,
    phone: str,
    location: dict[str, float] | None,
    discount_registration: str = "20%"
) -> BotUser:
    """Сохранить или обновить BotUser (профиль покупателя в боте)"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(BotUser).where(
                BotUser.telegram_id == user_id,
                BotUser.shop_id == shop_id
            )
        )
        bot_user = result.scalar_one_or_none()

        if not bot_user:
            bot_user = BotUser(
                shop_id=shop_id,
                telegram_id=user_id,
            )
            session.add(bot_user)

        bot_user.name = name
        if phone:
            bot_user.phone = phone
        if location:
            bot_user.location_lat = float(location.get("lat", 0))
            bot_user.location_lon = float(location.get("lon", 0))
        bot_user.discount_registration = discount_registration
        bot_user.is_registered = True

        await session.commit()
        await session.refresh(bot_user)
        return bot_user


def _normalize_phone(phone: str) -> str | None:
    """Нормализация и валидация номера телефона (формат UZ +998XXXXXXXXX).
    Возвращает None если номер не соответствует формату."""
    import re as _re
    digits = _re.sub(r'\D', '', phone)
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    return None


# Команда /start

async def _activate_admin_invite(token: str, shop_id: int, user: object) -> bool:
    """
    Активировать invite-токен администратора.
    Возвращает True если токен валиден и успешно активирован.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(BotAdmin).where(
                BotAdmin.invite_token == token,
                BotAdmin.shop_id == shop_id,
                BotAdmin.invite_used == False,
                BotAdmin.is_active == True,
            )
        )
        admin_record = result.scalar_one_or_none()
        if not admin_record:
            return False

        admin_record.telegram_id = user.id
        admin_record.username = user.username
        admin_record.first_name = user.first_name
        admin_record.invite_used = True
        await session.commit()
        return True


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, shop_id: int = 1) -> None:
    """Обработка команды /start. shop_id injected by ShopContextMiddleware."""
    await state.clear()

    # Сохраняем shop_id в state для последующих шагов
    await state.update_data(shop_id=shop_id)

    user_id = message.from_user.id

    # Проверяем deep link на invite администратора (?start=admin_TOKEN)
    start_arg = message.text.split(maxsplit=1)[1] if message.text and " " in message.text else ""
    log.info("cmd_start called for user %s with start_arg: %r", message.from_user.id, start_arg)

    # Grammerce deeplink-регистрация на платформе (сайт → t.me/<bot>?start=register)
    if start_arg == "register":
        secret = oauth_settings.platform_bot_shared_secret
        backend = (oauth_settings.backend_url or "").rstrip("/")
        if not secret or not backend:
            log.warning("register deeplink: PLATFORM_BOT_SHARED_SECRET or BACKEND_URL not set")
            await message.answer(
                "⚠️ Регистрация временно недоступна. Попробуйте позже."
            )
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{backend}/api/auth/telegram/issue",
                    headers={"X-Bot-Secret": secret, "Content-Type": "application/json"},
                    json={
                        "telegram_id": str(message.from_user.id),
                        "first_name": message.from_user.first_name,
                        "last_name": message.from_user.last_name,
                        "username": message.from_user.username,
                    },
                )
            if resp.status_code != 200:
                log.warning("register deeplink: issue returned %s: %s", resp.status_code, resp.text)
                await message.answer("⚠️ Не удалось подготовить вход. Попробуйте позже.")
                return
            body = resp.json()
            consume_url = body.get("consume_url")
            if not consume_url:
                await message.answer("⚠️ Не удалось получить ссылку. Попробуйте позже.")
                return
            # Метка кнопки: нет магазина → «Создать магазин», есть → «Открыть платформу».
            has_shop = body.get("has_shop", False)
            btn_label = "🚀 Открыть платформу" if has_shop else "✅ Создать магазин"
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=btn_label, url=consume_url)
            ]])
            await message.answer(
                "Нажмите кнопку ниже, чтобы открыть личный кабинет Grammerce.\n"
                "Ссылка одноразовая и действует 5 минут — если устарела, отправьте /start заново.",
                reply_markup=kb,
            )
        except Exception as e:
            log.exception("register deeplink failed: %s", e)
            await message.answer("⚠️ Ошибка подключения к платформе. Попробуйте позже.")
        return

    if start_arg.startswith("admin_"):
        invite_token = start_arg[6:]  # срезаем "admin_"
        activated = await _activate_admin_invite(invite_token, shop_id, message.from_user)
        if activated:
            await message.answer(
                "✅ <b>Вы назначены администратором магазина!</b>\n\n"
                "Теперь у вас есть доступ к панели управления.",
                parse_mode="HTML"
            )
            log.info("Admin invite activated for user %s in shop %s", user_id, shop_id)
        else:
            await message.answer("❌ Ссылка-приглашение недействительна или уже использована.")
        # Продолжаем показывать меню ниже

    # ── O2O: Обработка deep link (shop QR или promo QR) ──────────────────────
    import re as _re
    promo_from_link = ""
    is_shop_deep_link = False
    deep_link_shop_id = shop_id  # по умолчанию — shop_id из middleware
    if start_arg:
        # Извлекаем shop_id из deep link (shop_X_promo_CODE или shop_X)
        shop_match = _re.match(r'^shop_(\d+)', start_arg)
        if shop_match:
            deep_link_shop_id = int(shop_match.group(1))
            log.info("O2O deep link: extracted shop_id=%s (middleware shop_id=%s)", deep_link_shop_id, shop_id)
        
        # format: shop_X_promo_CODE  или  promo_CODE
        m = _re.match(r'^(?:shop_\d+_)?promo_(.+)$', start_arg)
        if m:
            promo_from_link = m.group(1)
            log.info("O2O promo from deep link: code=%s user=%s shop=%s", promo_from_link, user_id, deep_link_shop_id)
        # format: shop_X (без промо — простой QR магазина)
        elif _re.match(r'^shop_\d+$', start_arg):
            is_shop_deep_link = True
            log.info("O2O shop deep link: user=%s shop=%s", user_id, deep_link_shop_id)
    
    # Если прошли по промо-ссылке — сразу показываем кнопку магазина с промокодом
    # Используем deep_link_shop_id — именно из QR-ссылки, а не middleware
    # Язык пользователя для витрины (может быть None у нового пользователя)
    _user_lang_for_link = await _get_user_language(user_id, shop_id) or ""

    if promo_from_link:
        webapp_kb = _webapp_kb(deep_link_shop_id, promo_code=promo_from_link, lang=_user_lang_for_link)
        if webapp_kb:
            await message.answer(
                f"🎁 У вас есть промокод <b>{promo_from_link}</b>!\n"
                "Нажмите кнопку ниже, чтобы открыть магазин — скидка применится автоматически.",
                reply_markup=webapp_kb,
                parse_mode="HTML",
            )
        await state.update_data(promo_from_link=promo_from_link)
    # Если прошли по QR магазина — сразу показываем кнопку магазина
    elif is_shop_deep_link:
        webapp_kb = _webapp_kb(deep_link_shop_id, lang=_user_lang_for_link)
        if webapp_kb:
            await message.answer(
                "🛍 Добро пожаловать! Нажмите кнопку ниже, чтобы открыть магазин.",
                reply_markup=webapp_kb,
            )

    # Трекинг первого контакта в TelegramProfile
    try:
        async with async_session_factory() as session:
            svc = ProfileService(session)
            profile = await svc.get_or_create(
                shop_id=shop_id,
                telegram_id=user_id,
                name=message.from_user.full_name,
                username=message.from_user.username,
            )
            await svc.track_activity(profile.id)
            await session.commit()
    except Exception as e:
        log.warning("ProfileService.get_or_create failed in /start: %r", e)

    # Проверяем доступные языки магазина
    try:
        available_langs, default_lang = await _get_shop_languages(shop_id)

        # Если магазин поддерживает несколько языков — показываем выбор
        if len(available_langs) > 1:
            user_lang = await _get_user_language(user_id, shop_id)
            # Показываем выбор языка если: язык не установлен или не входит в актуальный список
            if user_lang is None or user_lang not in available_langs:
                await message.answer(
                    t("ru", "choose_language"),
                    reply_markup=_language_kb(available_langs)
                )
                return
        else:
            user_lang = await _get_user_language(user_id, shop_id) or default_lang

        await _send_welcome(message, state, shop_id, user_lang)
    except Exception as e:
        log.error("cmd_start error for user %s in shop %s: %r", user_id, shop_id, e, exc_info=True)
        # Fallback: всегда ответить пользователю, даже при ошибке
        try:
            from bot.handlers.keyboards import get_main_menu
            await message.answer(
                "👋 Добро пожаловать!\n\nВыберите действие в меню ниже:",
                reply_markup=get_main_menu(shop_id),
            )
        except Exception:
            await message.answer("👋 Добро пожаловать! Используйте /start для начала.")


@router.message(Command("language"))
async def cmd_language(message: Message, state: FSMContext, shop_id: int = 1) -> None:
    """Команда /language — позволяет пользователю сменить язык интерфейса бота."""
    available_langs, _ = await _get_shop_languages(shop_id)
    if len(available_langs) < 2:
        await message.answer("Магазин работает только на одном языке.")
        return
    await message.answer(t("ru", "choose_language"), reply_markup=_language_kb(available_langs))


async def _send_welcome(
    message: Message,
    state: FSMContext,
    shop_id: int,
    lang: str,
) -> None:
    """Отправить приветственное сообщение на нужном языке."""
    user_id = message.from_user.id

    # Читаем приветственное сообщение из конструктора (main_menu настройки)
    from bot.services.settings import DEFAULT_MAIN_MENU_SETTINGS, get_button_settings
    main_menu_settings = await get_button_settings(shop_id, "main_menu", DEFAULT_MAIN_MENU_SETTINGS)

    user_admin = await is_user_admin(shop_id, user_id)
    log.info("_send_welcome: user=%s shop=%s is_admin=%s lang=%s", user_id, shop_id, user_admin, lang)

    from utils.i18n import get_text

    if user_admin:
        admin_welcome = main_menu_settings.get("admin_welcome_message", None)
        welcome = admin_welcome or t(lang, "welcome_default")
        await state.update_data(admin_mode=True)
        menu = await get_dynamic_menu(shop_id, user_id=user_id, admin_mode=True, lang=lang)
    else:
        welcome_i18n = main_menu_settings.get("welcome_message_i18n")
        if welcome_i18n:
            welcome = get_text(welcome_i18n, lang, default=t(lang, "welcome_default"))
        elif lang == "ru":
            welcome = main_menu_settings.get("welcome_message") or t(lang, "welcome_default")
        else:
            # Нет i18n и язык не русский — используем переведённый дефолт
            welcome = t(lang, "welcome_default")
        menu = await get_dynamic_menu(shop_id, user_id=user_id, admin_mode=False, lang=lang)

    await message.answer(welcome, reply_markup=menu, parse_mode="HTML")


# Callback: выбор языка

@router.callback_query(F.data.startswith("select_lang:"))
async def cb_select_lang(callback: CallbackQuery, state: FSMContext, shop_id: int = 1) -> None:
    """Сохраняем выбранный язык и показываем приветствие."""
    lang = callback.data.split(":", 1)[1]
    available_langs, _ = await _get_shop_languages(shop_id)
    if lang not in available_langs:
        await callback.answer()
        return

    await _set_user_language(callback.from_user.id, shop_id, lang)
    await callback.answer(t(lang, "language_selected"))

    # Редактируем сообщение с выбором языка → убираем клавиатуру
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await state.update_data(shop_id=shop_id)
    await _send_welcome(callback.message, state, shop_id, lang)


# Начало регистрации

async def start_registration(message: Message, state: FSMContext, shop_id: int = 1) -> None:
    """Начало процесса регистрации. shop_id injected by ShopContextMiddleware."""
    await state.clear()

    settings = await _get_settings(shop_id)

    # Сохраняем shop_id и настройки в state
    await state.update_data(shop_id=shop_id, settings=settings)

    # Получаем язык пользователя
    user_lang = await _get_user_language(message.from_user.id, shop_id)

    # Проверяем, включена ли регистрация
    if not settings.get("is_enabled", True):
        await message.answer(t(user_lang, "registration_disabled"))
        return

    # Проверяем, не зарегистрирован ли уже
    bot_user = await _get_bot_user(message.from_user.id, shop_id)
    if bot_user and bot_user.is_registered:
        await message.answer(settings.get("already_registered_message") or t(user_lang, "already_registered"))
        return

    # Отправляем приветствие
    await message.answer(settings.get("welcome_message") or t(user_lang, "reg_welcome"))
    await state.update_data(user_lang=user_lang)

    # Определяем первый шаг
    cancel_text = settings.get("cancel_text") or t(user_lang, "btn_cancel")

    if settings.get("ask_name", True):
        await message.answer(
            settings.get("ask_name_text") or t(user_lang, "ask_name"),
            reply_markup=_cancel_kb(cancel_text)
        )
        await state.set_state(RegistrationStates.name)

    elif settings.get("ask_phone", True):
        await message.answer(
            settings.get("ask_phone_text") or t(user_lang, "ask_phone"),
            reply_markup=_phone_kb(
                settings.get("phone_button_text") or t(user_lang, "btn_send_contact"),
                cancel_text
            )
        )
        await state.set_state(RegistrationStates.phone)

    elif settings.get("ask_location", True):
        await message.answer(
            settings.get("ask_location_text") or t(user_lang, "ask_location"),
            reply_markup=_location_kb(
                settings.get("location_button_text") or t(user_lang, "btn_send_location"),
                settings.get("location_skip_text") or t(user_lang, "btn_skip"),
                cancel_text,
                settings.get("location_can_skip", True)
            )
        )
        await state.set_state(RegistrationStates.location)

    else:
        # Все поля отключены - сразу завершаем
        await _finish_registration(message, state, settings)


# Обработка имени

@router.message(RegistrationStates.name)
async def handle_name(message: Message, state: FSMContext) -> None:
    """Обработка ввода имени"""
    data = await state.get_data()
    settings = data.get("settings", DEFAULT_REGISTRATION_SETTINGS)
    user_lang = data.get("user_lang", "ru")
    cancel_text = settings.get("cancel_text") or t(user_lang, "btn_cancel")

    # Проверка на отмену
    if (message.text or "").strip().lower() == cancel_text.lower():
        await _cancel_flow(message, state, settings)
        return

    name = (message.text or "").strip()
    if not name or len(name) < 2:
        await message.answer(
            settings.get("name_error_text") or t(user_lang, "name_error"),
            reply_markup=_cancel_kb(cancel_text)
        )
        return

    await state.update_data(name=name)

    # Следующий шаг
    if settings.get("ask_phone", True):
        await message.answer(
            settings.get("ask_phone_text") or t(user_lang, "ask_phone"),
            reply_markup=_phone_kb(
                settings.get("phone_button_text") or t(user_lang, "btn_send_contact"),
                cancel_text
            )
        )
        await state.set_state(RegistrationStates.phone)

    elif settings.get("ask_location", True):
        await message.answer(
            settings.get("ask_location_text") or t(user_lang, "ask_location"),
            reply_markup=_location_kb(
                settings.get("location_button_text") or t(user_lang, "btn_send_location"),
                settings.get("location_skip_text") or t(user_lang, "btn_skip"),
                cancel_text,
                settings.get("location_can_skip", True)
            )
        )
        await state.set_state(RegistrationStates.location)

    else:
        await _finish_registration(message, state, settings)


# Обработка телефона

@router.message(RegistrationStates.phone, F.contact)
async def handle_phone_contact(message: Message, state: FSMContext) -> None:
    """Обработка телефона через кнопку контакта"""
    phone = message.contact.phone_number
    await _process_phone(message, state, phone)


@router.message(RegistrationStates.phone)
async def handle_phone_text(message: Message, state: FSMContext) -> None:
    """Обработка телефона текстом"""
    data = await state.get_data()
    settings = data.get("settings", DEFAULT_REGISTRATION_SETTINGS)
    user_lang = data.get("user_lang", "ru")
    cancel_text = settings.get("cancel_text") or t(user_lang, "btn_cancel")

    # Проверка на отмену
    if (message.text or "").strip().lower() == cancel_text.lower():
        await _cancel_flow(message, state, settings)
        return

    raw_phone = (message.text or "").strip()
    if not raw_phone:
        await message.answer(
            settings.get("phone_error_text") or t(user_lang, "phone_error"),
            reply_markup=_phone_kb(
                settings.get("phone_button_text") or t(user_lang, "btn_send_contact"),
                cancel_text
            )
        )
        return

    await _process_phone(message, state, raw_phone)


async def _process_phone(message: Message, state: FSMContext, raw_phone: str) -> None:
    """Общая обработка телефона"""
    data = await state.get_data()
    settings = data.get("settings", DEFAULT_REGISTRATION_SETTINGS)
    user_lang = data.get("user_lang", "ru")
    cancel_text = settings.get("cancel_text") or t(user_lang, "btn_cancel")

    phone = _normalize_phone(raw_phone)
    if not phone:
        await message.answer(
            settings.get("phone_error_text") or t(user_lang, "phone_error"),
            reply_markup=_phone_kb(
                settings.get("phone_button_text") or t(user_lang, "btn_send_contact"),
                cancel_text
            )
        )
        return

    await state.update_data(phone=phone)

    # Следующий шаг
    if settings.get("ask_location", True):
        await message.answer(
            settings.get("ask_location_text") or t(user_lang, "ask_location"),
            reply_markup=_location_kb(
                settings.get("location_button_text") or t(user_lang, "btn_send_location"),
                settings.get("location_skip_text") or t(user_lang, "btn_skip"),
                cancel_text,
                settings.get("location_can_skip", True)
            )
        )
        await state.set_state(RegistrationStates.location)
    else:
        await _finish_registration(message, state, settings)


# Обработка геолокации

@router.message(RegistrationStates.location, F.location)
async def handle_location(message: Message, state: FSMContext) -> None:
    """Обработка геолокации"""
    data = await state.get_data()
    settings = data.get("settings", DEFAULT_REGISTRATION_SETTINGS)

    location_payload = {
        "lat": float(message.location.latitude),
        "lon": float(message.location.longitude),
    }
    await state.update_data(location=location_payload)
    await _finish_registration(message, state, settings)


@router.message(RegistrationStates.location)
async def handle_location_text(message: Message, state: FSMContext) -> None:
    """Обработка текста в состоянии ожидания геолокации (пропуск/отмена)"""
    data = await state.get_data()
    settings = data.get("settings", DEFAULT_REGISTRATION_SETTINGS)
    user_lang = data.get("user_lang", "ru")
    cancel_text = settings.get("cancel_text") or t(user_lang, "btn_cancel")
    skip_text = settings.get("location_skip_text") or t(user_lang, "btn_skip")

    text = (message.text or "").strip().lower()

    if text == cancel_text.lower():
        await _cancel_flow(message, state, settings)
        return

    if text == skip_text.lower() and settings.get("location_can_skip", True):
        await state.update_data(location=None)
        await _finish_registration(message, state, settings)
        return

    # Неизвестный текст
    await message.answer(
        t(user_lang, "location_unknown"),
        reply_markup=_location_kb(
            settings.get("location_button_text") or t(user_lang, "btn_send_location"),
            skip_text,
            cancel_text,
            settings.get("location_can_skip", True)
        )
    )


# Завершение регистрации

async def _finish_registration(
    message: Message,
    state: FSMContext,
    settings: dict[str, Any]
) -> None:
    """Завершение регистрации и сохранение в BotUser"""
    data = await state.get_data()
    shop_id = data.get("shop_id", 1)
    user_lang = data.get("user_lang", "ru")

    name = data.get("name") or message.from_user.first_name or "Покупатель"
    phone = data.get("phone", "")
    location = data.get("location")
    discount = format_registration_discount(settings)

    # Сохраняем в BotUser
    await _save_bot_user(
        user_id=message.from_user.id,
        shop_id=shop_id,
        name=name,
        phone=phone,
        location=location,
        discount_registration=discount
    )

    # Обновляем TelegramProfile: отмечаем как зарегистрированного
    try:
        async with async_session_factory() as session:
            svc = ProfileService(session)
            profile = await svc.get_or_create(
                shop_id=shop_id,
                telegram_id=message.from_user.id,
                name=name,
                phone=phone,
            )
            profile.is_registered = True
            if location:
                profile.location = f"{location['lat']},{location['lon']}"
            await session.commit()
    except Exception as e:
        log.warning("ProfileService registration update failed: %r", e)

    # Формируем сообщение об успехе
    success_message = settings.get("success_message") or t(user_lang, "reg_success", discount=discount)
    # Если кастомный текст с плейсхолдером — форматируем; иначе t() уже вернул готовый текст
    if settings.get("success_message"):
        try:
            success_message = success_message.format(discount=discount)
        except (KeyError, ValueError):
            pass

    await state.clear()
    menu = await get_dynamic_menu(shop_id, user_id=message.from_user.id, lang=user_lang)
    await message.answer(success_message, reply_markup=ReplyKeyboardRemove())
    await message.answer(t(user_lang, "main_menu"), reply_markup=menu)

    # WebApp кнопка (только для HTTPS)
    webapp_kb = _webapp_kb(shop_id, lang=user_lang)
    if webapp_kb:
        await message.answer(t(user_lang, "open_shop"), reply_markup=webapp_kb)


async def _cancel_flow(
    message: Message,
    state: FSMContext,
    settings: dict[str, Any],
    shop_id: int = 1
) -> None:
    """Отмена регистрации"""
    data = await state.get_data()
    shop_id = data.get("shop_id", shop_id)
    user_lang = data.get("user_lang", "ru")
    await state.clear()
    cancelled_message = settings.get("cancelled_message") or t(user_lang, "reg_cancelled")
    menu = await get_dynamic_menu(shop_id, user_id=message.from_user.id, lang=user_lang)
    await message.answer(cancelled_message, reply_markup=ReplyKeyboardRemove())
    await message.answer(t(user_lang, "main_menu"), reply_markup=menu)


# Редактирование профиля (callback)

@router.callback_query(F.data.in_({CALLBACK_EDIT_NAME, CALLBACK_EDIT_PHONE, CALLBACK_EDIT_LOCATION}))
async def cb_edit(callback: CallbackQuery, state: FSMContext, shop_id: int = 1) -> None:
    """Обработка нажатия на кнопки редактирования. shop_id injected by ShopContextMiddleware."""
    await callback.answer()

    settings = await _get_settings(shop_id)
    await state.update_data(shop_id=shop_id, settings=settings)
    cancel_text = settings.get("cancel_text", "❌ Отмена")

    if callback.data == CALLBACK_EDIT_NAME:
        await callback.message.answer(
            settings.get("ask_name_text", "Введите новое имя:"),
            reply_markup=_cancel_kb(cancel_text)
        )
        await state.set_state(RegistrationStates.name)

    elif callback.data == CALLBACK_EDIT_PHONE:
        await callback.message.answer(
            settings.get("ask_phone_text", "Отправьте новый номер телефона:"),
            reply_markup=_phone_kb(
                settings.get("phone_button_text", "📱 Отправить контакт"),
                cancel_text
            )
        )
        await state.set_state(RegistrationStates.phone)

    elif callback.data == CALLBACK_EDIT_LOCATION:
        await callback.message.answer(
            settings.get("ask_location_text", "Отправьте новую геолокацию:"),
            reply_markup=_location_kb(
                settings.get("location_button_text", "📍 Отправить местоположение"),
                settings.get("location_skip_text", "⏭ Пропустить"),
                cancel_text,
                settings.get("location_can_skip", True)
            )
        )
        await state.set_state(RegistrationStates.location)


# Callback "О магазине"

@router.callback_query(F.data == CALLBACK_ABOUT)
async def show_about(callback: CallbackQuery) -> None:
    """Показать информацию о магазине"""
    await callback.answer()
    # TODO: Получить текст из настроек
    await callback.message.answer(
        "📍 <b>О магазине</b>\n\n"
        "Мы предлагаем качественные товары.\n\n"
        "🚚 <b>Доставка:</b> по всему городу\n"
        "💳 <b>Оплата:</b> наличными, Click, PayMe\n"
        "⏰ <b>Работаем:</b> ежедневно с 9:00 до 21:00",
        parse_mode="HTML"
    )
