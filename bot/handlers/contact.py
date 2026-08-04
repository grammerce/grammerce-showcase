"""
Контакты и чат с менеджером с поддержкой настроек из конструктора.
"""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
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
)
from sqlalchemy import select

from bot.services.settings import DEFAULT_CONTACT_SETTINGS, get_button_settings
from database import async_session_factory

from .keyboards import DynamicButtonFilter, get_dynamic_menu

router = Router(name="contact")


class ContactStates(StatesGroup):
    """FSM состояния для чата с менеджером"""
    waiting_message = State()
    admin_replying = State()


# Хелперы

async def _get_settings(shop_id: int = 1) -> dict[str, Any]:
    """Получить настройки контактов"""
    return await get_button_settings(shop_id, "contact", DEFAULT_CONTACT_SETTINGS)


async def _get_shop_admin_tg_ids(shop_id: int) -> list[int]:
    """Вернуть telegram_id владельца + активных BotAdmin магазина."""
    from models import BotAdmin, Shop
    ids: list[int] = []
    async with async_session_factory() as session:
        # Владелец магазина
        shop = await session.get(Shop, shop_id)
        if shop and shop.owner_tg_id:
            ids.append(shop.owner_tg_id)
        # Дополнительные бот-админы
        result = await session.execute(
            select(BotAdmin.telegram_id).where(
                BotAdmin.shop_id == shop_id,
                BotAdmin.invite_used == True,
                BotAdmin.is_active == True,
            )
        )
        ids.extend(row[0] for row in result.fetchall() if row[0] not in ids)
    # Фолбэка на глобальный env ADMIN_IDS здесь нет намеренно. Он означал, что
    # у магазина без owner_tg_id и без активных BotAdmin (свежий магазин, магазин
    # на онбординге, владелец сменил Telegram) переписка покупателей уходила
    # платформенным админам, а те могли отвечать от лица менеджера этого магазина.
    # Нет админов у магазина — значит, менеджер недоступен (см. process_chat_message).
    return ids


def _fmt_phone(raw: str) -> str:
    """Форматировать UZ-номер: любой формат → +998 XX XXX XX XX"""
    digits = "".join(c for c in raw if c.isdigit())
    if digits.startswith("998"):
        digits = digits[3:]
    digits = digits[:9]
    if len(digits) < 9:
        return raw  # не трогать если не похоже на UZ-номер
    return f"+998 {digits[:2]} {digits[2:5]} {digits[5:7]} {digits[7:9]}"


def _build_contact_keyboard(settings: dict[str, Any]) -> InlineKeyboardMarkup:
    """Построить клавиатуру с контактами"""
    buttons = []

    # Telegram
    if settings.get("show_telegram") and settings.get("telegram_url"):
        buttons.append([InlineKeyboardButton(
            text=settings.get("telegram_label", "📱 Telegram"),
            url=settings["telegram_url"]
        )])

    # Website
    if settings.get("show_website") and settings.get("website_url"):
        buttons.append([InlineKeyboardButton(
            text=settings.get("website_label", "🌐 Наш сайт"),
            url=settings["website_url"]
        )])

    # Instagram
    if settings.get("show_instagram") and settings.get("instagram_url"):
        buttons.append([InlineKeyboardButton(
            text=settings.get("instagram_label", "📸 Instagram"),
            url=settings["instagram_url"]
        )])

    # Map button — показывает venue-карточки по клику
    # Кнопка появляется если есть точки И тогл не выключен явно (show_map_button != False)
    locations = settings.get("locations") or []
    if locations and settings.get("show_map_button", True):
        map_label = settings.get("map_button_label", "📍 Адрес на карте")
        buttons.append([InlineKeyboardButton(
            text=map_label,
            callback_data="contact:show_map"
        )])

    # Chat with manager
    if settings.get("chat_enabled", True):
        buttons.append([InlineKeyboardButton(
            text=settings.get("chat_button_label", "💬 Написать менеджеру"),
            callback_data="contact:start_chat"
        )])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _cancel_kb(cancel_text: str = "❌ Отмена") -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cancel_text)]],
        resize_keyboard=True,
    )


