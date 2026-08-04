import hashlib
import json
import logging
import os
import secrets
import time

import bcrypt as _bcrypt_lib
from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_log = logging.getLogger(__name__)

# Токены с TTL (24 часа) — Redis-backed с fallback на in-memory dict

from config.settings import settings as _app_settings

TOKEN_TTL = _app_settings.token_ttl_seconds

# Redis client (lazy init)
_redis_client = None
_redis_init_done = False


def _get_redis():
    """Lazy-init Redis client. Returns None if Redis unavailable."""
    global _redis_client, _redis_init_done
    if _redis_init_done:
        return _redis_client
    _redis_init_done = True
    redis_url = _app_settings.redis_url or None
    if redis_url:
        try:
            import redis
            _redis_client = redis.from_url(redis_url, decode_responses=True)
            _redis_client.ping()
            _log.info("Auth tokens: Redis storage (%s)", redis_url.split("@")[-1] if "@" in redis_url else redis_url)
        except Exception as e:
            _log.warning("Auth tokens: Redis unavailable (%s), falling back to memory", e)
            _redis_client = None
    else:
        _log.info("Auth tokens: in-memory (no REDIS_URL)")
    return _redis_client


# In-memory fallback (used when Redis is unavailable)
_AUTH_TOKENS: dict[str, dict] = {}


def store_token(token: str, user_data: dict) -> None:
    """Сохранить токен с TTL. Redis-first, fallback in-memory."""
    r = _get_redis()
    if r:
        try:
            r.setex(f"auth:{token}", TOKEN_TTL, json.dumps(user_data, default=str))
            return
        except Exception:
            pass
    _AUTH_TOKENS[token] = {**user_data, "_expires_at": time.time() + TOKEN_TTL}


def get_current_user_from_token(token: str) -> dict | None:
    """Вернуть данные пользователя по токену. None если не найден или истёк.

    Sliding TTL: при каждом успешном чтении токен продлевается на TOKEN_TTL.
    Активные пользователи не вылетают из кабинета посреди работы; неактивные
    сессии всё равно умирают через TOKEN_TTL простоя.
    Затрагивает только ключи `auth:*` — одноразовые Telegram-токены входа
    (routers/auth.py, свой префикс, 5-мин TTL) не продлеваются.
    """
    r = _get_redis()
    if r:
        try:
            data = r.get(f"auth:{token}")
            if data:
                # Продлеваем срок жизни активной сессии
                try:
                    r.expire(f"auth:{token}", TOKEN_TTL)
                except Exception:
                    pass
                return json.loads(data)
            return None
        except Exception:
            pass
    # Fallback to in-memory
    entry = _AUTH_TOKENS.get(token)
    if not entry:
        return None
    if time.time() > entry.get("_expires_at", float("inf")):
        _AUTH_TOKENS.pop(token, None)
        return None
    # Sliding TTL и для in-memory ветки
    entry["_expires_at"] = time.time() + TOKEN_TTL
    return {k: v for k, v in entry.items() if k != "_expires_at"}


def delete_token(token: str) -> None:
    """Удалить токен (logout)."""
    r = _get_redis()
    if r:
        try:
            r.delete(f"auth:{token}")
        except Exception:
            pass
    _AUTH_TOKENS.pop(token, None)


# Обратная совместимость — публичный псевдоним для старого кода
AUTH_TOKENS = _AUTH_TOKENS


# Суперадмин платформы — только через личный Telegram-аккаунт
#
# Парольного входа у суперадмина больше нет. Раньше существовали три параллельных
# пути: env-пароль в /api/auth/login, env-Basic-Auth (принимался на КАЖДОМ
# admin-эндпоинте и не был закрыт rate-limit'ом) и флаг users.is_superadmin.
# Все три удалены — остался единственный: resolve_superadmin_by_telegram
# (env SUPERADMIN_TELEGRAM_IDS + запись в platform_users).
#
# Аварийный доступ, если Telegram недоступен: scripts/issue_superadmin_token.py
# (запускается только внутри контейнера, сетевой поверхности не добавляет).
#
# `security` оставлен: HTTPBasic(auto_error=False) стоит в сигнатурах зависимостей
# и молча отдаёт None. Креды из него больше нигде не принимаются.

