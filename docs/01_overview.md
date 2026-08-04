# 01. Общее описание проекта Grammerce

## Что это

**Grammerce** — мульти-тенант SaaS-платформа: Telegram Mini App магазины для бизнеса в Узбекистане. Один сервер, одна база данных, множество магазинов. Каждый магазин получает три связанные сущности:

1. **Telegram-бота** (отдельный токен на магазин, общий aiogram-Dispatcher).
2. **Витрину WebApp** — Telegram Mini App, открывается прямо из бота.
3. **Веб-кабинет владельца** — полноценную админ-панель в браузере (с возможностью открытия и из Telegram).

Изоляция данных между магазинами — через сквозной FK `shop_id` в каждой бизнес-таблице.

## Архитектура (текстовая диаграмма)

```
                    ┌──────────────────────────────────┐
                    │      ОДИН СЕРВЕР (FastAPI)       │
                    │   ┌──────────┐  ┌─────────────┐  │
                    │   │ BotMgr   │  │ HTTP роутер │  │
                    │   │ N ботов  │  │ /api/...    │  │
                    │   └──────────┘  └─────────────┘  │
                    └────────────┬─────────────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────────────┐
                    │   ОДНА БД (PostgreSQL)           │
                    │   shop_id-изоляция + RBAC        │
                    └──────────────────────────────────┘
                                 ▲
              ┌──────────────────┼──────────────────┐
              │                  │                  │
   ┌──────────┴───────┐  ┌───────┴────────┐ ┌───────┴────────┐
   │   Магазин 1      │  │   Магазин 2    │ │   Магазин N    │
   │ ┌──────┐ ┌─────┐ │  │     ...        │ │     ...        │
   │ │ Бот  │ │WebAp│ │  │                │ │                │
   │ └──────┘ └─────┘ │  │                │ │                │
   │ ┌──────────────┐ │  │                │ │                │
   │ │  Кабинет     │ │  │                │ │                │
   │ └──────────────┘ │  │                │ │                │
   └──────────────────┘  └────────────────┘ └────────────────┘
```

## Стек

| Слой | Технологии |
|------|-----------|
| **Backend** | Python 3.11, FastAPI, SQLAlchemy 2.0 (async), Pydantic v2, APScheduler |
| **БД** | PostgreSQL (production), SQLite (fallback для локальной разработки) |
| **Кеш / Storage** | Redis (для FSM Telegram + кеш токенов), MemoryStorage как fallback |
| **Telegram-бот** | aiogram 3.x, FSM, единый Dispatcher на N ботов |
| **Витрина WebApp** | React 18, Vite, JS (без TS), Tab-based навигация без React Router |
| **Кабинет владельца** | React 19, React Router 7, Vite, Tailwind CSS 4, i18next, Chart.js |
| **Платежи** | Click, Payme (JSON-RPC), Uzum, Mock (для dev) |
| **POS-интеграции** | МойСклад, BILLZ, Jowi, Mock |
| **Документооборот** | Didox (ЭСФ Узбекистан), reportlab (PDF-счёта) |
| **AI** | Генерация текста карточки товара и улучшение фото (по умолчанию mock, реальный провайдер — по env) |
| **Деплой** | Docker Compose (`app` + `db`) |
| **Тесты** | pytest, 66 файлов |

## Структура репозитория