async def rearm_support_chat(shop_id: int, customer_tg_id: int):
    """Снова открыть чат поддержки на стороне клиента после ответа менеджера.

    Ставит клиенту состояние ContactStates.waiting_message (+ shop_id/settings в
    данные), чтобы его СЛЕДУЮЩЕЕ сообщение снова дошло до кабинета/админа — даже
    если он ранее нажал «Отмена». Возвращает клавиатуру «Отмена», которую нужно
    прикрепить к сообщению-ответу менеджера, чтобы у клиента сменился reply-
    keyboard на «Отмена». Если бот магазина не запущен — вернёт None (тогда
    состояние не выставляется, но и сообщение всё равно не уйдёт).

    Вызывается из обоих путей ответа: бот (contact.admin_send_reply) и кабинет
    (routers/support_chat.admin_send_reply).
    """
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    from bot.manager import BotManager

    dp = BotManager._dp
    bot = BotManager.get_bot(shop_id)
    if dp is None or bot is None:
        return None

    settings = await _get_settings(shop_id)
    cancel_text = settings.get("cancel_text", "❌ Отмена")
    # Личный чат: chat_id == user_id == telegram_id клиента (как при обычном апдейте).
    key = StorageKey(bot_id=bot.id, chat_id=customer_tg_id, user_id=customer_tg_id)
    ctx = FSMContext(storage=dp.storage, key=key)
    await ctx.set_state(ContactStates.waiting_message)
    await ctx.update_data(shop_id=shop_id, settings=settings)
    return _cancel_kb(cancel_text)


# Показ контактов

@router.message(DynamicButtonFilter("contact"))
async def show_contacts(message: Message, shop_id: int = 1) -> None:
    """Показать контакты. shop_id injected by ShopContextMiddleware."""
    settings = await _get_settings(shop_id)

    # Проверяем, включена ли кнопка
    if not settings.get("is_enabled", True):
        await message.answer("Раздел временно недоступен.")
        return

    # Формируем текст
    title = settings.get("title", "Наши контакты")
    lines = [f"<b>{title}</b>", ""]

    # Телефон — форматируем и в <code> для удобного копирования
    if settings.get("show_phone", True) and settings.get("phone"):
        lines.append(f"📞 <code>{_fmt_phone(settings['phone'])}</code>")

    # Адрес
    if settings.get("show_address") and settings.get("address"):
        lines.append(f"📍 {settings['address']}")

    # Время работы
    if settings.get("show_working_hours") and settings.get("working_hours"):
        lines.append(f"⏰ {settings['working_hours']}")

    text = "\n".join(lines)
    keyboard = _build_contact_keyboard(settings)

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# Карта — отправка venue по кнопке

@router.callback_query(F.data == "contact:show_map")
async def show_map_locations(callback: CallbackQuery, shop_id: int = 1) -> None:
    """Отправить venue-карточки для всех точек из настроек."""
    settings = await _get_settings(shop_id)
    locations: list = settings.get("locations", [])
    title = settings.get("title", "Наш адрес")

    await callback.answer()

    sent = 0
    for loc in locations:
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is None or lon is None:
            continue
        loc_title = loc.get("title") or title
        loc_address = loc.get("address", "")
        try:
            await callback.message.bot.send_venue(
                chat_id=callback.message.chat.id,
                latitude=float(lat),
                longitude=float(lon),
                title=loc_title,
                address=loc_address,
            )
            sent += 1
        except Exception:
            pass

    if sent == 0:
        await callback.message.answer("Адреса на карте пока не указаны.")


# Чат с менеджером

