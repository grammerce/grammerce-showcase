from __future__ import annotations

import logging
import re as _re

from aiogram.filters import BaseFilter
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from bot.config import WEB_APP_URL
from bot.services.settings import (
    DEFAULT_CONTACT_SETTINGS,
    DEFAULT_PROFILE_SETTINGS,
    DEFAULT_PROMO_SETTINGS,
    DEFAULT_SHOP_WEBAPP_SETTINGS,
    get_button_settings,
    is_shop_owner,
)
from utils.i18n import get_text

log = logging.getLogger(__name__)
WEB_APP_IS_SECURE = WEB_APP_URL.lower().startswith("https://")
if not WEB_APP_IS_SECURE:
    log.warning("WEB_APP_URL must be HTTPS for Telegram WebApp buttons. Current: %s", WEB_APP_URL)


# Дефолтные константы (fallback если БД недоступна)
MENU_SHOP = "🛒 Магазин"
MENU_PROFILE = "👤 Личный кабинет"
MENU_PROMO = "🔥 Акции и скидки"
MENU_CONTACT = "📞 Связаться с нами"

# Админ-меню константы
ADMIN_STATS = "👁 Просмотреть статистику"
ADMIN_PROMOCODES = "📦 Сгенерировать промокоды"
ADMIN_BROADCAST = "📢 Рассылка"
ADMIN_SWITCH_MODE = "🔄 Переключить режим\n(Админ ↔ Пользователь)"


# Паттерн для обрезки эмодзи с краёв строки (чтобы избежать дублирования)
_EMOJI_EDGE_RE = _re.compile(
    r'^[\U0001F000-\U0001FFFF\U00002600-\U00002BFF\U0000FE00-\U0000FEFF\u200d\s]+'
    r'|[\U0001F000-\U0001FFFF\U00002600-\U00002BFF\U0000FE00-\U0000FEFF\u200d\s]+$'
)


def _strip_emoji_edges(text: str) -> str:
    """Убрать эмодзи и пробелы с начала и конца строки."""
    return _EMOJI_EDGE_RE.sub('', text).strip()


class DynamicButtonFilter(BaseFilter):
    """Единый фильтр для динамических кнопок главного меню.

    Матчит текст сообщения по label кнопки во всех языках (ru/uz/en).
    Сравнивает как точный текст, так и текст без эмодзи (для совместимости
    со старыми клавиатурами где emoji могут дублироваться).

    Использование:
        @router.message(DynamicButtonFilter("promo"))
        async def handle_promo(message, shop_id): ...
    """

    def __init__(self, button_key: str):
        self.button_key = button_key

    async def __call__(self, message: Message, shop_id: int) -> bool:
        if not message.text:
            return False
        msg_text = message.text
        msg_stripped = _strip_emoji_edges(msg_text)
        for lang in ("ru", "uz", "en"):
            label = (await get_button_labels(shop_id=shop_id, lang=lang)).get(self.button_key, "")
            if not label:
                continue
            if msg_text == label:
                return True
            if msg_stripped and msg_stripped == _strip_emoji_edges(label):
                return True
        return False


def _build_label(settings: dict, default_label: str, lang: str = "ru") -> str:
    """
    Собрать label кнопки из emoji + text.
    Поддерживает icon_position: 'before' (по-умолч.), 'after', 'none'.
    Если есть button_label_i18n — использует локализованный текст.
    Эмодзи с краёв текста обрезаются — избегаем дублирования если emoji уже в label.
    """
    i18n_dict = settings.get("button_label_i18n")
    if i18n_dict:
        # Если в i18n_dict нет ключа "ru", подставляем из button_label
        if "ru" not in i18n_dict and settings.get("button_label"):
            i18n_dict = dict(i18n_dict)  # не мутируем оригинал
            i18n_dict["ru"] = _strip_emoji_edges(settings["button_label"])
        label = get_text(i18n_dict, lang, fallback_lang="ru", default=settings.get("button_label", default_label))
    else:
        label = settings.get("button_label", default_label)
    emoji = settings.get("button_emoji", "")
    position = settings.get("icon_position", "before")

    if not emoji or position == "none":
        return label
    #Обрезаем эмодзи с краёв чтобы не было "  Акции"
    clean_label = _strip_emoji_edges(label)
    if position == "after":
        return f"{clean_label} {emoji}"
    # position == "before" (default)
    return f"{emoji} {clean_label}"


async def is_user_admin(shop_id: int, user_id: int | None) -> bool:
    """Проверить, является ли пользователь админом (владелец магазина по owner_tg_id)."""
    if not user_id:
        return False
    return await is_shop_owner(shop_id, user_id)