security = HTTPBasic(auto_error=False)

# RBAC — маппинг ролей на разрешения

ROLE_PERMISSIONS = {
    "owner": {
        "shop.view", "shop.edit",
        "products.view", "products.edit",
        "categories.view", "categories.edit",
        "orders.view", "orders.edit",
        "bot_settings.view", "bot_settings.edit",
        "bot_config.view", "bot_config.edit",
        "bot_admins.view", "bot_admins.edit",
        "promo.view", "promo.edit",
        "clients.view", "clients.message",
        "broadcast.view", "broadcast.send",
        "stats.view", "stats.export",
        "integration.view", "integration.edit",
        "billing.view", "billing.edit",
        "translate.use",
    },
    # Phase 2:
    "shop_admin": {
        "products.view", "products.edit",
        "categories.view", "categories.edit",
        "orders.view", "orders.edit",
        "clients.view", "clients.message",
        "stats.view",
    },
    "marketer": {
        "stats.view", "stats.export",
        "promo.view", "promo.edit",
        "broadcast.view", "broadcast.send",
        "clients.view",
    },
}

# Критические action_type (требуют одобрения суперадмина для non-superadmin)
CRITICAL_ACTIONS = {
    "change_bot_token",
    "remove_bot_admin",
    "add_bot_admin",
    "change_owner_tg_id",
    "change_integration",
    "send_broadcast",
}

# Хэширование паролей — bcrypt

# Общая строка, подмешиваемая к устаревшим SHA256-хэшам паролей. Это не соль
# в обычном смысле: соль генерируется на каждый пароль и публична по замыслу,
# а здесь одно значение на всех — то есть pepper, и он обязан быть секретным.
# Зная его, из утёкших хэшей пароли перебираются офлайн.
#
# Поэтому значение живёт только в окружении и не имеет дефолта: без переменной
# вход по устаревшим хэшам просто не работает (fail-closed). Новые пароли
# хэшируются bcrypt и от неё не зависят.
_LEGACY_SALT = os.getenv("LEGACY_PASSWORD_SALT", "")


