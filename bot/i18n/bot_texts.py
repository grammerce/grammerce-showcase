"""
Тексты бота для всех поддерживаемых языков.
Использование: BOT_TEXTS[lang]['ключ']
"""

BOT_TEXTS = {
    "ru": {
        # Выбор языка
        "choose_language": "🌐 Выберите язык / Tilni tanlang / Choose language:",
        "language_selected": "🇷🇺 Язык установлен: Русский",

        # Приветствие
        "welcome_default": "👋 Добро пожаловать!\n\nВыберите действие в меню ниже 👇",

        # Регистрация
        "registration_disabled": "Регистрация временно недоступна.",
        "already_registered": "Вы уже зарегистрированы!",
        "reg_welcome": "Давайте зарегистрируемся!",
        "ask_name": "Введите ваше имя:",
        "ask_phone": "Отправьте ваш номер телефона:",
        "ask_location": "Отправьте вашу геолокацию:",
        "name_error": "Пожалуйста, введите корректное имя",
        "phone_error": "Пожалуйста, введите корректный номер телефона",
        "location_unknown": "Пожалуйста, отправьте геолокацию через кнопку или нажмите 'Пропустить'.",
        "reg_success": "✅ Регистрация завершена!\n\nВам начислена скидка {discount} на первый заказ!",
        "reg_cancelled": "Регистрация отменена. Вы можете начать заново в любое время.",

        # Кнопки
        "btn_send_contact": "📱 Отправить контакт",
        "btn_send_location": "📍 Отправить местоположение",
        "btn_skip": "⏭ Пропустить",
        "btn_cancel": "❌ Отмена",
        "main_menu": "Главное меню:",

        # Магазин
        "open_shop": "Откройте магазин прямо сейчас 👇",
        "open_shop_btn": "🛍 Открыть магазин",

        # Профиль
        "my_profile": "👤 <b>Мой профиль</b>\n\nИмя: {name}\nТелефон: {phone}\nСкидка: {discount}",
        "no_profile": "Профиль не найден. Пройдите регистрацию.",
        "profile_disabled": "Личный кабинет временно недоступен.",
        "not_registered": "Вы еще не зарегистрированы.\nНажмите кнопку ниже для регистрации.",
        "want_to_register": "Хотите зарегистрироваться?",
        "btn_register": "📝 Зарегистрироваться",
        "btn_edit_name": "✏️ Изменить имя",
        "btn_edit_phone": "📱 Изменить телефон",
        "btn_edit_location": "📍 Изменить геолокацию",
        "btn_change_language": "🌐 Сменить язык",
        "update_data": "Можно обновить данные:",
        "one_language_only": "Магазин работает только на одном языке.",
        "location_not_set": "не указан",

        # Промо
        "promo_disabled": "Акции временно недоступны.",
        "promo_not_registered": "Сначала пройдите регистрацию, чтобы использовать промокоды.",
        "promo_enter_code": "Или введите промокод прямо здесь:",

        # Заказы
        "no_orders": "У вас ещё нет заказов.",
        "order_header": "📦 Заказ #{id}",
    },

    "uz": {
        # Выбор языка
        "choose_language": "🌐 Выберите язык / Tilni tanlang / Choose language:",
        "language_selected": "🇺🇿 Til tanlandi: O'zbek tili",

        # Приветствие
        "welcome_default": "👋 Xush kelibsiz!\n\nQuyidagi menyudan harakat tanlang 👇",

        # Регистрация
        "registration_disabled": "Ro'yxatdan o'tish vaqtincha mavjud emas.",
        "already_registered": "Siz allaqachon ro'yxatdan o'tgansiz!",
        "reg_welcome": "Keling, ro'yxatdan o'taylik!",
        "ask_name": "Ismingizni kiriting:",
        "ask_phone": "Telefon raqamingizni yuboring:",
        "ask_location": "Joylashuvingizni yuboring:",
        "name_error": "Iltimos, to'g'ri ism kiriting",
        "phone_error": "Iltimos, to'g'ri telefon raqam kiriting",
        "location_unknown": "Iltimos, joylashuvni tugma orqali yuboring yoki 'O'tkazib yuborish' tugmasini bosing.",
        "reg_success": "✅ Ro'yxatdan o'tish yakunlandi!\n\nBirinchi buyurtmangizga {discount} chegirma berildi!",
        "reg_cancelled": "Ro'yxatdan o'tish bekor qilindi. Istalgan vaqtda qayta boshlashingiz mumkin.",

        # Кнопки
        "btn_send_contact": "📱 Kontakt yuborish",
        "btn_send_location": "📍 Joylashuv yuborish",
        "btn_skip": "⏭ O'tkazib yuborish",
        "btn_cancel": "❌ Bekor qilish",
        "main_menu": "Asosiy menyu:",

        # Магазин
        "open_shop": "Do'konni hoziroq oching 👇",
        "open_shop_btn": "🛍 Do'konni ochish",

        # Профиль
        "my_profile": "👤 <b>Mening profilim</b>\n\nIsm: {name}\nTelefon: {phone}\nChegirma: {discount}",
        "no_profile": "Profil topilmadi. Ro'yxatdan o'ting.",
        "profile_disabled": "Shaxsiy kabinet vaqtincha mavjud emas.",
        "not_registered": "Siz hali ro'yxatdan o'tmagansiz.\nRo'yxatdan o'tish uchun quyidagi tugmani bosing.",
        "want_to_register": "Ro'yxatdan o'tmoqchimisiz?",
        "btn_register": "📝 Ro'yxatdan o'tish",
        "btn_edit_name": "✏️ Ismni o'zgartirish",
        "btn_edit_phone": "📱 Telefonni o'zgartirish",
        "btn_edit_location": "📍 Joylashuvni o'zgartirish",
        "btn_change_language": "🌐 Tilni o'zgartirish",
        "update_data": "Ma'lumotlarni yangilash mumkin:",
        "one_language_only": "Do'kon faqat bitta tilda ishlaydi.",
        "location_not_set": "ko'rsatilmagan",

        # Промо
        "promo_disabled": "Aksiyalar vaqtincha mavjud emas.",
        "promo_not_registered": "Promokodlarni ishlatish uchun avval ro'yxatdan o'ting.",
        "promo_enter_code": "Yoki promokodni shu yerda kiriting:",

        # Заказы
        "no_orders": "Sizda hali buyurtmalar yo'q.",
        "order_header": "📦 Buyurtma #{id}",
    },

    "en": {
        # Выбор языка
        "choose_language": "🌐 Выберите язык / Tilni tanlang / Choose language:",
        "language_selected": "🇬🇧 Language set: English",

        # Приветствие
        "welcome_default": "👋 Welcome!\n\nChoose an action from the menu below 👇",

        # Регистрация
        "registration_disabled": "Registration is temporarily unavailable.",
        "already_registered": "You are already registered!",
        "reg_welcome": "Let's register!",
        "ask_name": "Enter your name:",
        "ask_phone": "Send your phone number:",
        "ask_location": "Send your location:",
        "name_error": "Please enter a valid name",
        "phone_error": "Please enter a valid phone number",
        "location_unknown": "Please send your location via the button or tap 'Skip'.",
        "reg_success": "✅ Registration complete!\n\nYou've received a {discount} discount on your first order!",
        "reg_cancelled": "Registration cancelled. You can restart at any time.",

        # Кнопки
        "btn_send_contact": "📱 Send contact",
        "btn_send_location": "📍 Send location",
        "btn_skip": "⏭ Skip",
        "btn_cancel": "❌ Cancel",
        "main_menu": "Main menu:",

        # Магазин
        "open_shop": "Open the shop right now 👇",
        "open_shop_btn": "🛍 Open shop",

        # Профиль
        "my_profile": "👤 <b>My profile</b>\n\nName: {name}\nPhone: {phone}\nDiscount: {discount}",
        "no_profile": "Profile not found. Please register.",
        "profile_disabled": "Personal account is temporarily unavailable.",
        "not_registered": "You are not registered yet.\nTap the button below to register.",
        "want_to_register": "Would you like to register?",
        "btn_register": "📝 Register",
        "btn_edit_name": "✏️ Edit name",
        "btn_edit_phone": "📱 Edit phone",
        "btn_edit_location": "📍 Edit location",
        "btn_change_language": "🌐 Change language",
        "update_data": "You can update your info:",
        "one_language_only": "The shop operates in one language only.",
        "location_not_set": "not set",

        # Промо
        "promo_disabled": "Promotions are temporarily unavailable.",
        "promo_not_registered": "Please register first to use promo codes.",
        "promo_enter_code": "Or enter a promo code right here:",

        # Заказы
        "no_orders": "You have no orders yet.",
        "order_header": "📦 Order #{id}",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    """
    Получить текст бота на нужном языке.
    Fallback: ru.
    """
    texts = BOT_TEXTS.get(lang) or BOT_TEXTS["ru"]
    fallback = BOT_TEXTS["ru"]
    text = texts.get(key) or fallback.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text