def get_admin_menu() -> ReplyKeyboardMarkup:
    """
    Админ-меню — показывается когда владелец/админ переключается в режим администратора.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADMIN_STATS)],
            [KeyboardButton(text=ADMIN_PROMOCODES)],
            [KeyboardButton(text=ADMIN_BROADCAST)],
            [KeyboardButton(text=ADMIN_SWITCH_MODE)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def get_dynamic_menu(shop_id: int = 1, user_id: int | None = None, admin_mode: bool = False, lang: str = "ru") -> ReplyKeyboardMarkup:
    """
    Получить главное меню с динамическими названиями кнопок из БД.
    Если admin_mode=True и пользователь — админ, показать админ-меню.
    Если admin_mode=False но пользователь — админ, показать обычное меню + кнопку переключения.
    """
    try:
        # Проверяем, является ли пользователь админом
        user_is_admin = False
        if user_id:
            user_is_admin = await is_user_admin(shop_id, user_id)

        # Если админ-режим включён — показываем полное админ-меню
        if admin_mode and user_is_admin:
            return get_admin_menu()

        # Загружаем настройки каждой кнопки
        shop_settings = await get_button_settings(shop_id, "shop_webapp", DEFAULT_SHOP_WEBAPP_SETTINGS)
        profile_settings = await get_button_settings(shop_id, "profile", DEFAULT_PROFILE_SETTINGS)
        promo_settings = await get_button_settings(shop_id, "promo", DEFAULT_PROMO_SETTINGS)
        contact_settings = await get_button_settings(shop_id, "contact", DEFAULT_CONTACT_SETTINGS)

        # Получаем label из настроек (emoji + text) с учётом языка пользователя
        shop_label = _build_label(shop_settings, MENU_SHOP, lang)
        profile_label = _build_label(profile_settings, MENU_PROFILE, lang)
        promo_label = _build_label(promo_settings, MENU_PROMO, lang)
        contact_label = _build_label(contact_settings, MENU_CONTACT, lang)

        # Обычная текстовая кнопка (WebApp отправляется inline из menu.py)
        shop_button = KeyboardButton(text=shop_label)

        # Build keyboard - only include enabled buttons
        keyboard = []

        if shop_settings.get("is_enabled", True):
            keyboard.append([shop_button])

        if promo_settings.get("is_enabled", True):
            keyboard.append([KeyboardButton(text=promo_label)])

        if profile_settings.get("is_enabled", True):
            keyboard.append([KeyboardButton(text=profile_label)])

        if contact_settings.get("is_enabled", True):
            keyboard.append([KeyboardButton(text=contact_label)])

        # Если пользователь — админ в режиме «Пользователь»,
        # добавляем кнопку переключения внизу, чтобы мог вернуться
        if user_is_admin:
            keyboard.append([KeyboardButton(text=ADMIN_SWITCH_MODE)])

        return ReplyKeyboardMarkup(
            keyboard=keyboard,
            resize_keyboard=True,
            one_time_keyboard=False,
        )

    except Exception as e:
        log.error("Error loading dynamic menu: %s", e)
        # Fallback to static menu
        return get_main_menu(shop_id)


def get_main_menu(shop_id: int = 1) -> ReplyKeyboardMarkup:
    """
    Статическое меню (fallback).
    """
    shop_button = KeyboardButton(text=MENU_SHOP)

    return ReplyKeyboardMarkup(
        keyboard=[
            [shop_button],
            [KeyboardButton(text=MENU_PROMO)],
            [KeyboardButton(text=MENU_PROFILE)],
            [KeyboardButton(text=MENU_CONTACT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def get_button_labels(shop_id: int = 1, lang: str = "ru") -> dict:
    """
    Получить актуальные label всех кнопок для использования в фильтрах.
    Возвращает label для указанного языка.
    """
    try:
        shop_settings = await get_button_settings(shop_id, "shop_webapp", DEFAULT_SHOP_WEBAPP_SETTINGS)
        profile_settings = await get_button_settings(shop_id, "profile", DEFAULT_PROFILE_SETTINGS)
        promo_settings = await get_button_settings(shop_id, "promo", DEFAULT_PROMO_SETTINGS)
        contact_settings = await get_button_settings(shop_id, "contact", DEFAULT_CONTACT_SETTINGS)

        return {
            "shop": _build_label(shop_settings, MENU_SHOP, lang),
            "profile": _build_label(profile_settings, MENU_PROFILE, lang),
            "promo": _build_label(promo_settings, MENU_PROMO, lang),
            "contact": _build_label(contact_settings, MENU_CONTACT, lang),
        }
    except Exception as e:
        log.error("Error loading button labels: %s", e)
        return {
            "shop": MENU_SHOP,
            "profile": MENU_PROFILE,
            "promo": MENU_PROMO,
            "contact": MENU_CONTACT,
        }


__all__ = [
    "get_main_menu",
    "get_dynamic_menu",
    "get_admin_menu",
    "get_button_labels",
    "is_user_admin",
    "MENU_SHOP",
    "MENU_PROFILE",
    "MENU_PROMO",
    "MENU_CONTACT",
    "ADMIN_STATS",
    "ADMIN_PROMOCODES",
    "ADMIN_BROADCAST",
    "ADMIN_SWITCH_MODE",
]