```
project-root/
├── main.py                       — FastAPI entry-point, lifespan, подключение всех роутеров
├── models.py                     — 29 SQLAlchemy-моделей (+ Payment в payments/)
├── schemas.py                    — Pydantic-схемы запросов/ответов
├── database.py                   — async engine, sessionmaker, Redis token store
├── auth_utils.py                 — JWT, bcrypt, OAuth helpers, RBAC, dependency get_auth_user
├── docker-compose.yml            — оркестрация app + db
│
├── bot/                          — Telegram-бот
│   ├── manager.py                — BotManager: запуск N ботов параллельно
│   ├── config.py                 — настройки бота (токен, web_app_url, static_dir)
│   ├── handlers/                 — registration, menu, promo, contact, admin, keyboards, feedback
│   ├── services/settings.py      — управление 6 типами кнопок бота
│   └── middlewares/shop_context.py — пробрасывание shop_id в каждый update
│
├── routers/                      — 21 FastAPI-роутер (+ payments + integrations + mock)
│   ├── auth.py                   — JWT-логин, OAuth (Google/Apple/Telegram)
│   ├── public.py                 — публичный API витрины (/api/v1/shop/{id}/...)
│   ├── admin_catalog.py          — CRUD товаров и категорий, импорт CSV и ZIP-архивом
│   ├── admin_orders.py           — управление заказами
│   ├── admin_clients.py          — CRM клиентов
│   ├── admin_promo.py            — промокоды и QR
│   ├── admin_bot.py              — настройки бота
│   ├── admin_broadcast.py        — рассылки
│   ├── admin_integration.py      — POS-интеграции
│   ├── admin_translate.py        — авто-перевод контента
│   ├── admin_approvals.py        — одобрения критических действий
│   ├── admin_platform_admins.py  — CRUD платформенных админов (только суперадмин)
│   ├── admin_stats.py            — сводная статистика платформы (все магазины)
│   ├── ai_card.py                — AI-карточка товара: текст, фото, фидбэк
│   ├── referrals.py              — реферальные ссылки на бота платформы
│   ├── billing.py                — тарифы, подписки, счета
│   ├── stats.py                  — статистика и графики магазина
│   ├── support_chat.py           — чат поддержки (покупатель ↔ владелец)
│   ├── platform_support.py       — чат поддержки (владелец ↔ платформа)
│   ├── documents.py              — выдача PDF (счета, договоры)
│   ├── onboarding.py             — этапы онбординга владельца, заявка «под ключ»
│   └── helpers.py                — общие хелперы роутеров (без эндпоинтов)
│
├── payments/                     — платёжные шлюзы
│   ├── base.py                   — абстракция PaymentGateway
│   ├── factory.py                — get_gateway(provider, shop_config)
│   ├── click_gateway.py          — Click UZ
│   ├── payme_gateway.py          — Payme (JSON-RPC)
│   ├── uzum_gateway.py           — Uzum
│   ├── mock_gateway.py           — Mock с HTML-формой
│   ├── router.py                 — /payments/checkout + webhooks
│   ├── models.py                 — таблица payments
│   └── utils.py                  — общие утилиты
│
├── integrations/                 — POS-адаптеры
│   ├── base.py                   — абстракция PosConnector
│   ├── factory.py                — выбор адаптера по shop.integration_settings
│   ├── mock_adapter.py           — in-memory mock
│   ├── moysklad_adapter.py       — МойСклад
│   ├── billz_adapter.py          — BILLZ POS
│   ├── jowi_adapter.py           — Jowi
│   └── router.py                 — /api/webhooks/pos/{shop_id} (HMAC-SHA256)
│
├── services/                     — общие бизнес-сервисы
│   ├── scheduler.py              — APScheduler: abandoned carts, reviews, reactivation
│   ├── invoice_service.py        — PDF-счета, нумерация INV-2026-NNNNNN, Didox
│   ├── didox_service.py          — отправка ЭСФ
│   ├── billing_scheduler.py      — авто-генерация счетов
│   ├── approval_service.py       — workflow одобрений
│   ├── profile_service.py        — кеш метрик TelegramProfile
│   ├── contract_service.py       — B2B-договоры
│   ├── platform_bot_notify.py    — уведомления в маркет-бот платформы и менеджеру
│   ├── onboarding_messages.py    — тексты и кнопки пушей владельцу (онбординг + триал)
│   ├── onboarding_reminders.py   — напоминания: по дням, «застрял на шаге», оплата триала
│   ├── bot_identity.py           — @username бота магазина через getMe (+ backfill)
│   ├── funnel_sync.py            — синхронизация шагов воронки с ботом
│   └── seed.py                   — сид демо-магазина (вынесен из main.py)
│
├── config/                       — конфигурация
│   ├── settings.py               — центральный конфиг backend (БД, Redis, URL-ы, лимиты)
│   ├── billing.py                — константы биллинга (setup fee, тарифы)
│   └── oauth.py                  — Google / Apple / Telegram OAuth-настройки
│
├── src/                          — React-витрина WebApp (Vite)
│   ├── main.jsx                  — точка входа
│   ├── App.jsx                   — корневой компонент, Tab-роутинг
│   ├── components/               — 14 компонентов (ProductGrid, CartView, OrderModal, ...)
│   ├── context/ShopContext.jsx   — глобальное состояние магазина
│   ├── hooks/                    — useShopConfig.js, useDragScroll.js (drag-скролл категорий)
│   └── utils/                    — telegram.js, lang.js, theme.js (темы + токены витрины)
│
├── Cabinet_react_New/            — кабинет владельца магазина
│   ├── frontend/
│   │   ├── src/
│   │   │   ├── App.jsx           — Router (Browser или Memory), AuthProvider, ShopProvider
│   │   │   ├── Cabinet.jsx       — главный компонент-контейнер с sub-роутингом
│   │   │   ├── Login.jsx         — страница входа
│   │   │   ├── pages/            — 11 страниц (BotSettings, Statistics, PromoList, ...)
│   │   │   ├── components/       — 41+ компонент (по подпапкам: bot-settings, editor-panels, ...)
│   │   │   ├── api.js            — обёртки fetch для admin/v1 эндпоинтов
│   │   │   ├── auth.js           — JWT (Bearer)
│   │   │   └── context/          — AuthContext, ShopContext, ToastContext
│   │   └── vite.config.js        — base: /cabinet/, output в ../static/cabinet_build
│   └── static/cabinet_build/     — собранные ассеты (раздаются FastAPI в /cabinet/...)
│
├── migrations/                   — 47 SQL-миграций 002–046 (формат NNN_описание.sql) + README.md
│
├── tests/                        — 66 pytest-файлов (api, payments, billing, pos, RBAC, ...)
│
├── utils/                        — общие утилиты бэкенда
│   └── product_images.py         — сохранение картинок товаров в файлы (вместо base64 в БД)
│
├── scripts/                      — разовые скрипты (миграция картинок из base64 в файлы, ...)
│
├── media/                        — пользовательские файлы
│   ├── invoices/                 — сгенерированные PDF-счета (INV-2026-NNNNNN.pdf)
│   └── products/                 — картинки товаров (по shop_id)
│
├── public/                       — статический маркетинговый сайт (HTML/CSS/JS без сборки):
│   │                               лендинги, /login, /admin, оферта, robots.txt
│   └── embed/                    — живые admin-витрины для лендинга (RU/UZ/EN, iframe)
├── documentation/                — структурированная документация проекта (этот каталог)
├── md_s/                         — модульные технические инструкции (legacy)
├── mock/                         — mock-сервисы (например, didox_mock.py)
└── CLAUDE.md                     — инструкции для AI-агента (краткая выжимка)
```

