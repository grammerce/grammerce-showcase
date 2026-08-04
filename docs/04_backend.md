# 04. Backend

Backend Grammerce — асинхронное FastAPI-приложение поверх SQLAlchemy 2.0 (async) и PostgreSQL. Помимо HTTP API запускает мульти-бот инфраструктуру (aiogram), фоновые задания (APScheduler), webhook-приёмники для платежей и POS, генерацию PDF-счетов.

## Содержание

1. [Точка входа `main.py`](#точка-входа-mainpy)
2. [Ядро инфраструктуры](#ядро-инфраструктуры)
3. [HTTP-роутеры (`routers/`)](#http-роутеры-routers)
4. [Платежи (`payments/`)](#платежи-payments)
5. [POS-интеграции (`integrations/`)](#pos-интеграции-integrations)
6. [Сервисы (`services/`)](#сервисы-services)
7. [Telegram-бот (`bot/`)](#telegram-бот-bot)
8. [Pydantic-схемы (`schemas.py`)](#pydantic-схемы-schemaspy)
9. [Тесты (`tests/`)](#тесты-tests)

---

## Точка входа `main.py`

Файл [main.py](../main.py) — orchestrator: создание FastAPI-приложения, lifespan, монтирование статики, подключение роутеров.

### Lifespan

[main.py:104](../main.py#L104):
1. `_init_db()` — `Base.metadata.create_all` и копирование изображений в `dist/img/`.
2. Сид демо-магазина — вынесен в [services/seed.py](../services/seed.py) (рефакторинг R5.3, раньше жил прямо в `main.py`).
3. `_seed_platform_superadmin_if_empty()` ([main.py:84](../main.py#L84)) — заводит первого `platform_users` суперадмина, если таблица пуста.
4. `BotManager.start_all_bots()` — параллельный запуск всех ботов из БД.
5. `backfill_bot_usernames()` ([services/bot_identity.py](../services/bot_identity.py)) — фоновой задачей дозаписывает `@username` магазинам, у которых есть токен, но нет username. Идемпотентно, не блокирует старт.
6. Запуск планировщика [services/scheduler.py](../services/scheduler.py).
7. На shutdown — корректная остановка всех ботов, scheduler.

### Middleware

[main.py:206](../main.py#L206):
- `GZipMiddleware` (минимальный размер 500 байт)
- `CORSMiddleware` (origins из env `CORS_ALLOWED_ORIGINS` или `WEB_APP_URL` + `FRONTEND_URL` + localhost для dev; origins нормализуются — без хвостового слэша, иначе браузер не матчил их и вход отваливался)
- Глобальный exception handler [main.py:227](../main.py#L227) — не утечёт internal error наружу, всегда `500 {"detail": "Internal server error"}`.

### Подключение роутеров

[main.py:304-328](../main.py#L304) — **25 вызовов `include_router`** на 21 модуль в `routers/` (`stats` подключается дважды — legacy и v1) плюс payments / POS / Didox-mock:

```python
app.include_router(stats.router)                 # 1. legacy /summary, /stats/...
app.include_router(stats.router_v1)              # 2. /api/v1/shop/{id}/stats/...
app.include_router(auth.router)                  # 3. /api/auth/...
app.include_router(admin_catalog.router)         # 4. /api/admin/...
app.include_router(admin_bot.router)             # 5. /api/admin/bot/...
app.include_router(public.router)                # 6. /api/v1/shop/{id}/... + /api/public/...
app.include_router(admin_orders.router)          # 7. /api/admin/...
app.include_router(admin_promo.router)           # 8. /api/v1/shop/{id}/promo/...
app.include_router(admin_clients.router)         # 9. /api/v1/shop/{id}/clients/...
app.include_router(admin_broadcast.router)       # 10. /api/v1/shop/{id}/broadcast/...
app.include_router(admin_integration.router)     # 11. /api/v1/shop/{id}/integration/...
app.include_router(admin_translate_router)       # 12. /api/v1/shop/{id}/translate/...
app.include_router(billing_router)               # 13. /api/v1/shop/{id}/billing/...
app.include_router(payments_router)              # 14. /payments/...
app.include_router(pos_webhook_router)           # 15. /api/webhooks/pos/...
app.include_router(admin_approvals_router)       # 16. /api/admin/approvals/...
app.include_router(admin_platform_admins_router) # 17. /api/admin/platform-admins/...
app.include_router(support_chat_router)          # 18. /api/admin/support/...      (покупатель ↔ владелец)
app.include_router(platform_support_router)      # 19. /api/cabinet/support/... + /api/admin/platform-support/...
app.include_router(documents_router)             # 20. /api/v1/shop/{id}/documents/...
app.include_router(onboarding_router)            # 21. /api/v1/shop/{id}/onboarding/...
app.include_router(admin_stats_router)           # 22. /api/admin/platform-stats
app.include_router(ai_card_router)               # 23. /api/v1/ai-card/...
app.include_router(referrals_router)             # 24. /api/platform/referrals/... + /api/admin/referrals/...
app.include_router(didox_mock_router)            # 25. mock для локальной разработки Didox
```

### Health и SPA-раздача

- `GET /api/health` → `{"status": "ok"}` ([main.py:291](../main.py#L291)).
- `/cabinet/...` → раздаёт собранный кабинет ([main.py:300-320](../main.py#L300)) с SPA catch-all.

### Статические публичные страницы (`public/`)

Маркетинговый сайт Grammerce (не React, обычный HTML/CSS/JS) лежит в [public/](../public/) и отдаётся **явными роутами** в [main.py:464-593](../main.py#L464) по паттерну «файл есть → `FileResponse`, иначе `404`». Сама статика (css/js/assets) монтируется на `/public` через `StaticFiles`.

| Роут | Файл | Назначение |
|------|------|-----------|
| `GET /` | public/index.html | Главный лендинг (в секции «Платформа» — живые admin-витрины из `public/embed/`) |
| `GET /examples` | public/examples.html | Примеры магазинов; данные — `GET /api/public/showcase` (магазины с `config.showcase.enabled`, миграция 042) |
| `GET /audience` | public/audience.html | Страница под ICP; кнопка «Заказать под ключ» открывает форму заявки (`public/js/turnkey.js` → `POST /api/public/turnkey-request`) |
| `GET /investors` | public/investors.html | Страница для инвесторов |
| `GET /admin` | public/admin.html | Суперадмин-панель платформы (магазины, поддержка, рефералы) |
| `GET /login` | public/login.html | Промежуточная страница входа (проброс OAuth-токена в кабинет) |
| `GET /clarity` | public/clarity/index.html | Лендинг CLARITY (покадровая scroll-анимация, `frames_mob/`) |
| `GET /moneyLang` | public/moneyLang/index.html | Лендинг «Язык денег» |
| `GET /legal/offer` | public/legal/offer.html | Публичная оферта |
| `GET /legal/privacy` | public/legal/privacy.html | Политика конфиденциальности |
| `GET /uzsellerclub` | public/uzsellerclub.html | Приватный партнёрский лендинг «Grammerce × Uz Seller Club» (`noindex`) |
| `GET /research/survey` | public/research/survey.html | Опросник-исследование селлеров — Telegram WebApp (`noindex`) |
| `GET /robots.txt`, `/sitemap.xml`, `/favicon.svg` | public/… | SEO-служебные файлы |

Каталог [public/embed/](../public/embed/) — самодостаточные HTML-бандлы живой admin-витрины для встраивания в лендинг через `iframe`: `admin-desktop.html` / `admin-mobile.html` и их `-uz` / `-en` версии. Переключаются по языку страницы, к URL добавляется кэш-бастинг `?v`.

Эти страницы **не требуют сборки** — раздаются с диска как есть (на проде запекаются в Docker-образ через `COPY . .`, поэтому правка `public/` требует ребилда `app`). Детали страниц и дизайн-система — [03_frontend.md](03_frontend.md) (Часть C); опросник+лендинг как фича — [06_features.md](06_features.md) (фича 17).

---

## Ядро инфраструктуры

### Подключение к БД ([database.py](../database.py))

- **Engine:** async SQLAlchemy с пулом `pool_size=30, max_overflow=70, pool_recycle=3600` — рассчитан на 100+ магазинов.
- **Session factory:** `async_sessionmaker(class_=AsyncSession, expire_on_commit=False)`.
- **Dependency:** `get_db() -> AsyncGenerator[AsyncSession]` — стандартная FastAPI зависимость для роутеров.
- **Fallback:** если `DATABASE_URL` не задан — SQLite файл (для локального dev).
- **Token store** ([auth_utils.py](../auth_utils.py)): Redis (если задан `REDIS_URL`) или in-memory dict, TTL = 86400 сек.

### Аутентификация и RBAC ([auth_utils.py](../auth_utils.py))

| Компонент | Что делает |
|-----------|-----------|
| `hash_password` / `verify_password` | bcrypt |
| `create_access_token` / `verify_token` | JWT (HS256), `JWT_SECRET` из env |
| `get_auth_user` (FastAPI Dependency) | Проверяет Bearer-токен → `User`. **Basic Auth больше не принимается** |
| `resolve_superadmin_by_telegram(db, tg_id)` | Единственный путь к правам суперадмина: `tg_id` в env `SUPERADMIN_TELEGRAM_IDS` **И** запись в `platform_users` (`role='superadmin'`, `is_active`). Fail-closed при пустом env |
| `require_role(role)` | Зависимость, проверяющая роль владельца магазина |
| `require_permission(perm)` | Проверяет permission из shop_members.permissions JSON |
| `RBAC_PERMISSIONS` | mapping `owner / shop_admin / marketer` → set permissions |
| Approval workflow | Критические действия (`change_bot_token`, `delete_product_dropship`, `send_broadcast_mass`) → создают [ApprovalRequest](../models.py#L882) |

**Вход суперадмина — только Telegram.** Парольных путей три штуки было, все удалены:

| Что убрано | Чем было опасно |
|---|---|
| env-Basic-Auth (`ADMIN_USERS`) | Принимался в `get_auth_user`, `verify_admin`, `require_admin`, `get_platform_user` и `admin_promo.py` — то есть на каждом admin-эндпоинте, причём rate-limit висел только на `/api/auth/login`, так что перебор пароля ничем не ограничивался |
| env-пароль в `/api/auth/login` | Выдавал `is_superadmin: True` прямо из `.env` |
| `users.is_superadmin` в токене | Второй, параллельный источник прав на таблице владельцев магазинов |

Осталось два входа:
- `POST /api/auth/telegram/webapp` — `initData`, подписанный ботом. Для суперадмина TTL строже: **300 секунд** вместо суточного дефолта (`SUPERADMIN_INIT_DATA_MAX_AGE`).
- `GET /api/auth/telegram/consume` — вход с компьютера, **с подтверждением в Telegram** (см. ниже).

**Аварийный доступ:** `scripts/issue_superadmin_token.py`, запускается только внутри контейнера.

### Подтверждение входа с компьютера

`POST /api/auth/telegram/issue` проверяет только заголовок `X-Bot-Secret`, а `telegram_id` берёт из тела запроса — без доказательства владения аккаунтом. Владелец секрета мог бы выпустить ссылку входа на суперадминский id, поэтому для суперадмина `consume` **не логинит сразу**:

1. отдаёт страницу ожидания (`_desktop_confirm_page`), которая опрашивает `GET /api/auth/telegram/desktop-status`;
2. платформа шлёт в личный чат суперадмина сообщение с IP и User-Agent + кнопку на `GET /api/auth/telegram/confirm-desktop?code=…`;
3. после нажатия выдаётся платформенный токен. Окно — **60 секунд** (`SUPERADMIN_DESKTOP_CONFIRM_TTL`).

Ожидающие подтверждения входы лежат в Redis (fallback — память) под префиксом `pending_login:`, helpers — в [auth_utils.py](../auth_utils.py). Для обычных пользователей `consume` работает как раньше, без подтверждения.

### Центральный конфиг ([config/settings.py](../config/settings.py))

Рефакторинг R2: свёл разрозненные `os.getenv` для общих настроек (БД, Redis, URL-ы, окружение, лимиты) в одно место с единым `load_dotenv`.

- **Все поля — строковые с дефолтами**, числа парсятся отдельными свойствами с защитой от пустой строки. Это осознанное решение: строгий типизированный `BaseSettings` ронял всё приложение на пустом `PLATFORM_MANAGER_CHAT_ID` в `.env`. Импорт конфига не должен падать ни при каком содержимом `.env`.
- Старые `os.getenv` по коду продолжают работать — миграция call-sites инкрементальная, ломать разом не стали.
- Специфические группы живут отдельно: [config/oauth.py](../config/oauth.py) (OAuth) и [config/billing.py](../config/billing.py) (константы биллинга, setup fee).

### OAuth ([config/oauth.py](../config/oauth.py))

`OAuthSettings` (Pydantic):
- **Google:** `client_id`, `client_secret`
- **Apple:** `team_id`, `key_id`, `private_key`
- **Telegram:** `bot_token` (платформенный маркет-бот), `platform_bot_shared_secret`

Redirect URI вычисляется как `{BACKEND_URL}/api/auth/{provider}/callback`.

---

## HTTP-роутеры (`routers/`)

Полный список файлов в [routers/](../routers/):

| Файл | Префикс URL | Назначение |
|------|-------------|-----------|
| [auth.py](../routers/auth.py) | `/api/auth` | JWT-логин, регистрация, OAuth (Google/Apple/Telegram), `/me` |
| [public.py](../routers/public.py) | `/api/v1/shop/{shop_id}` | Публичный API витрины (товары, категории, заказы покупателя, валидация промо) |
| [admin_catalog.py](../routers/admin_catalog.py) | `/api/admin` | CRUD товаров и категорий, скачивание шаблона, импорт CSV **и ZIP-архивом** (CSV + картинки одним файлом) |
| [admin_orders.py](../routers/admin_orders.py) | `/api/admin` | Список / детали / смена статуса / удаление заказов; список магазинов |
| [admin_clients.py](../routers/admin_clients.py) | `/api/v1/shop/{shop_id}/clients` | CRM: список с фильтрами, история, отправка сообщения / промокода |
| [admin_promo.py](../routers/admin_promo.py) | `/api/v1/shop/{shop_id}/promo` | CRUD промокодов, QR-коды (одиночный + batch ZIP), CSV |
| [admin_bot.py](../routers/admin_bot.py) | `/api/admin/bot` | Настройки бота: 6 типов кнопок, токен, аватар, администраторы |
| [admin_broadcast.py](../routers/admin_broadcast.py) | `/api/v1/shop/{shop_id}/broadcast` | Рассылки: preview-сегментов, отправка, история |
| [admin_integration.py](../routers/admin_integration.py) | `/api/v1/shop/{shop_id}/integration` | POS: settings, test, sync, sync-log, push-log |
| [admin_translate.py](../routers/admin_translate.py) | `/api/v1/shop/{shop_id}/translate` | Авто-определение языка и перевод контента |
| [admin_approvals.py](../routers/admin_approvals.py) | `/api/admin/approvals` | Очередь критических действий: list / approve / deny / pending count |
| [admin_platform_admins.py](../routers/admin_platform_admins.py) | `/api/admin/platform-admins` | CRUD платформенных админов и назначение им магазинов (`GET/PUT .../{id}/shops`). Только суперадмин |
| [admin_stats.py](../routers/admin_stats.py) | `/api/admin/platform-stats` | Сводная статистика **по всей платформе**: магазины, воронка онбординга, оплаты. Для суперадмин-панели |
| [platform_support.py](../routers/platform_support.py) | `/api/cabinet/support` + `/api/admin/platform-support` | Чат «владелец ↔ платформа». Владелец пишет через `/api/cabinet/...`, платформа отвечает через `/api/admin/...`. **Не путать с `support_chat.py`** |
| [ai_card.py](../routers/ai_card.py) | `/api/v1/ai-card` | AI-карточка товара: `POST /enhance-image`, `POST /generate-text`, `POST /feedback` (лайк/дизлайк) |
| [referrals.py](../routers/referrals.py) | `/api/platform/referrals` + `/api/admin/referrals` | `POST /track` (дёргает бот платформы при `/start ref_<code>`), CRUD ссылок и `POST /{id}/toggle` для суперадмина |
| [billing.py](../routers/billing.py) | `/api/v1/shop/{shop_id}/billing` | Тарифы, подписка, счета (PDF), реквизиты компании |
| [stats.py](../routers/stats.py) | `/summary` + `/api/v1/shop/{shop_id}/stats` | Сводка, графики, AZMA-отчёт для бухгалтерии |
| [documents.py](../routers/documents.py) | `/api/v1/shop/{shop_id}/documents` | Скачивание PDF-документов (счета, договоры) |
| [support_chat.py](../routers/support_chat.py) | `/api/admin/support` | Чат поддержки «покупатель ↔ владелец»: conversations / messages / send / unread-count |
| [onboarding.py](../routers/onboarding.py) | `/api/v1/shop/{shop_id}/onboarding` + `/api/admin/onboarding` | Шаги онбординга, чеклисты, события, заявка «под ключ» (`submit_delegated_request`) |
| [helpers.py](../routers/helpers.py) | — | Общие хелперы роутеров без эндпоинтов: `parse_discount`, `get_shop_or_404` (рефакторинг R3 — раньше дублировались по файлам) |
| [payments/router.py](../payments/router.py) | `/payments` | `POST /checkout`, webhook-эндпоинты для Click / Payme / Uzum / Mock |
| [integrations/router.py](../integrations/router.py) | `/api/webhooks/pos` | Приём событий от POS-систем (HMAC-SHA256 verify) |
| [mock/didox_mock.py](../mock/didox_mock.py) | `/mock/didox` | Mock Didox API для локального dev (только если включено) |

### Соглашения по эндпоинтам

- **Современный шаблон URL:** `/api/v1/shop/{shop_id}/<resource>`. Старые роутеры (`/api/admin/...`) берут `shop_id` из контекста авторизации текущего пользователя.
- **Все admin-эндпоинты** проверяют через `Depends(get_auth_user)`, что текущий user является owner или member магазина с нужным permission.
- **ВСЕ запросы фильтруются по `shop_id`** — нарушение правила = cross-tenant утечка.
- Цена товара **фиксируется** в `order_items.price` при создании заказа (снимок). Изменение цены товара не задним числом не меняет уже существующие заказы.
- Все денежные суммы — **целые UZS**, никогда `float`.
- **Удаление магазина** (`DELETE /api/admin/shops/{id}`, [admin_orders.py](../routers/admin_orders.py) `admin_delete_shop`, только суперадмин) удаляет дочерние сущности явными `DELETE` + каскад по FK, а также владельца-`User` (если его роль ≠ `admin`). Удаление `cart_items` обёрнуто проверкой `to_regclass` — таблицы может не быть в схеме (нет модели/миграции), иначе `DELETE` ронял всю транзакцию (500).

### Часто используемые публичные эндпоинты витрины ([public.py](../routers/public.py))

| Метод | Путь | Назначение |
|-------|------|-----------|
| GET | `/api/v1/shop/{id}/config` | Конфиг магазина (тема, язык, view_mode, кнопки корзины) |
| GET | `/api/v1/shop/{id}/products?lang=ru` | Список товаров |
| GET | `/api/v1/shop/{id}/categories` | Категории |
| GET | `/api/v1/shop/{id}/profile?telegram_id=` | Профиль покупателя |
| POST | `/api/v1/shop/{id}/orders` | Создание заказа |
| GET | `/api/v1/shop/{id}/orders/{order_id}` | Статус заказа (для polling) |
| GET | `/api/v1/shop/{id}/promo/validate?code=` | Проверка валидности промокода |

---

## Платежи ([payments/](../payments/))

### Архитектура

Паттерн **Adapter + Factory**:

| Файл | Что |
|------|-----|
| [payments/base.py](../payments/base.py) | Абстрактный `PaymentGateway`: `create_payment()`, `verify_webhook()`, `get_payment_status()`, `cancel_payment()` |
| [payments/factory.py](../payments/factory.py) | `get_gateway(provider, shop_config)` — выбирает реализацию по `shop.config.cart.payments.*` или env. Если ключей нет → `MockGateway` |
| [payments/click_gateway.py](../payments/click_gateway.py) | Click UZ — поля `service_id`, `merchant_id`, `secret_key`. Webhook: HTTP callback |
| [payments/payme_gateway.py](../payments/payme_gateway.py) | Payme — `merchant_id`, `key`. Протокол: **JSON-RPC** (Payme специфичен) |
| [payments/uzum_gateway.py](../payments/uzum_gateway.py) | Uzum — `merchant_id`, `api_key` |
| [payments/mock_gateway.py](../payments/mock_gateway.py) | Mock с HTML-формой для локального dev |
| [payments/router.py](../payments/router.py) | `POST /payments/checkout` (создание), webhook-эндпоинты |
| [payments/models.py](../payments/models.py) | Таблица `payments` (FK на `orders`) — статус, provider, external_id |
| [payments/utils.py](../payments/utils.py) | Общие утилиты (нормализация суммы, форматы) |

### Жизненный цикл платежа

1. Витрина создаёт заказ → `POST /api/v1/shop/{id}/orders` с `payment_method`.
2. Если онлайн-оплата — backend вызывает `gateway.create_payment(order)` → возвращает URL для редиректа.
3. Покупатель оплачивает на стороне провайдера.
4. Провайдер шлёт webhook → роутер из [payments/router.py](../payments/router.py) проверяет подпись через `gateway.verify_webhook()`, обновляет `Payment.status` и `Order.status`.
5. Витрина (`OrderStatusPage`) видит изменение через polling.

---

## POS-интеграции ([integrations/](../integrations/))

Тот же паттерн **Adapter + Factory**.

| Файл | Что |
|------|-----|
| [integrations/base.py](../integrations/base.py) | Абстрактный `PosConnector`: `sync_products()`, `push_order()`, `get_order_status()`, `get_client_data()` |
| [integrations/factory.py](../integrations/factory.py) | `get_pos_connector(shop)` — выбор по `shop.integration_settings["type"]`. Fallback на `MockPosAdapter` |
| [integrations/mock_adapter.py](../integrations/mock_adapter.py) | In-memory mock |
| [integrations/moysklad_adapter.py](../integrations/moysklad_adapter.py) | МойСклад REST API (`api_token`, `organization_id`) |
| [integrations/billz_adapter.py](../integrations/billz_adapter.py) | BILLZ POS (`api_key`, `api_secret`) |
| [integrations/jowi_adapter.py](../integrations/jowi_adapter.py) | Jowi (`api_key`, `api_secret`, `restaurant_id`) |
| [integrations/router.py](../integrations/router.py) | `POST /api/webhooks/pos/{shop_id}` — HMAC-SHA256 верификация подписи; обновление `Order.status`, `TelegramProfile.cached_*`; рассылка уведомления покупателю в бот |

### Связь с моделями

- `products.external_id`, `products.sync_status`, `products.last_synced_at` — состояние синхронизации.
- `orders.external_id` — id заказа в POS.
- `shops.integration_settings` (JSON) — `{"type": "moysklad", "api_token": "...", ...}`.

---

## Сервисы ([services/](../services/))

Бизнес-логика верхнего уровня и фоновые задания.

| Файл | Что |
|------|-----|
| [services/scheduler.py](../services/scheduler.py) | `APScheduler` (`AsyncIOScheduler`): `check_abandoned_carts` (push через 2ч), `request_reviews` (через 24ч после `delivered`), `reactivation` (>30 дней неактивности). Каждая задача идемпотентна |
| [services/invoice_service.py](../services/invoice_service.py) | Генерация PDF-счетов через `reportlab`, нумерация `INV-2026-NNNNNN`, отправка в Didox (ЭСФ). Методы: `create_invoice()`, `generate_pdf()`, `mark_as_paid()` |
| [services/didox_service.py](../services/didox_service.py) | Отправка ЭСФ в Didox (электронный документооборот Узбекистана) |
| [services/billing_scheduler.py](../services/billing_scheduler.py) | Авто-генерация счетов в конце биллинг-периода |
| [services/approval_service.py](../services/approval_service.py) | Создание и обработка `ApprovalRequest` |
| [services/profile_service.py](../services/profile_service.py) | Кеш метрик `TelegramProfile` (`cached_orders_count`, `cached_ltv`, `last_funnel_step`) |
| [services/contract_service.py](../services/contract_service.py) | B2B-договоры |
| [services/platform_bot_notify.py](../services/platform_bot_notify.py) | Уведомления из платформы в маркет-бот (POST на `platform_bot_notify_url`) + `send_manager_telegram` — прямая доставка заявки менеджеру через Bot API |
| [services/onboarding_reminders.py](../services/onboarding_reminders.py) | Три трека напоминаний: `send_day_reminders(day)` (по дням онбординга), `send_stuck_step_reminders(minutes)` («застрял на текущем шаге»), `send_trial_payment_reminders()` (оплата триала → блокировка → удаление). Есть тест-режим с тиком в минутах вместо суточного cron |
| [services/onboarding_messages.py](../services/onboarding_messages.py) | Тексты и кнопки пушей владельцу: `STEP_MESSAGES`, `STEP_STUCK_MESSAGES`, `TRIAL_MESSAGES`, `support_reply_markup()` / `pay_reply_markup()`. Кнопки — `web_app` («Открыть платформу» / «Оплатить») + deep-link в поддержку, а не ссылки в браузер |
| [services/bot_identity.py](../services/bot_identity.py) | Единая точка получения `@username` бота магазина через Telegram `getMe`: `fetch_bot_username`, `set_shop_bot_username`, `backfill_bot_usernames`. Нужно, чтобы QR и deeplink вели в бот **магазина**, а не платформы |
| [services/funnel_sync.py](../services/funnel_sync.py) | Синхронизация шагов воронки покупателя с ботом |
| [services/seed.py](../services/seed.py) | Сид демо-магазина (вынесен из `main.py`, рефакторинг R5.3) |

---

## Производительность и устойчивость

Результат сессии оптимизации/рефакторинга (июль 2026, отчёт — [md_s/REPORT_optimization_refactoring.md](../md_s/REPORT_optimization_refactoring.md)):

| Что | Как |
|-----|-----|
| **Картинки товаров** | Новые изображения пишутся файлами в `media/products/{shop_id}/` ([utils/product_images.py](../utils/product_images.py)), в БД — путь. Раньше base64 раздувал таблицу и каждый ответ витрины. Разовый перенос старых картинок — скриптом из [scripts/](../scripts/) |
| **HTTP-кэш** | `Cache-Control` на статике и публичных JSON витрины. Исключение — `index.html` кабинета (`no-cache`), иначе браузер держит ссылки на удалённые lazy-чанки |
| **Первая загрузка витрины** | Code splitting, `defer`, параллельный префетч конфига и товаров |
| **Стабильность входа** | Redis-persistence для token store + sliding TTL токенов (активная сессия не протухает посреди работы) + нормализация CORS-origins |
| **401 в кабинете** | Не разлогинивает вслепую: сначала подтверждает сессию через `GET /api/auth/me`. Раньше единичный 401 от любого эндпоинта выкидывал владельца из кабинета |

---

## Telegram-бот ([bot/](../bot/))

### BotManager ([bot/manager.py](../bot/manager.py))

Мульти-бот архитектура для N магазинов одновременно:

- **Единый Dispatcher** ([bot/manager.py:71-86](../bot/manager.py#L71)) — все боты используют один `aiogram.Dispatcher` с одной FSM-Storage (Redis или Memory).
- **Словарь магазинов** [bot/manager.py:52](../bot/manager.py#L52): `Dict[shop_id, Tuple[Bot, asyncio.Task]]`.
- **Token → shop_id mapping** [bot/manager.py:74](../bot/manager.py#L74): `Dispatcher["token_shop_map"]` — позволяет middleware определить магазин по токену.
- **Семафор** [bot/manager.py:55](../bot/manager.py#L55) на 200 одновременных update.

**Методы:**

| Метод | Описание |
|-------|----------|
| `start_all_bots()` | Параллельный запуск всех ботов из БД (≈ 2–3 сек на 100 ботов) |
| `start_bot(shop_id, token)` | Создание `Bot` + polling task |
| `_poll_bot()` | Custom polling loop (`getUpdates` + `feed_update`) |
| `stop_bot(shop_id)` | Cancel task + close session |
| `get_bot(shop_id)` | Получить экземпляр Bot для отправки уведомления (рассылка, заказ) |

### Хендлеры ([bot/handlers/](../bot/handlers/))

| Файл | Назначение | FSM States | Router |
|------|-----------|------------|--------|
| [registration.py](../bot/handlers/registration.py) | Регистрация покупателя: имя → телефон → гео → скидка 20% | `RegistrationStates(name, phone, location, edit_field)` | `registration_router` |
| [menu.py](../bot/handlers/menu.py) | `/start`, личный кабинет, динамические кнопки из BotSettings | — | `menu_router` |
| [promo.py](../bot/handlers/promo.py) | Ввод промокода, валидация, применение | `PromoStates(waiting_code)` | `promo_router` |
| [contact.py](../bot/handlers/contact.py) | Контакты, двусторонний чат с менеджером, отправка гео заказа (`send_venue`) | `ContactStates(waiting_message, admin_replying)` | `contact_router` |
| [admin.py](../bot/handlers/admin.py) | Админ-команды в боте: статистика, генерация промокодов, рассылки | `PromoGenStates(waiting_type, waiting_value)` | `admin_router` |
| [keyboards.py](../bot/handlers/keyboards.py) | Динамические inline-клавиатуры на основе `BotSettings` (`get_dynamic_menu`) | — | — |
| [feedback.py](../bot/handlers/feedback.py) | Оценки и отзывы после заказа | — | — |

### Сервисы и middleware

- [bot/services/settings.py](../bot/services/settings.py) — управление 6 типами кнопок (`registration`, `profile`, `promo`, `contact`, `shop_webapp`, `main_menu`). Функции: `get_button_settings()`, `save_button_settings()`, `ensure_bot_settings()`. Дефолты — константы `DEFAULT_*_SETTINGS`.
- [bot/middlewares/shop_context.py](../bot/middlewares/shop_context.py) — `ShopContextMiddleware`. На каждый update инжектит `shop_id` в `data`. Если `shop_id` не вычислен — update сбрасывается (`return None`). Защита от cross-tenant утечек.
- [bot/config.py](../bot/config.py) — `Settings` (`bot_token`, `admin_ids`, `database_url`, `static_dir`, `web_app_url`).

### Чат поддержки в боте ([bot/handlers/contact.py](../bot/handlers/contact.py))

Диалог держится на FSM-состоянии `ContactStates.waiting_message` — оно **не сбрасывается после первого сообщения** (`contact.py:351`), поэтому клиент может писать подряд, а не заново открывать чат на каждое сообщение.

- `rearm_support_chat(shop_id, customer_tg_id)` ([contact.py:131](../bot/handlers/contact.py#L131)) — переоткрывает чат у клиента, когда менеджер ответил. Вызывается из двух мест: из самого бота ([contact.py:429](../bot/handlers/contact.py#L429)) и из кабинета через [routers/support_chat.py](../routers/support_chat.py#L289). Без этого ответ менеджера приходил, но клиент не мог на него ответить — диалог был односторонним.
- Хендлер сообщений подписан на `~F.text.startswith("/")` ([contact.py:262](../bot/handlers/contact.py#L262)): открытый чат **не перехватывает команды бота** — `/start` и остальные продолжают работать во время диалога.

### FSM-флоу регистрации покупателя

```
/start
  → ask_name (RegistrationStates.name)
    user отвечает текстом
  → ask_phone (RegistrationStates.phone) + ReplyKeyboardMarkup(request_contact=True)
    пришёл Contact
  → ask_location (RegistrationStates.location) + ReplyKeyboardMarkup(request_location=True)
    пришла Location
  → создать BotUser(is_registered=True, discount_registration="20%")
  → создать Customer(name, phone, telegram_id, shop_id)
  → завершить FSM, RemoveKeyboard, показать главное меню
```

Связанные модели: [BotUser](../models.py#L476), [Customer](../models.py#L301), [TelegramProfile](../models.py#L522).

---

## Pydantic-схемы ([schemas.py](../schemas.py))

Группы схем для запросов и ответов:

| Группа | Что внутри |
|--------|-----------|
| Магазин | `ShopBase`, `ShopCreate`, `ShopResponse`, `SetupShopRequest` |
| Товар | `ProductBase`, `ProductCreate`, `ProductUpdate`, `ProductResponse` (с `availability_status: in_stock | preorder | out_of_stock`) |
| Вариации | `variants` в схемах товара — список осей и значений; `selected_options` в схемах позиции заказа — что выбрал покупатель |
| Категория | `CategoryCreate`, `CategoryResponse` |
| Заказ | `OrderCreate`, `OrderItemCreate`, `OrderResponse`, `OrderStatusUpdate` |
| Промокод | `PromocodeCreate`, `PromocodeResponse`, `PromoValidateResponse` |
| Аутентификация | `LoginRequest`, `RegisterRequest`, `TokenResponse`, `OAuthCallback` |
| Telegram-OAuth | `TelegramAuthIssueRequest`, `TelegramAuthIssueResponse` (для deeplink из маркет-бота) |
| Биллинг | `PlanResponse`, `SubscriptionResponse`, `InvoiceResponse` (со скидкой `discount_amount`) |
| AI-карточка | Схемы запроса/ответа генерации текста, улучшения фото и фидбэка (`rating` ±1) |
| Рефералы | Схемы трекинга перехода и CRUD реферальных ссылок |

> **Правило.** При любом изменении модели в [models.py](../models.py) — обязательно обновлять соответствующую Pydantic-схему в [schemas.py](../schemas.py).

---

## Тесты ([tests/](../tests/))

Всего **66 файлов**. Категории:

| Категория | Файлы |
|-----------|-------|
| Базовые модели и API | `test_models.py`, `test_api.py`, `test_orders.py`, `test_order_flow.py` |
| Платежи | `test_payment_base.py`, `test_payment_flow.py`, `test_payment_integration.py`, `test_payments_cabinet.py`, `test_click_gateway.py`, `test_payme_gateway.py` |
| POS-интеграции | `test_pos_base.py`, `test_pos_integration.py`, `test_pos_webhooks.py`, `test_moysklad_adapter.py`, `test_integration_settings.py`, `test_catalog_sync.py`, `test_order_push.py` |
| Промокоды | `test_promo_api.py`, `test_promo_integration.py`, `test_promo_products.py` |
| Биллинг | `test_billing_models.py`, `test_billing_api.py`, `test_billing_e2e.py`, `test_billing_integration.py`, `test_subscription_lifecycle.py`, `test_invoice_service.py`, `test_didox.py`, `test_roles_and_billing.py` |
| Маркетинг и сегменты | `test_marketing_dashboard.py`, `test_segments.py`, `test_telegram_profiles.py`, `test_clients_api.py`, `test_automation.py` |
| Авторизация и multi-tenant | `test_auth_utils.py`, `test_shop_context_middleware.py`, `test_platform_bot_notify.py` |
| Дропшипинг | `test_dropship.py` |
| Онбординг | `test_onboarding.py`, `test_trial_payment_reminders.py` |
| Каталог: импорт | `test_catalog_archive_import.py` (ZIP = CSV + картинки) |
| Рост | `test_referrals.py`, `test_promo_qr_e2e.py` |
| Бот → WebApp: сборка URL кабинета и витрины | `test_webapp_url_routing.py` |
| Витрины-ниши | `test_flower_shop.py` |

### Запуск

```bash
pytest tests/ -v                          # все тесты
pytest tests/ -k "test_promo" -v          # фильтр по имени
pytest tests/test_payme_gateway.py -v     # один файл
pytest tests/ -x                          # стоп на первой ошибке
```
