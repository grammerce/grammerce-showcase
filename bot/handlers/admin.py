"""
Admin Handlers — полный админ-режим для владельцев магазинов.

Включает:
- Просмотр статистики
- Генерация промокодов
- Рассылка сообщений
- Переключение режима Админ ↔ Пользователь
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import string
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from auth_utils import generate_token, store_token
from bot.config import WEB_APP_URL, async_session_factory
from integrations.accounting import accounting_provider
from models import BotUser, Customer, Order, Promocode

from .keyboards import (
    ADMIN_BROADCAST,
    ADMIN_PROMOCODES,
    ADMIN_STATS,
    get_admin_menu,
    get_dynamic_menu,
    is_user_admin,
)

log = logging.getLogger(__name__)
router = Router(name="admin")


class PromoGenStates(StatesGroup):
    """FSM для настройки параметров промокода перед генерацией."""
    waiting_type = State()   # выбор типа скидки
    waiting_value = State()  # ввод числового значения


# Статусы заказов считаются «принятыми»
# partially_paid / awaiting_supply — дропшип (частичная предоплата / ожидание поставки)
ACCEPTED_STATUSES = ("accepted", "paid", "partially_paid", "awaiting_supply", "delivered", "completed")

PERIOD_LABELS = {
    "today": "Сегодня",
    "month": "Текущий месяц",
    "all": "Всё время",
}


# Helpers

async def _check_admin(user_id: int, shop_id: int) -> bool:
    """Проверить, является ли пользователь админом."""
    return await is_user_admin(shop_id, user_id)


def _date_from(period: str) -> datetime | None:
    now = datetime.now(UTC).replace(tzinfo=None)
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None  # "all"


async def _get_stats(shop_id: int, period: str = "today", only_accepted: bool = False) -> dict:
    """Собрать статистику из БД."""
    date_from = _date_from(period)

    async with async_session_factory() as session:
        # Пользователи (всегда общее число)
        customers_count = await session.scalar(
            select(func.count(Customer.id)).where(Customer.shop_id == shop_id)
        ) or 0

        # Активные промокоды
        active_promos = await session.scalar(
            select(func.count(Promocode.id)).where(
                Promocode.shop_id == shop_id,
                Promocode.is_active.is_(True),
            )
        ) or 0

        # Заказы — SQL-агрегация вместо загрузки в память Python
        agg_q = select(
            func.count(Order.id).label("cnt"),
            func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
            func.coalesce(func.sum(Order.original_total), 0).label("original_sum"),
            func.coalesce(func.sum(Order.discount), 0).label("total_discount"),
            func.coalesce(func.sum(Order.cost), 0).label("cost"),
        ).where(Order.shop_id == shop_id)
        if date_from:
            agg_q = agg_q.where(Order.created_at >= date_from)
        if only_accepted:
            agg_q = agg_q.where(Order.status.in_(ACCEPTED_STATUSES))

        agg_row = (await session.execute(agg_q)).one()

    orders_count = agg_row.cnt
    revenue = float(agg_row.revenue)
    original_sum = float(agg_row.original_sum)
    total_discount = float(agg_row.total_discount)
    cost = float(agg_row.cost)
    profit = revenue - cost

    return {
        "customers_count": customers_count,
        "active_promos": active_promos,
        "orders_count": orders_count,
        "original_sum": original_sum,
        "total_discount": total_discount,
        "cost": cost,
        "revenue": revenue,
        "profit": profit,
    }


def _fmt(value: float) -> str:
    return f"{int(value):,}".replace(",", " ")


def _format_text(data: dict, period: str, only_accepted: bool) -> str:
    period_label = PERIOD_LABELS.get(period, period)
    filter_label = "только принятые" if only_accepted else "все заказы"
    return (
        f"📊 <b>Статистика магазина</b>\n\n"
        f"👥 Клиентов: {data['customers_count']}\n"
        f"🏷️ Активных промокодов: {data['active_promos']}\n\n"
        f"📋 <b>Отчёт — {period_label}</b> ({filter_label})\n"
        f"  • Заказов: {data['orders_count']}\n"
        f"  • Исходная сумма: {_fmt(data['original_sum'])} сум\n"
        f"  • Скидки: -{_fmt(data['total_discount'])} сум\n"
        f"  • Себестоимость: {_fmt(data['cost'])} сум\n"
        f"  • Выручка: {_fmt(data['revenue'])} сум\n"
        f"  • Прибыль: {_fmt(data['profit'])} сум"
    )


def _keyboard(period: str, only_accepted: bool) -> InlineKeyboardMarkup:
    def mark(p: str) -> str:
        return f"• {PERIOD_LABELS[p]}" if p == period else PERIOD_LABELS[p]

    acc_icon = "✅" if only_accepted else "❌"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=mark("today"),  callback_data=f"stats:today:{only_accepted}"),
            InlineKeyboardButton(text=mark("month"),  callback_data=f"stats:month:{only_accepted}"),
            InlineKeyboardButton(text=mark("all"),    callback_data=f"stats:all:{only_accepted}"),
        ],
        [
            InlineKeyboardButton(
                text=f"Только принятые: {acc_icon}",
                callback_data=f"stats:{period}:{not only_accepted}",
            )
        ],
    ])


# /start для админа — автоматическое определение режима

# Ключ в FSMContext data для хранения текущего режима
ADMIN_MODE_KEY = "admin_mode"


# Переключение режима (Админ ↔ Пользователь)

@router.message(F.text.startswith("🔄 Переключить режим"))
async def switch_mode(message: Message, state: FSMContext, shop_id: int = 1) -> None:
    """Переключить между пользовательским и админ-режимом."""
    if not await _check_admin(message.from_user.id, shop_id):
        return

    data = await state.get_data()
    current_mode = data.get(ADMIN_MODE_KEY, True)  # Если в админ-меню, значит True
    new_mode = not current_mode

    await state.update_data(**{ADMIN_MODE_KEY: new_mode})

    if new_mode:
        # Переключаемся в админ-режим
        menu = get_admin_menu()
        await message.answer(
            "🔄 <b>Режим: Администратор</b>\n\nВам доступны инструменты управления магазином.",
            reply_markup=menu,
            parse_mode="HTML",
        )
    else:
        # Переключаемся в пользовательский режим
        menu = await get_dynamic_menu(shop_id, user_id=message.from_user.id, admin_mode=False)
        await message.answer(
            "🔄 <b>Режим: Пользователь</b>\n\nМеню магазина.",
            reply_markup=menu,
            parse_mode="HTML",
        )


# Статистика

@router.message(F.text == ADMIN_STATS)
@router.message(F.text == "📊 Статистика")
@router.message(Command("stats"))
async def cmd_stats(message: Message, shop_id: int = 1) -> None:
    """Показать статистику (только для админов)."""
    if not await _check_admin(message.from_user.id, shop_id):
        return

    data = await _get_stats(shop_id, "today", False)
    await message.answer(
        _format_text(data, "today", False),
        reply_markup=_keyboard("today", False),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("stats:"))
async def stats_callback(callback: CallbackQuery, shop_id: int = 1) -> None:
    """Обработка нажатий на inline-кнопки фильтров статистики."""
    if not await _check_admin(callback.from_user.id, shop_id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, period, only_accepted_str = callback.data.split(":")
    only_accepted = only_accepted_str == "True"

    data = await _get_stats(shop_id, period, only_accepted)
    await callback.message.edit_text(
        _format_text(data, period, only_accepted),
        reply_markup=_keyboard(period, only_accepted),
        parse_mode="HTML",
    )
    await callback.answer()


# Генерация промокодов

async def _generate_unique_code(session, shop_id: int, length: int = 8) -> str:
    """Сгенерировать криптографически случайный уникальный промокод."""
    chars = string.ascii_uppercase + string.digits
    for _ in range(10):
        code = "".join(secrets.choice(chars) for _ in range(length))
        exists = await session.scalar(
            select(Promocode).where(Promocode.code == code, Promocode.shop_id == shop_id)
        )
        if not exists:
            return code
    raise RuntimeError("Failed to generate unique promo code after 10 attempts")


@router.message(F.text == ADMIN_PROMOCODES)
@router.message(Command("promocodes"))
async def cmd_promocodes(message: Message, shop_id: int = 1) -> None:
    """Показать меню промокодов."""
    if not await _check_admin(message.from_user.id, shop_id):
        return

    async with async_session_factory() as session:
        active_count = await session.scalar(
            select(func.count(Promocode.id)).where(
                Promocode.shop_id == shop_id,
                Promocode.is_active.is_(True),
            )
        ) or 0

    # Generate a temporary cabinet session token for auto-login (BAG 9)
    cab_token = generate_token()
    store_token(cab_token, {
        "user_id": 0,
        "email": f"tg_{message.from_user.id}@bot",
        "role": "owner",
        "shop_id": shop_id,
    })
    cabinet_url = f"{WEB_APP_URL.rstrip('/')}/cabinet/?token={cab_token}&shop_id={shop_id}&page=promotions"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Сгенерировать 1 промокод", callback_data="promo_gen:1")],
        [InlineKeyboardButton(text="🎲 Сгенерировать 5 промокодов", callback_data="promo_gen:5")],
        [InlineKeyboardButton(text="📋 Список активных", callback_data="promo_list")],
        [InlineKeyboardButton(text="🖥 Управлять в кабинете", web_app=WebAppInfo(url=cabinet_url))],
    ])

    await message.answer(
        f"📦 <b>Промокоды</b>\n\nАктивных промокодов: {active_count}",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("promo_gen:"))
async def generate_promocodes_start(callback: CallbackQuery, state: FSMContext, shop_id: int = 1) -> None:
    """Шаг 1: спросить тип скидки перед генерацией."""
    if not await _check_admin(callback.from_user.id, shop_id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    count = int(callback.data.split(":")[1])
    await state.update_data(promo_count=count, promo_shop_id=shop_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="% Процент от суммы", callback_data="promo_type:percent")],
        [InlineKeyboardButton(text="💰 Фиксированная сумма (сум)", callback_data="promo_type:fixed")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="promo_type:cancel")],
    ])
    await callback.message.edit_text(
        f"🎲 Генерация {count} промокод(ов)\n\nВыберите тип скидки:",
        reply_markup=kb,
    )
    await state.set_state(PromoGenStates.waiting_type)
    await callback.answer()


@router.callback_query(PromoGenStates.waiting_type, F.data.startswith("promo_type:"))
async def generate_promocodes_type(callback: CallbackQuery, state: FSMContext) -> None:
    """Шаг 2: принять тип скидки, запросить значение."""
    discount_type = callback.data.split(":")[1]

    if discount_type == "cancel":
        await state.clear()
        await callback.message.edit_text("❌ Генерация отменена.")
        await callback.answer()
        return

    await state.update_data(discount_type=discount_type)

    label = "процент (например: 15 для скидки 15%)" if discount_type == "percent" else "сумму (например: 5000 для скидки 5000 сум)"
    await callback.message.edit_text(
        f"Введите {label}:",
    )
    await state.set_state(PromoGenStates.waiting_value)
    await callback.answer()


@router.message(PromoGenStates.waiting_value)
async def generate_promocodes_value(message: Message, state: FSMContext) -> None:
    """Шаг 3: принять значение, создать промокоды."""
    data = await state.get_data()
    count = data.get("promo_count", 1)
    shop_id = data.get("promo_shop_id", 1)
    discount_type = data.get("discount_type", "percent")

    raw = (message.text or "").strip()
    try:
        value = int(float(raw))
        if value <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите положительное число. Попробуйте ещё раз:")
        return

    if discount_type == "percent" and value > 100:
        await message.answer("Процент не может быть больше 100. Попробуйте ещё раз:")
        return

    codes = []
    async with async_session_factory() as session:
        for _ in range(count):
            code = await _generate_unique_code(session, shop_id)
            promo = Promocode(
                shop_id=shop_id,
                code=code,
                value=value,
                discount_type=discount_type,
                is_active=True,
            )
            session.add(promo)
            codes.append(code)
        await session.commit()

    await state.clear()

    discount_label = f"{value}%" if discount_type == "percent" else f"{value:,} сум".replace(",", " ")
    codes_text = "\n".join(f"• <code>{c}</code>" for c in codes)
    await message.answer(
        f"✅ Сгенерировано {count} промокод(ов) (скидка {discount_label}):\n\n{codes_text}",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "promo_list")
async def list_promocodes(callback: CallbackQuery, shop_id: int = 1) -> None:
    """Показать активные промокоды."""
    if not await _check_admin(callback.from_user.id, shop_id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    async with async_session_factory() as session:
        result = await session.execute(
            select(Promocode)
            .where(Promocode.shop_id == shop_id, Promocode.is_active.is_(True))
            .limit(20)
        )
        promos = result.scalars().all()

    if not promos:
        await callback.message.edit_text("Нет активных промокодов.")
        await callback.answer()
        return

    lines = []
    for p in promos:
        if p.discount_type == "percent":
            discount = f"{p.value}%"
        else:
            discount = f"{_fmt(float(p.value or 0))} сум"
        lines.append(f"• <code>{p.code}</code> — {discount}")

    await callback.message.edit_text(
        f"📋 <b>Активные промокоды</b> ({len(promos)}):\n\n" + "\n".join(lines),
        parse_mode="HTML",
    )
    await callback.answer()


# Рассылка

@router.message(F.text == ADMIN_BROADCAST)
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext, shop_id: int = 1) -> None:
    """Начать рассылку."""
    if not await _check_admin(message.from_user.id, shop_id):
        return

    # Получаем количество пользователей бота
    async with async_session_factory() as session:
        users_count = await session.scalar(
            select(func.count(BotUser.id)).where(BotUser.shop_id == shop_id)
        ) or 0

    await state.update_data(broadcast_shop_id=shop_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Написать сообщение", callback_data="broadcast_compose")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")],
    ])

    await message.answer(
        f"📢 <b>Рассылка</b>\n\n"
        f"Пользователей бота: {users_count}\n\n"
        f"Отправьте текст сообщения для рассылки всем пользователям.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена рассылки."""
    await callback.message.edit_text("❌ Рассылка отменена.")
    await callback.answer()


