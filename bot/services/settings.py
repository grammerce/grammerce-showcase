"""
Сервис для получения настроек бота из конструктора.

Настройки хранятся в таблице bot_settings и связаны с магазином (shop_id).
Каждая кнопка имеет свои настройки в JSON-поле.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from bot.config import async_session_factory
from models import BotSettings, Shop

# Настройки по умолчанию для каждой кнопки

DEFAULT_REGISTRATION_SETTINGS: dict[str, Any] = {
    "is_enabled": True,
    "button_label": "📝 Регистрация",
    "button_emoji": "📝",
    "welcome_message": "Давайте зарегистрируемся! Это займет пару минут.",

    # Имя
    "ask_name": True,
    "ask_name_text": "Введите ваше имя:",
    "name_error_text": "Пожалуйста, введите корректное имя",

    # Телефон
    "ask_phone": True,
    "ask_phone_text": "Отправьте ваш номер телефона:",
    "phone_button_text": "📱 Отправить контакт",
    "phone_error_text": "Пожалуйста, введите корректный номер телефона",

    # Геолокация
    "ask_location": True,
    "ask_location_text": "Отправьте вашу геолокацию для доставки:",
    "location_button_text": "📍 Отправить местоположение",
    "location_skip_text": "⏭ Пропустить",
    "location_can_skip": True,

    # Скидка при регистрации (управляется из кабинета: вкл/выкл + тип + значение)
    "registration_discount_enabled": False,   # по умолчанию выключена (владелец включает сам)
    "registration_discount": "20%",           # legacy-строка (фоллбэк/дисплей)
    "registration_discount_type": "percent",  # percent | fixed
    "registration_discount_value": 20,        # 20 (%) либо сумма в сумах (fixed)

    # Сообщения
    "success_message": "✅ Регистрация завершена!\n\nВам начислена скидка {discount} на первый заказ!",
    "already_registered_message": "Вы уже зарегистрированы!",
    "cancel_text": "❌ Отмена",
    "cancelled_message": "Регистрация отменена. Вы можете начать заново в любое время.",
}


def format_registration_discount(settings: dict[str, Any]) -> str:
    """Строка скидки за регистрацию из настроек кнопки: "20%" или "15000 сум".

    Источник истины — registration_discount_type (percent|fixed) +
    registration_discount_value. Фоллбэк — legacy-строка registration_discount.
    Если скидка выключена тумблером — возвращаем "0%" (нулевая скидка на кассе).
    """
    if settings.get("registration_discount_enabled") is False:
        return "0%"
    disc_type = settings.get("registration_discount_type")
    disc_value = settings.get("registration_discount_value")
    if disc_type and disc_value is not None:
        try:
            v = float(disc_value)
            if disc_type == "fixed":
                return f"{int(v)} сум"
            # percent (0.20 из legacy-доли → 20%)
            if 0 < v <= 1:
                v *= 100
            return f"{int(v)}%" if v == int(v) else f"{v}%"
        except (ValueError, TypeError):
            pass
    return settings.get("registration_discount", "20%")


DEFAULT_PROFILE_SETTINGS: dict[str, Any] = {
    "is_enabled": True,
    "button_label": "👤 Личный кабинет",
    "button_emoji": "👤",
    "title": "Ваш профиль",

    # Что показывать
    "show_name": True,
    "show_phone": True,
    "show_location": True,
    "show_discounts": True,
    "show_orders_count": True,
    "show_bonus_balance": True,

    # Тексты
    "name_label": "Имя",
    "phone_label": "Телефон",
    "location_label": "Адрес доставки",
    "discounts_title": "Ваши скидки",
    "orders_label": "Заказов",
    "bonus_label": "Бонусный баланс",

    # Для незарегистрированных
    "not_registered_message": "Вы еще не зарегистрированы.\nНажмите кнопку ниже для регистрации.",
    "register_button_text": "📝 Зарегистрироваться",

    # Редактирование
    "edit_name_text": "Введите новое имя:",
    "edit_phone_text": "Отправьте новый номер телефона:",
    "edit_location_text": "Отправьте новую геолокацию:",
}

DEFAULT_PROMO_SETTINGS: dict[str, Any] = {
    "is_enabled": True,
    "button_label": "🔥 Акции и скидки",
    "button_emoji": "🔥",
    "welcome_message": "🎁 Введите промокод для получения скидки:",

    # Промокоды
    "promo_input_placeholder": "Введите промокод...",
    "promo_button_text": "✅ Применить",
    "cancel_text": "❌ Отмена",

    # Сообщения
    "success_message": "✅ Промокод активирован!\n\nВаша скидка: {discount}",
    "invalid_message": "❌ Неверный промокод. Проверьте правильность ввода.",
    "already_used_message": "❌ Этот промокод уже использован.",
    "expired_message": "❌ Срок действия промокода истёк.",

    # Отображение скидок
    "show_current_discounts": True,
    "current_discounts_title": "Ваши текущие скидки:",
    "no_discounts_text": "У вас пока нет активных скидок.",
    "discount_format": "-{value} сум",
    "percent_format": "-{value}%",

    # Подсказка с тестовым промокодом (пустая = не показывать)
    "hint_code": "",
}

DEFAULT_CONTACT_SETTINGS: dict[str, Any] = {
    "is_enabled": True,
    "button_label": "📞 Связаться с нами",
    "button_emoji": "📞",
    "title": "Наши контакты",

    # Телефон
    "show_phone": True,
    "phone": "+998 XX XXX XX XX",
    "phone_label": "Телефон для связи",

    # Адрес
    "show_address": True,
    "address": "г. Ташкент, ул. Примерная, д. 1",
    "address_label": "Адрес",

    # Время работы
    "show_working_hours": True,
    "working_hours": "Пн-Вс: 9:00 - 21:00",
    "working_hours_label": "Время работы",

    # Социальные сети
    "show_instagram": True,
    "instagram_url": "",
    "instagram_label": "Instagram",

    "show_telegram": True,
    "telegram_url": "",
    "telegram_label": "Telegram",

    "show_website": True,
    "website_url": "",
    "website_label": "Наш сайт",

    # Чат с менеджером
    "chat_enabled": True,
    "chat_button_label": "💬 Написать менеджеру",
    "chat_started_message": "Администратор скоро ответит вам. Напишите ваш вопрос.",
    "chat_ended_message": "Чат завершён. Спасибо за обращение!",

    # Геолокации (список точек на карте)
    "locations": [],
    "show_map_button": False,
    "map_button_label": "📍 Адрес на карте",
}

DEFAULT_SHOP_WEBAPP_SETTINGS: dict[str, Any] = {
    "is_enabled": True,
    "button_label": "🛒 Магазин",
    "button_emoji": "🛒",

    # WebApp
    "webapp_url": "",  # Будет подставляться динамически
    "open_button_label": "Открыть магазин",

    # Сообщения
    "welcome_message": "🛍 Добро пожаловать в наш магазин!",
    "order_received_message": "✅ Ваш заказ #{order_id} принят!\n\nМы свяжемся с вами для подтверждения.",

    # Шаблон уведомления админу
    "order_admin_template": "🛒 Новый заказ #{order_id}\n\n{items}\n\n💰 Итого: {total}\n👤 Клиент: {customer_name}\n📞 Телефон: {customer_phone}",
}

DEFAULT_MAIN_MENU_SETTINGS: dict[str, Any] = {
    # Порядок кнопок (список button_key)
    "buttons_order": ["shop", "profile", "promo", "contact"],

    # Приветственное сообщение /start
    "welcome_message": "👋 Добро пожаловать!\n\nВыберите действие в меню ниже:",

    # Показывать кнопку "О магазине"
    "show_about": True,
    "about_button_label": "ℹ️ О магазине",
    "about_text": "",
}


# Функции получения настроек

async def get_bot_settings(shop_id: int) -> BotSettings | None:
    """Получить все настройки бота для магазина"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(BotSettings).where(BotSettings.shop_id == shop_id)
        )
        return result.scalar_one_or_none()