## Принципы мульти-тенантности

1. **Сквозной `shop_id`.** Все бизнес-сущности (`products`, `categories`, `orders`, `customers`, `promocodes`, `bot_settings`, `bot_users`, `telegram_profiles`, ...) имеют FK `shop_id` с `ondelete="CASCADE"`. Любой запрос обязан фильтровать по `shop_id`.
2. **Идентификация покупателя.** Уникальная пара `(shop_id, telegram_id)` — один и тот же Telegram-пользователь у разных магазинов = разные `Customer`/`BotUser`/`TelegramProfile`.
3. **API-маршрутизация.** Современные эндпоинты используют префикс `/api/v1/shop/{shop_id}/...`. Старые `/api/admin/...` берут `shop_id` из контекста авторизации.
4. **Мульти-бот.** [bot/manager.py](../bot/manager.py) держит единый `Dispatcher` и словарь `Dict[shop_id, (Bot, asyncio.Task)]`. На каждый магазин — отдельный polling-loop, но обработка update проходит через один Dispatcher с маппингом `token → shop_id`. Семафор ограничивает 200 одновременных update.
5. **Защита от cross-tenant утечек.** [bot/middlewares/shop_context.py](../bot/middlewares/shop_context.py) инжектит `shop_id` в `data` каждого хендлера; если `shop_id` не вычислен — update сбрасывается.