@router.callback_query(F.data == "broadcast_compose")
async def broadcast_compose(callback: CallbackQuery, state: FSMContext) -> None:
    """Составление рассылки.

    Отправка рассылки из бота ещё не реализована (состояние "broadcast_text"
    ранее ставилось, но не обрабатывалось — сообщение никуда не уходило).
    Рабочая рассылка доступна в веб-кабинете (routers/admin_broadcast.py).
    Здесь честно сообщаем об этом вместо тупика.
    """
    await state.clear()
    await callback.message.edit_text(
        "📢 Рассылка по клиентам доступна в веб-кабинете магазина "
        "(раздел «Рассылки»). Отправка прямо из бота скоро появится."
    )
    await callback.answer()


# Обработка заказов — кнопки в уведомлении администратора

async def _bg_sync_azma_and_notify(order_id: int, shop_id: int, bot, customer_tg_id: int | None):
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Order).options(selectinload(Order.items)).where(Order.id == order_id, Order.shop_id == shop_id)
            )
            order = result.scalar_one_or_none()
            if not order:
                return

            res = await accounting_provider.sync_order(order, order.items)
            if res.get("ok"):
                order.sync_status = "synced"
                order.external_id = res.get("external_id")
                order.fiscal_url = res.get("fiscal_url")
                await session.commit()
                
                # Отправить сообщение пользователю, если есть telegram_id
                if customer_tg_id and order.fiscal_url:
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🧾 Электронный чек", url=order.fiscal_url)
                    ]])
                    try:
                        await bot.send_message(
                            chat_id=customer_tg_id,
                            text=f"✅ Ваш заказ #{order.id} принят!\n\nЭлектронный чек сформирован:",
                            reply_markup=kb
                        )
                    except Exception as e:
                        log.error(f"Не удалось отправить чек покупателю: {e}")
            else:
                order.sync_status = "error"
                await session.commit()
    except Exception as e:
        log.error(f"Ошибка при фоновой синхронизации AZMA: {e}")