async def get_button_settings(
    shop_id: int,
    button_name: str,
    defaults: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Получить настройки конкретной кнопки.

    Args:
        shop_id: ID магазина
        button_name: Название кнопки (registration, profile, promo, contact, shop_webapp, main_menu)
        defaults: Настройки по умолчанию (если не указаны, используются встроенные)

    Returns:
        Словарь с настройками (объединение defaults и сохранённых настроек)
    """
    # Выбираем дефолты по названию кнопки
    if defaults is None:
        defaults_map = {
            "registration": DEFAULT_REGISTRATION_SETTINGS,
            "profile": DEFAULT_PROFILE_SETTINGS,
            "promo": DEFAULT_PROMO_SETTINGS,
            "contact": DEFAULT_CONTACT_SETTINGS,
            "shop_webapp": DEFAULT_SHOP_WEBAPP_SETTINGS,
            "main_menu": DEFAULT_MAIN_MENU_SETTINGS,
        }
        defaults = defaults_map.get(button_name, {})

    # Получаем сохранённые настройки
    bot_settings = await get_bot_settings(shop_id)

    if not bot_settings:
        return defaults.copy()

    # Получаем JSON-поле по названию кнопки
    saved_settings = getattr(bot_settings, button_name, None) or {}

    # Объединяем: сохранённые настройки перезаписывают дефолты
    result = defaults.copy()
    result.update(saved_settings)

    return result


async def save_button_settings(
    shop_id: int,
    button_name: str,
    settings: dict[str, Any]
) -> bool:
    """
    Сохранить настройки кнопки.

    Args:
        shop_id: ID магазина
        button_name: Название кнопки
        settings: Новые настройки

    Returns:
        True если сохранено успешно
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(BotSettings).where(BotSettings.shop_id == shop_id)
        )
        bot_settings = result.scalar_one_or_none()

        if not bot_settings:
            bot_settings = BotSettings(shop_id=shop_id)
            session.add(bot_settings)

        # Обновляем нужное поле
        setattr(bot_settings, button_name, settings)
        flag_modified(bot_settings, button_name)  # принудительно помечаем JSON-поле как изменённое

        await session.commit()
        return True