## RBAC (Role-Based Access Control)

RBAC построен на двух уровнях.

### Платформенный уровень — таблица `platform_users`

| Роль | Назначение | Может |
|------|-----------|-------|
| **superadmin** | Суперадмин платформы | Всё, включая удаление магазинов, управление другими админами, одобрение approval-запросов |
| **admin** | Платформенный админ | Всё, что superadmin, КРОМЕ: удаление магазинов, CRUD платформенных админов, approval-операции; работает только с назначенными магазинами (M2M `platform_admin_shops`) |

- **Суперадмин входит ТОЛЬКО через личный Telegram-аккаунт — пароля у него нет.** Требуются обе проверки: `telegram_id` перечислен в env `SUPERADMIN_TELEGRAM_IDS` **и** есть запись в `platform_users` (`role='superadmin'`, `is_active`). Реализация — `resolve_superadmin_by_telegram` в [auth_utils.py](../auth_utils.py). Компрометации одной только БД для захвата прав недостаточно.
- **Штатный `admin` — логин/пароль** (bcrypt): `POST /api/auth/platform-login` или `/api/auth/login`. Попытка парольного входа под суперадмином → 403.
- Для `admin` запрос данных чужого магазина → 404 (а не 403 — чтобы не раскрывать существование).
- При смене пароля или деактивации `token_version` инкрементируется и все ранее выданные Bearer-токены становятся невалидными.
- **Env-Basic-Auth суперадмина удалён.** Раньше `SUPERADMIN_EMAIL`/`SUPERADMIN_PASSWORD` давали права суперадмина на каждом admin-эндпоинте и не были закрыты rate-limit'ом. Переменные больше не читаются кодом.
- Аварийный доступ (Telegram недоступен): `docker compose exec app python scripts/issue_superadmin_token.py` — только по SSH, сетевого эндпоинта нет.
- Подробнее: [md_s/platform_admins.md](../md_s/platform_admins.md), фича 24 в [06_features.md](06_features.md).

### Shop-уровень — таблица `shop_members` (связь `users` ↔ `shops` с ролью)

| Роль | Назначение | Permissions (JSON) |
|------|-----------|---------------------|
| **owner** | Владелец магазина | Полный доступ ко всему |
| **shop_admin** | Администратор магазина | products.*, orders.*, clients.* |
| **marketer** | Маркетолог | stats.view, broadcasts.*, promo.* |

Критические действия (смена `bot_token`, удаление товаров в dropshipping-режиме, массовые рассылки) идут через таблицу `approval_requests` — требуют одобрения суперадмина платформы. Логика — [services/approval_service.py](../services/approval_service.py), UI — [Cabinet_react_New/frontend/src/pages/ApprovalsPage.jsx](../Cabinet_react_New/frontend/src/pages/ApprovalsPage.jsx).

## Команды

### Backend и боты
```bash
docker compose up -d --build               # старт (app + db)
docker compose down                        # остановка
docker compose logs app --tail=50          # логи backend
docker compose logs app -f                 # стрим логов
docker compose exec db psql -U postgres -d retail_saas_db   # доступ к БД
docker compose exec app bash               # оболочка в контейнере приложения
```

### Витрина (Telegram WebApp)
```bash
cd src/
npm install
npm run dev                                # dev-сервер на http://localhost:5173
npm run build                              # production-сборка в src/dist/
```