@router.callback_query(F.data.startswith("accept_order:"))
async def accept_order_callback(callback: CallbackQuery, shop_id: int = 1) -> None:
    """Кнопка 'Заказ принят' — обновляет статус заказа на accepted."""
    if not await _check_admin(callback.from_user.id, shop_id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])

    async with async_session_factory() as session:
        result = await session.execute(
            select(Order).where(Order.id == order_id, Order.shop_id == shop_id)
        )
        order = result.scalar_one_or_none()

        if not order:
            await callback.answer("Заказ не найден", show_alert=True)
            return

        if order.status == "accepted":
            await callback.answer("Заказ уже принят", show_alert=False)
            return

        order.status = "accepted"
        await session.commit()

    # Редактируем сообщение — убираем кнопки, добавляем отметку
    try:
        original_text = callback.message.text or callback.message.caption or ""
        await callback.message.edit_text(
            original_text + "\n\n✅ <b>Заказ принят</b>",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        pass  # Если сообщение нельзя отредактировать — игнорируем

    # Запускаем фоновую синхронизацию с AZMA
    customer_tg_id = None
    if order.meta and "telegram_user_id" in order.meta:
        customer_tg_id = order.meta["telegram_user_id"]
    
    asyncio.create_task(_bg_sync_azma_and_notify(
        order_id=order.id, shop_id=shop_id, bot=callback.bot, customer_tg_id=customer_tg_id
    ))

    await callback.answer("✅ Заказ принят и записан в статистику!")


@router.callback_query(F.data.startswith("contact_client:"))
async def contact_client_callback(callback: CallbackQuery, shop_id: int = 1) -> None:
    """Кнопка 'Связаться' — выводит профиль клиента."""
    if not await _check_admin(callback.from_user.id, shop_id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])

    async with async_session_factory() as session:
        result = await session.execute(
            select(Order).where(Order.id == order_id, Order.shop_id == shop_id)
        )
        order = result.scalar_one_or_none()

        if not order:
            await callback.answer("Заказ не найден", show_alert=True)
            return

        # Пробуем найти BotUser по telegram_id из meta
        customer_tg_id = order.meta.get("telegram_user_id") if order.meta else None
        bot_user = None
        if customer_tg_id:
            bu_result = await session.execute(
                select(BotUser).where(
                    BotUser.telegram_id == customer_tg_id,
                    BotUser.shop_id == shop_id,
                )
            )
            bot_user = bu_result.scalar_one_or_none()

    # Формируем текст профиля
    name = (order.meta or {}).get("customer_name") or "—"
    phone = (order.meta or {}).get("customer_phone") or "—"

    if bot_user and bot_user.is_registered:
        username = ""
        if customer_tg_id:
            # Попытка получить username — из Customer
            async with async_session_factory() as session:
                cust_result = await session.execute(
                    select(Customer).where(
                        Customer.telegram_id == customer_tg_id,
                        Customer.shop_id == shop_id,
                    )
                )
                customer = cust_result.scalar_one_or_none()
                if customer and customer.username:
                    username = f"@{customer.username}"

        has_location = bool(bot_user.location_lat and bot_user.location_lon)

        text = (
            f"👤 <b>Профиль клиента (заказ #{order_id}):</b>\n"
            f"👤 Имя: {bot_user.name or name}\n"
            f"📞 Телефон: {bot_user.phone or phone}\n"
            f"💬 Юзернейм: {username or '—'}"
        )
    else:
        has_location = False
        # Незарегистрированный — данные из meta заказа
        text = (
            f"👤 <b>Профиль клиента (заказ #{order_id}):</b>\n"
            f"👤 Имя: {name}\n"
            f"📞 Телефон: {phone}\n"
            f"💬 Telegram ID: {customer_tg_id or '—'}\n"
            f"⚠️ Пользователь не зарегистрирован в боте"
        )

    await callback.message.answer(text, parse_mode="HTML")
    if has_location and bot_user:
        await callback.message.answer_location(
            float(bot_user.location_lat), float(bot_user.location_lon)
        )
    await callback.answer()
