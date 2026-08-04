# 05. База данных

PostgreSQL с асинхронным SQLAlchemy 2.0. Всего **30 таблиц** (29 в [models.py](../models.py) + 1 в [payments/models.py](../payments/models.py)) и **48 SQL-миграций** (номера `002`–`046` и `048`, два номера задвоены) в [migrations/](../migrations/).

## Содержание

1. [Подключение и пул](#подключение-и-пул)
2. [Соглашения и правила](#соглашения-и-правила)
3. [Все таблицы](#все-таблицы)
4. [Схема связей](#схема-связей)
5. [Миграции](#миграции)
6. [Доступ к БД](#доступ-к-бд)

---

## Подключение и пул

[database.py](../database.py):
- `engine = create_async_engine(DATABASE_URL, pool_size=30, max_overflow=70, pool_recycle=3600)` — рассчитан на 100+ магазинов параллельно.
- `async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)`.
- Dependency `get_db()` для FastAPI-роутеров.
- Если `DATABASE_URL` не задан — fallback на SQLite-файл (для локальной разработки).
- `Base = declarative_base()` — общая база для всех моделей.

---

## Соглашения и правила

1. **Сквозной `shop_id`.** Все бизнес-сущности имеют FK `shop_id` с `ondelete="CASCADE"`. Это фундамент мульти-тенантности.
2. **Никакого `DROP COLUMN`.** Только расширение схемы: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Удалённое поле сначала устаревает (документируется), потом просто не используется кодом, но физически остаётся.
3. **Идемпотентность миграций.** Все миграции должны можно безопасно запускать повторно (используются `IF NOT EXISTS`, `ON CONFLICT DO NOTHING`, проверки на существование).
4. **Денежные суммы — целые UZS.** Никогда `float`. Тип в БД — `NUMERIC(12, 2)` или `INTEGER` (зависит от поля).
5. **Снимок цены в `order_items`.** Цена фиксируется в `order_items.price_at_moment` при создании заказа, выбранные вариации — в `order_items.selected_options`. Изменение товара в каталоге задним числом не меняет старые заказы; `product_id` при удалении товара становится `NULL`, история не рушится.
6. **Уникальная пара `(shop_id, telegram_id)`** для покупателей: `customers`, `bot_users`, `telegram_profiles`. Один Telegram-юзер у разных магазинов = разные записи.
7. **JSON-поля для гибкости:** `bot_settings.*` (6 ключей-кнопок), `shops.config` (тема, валюта, `view_mode`, `navShowLabels`, `showcase`), `shops.integration_settings` (POS), `products.variants`, `order_items.selected_options`, `orders.meta` (в т.ч. гео доставки), `promocodes.product_ids`, `shop_members.permissions`, `broadcast_logs.filters`, `onboarding_events.payload`, `ai_card_examples.title/bullets`.
8. **Картинки — файлами, не в БД.** Новые изображения товаров сохраняются в `media/products/` через [utils/product_images.py](../utils/product_images.py); в колонке лежит путь. Base64 остался только в исторических записях.

---

## Все таблицы

### Группа 1. Пользователи и доступ

#### `users` ([models.py:26](../models.py#L26))
Владельцы магазинов и сотрудники команд.
- `id` PK, `email` UNIQUE, `password_hash` (bcrypt), `name`, `created_at`.
- OAuth-поля: `google_id`, `apple_id`, `telegram_id` (миграция 016), `tg_username` — Telegram @handle (миграция 041).
- Связи: `1:N → shops` (как owner), `1:N → shop_members`.

#### `bot_auth_tokens` ([models.py:71](../models.py#L71))
Одноразовые токены для deeplink-регистрации через маркет-бот платформы.
- `id` PK, `token` UNIQUE, `telegram_id`, `tg_username`, `created_at`, `used_at`, `expires_at`.
- `tg_lang` — язык интерфейса (`ru`/`uz`), бот передаёт его в `issue`, платформа прокидывает в redirect → кабинет открывается на нужном языке (миграция 037).
- Создаётся при `POST /api/auth/telegram/issue` от платформенного бота с shared-secret.
- Миграция: 031, 037.

#### `offer_acceptances` ([models.py:88](../models.py#L88))
Логирование акцептов публичной оферты.
- `id` PK, `user_id` FK → `users`, `accepted_at`, `ip_address`, `user_agent`, `offer_version`.
- Миграция: 021.

#### `shop_members` ([models.py:858](../models.py#L858))
Связь user ↔ shop с ролью (RBAC).
- `id` PK, `shop_id` FK, `user_id` FK, UNIQUE `(shop_id, user_id)`.
- `role`: `owner | shop_admin | marketer`.
- `permissions` JSON — конкретные пермишены, override от роли.
- Индекс: `idx_shop_members_shop` `(shop_id)`.
- Миграция: 018.

#### `approval_requests` ([models.py:896](../models.py#L896))
Очередь критических действий, требующих одобрения суперадмина.
- `id` PK, `shop_id` FK, `user_id` FK (кто запросил), `action`, `payload` JSON, `status` (`pending | approved | rejected`), `decided_by`, `decided_at`, `note`.
- Примеры actions: `change_bot_token`, `delete_product_dropship`, `send_broadcast_mass`.
- Миграция: 018.

### Группа 1б. Платформенный уровень

Отдельный от магазинов контур: сотрудники Grammerce, а не владельцы. Подробнее о ролях — [01_overview.md](01_overview.md) → RBAC.

#### `platform_users` ([models.py:997](../models.py#L997))
Суперадмины и админы платформы. Отделены от `users` (владельцы / OAuth).
- `id` PK, `login` UNIQUE (индекс), `role` (`superadmin | admin`), `is_active`.
- `password_hash` **nullable**: у суперадмина пароля нет — он входит только через Telegram. Штатные `admin` пароль сохраняют (bcrypt).
- `telegram_id` UNIQUE (частичный индекс, NULL-ы не конфликтуют) — личный Telegram-аккаунт суперадмина. **Доступ выдаётся только если тот же id есть и в env `SUPERADMIN_TELEGRAM_IDS`**: одной записи в БД недостаточно, поэтому утечка базы прав не даёт. Миграция 048.
- `token_version` — инкрементируется при смене пароля или деактивации; в JWT кладётся `pwd_v`, поэтому все ранее выданные Bearer-токены разом становятся невалидными.
- `created_by_id` FK → `platform_users.id` (`ON DELETE SET NULL`), `created_at`, `updated_at`.
- Записи суперадминов создаются автоматически при старте по `SUPERADMIN_TELEGRAM_IDS` (`seed_platform_superadmin`, идемпотентно) с `login='tg:<id>'`.
- Миграции: 034, 048.

#### `platform_admin_shops` ([models.py:1032](../models.py#L1032))
M2M «какой админ какие магазины ведёт». `admin` видит только назначенные магазины, чужой магазин отдаёт 404 (не 403 — чтобы не раскрывать существование).
- `id` PK, `admin_id` FK → `platform_users`, `shop_id` FK → `shops`, UNIQUE `(admin_id, shop_id)`.
- `assigned_by_id` FK → `platform_users` (`SET NULL`), `assigned_at`.
- Индексы: `ix_platform_admin_shops_admin_id`, `ix_platform_admin_shops_shop_id`.
- Миграция: 034.

#### `platform_support_messages` ([models.py:1061](../models.py#L1061))
Чат «владелец магазина ↔ платформенный админ». **Не путать с `support_messages`** — там чат «покупатель ↔ владелец». Один thread на `shop_id`.
- `id` PK, `shop_id` FK, `sender` (`owner | platform`), `sender_user_id` (id из `users` или `platform_users` — в зависимости от `sender`), `text`, `is_read`, `meta` JSON, `created_at`.
- Сюда же падают заявки «под ключ» из кабинета и с лендинга (`sender="owner"`).
- Индексы: `idx_psm_shop_created`, `idx_psm_shop_unread`.
- Миграция: 036.

### Группа 2. Магазины

#### `shops` ([models.py:108](../models.py#L108))
Корневая сущность мульти-тенантности.
- `id` PK, `user_id` FK → `users` (owner), `name`, `slug`, `bot_token`, `bot_username`, `owner_tg_id`.
- `config` JSON — тема, цвета, `view_mode` (зависит от ниши: одежда, цветы, рестораны, ...), `navShowLabels` (подписи нижнего меню витрины), `showcase.enabled` (публичная витрина-кейс для `/examples`, миграция 042), `bot_username` (проставляется через getMe, см. [services/bot_identity.py](../services/bot_identity.py)).
- `integration_settings` JSON — POS-провайдер и его ключи.
- `is_dropshipping` BOOL, `prepayment_percent` INT (миграция 032).
- `payer_type` — тип плательщика за тариф: физлицо / организация. Физлицо платит через Click без реквизитов организации (миграция 038).
- `is_active`, `created_at`, `updated_at`.
- Служебный скрытый магазин-приёмник заявок «под ключ» с лендинга заводится миграцией 043.

### Группа 3. Каталог

#### `categories` ([models.py:225](../models.py#L225))
Категории товаров с поддержкой иерархии.
- `id` PK, `shop_id` FK, `name`, `name_i18n` JSON, `description_i18n` JSON, `parent_id` FK → `categories.id` (self-reference).
- `sort_order`, `is_active`.
- Миграция 015 — i18n-поля.

#### `products` ([models.py:259](../models.py#L259))
Товары магазина.
- `id` PK, `shop_id` FK, `category_id` FK, `name`, `description`, `price` NUMERIC, `old_price` NUMERIC, `sku`, `sort_order`.
- `name_i18n` JSON, `description_i18n` JSON.
- `image_url`, `images` JSON (до 5 изображений — миграция 003), `video_url`. **С июля 2026 хранятся пути к файлам в `media/products/{shop_id}/`, а не base64** — см. [utils/product_images.py](../utils/product_images.py). Старые base64-значения продолжают работать; разовый перенос — скриптом из [scripts/](../scripts/).
- `variants` JSON — оси вариаций (размер, цвет, объём) и их значения. Ось с единственным значением витрина показывает как характеристику, а не как выбор.
- POS: `external_id`, `sync_status`, `last_synced_at` (миграция 011).
- Дропшиппинг: `delivery_time_text`, `availability_status` (`in_stock | preorder | out_of_stock`) — миграция 032.
- `is_active`, `created_at`, `updated_at`.
- Индексы: `idx_products_shop_active (shop_id, is_active)`.

### Группа 4. Покупатели

#### `customers` ([models.py:310](../models.py#L310))
Покупатели магазина (создаются при первом заказе).
- `id` PK, `shop_id` FK, `telegram_id`, `name`, `phone` (миграция 005), `created_at`.
- UNIQUE `(shop_id, telegram_id)`.

#### `bot_users` ([models.py:488](../models.py#L488))
Зарегистрированные через бот пользователи (FSM-флоу: имя → телефон → гео → скидка 20%).
- `id` PK, `shop_id` FK, `tg_id`, `name`, `phone`, `latitude`, `longitude`, `discount_registration` (например, `"20%"`), `is_registered` BOOL, `registered_at`.
- UNIQUE `(shop_id, tg_id)`.

#### `telegram_profiles` ([models.py:534](../models.py#L534))
Маркетинговый слой поверх `bot_users` — кешированные метрики и состояние воронки.
- `id` PK, `shop_id` FK, `tg_id`, UNIQUE `(tg_id, shop_id)`.
- `cached_orders_count`, `cached_ltv` (lifetime value), `cached_avg_check`.
- `last_funnel_step`, `last_active_at`.
- `broadcasts_received` INT.
- Миграция 009.

### Группа 5. Заказы

#### `orders` ([models.py:337](../models.py#L337))
Заказы покупателей.
- `id` PK, `shop_id` FK, `customer_id` FK → `customers`.
- `number` INT — **порядковый номер в пределах магазина** (#1, #2, …), его видит покупатель. Глобальный `id` остаётся для внутренних ссылок. Миграция 040 (с бэкофиллом существующих заказов).
- `meta` JSON — служебные данные заказа, в т.ч. координаты точки доставки (`lat`/`lng`), которые бот отправляет админу через `send_venue`. Отдельной миграции под гео нет — координаты живут в `meta`.
- `status`: `new | accepted | paid | shipped | delivered | cancelled`.
- `total_amount`, `original_total`, `discount`, `cost` (миграция 004).
- `prepaid_amount` (для дропшипинга).
- `order_uuid` UNIQUE — внешний идентификатор для ссылок и платёжных шлюзов.
- Контактные данные и адрес доставки, метод оплаты, применённый промокод **отдельными колонками не хранятся** — они лежат в `meta` JSON либо берутся из связанного `customers`.
- POS-синхронизация: `external_order_id`, `external_status`, `integration_status`, `sync_status`.
- AZMA fields: `external_id`, `fiscal_url` (миграция 017).
- `created_at`, `updated_at`.
- Индексы: `idx_orders_shop_status (shop_id, status)`, `idx_orders_shop_created (shop_id, created_at)`.

#### `order_items` ([models.py:390](../models.py#L390))
Позиции заказа (снимок товара на момент покупки).
- `id` PK, `order_id` FK, `product_id` FK (`ON DELETE SET NULL` — удаление товара не рушит историю заказов).
- `price_at_moment` NUMERIC — **цена зафиксирована при создании заказа**, изменение каталога не меняет старые заказы.
- `quantity` INT.
- `selected_options` JSON — выбранные значения вариаций (`{"Размер": "M", "Цвет": "чёрный"}`), берутся из `products.variants`.

#### `payments` ([payments/models.py:18](../payments/models.py#L18))
Платежи через шлюзы (Click / Payme / Uzum / Mock).
- `id` PK, `order_id` FK, `shop_id` FK.
- `provider` (`click | payme | uzum | mock`), `external_id`, `status` (`pending | paid | failed | cancelled`).
- `amount`, `created_at`, `updated_at`, `paid_at`.
- Миграция 008.

### Группа 6. Бот

#### `bot_settings` ([models.py:448](../models.py#L448))
Конфиг бота для магазина — 6 кнопок главного меню.
- `id` PK, `shop_id` FK UNIQUE.
- 6 JSON-полей: `registration`, `profile`, `promo`, `contact`, `shop_webapp`, `main_menu` — каждое содержит конфигурацию своей кнопки (текст, действие, параметры).

#### `bot_admins` ([models.py:792](../models.py#L792))
Дополнительные администраторы магазина с доступом к админ-командам в боте.
- `id` PK, `shop_id` FK, `tg_id`, UNIQUE `(shop_id, tg_id)`.
- `invite_token`, `invited_by`, `invited_at`, `accepted_at`.
- Индекс: `idx_bot_admins_shop (shop_id)`.

### Группа 7. Маркетинг

#### `promocodes` ([models.py:411](../models.py#L411))
Промокоды.
- `id` PK, `shop_id` FK, `code` UNIQUE с shop_id (`uq_promo_shop_code`).
- `discount_type`: `fixed | percent`, `value` INT (сумма скидки в UZS либо проценты).
- `usage_count`, `max_usage` (миграция 006).
- `apply_to`: `all | selected` (миграция 007), `product_ids` JSON — список ID товаров при `selected`.
- `used_by` BIGINT — **telegram_id того, КОМУ промокод выдан**, а не того, кто его уже потратил. Личный промокод, выданный владельцу магазина, поэтому применяется им самим.
- `is_active`, `expires_at`, `created_at`.
- Демо-промокод `DEMO100K` для демо-магазинов (`max_usage = NULL` → без общего лимита, каждый пользователь применяет один раз) — миграция 035.

#### `broadcast_logs` ([models.py:825](../models.py#L825))
История массовых рассылок.
- `id` PK, `shop_id` FK, `promo_id` FK → `promocodes` (опционально).
- `filters` JSON — критерии сегментации.
- `text`, `total_sent`, `total_delivered`, `total_failed`.
- `created_at`, `updated_at` (миграция 033).

#### `support_messages` ([models.py:940](../models.py#L940))
Чат поддержки между покупателем и админом магазина.
- `id` PK, `shop_id` FK, `customer_tg_id`, `sender` (`customer | admin`), `text`, `is_read`, `created_at`.
- Миграция 020.

#### `onboarding_events` ([models.py:968](../models.py#L968))
Аналитика онбординга владельца.
- `id` PK, `shop_id` FK, `event_name`, `payload` JSON, `created_at`.
- Миграция 022 (`022_onboarding.sql`).

### Группа 8. Биллинг

#### `plans` ([models.py:615](../models.py#L615))
Тарифные планы платформы (Start / Pro / Enterprise).
- `id` PK, `slug`, `name`, `price_monthly`, `price_yearly`, `setup_fee_amount` (миграции 024, 026, **039**).
- ⚠️ Актуальный setup fee — **3 900 000 UZS** (миграция 039 вернула значение после того, как 026 понизила его до 3 600 000). Не ориентируйся на 026.
- `max_products`, `max_bots`, `pos_integration` BOOL, `broadcasts_quota`.
- `invoice_type` (`with_vat | no_vat`).
- Миграция 012, 025.

#### `subscriptions` ([models.py:646](../models.py#L646))
Подписка магазина на план.
- `id` PK, `shop_id` FK, `plan_id` FK.
- `status`: `trial | active | past_due | suspended | cancelled`.
- `current_period_start`, `current_period_end`.
- `billing_cycle` (`monthly | yearly` — миграция 024).
- `setup_fee_paid` BOOL (миграция 030 для демо-магазинов).

#### `invoices` ([models.py:710](../models.py#L710))
Счета B2B (PDF + Didox ЭСФ).
- `id` PK, `subscription_id` FK, `shop_id` FK, `number` (`INV-2026-NNNNNN`).
- `invoice_type` (`setup_fee | subscription`).
- `amount`, `vat_amount`, `total`.
- `discount_amount` INT — скидка на счёт (например, по реферальной ссылке). Формула: `total = amount − discount_amount + vat(amount − discount_amount)`. Миграция 046.
- `seller_details` JSON, `buyer_details` JSON.
- `status`: `draft | issued | paid | cancelled`.
- `pdf_path` (физический файл в [media/invoices/](../media/invoices/)).
- `didox_document_id` — ID документа в Didox после отправки.
- Миграция 022 (`022_didox_integration.sql`), 027, 046 (`discount_amount`).

### Группа 9. AI-карточка товара

Модуль «AI Карточка товара»: генерация текста и подготовка фото под формат маркетплейса. Спецификация — [md_s/grammerce_ai_card_spec.md](../md_s/grammerce_ai_card_spec.md). По умолчанию всё работает на моках (`AI_CARD_MOCK_MODE=true`).

#### `ai_card_examples` ([models.py:1093](../models.py#L1093))
Библиотека примеров для few-shot / RAG-генерации текста.
- `id` PK, `shop_id` FK **nullable** — `NULL` означает глобальный seed-пример, заданный `shop_id` — одобренный пример конкретного магазина.
- `category`, `title` JSON (`{"ru": ..., "uz": ...}`), `description`, `bullets` JSON, `source` (`seed | approved`), `created_at`.
- Индекс: `ix_ai_card_examples_category`.
- Миграция: 044.

#### `ai_card_generations` ([models.py:1122](../models.py#L1122))
Журнал генераций: что предложил AI и что в итоге сохранил пользователь.
- `id` PK, `shop_id` FK, `product_id` (nullable — товар мог ещё не сохраниться), `category`.
- `ai_title` / `ai_description` — предложение AI; `final_title` / `final_description` — что сохранили.
- `edited` — были ли правки; `accepted` — сохранено без правок (кандидат в `ai_card_examples`).
- `image_before_hash` / `image_after_hash` — sha256, а не base64: журнал не раздувает БД.
- `rating` SMALLINT — явная оценка: `+1` лайк, `-1` дизлайк, `NULL` нет оценки. **Дизлайкнутый текст не пополняет RAG-библиотеку** (миграция 045).
- Индекс: `ix_ai_card_generations_shop`.
- Миграция: 044, 045.

### Группа 10. Рефералы

#### `referral_links` ([models.py:1157](../models.py#L1157))
Именованные реферальные ссылки на бота платформы (создаёт суперадмин). Формат: `t.me/<bot>?start=ref_<code>`.
- `id` PK, `code` UNIQUE, `label`, `discount_type` (`fixed` — UZS, `percent` — 0..100), `discount_value`.
- `is_active`, `max_conversions` (`NULL` = без лимита), `created_at`.
- Миграция: 046.

#### `referral_visits` ([models.py:1178](../models.py#L1178))
Заходы по ссылке; UNIQUE `(link_id, telegram_id)` — повторный клик того же человека не плодит записи.
- `id` PK, `link_id` FK → `referral_links` (CASCADE), `telegram_id` BIGINT, `username`, `first_name`.
- `converted_shop_id` FK → `shops` (`SET NULL`), `converted_at` — заполняются, когда пришедший создал магазин и оформил заявку «под ключ» со скидкой.
- Индексы: `ix_referral_visits_link`, `ix_referral_visits_tg`.
- Миграция: 046.

### Группа 11. Заявки «под ключ» ⚠️ только в ветке

> **`setup_requests` — в ветке `setupFeeRepair`, в `dev` её НЕТ.** Миграция `047_setup_requests.sql` на prod **не применена**. Таблица хранит анкету и стадии реального трека «под ключ» (экран админа + пуши). Не считай её существующей при работе с `dev`; модель — в [models.py](../models.py) той же ветки, тесты — `tests/test_setup_requests.py`.

---

## Схема связей

```
users (владельцы)
  │
  ├─ 1:N ─ shops (магазин — корневая сущность)
  │           │
  │           ├─ 1:N ─ categories (parent_id self-ref)
  │           ├─ 1:N ─ products ──► category_id
  │           │
  │           ├─ 1:N ─ customers
  │           │           └─ 1:N ─ orders
  │           │                       ├─ 1:N ─ order_items ──► products
  │           │                       └─ 1:N ─ payments
  │           │
  │           ├─ 1:N ─ bot_users  ┐
  │           ├─ 1:N ─ telegram_profiles  ─ (UNIQUE shop_id+tg_id)
  │           ├─ 1:N ─ bot_admins ┘
  │           │
  │           ├─ 1:1 ─ bot_settings (UNIQUE shop_id)
  │           │
  │           ├─ 1:N ─ promocodes
  │           ├─ 1:N ─ broadcast_logs ──► promocodes (optional)
  │           ├─ 1:N ─ support_messages          (покупатель ↔ владелец)
  │           ├─ 1:N ─ platform_support_messages (владелец ↔ платформа)
  │           ├─ 1:N ─ onboarding_events
  │           │
  │           ├─ 1:N ─ ai_card_examples    (shop_id NULL = глобальный seed)
  │           ├─ 1:N ─ ai_card_generations
  │           │
  │           ├─ 1:1 ─ subscription ──► plans
  │           │           └─ 1:N ─ invoices
  │           │
  │           ├─ 1:N ─ shop_members ──► users  (RBAC: role + permissions)
  │           └─ 1:N ─ approval_requests ──► users
  │
  └─ 1:N ─ offer_acceptances

platform_users (сотрудники платформы — отдельный контур от users)
  └─ M:N ─ platform_admin_shops ──► shops

referral_links
  └─ 1:N ─ referral_visits ──► shops (converted_shop_id, SET NULL)

bot_auth_tokens — отдельная таблица (не привязана к shops)
```

Все стрелки `1:N → shops` имеют `ondelete="CASCADE"` — удаление магазина каскадно удаляет все связанные данные.

---

## Миграции

Все 47 файлов в [migrations/](../migrations/). Формат имени: `NNN_описание.sql`. Применяются вручную через `psql` строго по возрастанию номера. Правила нумерации и канонический порядок внутри задвоенных номеров — [migrations/README.md](../migrations/README.md).

| № | Файл | Назначение | Дата |
|---|------|------------|------|
| 002 | [002_add_product_fields.sql](../migrations/002_add_product_fields.sql) | products: `old_price`, `sku`, `sort_order` | 2026-01-10 |
| 003 | [003_add_product_images.sql](../migrations/003_add_product_images.sql) | products: `images` JSON (до 5 фото) | 2026-01-11 |
| 004 | [004_add_order_stats.sql](../migrations/004_add_order_stats.sql) | orders: `original_total`, `discount`, `cost` | — |
| 005 | [005_add_customer_phone.sql](../migrations/005_add_customer_phone.sql) | customers: `phone` (для ветки order_flow) | — |
| 006 | [006_extend_promocodes.sql](../migrations/006_extend_promocodes.sql) | promocodes: `usage_count`, `max_usage` | — |
| 007 | [007_promo_products.sql](../migrations/007_promo_products.sql) | promocodes: `apply_to`, `product_ids` JSON | — |
| 008 | [008_create_payments.sql](../migrations/008_create_payments.sql) | Создание таблицы `payments` (Click/Payme/Uzum) | — |
| 009 | [009_create_telegram_profiles.sql](../migrations/009_create_telegram_profiles.sql) | Создание `telegram_profiles` (маркетинговый слой) | — |
| 010 | [010_broadcast_log.sql](../migrations/010_broadcast_log.sql) | Создание `broadcast_logs` | — |
| 011 | [011_pos_integration_fields.sql](../migrations/011_pos_integration_fields.sql) | Поля для POS: `external_id`, `sync_status` | — |
| 012 | [012_billing.sql](../migrations/012_billing.sql) | Биллинг: `plans`, `subscriptions`, `invoices` | — |
| 013 | [013_composite_indexes.sql](../migrations/013_composite_indexes.sql) | Составные индексы для multi-tenant запросов (CONCURRENTLY) | — |
| 014 | [014_missing_timestamps.sql](../migrations/014_missing_timestamps.sql) | Добавить недостающие `created_at` / `updated_at` | — |
| 015 | [015_i18n_fields.sql](../migrations/015_i18n_fields.sql) | i18n-поля для товаров, категорий, магазинов | 2026-03-08 |
| 016 | [016_add_oauth_fields.sql](../migrations/016_add_oauth_fields.sql) | users: `google_id`, `apple_id`, `telegram_id` | — |
| 017 | [017_add_azma_order_fields.sql](../migrations/017_add_azma_order_fields.sql) | orders: AZMA-поля (`external_id`, `fiscal_url`) | — |
| 018 | [018_rbac.sql](../migrations/018_rbac.sql) | RBAC: `shop_members`, `approval_requests` | — |
| 019 | [019_password_plain.sql](../migrations/019_password_plain.sql) | users: `password_plain` (для просмотра суперадмином) | — |
| 020 | [020_support_messages.sql](../migrations/020_support_messages.sql) | Создание `support_messages` (чат клиент↔админ) | — |
| 021 | [021_legal_onboarding.sql](../migrations/021_legal_onboarding.sql) | Юридический онбординг: акцепт оферты + setup fee | — |
| 022 | [022_didox_integration.sql](../migrations/022_didox_integration.sql) | Mock Didox + интеграционный слой ЭСФ | — |
| 022 | [022_onboarding.sql](../migrations/022_onboarding.sql) | Онбординг-чек-лист владельца магазина | — |
| 023 | [023_fix_flowers_i18n.sql](../migrations/023_fix_flowers_i18n.sql) | Fix Flowers_DeMo: включить мультиязычность | — |
| 023 | [023_onboarding_tour.sql](../migrations/023_onboarding_tour.sql) | Интерактивный тур внутри онбординга | — |
| 024 | [024_billing_cycle.sql](../migrations/024_billing_cycle.sql) | plans: `invoice_type`, `setup_fee_amount` | 2026-04-06 |
| 025 | [025_update_plans.sql](../migrations/025_update_plans.sql) | Синхронизация планов: slugs и цены из фронтенда | — |
| 026 | [026_fix_setup_fee_amount.sql](../migrations/026_fix_setup_fee_amount.sql) | Setup fee = 3 600 000 UZS (вместо 3 900 000) | — |
| 027 | [027_cancel_old_vat_invoices.sql](../migrations/027_cancel_old_vat_invoices.sql) | Отмена старых VAT-счетов (переход на НДС=0) | — |
| 028 | [028_remove_password_plain.sql](../migrations/028_remove_password_plain.sql) | **Security fix:** удаление `password_plain` | — |
| 029 | [029_scaling_indexes.sql](../migrations/029_scaling_indexes.sql) | Индексы для масштабирования на 100+ магазинов | — |
| 030 | [030_activate_demo_shop.sql](../migrations/030_activate_demo_shop.sql) | Активация демо-магазина для тестирования POS | — |
| 031 | [031_bot_auth_tokens.sql](../migrations/031_bot_auth_tokens.sql) | Создание `bot_auth_tokens` (deeplink из маркет-бота) | — |
| 032 | [032_dropshipping.sql](../migrations/032_dropshipping.sql) | shops: `is_dropshipping`, `prepayment_percent`; products: `availability_status`, `delivery_time_text` | — |
| 033 | [033_broadcast_logs_updated_at.sql](../migrations/033_broadcast_logs_updated_at.sql) | broadcast_logs: добавить `updated_at` | — |
| 034 | [034_platform_admins.sql](../migrations/034_platform_admins.sql) | Создание `platform_users` + `platform_admin_shops` (две платформенные роли) | — |
| 035 | [035_demo_promo_code.sql](../migrations/035_demo_promo_code.sql) | Тестовый промокод `DEMO100K` для демо-магазинов (`max_usage = NULL`) | — |
| 036 | [036_platform_support_messages.sql](../migrations/036_platform_support_messages.sql) | Создание `platform_support_messages` (чат владелец ↔ платформа) | — |
| 037 | [037_bot_auth_token_lang.sql](../migrations/037_bot_auth_token_lang.sql) | bot_auth_tokens: `tg_lang` — кабинет открывается на языке из бота | — |
| 038 | [038_payer_type.sql](../migrations/038_payer_type.sql) | shops: `payer_type` — физлицо / организация (физлицо платит без реквизитов) | — |
| 039 | [039_setup_fee_3900000.sql](../migrations/039_setup_fee_3900000.sql) | **Setup fee = 3 900 000 UZS** — откат понижения из 026 | — |
| 040 | [040_order_number_per_shop.sql](../migrations/040_order_number_per_shop.sql) | orders: `number` — порядковый номер в пределах магазина + бэкофилл | — |
| 041 | [041_add_tg_username_to_users.sql](../migrations/041_add_tg_username_to_users.sql) | users: `tg_username` (Telegram @handle) | — |
| 042 | [042_showcase_demo_shops.sql](../migrations/042_showcase_demo_shops.sql) | 3 демо-магазина помечены как публичные витрины-кейсы для `/examples` | — |
| 043 | [043_landing_leads_shop.sql](../migrations/043_landing_leads_shop.sql) | Системный скрытый магазин-приёмник заявок «под ключ» с лендинга | — |
| 044 | [044_ai_card.sql](../migrations/044_ai_card.sql) | Создание `ai_card_examples` + `ai_card_generations` (AI-карточка товара) | 2026-07 |
| 045 | [045_ai_card_rating.sql](../migrations/045_ai_card_rating.sql) | ai_card_generations: `rating` — лайк/дизлайк AI-генерации | 2026-07-18 |
| 046 | [046_referrals.sql](../migrations/046_referrals.sql) | Создание `referral_links` + `referral_visits`; invoices: `discount_amount` | 2026-07-18 |
| 047 | `047_setup_requests.sql` ⚠️ | Создание `setup_requests` (реальный трек «под ключ»). **Только в ветке `setupFeeRepair`, в `dev` и на prod отсутствует** | 2026-07-19 |
| 048 | [048_platform_user_telegram.sql](../migrations/048_platform_user_telegram.sql) | platform_users: `telegram_id` UNIQUE + `password_hash` становится nullable — вход суперадмина только через Telegram | 2026-08-02 |

> **Дубликаты номеров.** Есть две миграции `022` и две `023` — побочный эффект параллельной разработки веток (онбординг и Didox/i18n шли одновременно). Файлы **не переименованы**: они уже применены на prod под этими именами, переименование создало бы риск повторного применения. Канонический порядок внутри номера зафиксирован в [migrations/README.md](../migrations/README.md). Все миграции идемпотентны (`IF NOT EXISTS`).
>
> **Новым миграциям — следующий свободный номер (`048`, …).** Номера не переиспользуются.

---

## Доступ к БД

### В контейнере
```bash
docker compose exec db psql -U postgres -d retail_saas_db
```

### Полезные SQL для отладки
```sql
-- Сколько магазинов и активных подписок
SELECT s.id, s.name, sub.status FROM shops s
LEFT JOIN subscriptions sub ON sub.shop_id = s.id
ORDER BY s.id;

-- Топ-10 клиентов магазина по сумме
SELECT c.name, c.phone, SUM(o.total_amount) AS total
FROM customers c JOIN orders o ON o.customer_id = c.id
WHERE c.shop_id = 1 AND o.status = 'paid'
GROUP BY c.id ORDER BY total DESC LIMIT 10;

-- Рассылки за месяц
SELECT created_at, total_sent, total_delivered FROM broadcast_logs
WHERE shop_id = 1 AND created_at > NOW() - INTERVAL '30 days';
```