### Кабинет владельца
```bash
cd Cabinet_react_New/frontend/
npm install
npm run dev                                # dev-сервер на http://localhost:5173
npm run build                              # сборка в Cabinet_react_New/static/cabinet_build/
```
В production кабинет раздаётся самим FastAPI по `/cabinet/` ([main.py:300-320](../main.py#L300)).

### Тесты
```bash
pytest tests/ -v                           # все тесты
pytest tests/ -k "test_promo" -v           # фильтр по имени
pytest tests/test_payme_gateway.py -v      # один файл
pytest tests/ -x                           # остановиться на первой ошибке
```

### Миграции
Миграции вручные, ASCII SQL в `migrations/NNN_описание.sql`, применяются строго по возрастанию номера через `psql` (см. [05_database.md](05_database.md)). Правило: только `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, никогда не `DROP COLUMN`. Порядок применения, коллизии номеров `022`/`023` и правило нумерации новых — [migrations/README.md](../migrations/README.md).

## Переменные окружения (без секретов)

Минимально необходимые переменные `.env` (значения смотри в `.env.example`, если он есть, или `docker-compose.yml`). Единая точка чтения общих настроек — [config/settings.py](../config/settings.py); специфические группы остаются в [config/oauth.py](../config/oauth.py) и [config/billing.py](../config/billing.py).

| Переменная | Назначение |
|-----------|-----------|
| `BOT_TOKEN` | Токен legacy/fallback бота (не магазина) |
| `TELEGRAM_BOT_TOKEN` | Токен главного бота платформы @Grammerce_bot — заявки менеджеру, пуши владельцам. **Это НЕ `BOT_TOKEN`** |
| `PLATFORM_MANAGER_CHAT_ID` | Telegram chat_id менеджера — получателя заявок «под ключ» |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@db:5432/retail_saas_db` |
| `REDIS_URL` | Опционально; включает Redis-FSM и Redis-token-store |
| `WEB_APP_URL` | Публичный URL витрины (для кнопок в боте) |
| `FRONTEND_URL` | URL фронтенда (для CORS) |
| `BACKEND_URL` | URL backend (для OAuth callback) |
| `CORS_ALLOWED_ORIGINS` | Список через запятую (override) |
| `SUPERADMIN_TELEGRAM_IDS` | Telegram chat_id суперадминов через запятую. Пусто = вход суперадмина закрыт (fail-closed). Заменил `SUPERADMIN_EMAIL`/`SUPERADMIN_PASSWORD`, которые удалены |
| `JWT_SECRET` | Секрет подписи JWT |
| `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GOOGLE_CLIENT_SECRET` | Google OAuth |
| `OAUTH_APPLE_TEAM_ID`, `OAUTH_APPLE_KEY_ID`, `OAUTH_APPLE_PRIVATE_KEY` | Apple OAuth |
| `PLATFORM_BOT_TOKEN`, `PLATFORM_BOT_SHARED_SECRET` | Маркет-бот платформы |
| `DIDOX_*` | Реквизиты Didox API |
| `AI_CARD_MOCK_MODE` | `true` по умолчанию — все AI-вызовы заглушки. Реальный провайдер включается только явно ([integrations/ai_card/settings.py](../integrations/ai_card/settings.py)) |
| `AI_CARD_TARGET_W`, `AI_CARD_TARGET_H`, `AI_CARD_BG_COLOR` | Целевой формат карточки маркетплейса (по умолчанию 1080×1440, 3:4, белый фон) |
| `ONBOARDING_REMINDER_TEST_MODE`, `ONBOARDING_REMINDER_TEST_TICK_MINUTES` | Тест-режим напоминаний: вместо суточного cron — тик раз в N минут ([services/scheduler.py](../services/scheduler.py)) |
| `ONBOARDING_STUCK_STEP_MINUTES` | Через сколько минут на одном шаге владелец считается «застрявшим» |
| `ENVIRONMENT` | `development` / `production` — влияет на CORS и отладочные роуты |

## Куда смотреть дальше

- [02_journal.md](02_journal.md) — журнал изменений проекта (changelog)
- [03_frontend.md](03_frontend.md) — все вкладки витрины и страницы кабинета
- [04_backend.md](04_backend.md) — HTTP-роутеры, сервисы, бот, тесты
- [05_database.md](05_database.md) — таблицы и миграции
- [06_features.md](06_features.md) — каждая бизнес-фича end-to-end (фронт + бэк + БД + бот)
- [ONBOARDING_FLOW_COMPLETE.md](ONBOARDING_FLOW_COMPLETE.md) — полный путь нового владельца от входа до первого заказа
- [../CLAUDE.md](../CLAUDE.md) — инструкции для AI-агента и краткие конвенции
- [../md_s/](../md_s/) — модульные технические инструкции (legacy)