async def ensure_bot_settings(shop_id: int) -> BotSettings:
    """
    Получить или создать запись BotSettings для магазина с дефолтными значениями.
    Вызывается при первом GET чтобы гарантировать наличие записи в БД.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(BotSettings).where(BotSettings.shop_id == shop_id)
        )
        bot_settings = result.scalar_one_or_none()

        if not bot_settings:
            bot_settings = BotSettings(
                shop_id=shop_id,
                registration=DEFAULT_REGISTRATION_SETTINGS.copy(),
                profile=DEFAULT_PROFILE_SETTINGS.copy(),
                promo=DEFAULT_PROMO_SETTINGS.copy(),
                contact=DEFAULT_CONTACT_SETTINGS.copy(),
                shop_webapp=DEFAULT_SHOP_WEBAPP_SETTINGS.copy(),
                main_menu=DEFAULT_MAIN_MENU_SETTINGS.copy(),
            )
            session.add(bot_settings)
            await session.commit()
            await session.refresh(bot_settings)

        return bot_settings


async def get_shop_id_by_bot_token(bot_token: str) -> int | None:
    """Получить shop_id по токену бота"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Shop.id).where(Shop.bot_token == bot_token)
        )
        row = result.first()
        return row[0] if row else None


async def is_shop_owner(shop_id: int, user_id: int) -> bool:
    """
    Проверить, является ли user_id администратором магазина.
    Учитывает: владельца (shops.owner_tg_id) и дополнительных BotAdmin.
    """
    from models import BotAdmin
    async with async_session_factory() as session:
        # Проверка владельца
        owner_result = await session.execute(
            select(Shop.id).where(
                Shop.id == shop_id,
                Shop.owner_tg_id == user_id
            )
        )
        if owner_result.scalar_one_or_none() is not None:
            return True

        # Проверка дополнительных администраторов
        admin_result = await session.execute(
            select(BotAdmin.id).where(
                BotAdmin.shop_id == shop_id,
                BotAdmin.telegram_id == user_id,
                BotAdmin.invite_used == True,
                BotAdmin.is_active == True,
            )
        )
        return admin_result.scalar_one_or_none() is not None