@router.callback_query(F.data == "contact:start_chat")
async def start_chat(callback: CallbackQuery, state: FSMContext, shop_id: int = 1) -> None:
    """Начать чат с менеджером. shop_id injected by ShopContextMiddleware."""
    settings = await _get_settings(shop_id)

    await callback.answer()

    # Сохраняем настройки в state
    await state.update_data(shop_id=shop_id, settings=settings)

    welcome = settings.get(
        "chat_started_message",
        "Администратор скоро ответит вам. Напишите ваш вопрос."
    )

    await callback.message.answer(
        welcome,
        reply_markup=_cancel_kb(settings.get("cancel_text", "❌ Отмена"))
    )
    await state.set_state(ContactStates.waiting_message)


@router.message(ContactStates.waiting_message, ~F.text.startswith("/"))
async def process_chat_message(message: Message, state: FSMContext) -> None:
    """Переслать сообщение админу и сохранить в support_messages.

    Фильтр ~F.text.startswith("/"): команды (/start, /language, /stats…) НЕ
    перехватываются как текст чата, а уходят своим обработчикам. Так открытый
    чат поддержки не ломает команды бота. Медиа (text=None) фильтр пропускает.
    """
    from models import SupportMessage

    data = await state.get_data()
    settings = data.get("settings", DEFAULT_CONTACT_SETTINGS)
    shop_id = data.get("shop_id", 1)
    cancel_text = settings.get("cancel_text", "❌ Отмена")

    # Проверка на отмену
    if (message.text or "").strip().lower() == cancel_text.lower():
        await state.clear()
        menu = await get_dynamic_menu(shop_id)
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        await message.answer("Главное меню:", reply_markup=menu)
        return

    user = message.from_user
    msg_text = message.text or "[медиа]"

    # ── Сохраняем сообщение в support_messages (для кабинета) ──
    try:
        async with async_session_factory() as session:
            sm = SupportMessage(
                shop_id=shop_id,
                customer_tg_id=user.id,
                sender="customer",
                text=msg_text,
            )
            session.add(sm)
            await session.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to save support message to DB: %r", e)

    # ── Формируем сообщение для админа (push-уведомление в Telegram) ──
    admin_text = "📨 <b>Новое сообщение от пользователя:</b>\n\n"
    admin_text += f"👤 {user.full_name}"
    if user.username:
        admin_text += f" (@{user.username})"
    admin_text += f"\n🆔 ID: {user.id}\n\n"
    admin_text += f"💬 {msg_text}"

    # Клавиатура для ответа
    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="↩️ Ответить",
            callback_data=f"chat:reply:{shop_id}:{user.id}"
        )]
    ])

    # Отправляем бот-админам магазина (владелец + BotAdmin из БД)
    admin_ids = await _get_shop_admin_tg_ids(shop_id)
    sent_count = 0

    for admin_id in admin_ids:
        try:
            await message.bot.send_message(
                admin_id,
                admin_text,
                reply_markup=admin_keyboard,
                parse_mode="HTML"
            )
            sent_count += 1
        except Exception:
            pass

    # Ответ пользователю. НЕ сбрасываем состояние — держим чат поддержки открытым,
    # чтобы каждое следующее сообщение клиента (в т.ч. ответ на реплику менеджера)
    # тоже доходило до кабинета/админа. Выход — по кнопке «Отмена».
    # Раньше здесь был state.clear(), из-за чего до менеджера доходило ТОЛЬКО первое
    # сообщение, а последующие терялись.
    if sent_count > 0:
        sent_msg = settings.get(
            "chat_sent_message",
            "✅ Сообщение отправлено! Ожидайте ответа менеджера."
        )
        await message.answer(sent_msg, reply_markup=_cancel_kb(cancel_text))
    else:
        await message.answer(
            "К сожалению, не удалось связаться с менеджером. Попробуйте позже.",
            reply_markup=_cancel_kb(cancel_text)
        )
    # state остаётся ContactStates.waiting_message — диалог продолжается


# Ответ админа