def hash_password(password: str) -> str:
    """Хэшировать пароль через bcrypt."""
    return _bcrypt_lib.hashpw(password.encode(), _bcrypt_lib.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Проверить пароль. Поддерживает bcrypt и устаревший SHA256 (для миграции)."""
    if not password_hash:
        return False
    # Устаревший SHA256 формат (64-символьный hex без $)
    if len(password_hash) == 64 and not password_hash.startswith("$"):
        if not _LEGACY_SALT:
            _log.warning(
                "Проверка устаревшего SHA256-хэша пропущена: не задан "
                "LEGACY_PASSWORD_SALT. Задайте его или переведите пользователя "
                "на bcrypt через сброс пароля."
            )
            return False
        legacy = hashlib.sha256(f"{password}{_LEGACY_SALT}".encode()).hexdigest()
        return secrets.compare_digest(legacy, password_hash)
    # bcrypt
    try:
        return _bcrypt_lib.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def generate_token() -> str:
    """Генерировать криптостойкий токен."""
    return secrets.token_urlsafe(32)


# Rate limiting для логина (защита от перебора паролей)

LOGIN_RATE_LIMIT = int(os.environ.get("LOGIN_RATE_LIMIT", "10"))
LOGIN_RATE_WINDOW = int(os.environ.get("LOGIN_RATE_WINDOW", "300"))  # секунд
_LOGIN_ATTEMPTS: dict[str, list] = {}


def get_client_ip(request) -> str:
    """IP клиента с учётом обратного прокси.

    request.client.host за nginx — это адрес ПРОКСИ, а не пользователя: uvicorn
    доверяет X-Forwarded-For только с 127.0.0.1 (forwarded_allow_ips), а nginx
    ходит из docker-сети. Если этого не учитывать, per-IP лимит становится
    глобальным счётчиком: несколько попыток кладут регистрацию всей платформе.

    Заголовок подделываем? Да, если до приложения можно достучаться в обход
    nginx (порт 8005 опубликован на хосте — закройте его фаерволом). Но выбор
    здесь такой: подделываемый ключ лимита позволяет атакующему обойти СВОЙ
    лимит, а IP прокси в качестве ключа блокирует ВСЕХ легитимных пользователей.
    Второе хуже, поэтому доверяем заголовку. Тот же приём уже применяется для
    логирования в routers/auth.py.
    """
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if xff:
        return xff
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return real_ip
    return request.client.host if (request and request.client) else "?"


def enforce_rate_limit(
    bucket: str,
    identifier: str,
    limit: int = LOGIN_RATE_LIMIT,
    window: int = LOGIN_RATE_WINDOW,
    message: str = "Слишком много запросов. Повторите позже.",
) -> None:
    """Ограничить частоту обращений для (bucket, identifier).

    Redis-first (атомарный счётчик с TTL), fallback in-memory (скользящее окно).
    При превышении — HTTP 429.

    bucket разделяет счётчики разных ручек, чтобы попытки входа не смешивались
    с регистрацией и проверкой email.
    """
    r = _get_redis()
    if r:
        try:
            key = f"{bucket}_rl:{identifier}"
            cnt = r.incr(key)
            if cnt == 1:
                r.expire(key, window)
            if cnt > limit:
                raise HTTPException(429, message)
            return
        except HTTPException:
            raise
        except Exception:
            pass
    # In-memory fallback (скользящее окно)
    now = time.time()
    mem_key = f"{bucket}:{identifier}"
    attempts = [t for t in _LOGIN_ATTEMPTS.get(mem_key, []) if now - t < window]
    attempts.append(now)
    _LOGIN_ATTEMPTS[mem_key] = attempts
    if len(attempts) > limit:
        raise HTTPException(429, message)


def enforce_login_rate_limit(identifier: str) -> None:
    """Ограничить число попыток входа для identifier (например ip:email).

    Мягкая защита от credential stuffing поверх bcrypt.
    """
    enforce_rate_limit(
        "login", identifier,
        message="Слишком много попыток входа. Повторите позже.",
    )


# Вспомогательные функции аутентификации

def _resolve_admin_shop_id(request: Request) -> int:
    """Получить shop_id из заголовка X-Shop-Id (для Basic Auth суперадмина)."""
    header = request.headers.get("X-Shop-Id") if request else None
    if header:
        try:
            return int(header)
        except ValueError:
            pass
    return 1


def _basic_auth_is_superadmin(request: Request) -> bool:
    """Устарело: env-Basic-Auth суперадмина удалён. Всегда False.

    Оставлено как заглушка на случай оставшихся вызовов — чтобы они читались
    fail-closed, а не падали с ImportError.
    """
    return False


def _extract_user_from_request(request: Request, credentials=None) -> dict:
    """Извлечь аутентифицированного пользователя из Bearer-токена.

    Basic Auth больше не принимается: env-креды давали права суперадмина на любом
    admin-эндпоинте и не были закрыты rate-limit'ом.
    """
    auth_header = request.headers.get("Authorization", "")

    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user_data = get_current_user_from_token(token)
        if user_data:
            return user_data

    raise HTTPException(status_code=401, detail="Требуется авторизация")


async def get_auth_user(
    authorization: str | None = Header(None),
    request: Request = None,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> dict:
    """FastAPI Dependency: вернуть текущего авторизованного пользователя."""
    auth_header = authorization or ""

    # Bearer token
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user_data = get_current_user_from_token(token)
        if user_data:
            return user_data
        raise HTTPException(401, "Недействительный или истёкший токен")

    raise HTTPException(401, "Требуется авторизация")


def verify_admin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    """Проверить учётные данные администратора. Возвращает username."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user_data = get_current_user_from_token(token)
        if user_data:
            return user_data.get("email", "admin")

    raise HTTPException(status_code=401, detail="Invalid credentials")


async def require_admin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> dict:
    """FastAPI Dependency: требует роль admin или owner."""
    auth_header = request.headers.get("Authorization", "")

    # Bearer token
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user_data = get_current_user_from_token(token)
        if user_data:
            return user_data

    raise HTTPException(status_code=401, detail="Invalid credentials")


# RBAC-зависимости

async def require_superadmin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> dict:
    """FastAPI Dependency: только суперадмин платформы. 403 для всех остальных."""
    user_data = _extract_user_from_request(request, credentials)
    if not user_data.get("is_superadmin"):
        raise HTTPException(status_code=403, detail="Доступ только для администратора платформы")
    return user_data


def is_superadmin(user_data: dict) -> bool:
    """Проверить, является ли пользователь суперадмином."""
    return bool(user_data.get("is_superadmin"))


async def verify_shop_owner(shop_id: int, request: Request, db) -> dict:
    """Проверяет, что текущий юзер — владелец shop_id (или суперадмин).

    Возвращает user_data. Raises 403 если не владелец, 404 если магазин не найден.

    Суперадмин (Bearer-токен с is_superadmin=True) обходит проверку —
    соответствует существующему паттерну is_superadmin().

    Платформенный admin (platform_role='admin') проходит, если shop_id входит в
    список назначенных ему магазинов (platform_admin_shops); иначе 404 «Shop not
    found» — чтобы не раскрывать существование магазина.
    """
    auth_header = request.headers.get("Authorization", "")

    # Bearer-токен → данные юзера из токена
    user_data: dict = {}
    if auth_header.startswith("Bearer "):
        token_data = get_current_user_from_token(auth_header[7:])
        if token_data:
            user_data = token_data

    # Суперадмин из Bearer-токена
    if is_superadmin(user_data):
        return user_data

    # Платформенный admin: проверяем активность, версию токена и доступ к shop.
    if user_data.get("platform_role") == "admin" and user_data.get("platform_user_id"):
        await _verify_platform_user_state(db, user_data)
        if not await _platform_admin_has_shop(db, int(user_data["platform_user_id"]), shop_id):
            raise HTTPException(status_code=404, detail="Shop not found")
        return user_data

    # Нет Bearer-данных. Единственный легитимный случай — env-Basic-Auth суперадмин.
    # Проверяем креды ЯВНО (fail-closed), а не доверяем «пустому user_data» —
    # иначе запрос без валидной авторизации трактовался бы как суперадмин.
    if not user_data:
        if _basic_auth_is_superadmin(request):
            return {"is_superadmin": True, "shop_role": "superadmin"}
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    # Обычный owner — проверяем, его ли магазин.
    from sqlalchemy import select

    from models import Shop

    result = await db.execute(select(Shop).where(Shop.id == shop_id))
    shop = result.scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    if shop.owner_id != user_data.get("user_id"):
        raise HTTPException(status_code=403, detail="Not your shop")
    return user_data


# Платформенные админы — две роли (superadmin/admin) на уровне платформы.

async def _verify_platform_user_state(db, user_data: dict) -> None:
    """Сверяет состояние платформенного пользователя с БД.

    Если is_active=False или token_version в БД больше, чем pwd_v в токене —
    токен инвалидируется (удаляется из стора) и поднимается 401.
    """
    from sqlalchemy import select

    from models import PlatformUser

    user_id = int(user_data.get("platform_user_id") or 0)
    if not user_id:
        raise HTTPException(status_code=401, detail="Недействительный токен")

    result = await db.execute(select(PlatformUser).where(PlatformUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        # Удалить токен из стора, чтобы он больше нигде не прошёл
        token = user_data.get("_token")
        if token:
            delete_token(token)
        raise HTTPException(status_code=401, detail="Учётная запись неактивна")

    if int(user_data.get("pwd_v") or 0) != int(user.token_version):
        token = user_data.get("_token")
        if token:
            delete_token(token)
        raise HTTPException(status_code=401, detail="Сессия завершена (пароль изменён)")


async def _platform_admin_has_shop(db, admin_id: int, shop_id: int) -> bool:
    """Назначен ли магазин этому платформенному админу."""
    from sqlalchemy import exists, select

    from models import PlatformAdminShop

    stmt = select(
        exists().where(
            (PlatformAdminShop.admin_id == admin_id)
            & (PlatformAdminShop.shop_id == shop_id)
        )
    )
    result = await db.execute(stmt)
    return bool(result.scalar())


async def get_platform_admin_assigned_shop_ids(db, admin_id: int) -> list[int]:
    """Список ID магазинов, назначенных платформенному админу."""
    from sqlalchemy import select

    from models import PlatformAdminShop

    result = await db.execute(
        select(PlatformAdminShop.shop_id).where(PlatformAdminShop.admin_id == admin_id)
    )
    return [row[0] for row in result.all()]


# Подтверждение входа суперадмина с компьютера (2-й фактор)
#
# Зачем: POST /api/auth/telegram/issue доверяет только заголовку X-Bot-Secret, а
# telegram_id принимает из тела запроса. Владелец этого секрета мог бы выпустить
# ссылку входа на ЛЮБОЙ telegram_id — включая суперадминский. Поэтому для
# суперадмина ссылка не логинит сразу: платформа шлёт подтверждение в Telegram, и
# войти можно только нажав кнопку в самом аккаунте. Секрета бота недостаточно.

PENDING_LOGIN_PREFIX = "pending_login:"
_PENDING_LOGINS: dict[str, dict] = {}


def store_pending_desktop_login(code: str, payload: dict, ttl: int) -> None:
    """Сохранить ожидающий подтверждения вход. Redis-first, fallback in-memory."""
    payload = {**payload, "expires_at": time.time() + ttl}
    r = _get_redis()
    if r:
        try:
            r.setex(f"{PENDING_LOGIN_PREFIX}{code}", ttl, json.dumps(payload, default=str))
            return
        except Exception:
            pass
    _PENDING_LOGINS[code] = payload


def get_pending_desktop_login(code: str) -> dict | None:
    """Прочитать ожидающий вход. None если нет или протух."""
    r = _get_redis()
    if r:
        try:
            raw = r.get(f"{PENDING_LOGIN_PREFIX}{code}")
            return json.loads(raw) if raw else None
        except Exception:
            pass
    entry = _PENDING_LOGINS.get(code)
    if not entry:
        return None
    if time.time() > entry.get("expires_at", 0):
        _PENDING_LOGINS.pop(code, None)
        return None
    return entry


def confirm_pending_desktop_login(code: str, extra: dict | None = None) -> dict | None:
    """Отметить вход подтверждённым. Возвращает запись или None.

    extra — поля, которые надо сохранить вместе с флагом (прежде всего token).
    Их обязательно передавать сюда, а не проставлять в словаре у вызывающего:
    при работе через Redis get_pending_desktop_login возвращает json.loads(...),
    то есть КОПИЮ. Мутация словаря у вызывающего в хранилище не попадала, и
    следом эта функция перезаписывала запись копией без токена. Внешне это
    выглядело как «подтвердил вход в Telegram, а страница на ПК висит до
    таймаута»: статус отдавал confirmed=true с token=null.
    """
    entry = get_pending_desktop_login(code)
    if not entry:
        return None
    if extra:
        entry.update(extra)
    entry["confirmed"] = True
    remaining = max(1, int(entry.get("expires_at", 0) - time.time()))
    store_pending_desktop_login(code, entry, remaining)
    return entry


def pop_pending_desktop_login(code: str) -> None:
    """Удалить запись (после выдачи токена — строго одноразово)."""
    r = _get_redis()
    if r:
        try:
            r.delete(f"{PENDING_LOGIN_PREFIX}{code}")
        except Exception:
            pass
    _PENDING_LOGINS.pop(code, None)


async def resolve_superadmin_by_telegram(db, tg_id) -> object | None:
    """Суперадмин ли этот Telegram-аккаунт. Возвращает PlatformUser или None.

    Две обязательные проверки, обе должны пройти:
      1) tg_id перечислен в env SUPERADMIN_TELEGRAM_IDS;
      2) есть PlatformUser с этим telegram_id, role='superadmin', is_active=True.

    Смысл дублирования: env лежит на сервере, запись — в БД. Компрометации одной
    только базы (SQL-инъекция, утёкший бэкап, доступ у подрядчика) недостаточно,
    чтобы выдать себе права суперадмина — нужен ещё доступ к серверу и рестарт.

    Fail-closed: пустой SUPERADMIN_TELEGRAM_IDS не даёт доступ никому.
    """
    if not tg_id:
        return None

    tg_id_str = str(tg_id).strip()
    allowed = _app_settings.superadmin_telegram_ids
    if not allowed or tg_id_str not in allowed:
        return None

    from sqlalchemy import select

    from models import PlatformUser

    result = await db.execute(
        select(PlatformUser).where(PlatformUser.telegram_id == tg_id_str)
    )
    user = result.scalar_one_or_none()
    if not user or user.role != "superadmin" or not user.is_active:
        return None
    return user


async def authenticate_platform_user(db, login: str, password: str):
    """Найти платформенного пользователя по логину и проверить пароль.

    Возвращает PlatformUser если ок, иначе None. Не проверяет is_active —
    это делает вызывающий, чтобы дать осмысленное 401.
    """
    from sqlalchemy import select

    from models import PlatformUser

    result = await db.execute(
        select(PlatformUser).where(PlatformUser.login == login.strip())
    )
    user = result.scalar_one_or_none()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def issue_platform_token(user) -> str:
    """Выпустить Bearer-токен для платформенного пользователя.

    В payload кладётся pwd_v (текущая token_version) для механизма инвалидации
    при смене пароля или деактивации.
    """
    token = generate_token()
    is_super = (user.role == "superadmin")
    store_token(token, {
        "platform_user_id": user.id,
        "user_id": 0,  # совместимость со старым кодом, ожидающим user_id
        "email": user.login,
        "login": user.login,
        "role": "admin",  # для совместимости с require_admin
        "shop_id": None,
        "is_superadmin": is_super,
        "shop_role": "superadmin" if is_super else "admin",
        "platform_role": user.role,
        "pwd_v": user.token_version,
    })
    return token


async def get_platform_user(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
    db=Depends(lambda: None),
) -> dict:
    """FastAPI Dependency: требует валидного Bearer-токена платформенного юзера
    ИЛИ env-Basic-Auth суперадмина (fallback).

    Сверяется с БД для bearer-токенов: is_active + token_version.
    `db` берётся из FastAPI DI (`get_db`); если вызывается вне DI или db=None —
    открываем session через глобальную фабрику.
    """
    auth_header = request.headers.get("Authorization", "")

    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user_data = get_current_user_from_token(token)
        if not user_data:
            raise HTTPException(status_code=401, detail="Недействительный или истёкший токен")
        if user_data.get("platform_user_id"):
            user_data["_token"] = token
            if db is None:
                from database import async_session_factory
                async with async_session_factory() as session:
                    await _verify_platform_user_state(session, user_data)
            else:
                await _verify_platform_user_state(db, user_data)
            return user_data
        if user_data.get("is_superadmin"):
            return user_data
        raise HTTPException(status_code=401, detail="Требуется вход в кабинет администратора")

    # env-Basic-Auth как fallback суперадмина удалён: единственный вход —
    # Bearer, выданный после Telegram-аутентификации.
    raise HTTPException(status_code=401, detail="Требуется авторизация")


async def require_platform_superadmin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
    db=Depends(lambda: None),
) -> dict:
    """FastAPI Dependency: только суперадмин платформы (DB или env). 403 иначе."""
    user_data = await get_platform_user(request, credentials, db)
    if user_data.get("platform_role") != "superadmin" and not user_data.get("is_superadmin"):
        raise HTTPException(status_code=403, detail="Доступ только для суперадмина платформы")
    return user_data


async def require_platform_user(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
    db=Depends(lambda: None),
) -> dict:
    """FastAPI Dependency: любой платформенный юзер (superadmin или admin)."""
    return await get_platform_user(request, credentials, db)


async def assert_platform_user_can_access_shop(user: dict, shop_id: int, db) -> None:
    """Платформенный admin (не superadmin) может работать только с назначенными
    магазинами через platform_admin_shops. Суперадмин — со всеми. 404 при отсутствии
    доступа (как verify_shop_owner — не раскрываем существование).
    """
    if user.get("is_superadmin") or user.get("platform_role") == "superadmin":
        return
    admin_id = user.get("platform_user_id")
    if not admin_id:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    from sqlalchemy import select as _select

    from models import PlatformAdminShop
    result = await db.execute(
        _select(PlatformAdminShop.id).where(
            PlatformAdminShop.admin_id == admin_id,
            PlatformAdminShop.shop_id == shop_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")


def _wire_db_default(fn):
    """Подменяет default параметра db на Depends(get_db). Позволяет избежать
    циклического импорта database↔auth_utils на module-load."""
    import inspect

    from database import get_db as _get_db

    sig = inspect.signature(fn)
    new_params = []
    for name, p in sig.parameters.items():
        if name == "db":
            new_params.append(p.replace(default=Depends(_get_db)))
        else:
            new_params.append(p)
    fn.__signature__ = sig.replace(parameters=new_params)


_wire_db_default(get_platform_user)
_wire_db_default(require_platform_superadmin)
_wire_db_default(require_platform_user)


async def seed_platform_superadmin(db) -> int | None:
    """Привязать суперадминов из SUPERADMIN_TELEGRAM_IDS к platform_users.

    Идемпотентно и запускается на каждом старте (не только при пустой таблице):
    добавить себе второй аккаунт = дописать id в .env и перезапустить контейнер.

    Пароля у таких записей нет (`password_hash=None`) — вход только через Telegram.
    Если env пуст, ничего не создаём: суперадмин недоступен до правки .env, что
    правильнее открытой двери. Возвращает id последней созданной записи или None.
    """
    from sqlalchemy import select

    from models import PlatformUser

    tg_ids = _app_settings.superadmin_telegram_ids
    if not tg_ids:
        _log.warning(
            "SUPERADMIN_TELEGRAM_IDS не задан — суперадмин недоступен. "
            "Аварийный вход: scripts/issue_superadmin_token.py"
        )
        return None

    created_id: int | None = None
    for tg_id in sorted(tg_ids):
        result = await db.execute(
            select(PlatformUser).where(PlatformUser.telegram_id == tg_id)
        )
        if result.scalar_one_or_none():
            continue

        user = PlatformUser(
            login=f"tg:{tg_id}",
            password_hash=None,
            role="superadmin",
            is_active=True,
            token_version=1,
            telegram_id=tg_id,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        created_id = user.id
        _log.info("Привязан суперадмин по Telegram: tg_id=%s id=%s", tg_id, user.id)

    return created_id