@router.callback_query(F.data.startswith("chat:reply:"))
async def admin_reply_start(callback: CallbackQuery, state: FSMContext, shop_id: int = 1) -> None:
    """Админ начинает отвечать"""
    # Проверяем, что это админ магазина
    admin_ids = await _get_shop_admin_tg_ids(shop_id)
    if callback.from_user.id not in admin_ids:
        await callback.answer("У вас нет прав для этого действия", show_alert=True)
        return

    # Формат callback_data: "chat:reply:{shop_id}:{user_id}"
    #
    # ВАЖНО: callback_data — НЕ доверенный ввод. Кнопку рисуем мы, но кастомный
    # MTProto-клиент (Telethon/Pyrogram) отправляет в messages.getBotCallbackAnswer
    # произвольный data. Раньше права проверялись против shop_id из middleware,
    # а писали по shop_id из callback — админ магазина A слал
    # "chat:reply:<B>:<жертва>" и от лица менеджера магазина B писал его клиенту
    # (и принудительно открывал тому чат поддержки B).
    # Поэтому магазин берём ТОЛЬКО из middleware — он привязан к токену бота,
    # через которого пришёл апдейт; значение из callback лишь сверяем.
    parts = callback.data.split(":")
    try:
        if len(parts) == 4:
            if int(parts[2]) != shop_id:
                await callback.answer("У вас нет прав для этого действия", show_alert=True)
                return
            user_id = int(parts[3])
        else:
            # Обратная совместимость: старый формат "chat:reply:{user_id}"
            user_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("Некорректный запрос", show_alert=True)
        return

    reply_shop_id = shop_id

    await state.update_data(reply_to_user=user_id, reply_shop_id=reply_shop_id)
    await state.set_state(ContactStates.admin_replying)

    await callback.answer()
    await callback.message.answer(
        f"Введите ответ для пользователя ID: {user_id}\n\n"
        "Или отправьте /cancel для отмены."
    )


@router.message(ContactStates.admin_replying)
async def admin_send_reply(message: Message, state: FSMContext) -> None:
    """Админ отправляет ответ и сохраняет в support_messages."""
    from models import SupportMessage

    # Отмена
    if message.text and message.text.lower() == "/cancel":
        await state.clear()
        await message.answer("Отменено.")
        return

    data = await state.get_data()
    user_id = data.get("reply_to_user")
    # Дефолта 1 здесь быть не должно: при потере state ответ уходил бы в магазин №1.
    reply_shop_id = data.get("reply_shop_id")

    if not user_id or not reply_shop_id:
        await state.clear()
        await message.answer("Ошибка: не найден получатель.")
        return

    # Права проверяем повторно, а не только на входе в состояние: состояние живёт
    # между апдейтами, и админа могли деактивировать/удалить из магазина после того,
    # как он нажал «Ответить».
    admin_ids = await _get_shop_admin_tg_ids(reply_shop_id)
    if message.from_user.id not in admin_ids:
        await state.clear()
        await message.answer("У вас нет прав для этого действия.")
        return

    reply_text = message.text or ""

    # ── Сохраняем ответ админа в support_messages (для витрины/кабинета) ──
    try:
        async with async_session_factory() as session:
            sm = SupportMessage(
                shop_id=reply_shop_id,
                customer_tg_id=user_id,
                sender="admin",
                text=reply_text,
                is_read=True,
            )
            session.add(sm)
            await session.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to save admin reply to DB: %r", e)

    # ── Отправляем ответ пользователю через Telegram ──
    # Переоткрываем чат на стороне клиента: возвращаем ему состояние чата +
    # клавиатуру «Отмена», чтобы его ответ снова дошёл до кабинета/админа.
    try:
        cancel_kb = await rearm_support_chat(reply_shop_id, user_id)
        await message.bot.send_message(
            user_id,
            f"📨 <b>Ответ от менеджера:</b>\n\n{reply_text}",
            parse_mode="HTML",
            reply_markup=cancel_kb,
        )
        await message.answer("✅ Ответ успешно отправлен пользователю!")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить ответ: {e}")

    await state.clear()
