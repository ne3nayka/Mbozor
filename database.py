import asyncio
import aiosqlite
import logging
from typing import Optional
from config import DB_NAME, CATEGORIES, SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE, SELLER_BASE_ID, BUYER_BASE_ID, \
    ADMIN_BASE_ID
from datetime import datetime, timedelta
from utils import parse_uz_datetime, format_uz_datetime

logger = logging.getLogger(__name__)
db_lock = asyncio.Lock()

VALID_ROLES = (SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE)
VALID_STATUSES = ('active', 'pending_response', 'archived', 'deleted')
DB_TIMEOUT = 15  # Вынесен в константу для гибкости

async def init_db() -> None:
    try:
        logger.info("Инициализация базы данных")
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    phone_number TEXT NOT NULL,
                    role TEXT NOT NULL,
                    region TEXT,
                    district TEXT,
                    company_name TEXT,
                    unique_id TEXT UNIQUE,
                    created_at TEXT DEFAULT (strftime('%d %B %Y йил %H:%M:%S', 'now'))
                );
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    sort TEXT NOT NULL,
                    volume_ton REAL NOT NULL CHECK(volume_ton > 0),
                    price REAL NOT NULL CHECK(price > 0),
                    photos TEXT,
                    unique_id TEXT UNIQUE NOT NULL,
                    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'pending_response', 'archived', 'deleted')),
                    created_at TEXT DEFAULT (strftime('%d %B %Y йил %H:%M:%S', 'now')),
                    channel_message_id INTEGER,
                    final_price REAL,
                    archived_at TEXT,
                    region TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    sort TEXT NOT NULL,
                    volume_ton REAL NOT NULL CHECK(volume_ton > 0),
                    price REAL NOT NULL CHECK(price > 0),
                    region TEXT NOT NULL,
                    unique_id TEXT UNIQUE NOT NULL,
                    channel_message_id INTEGER,
                    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'pending_response', 'archived', 'deleted')),
                    created_at TEXT DEFAULT (strftime('%d %B %Y йил %H:%M:%S', 'now')),
                    final_price REAL,
                    archived_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS counters (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payments (
                    user_id INTEGER PRIMARY KEY,
                    channel_expires TEXT,
                    bot_expires TEXT,
                    trial_used BOOLEAN DEFAULT FALSE
                );
                CREATE TABLE IF NOT EXISTS deleted_users (
                    delete_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    phone_number TEXT,
                    role TEXT,
                    region TEXT,
                    district TEXT,
                    company_name TEXT,
                    unique_id TEXT,
                    deleted_at TEXT DEFAULT (strftime('%d %B %Y йил %H:%M:%S', 'now')),
                    blocked BOOLEAN DEFAULT FALSE  -- Добавлен столбец blocked
                );
            ''')

            # Создание индексов (без изменений)
            await conn.executescript('''
                CREATE INDEX IF NOT EXISTS idx_products_user_id ON products(user_id);
                CREATE INDEX IF NOT EXISTS idx_products_unique_id ON products(unique_id);
                CREATE INDEX IF NOT EXISTS idx_products_created_at ON products(created_at);
                CREATE INDEX IF NOT EXISTS idx_requests_user_id ON requests(user_id);
                CREATE INDEX IF NOT EXISTS idx_requests_unique_id ON requests(unique_id);
            ''')

            # Миграция таблиц (добавлена миграция для deleted_users)
            await _migrate_table(conn, "products", {
                "channel_message_id": "INTEGER",
                "status": "TEXT DEFAULT 'active'",
                "final_price": "REAL",
                "created_at": "TEXT",
                "archived_at": "TEXT",
                "region": "TEXT NOT NULL DEFAULT 'Не указан'"
            })
            await _migrate_table(conn, "requests", {
                "channel_message_id": "INTEGER",
                "status": "TEXT DEFAULT 'active'",
                "final_price": "REAL",
                "created_at": "TEXT",
                "archived_at": "TEXT",
                "region": "TEXT NOT NULL DEFAULT 'Не указан'"
            })
            await _migrate_table(conn, "deleted_users", {
                "blocked": "BOOLEAN DEFAULT FALSE"
            })

            # Инициализация данных (без изменений)
            await conn.executemany(
                "INSERT OR IGNORE INTO counters (name, value) VALUES (?, ?)",
                [("products", 0), ("requests", 0), ("sellers", 0), ("buyers", 0), ("admins", 0)]
            )
            await conn.executemany(
                "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                [(cat,) for cat in CATEGORIES]
            )
            await conn.commit()
        logger.info("База данных успешно инициализирована")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")
        raise

async def _migrate_table(conn: aiosqlite.Connection, table_name: str, columns: dict) -> None:
    """Применяет миграцию для добавления отсутствующих столбцов в таблицу."""
    async with conn.execute(f"PRAGMA table_info({table_name})") as cursor:
        existing_columns = {row[1] for row in await cursor.fetchall()}

    for col_name, col_type in columns.items():
        if col_name not in existing_columns:
            logger.info(f"Добавление столбца {col_name} в таблицу {table_name}")
            await conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")
            if col_name == "created_at":
                await conn.execute(
                    f"UPDATE {table_name} SET created_at = strftime('%d %B %Y йил %H:%M:%S', 'now') WHERE created_at IS NULL")

async def generate_user_id(role: str) -> str:
    """Генерирует уникальный ID для пользователя на основе роли."""
    if role not in VALID_ROLES:
        raise ValueError(f"Недопустимая роль: {role}. Ожидается одна из {VALID_ROLES}")
    counter_name = "buyers" if role == BUYER_ROLE else "sellers" if role == SELLER_ROLE else "admins"
    base_id = BUYER_BASE_ID if role == BUYER_ROLE else SELLER_BASE_ID if role == SELLER_ROLE else ADMIN_BASE_ID
    logger.debug(f"Генерация ID для роли: {role}, счетчик: {counter_name}, базовый ID: {base_id}")
    async with db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("SELECT value FROM counters WHERE name = ?", (counter_name,)) as cursor:
                result = await cursor.fetchone()
                current_value = result[0] if result else 0
            new_value = current_value + 1
            unique_id = f"{base_id[:-len(str(new_value))]}{new_value}"
            await conn.execute(
                "INSERT OR REPLACE INTO counters (name, value) VALUES (?, ?)",
                (counter_name, new_value)
            )
            await conn.commit()
            logger.debug(f"Сгенерирован unique_id: {unique_id}")
            return unique_id

async def ensure_payment_record(user_id: int) -> None:
    """Обеспечивает наличие записи об оплате для пользователя."""
    if not isinstance(user_id, int):
        raise ValueError(f"Ожидается целое число для user_id, получено: {type(user_id)}")
    logger.debug(f"Обеспечение записи оплаты для user_id {user_id}")
    for attempt in range(5):
        try:
            async with db_lock:
                async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                    await conn.execute(
                        "INSERT OR IGNORE INTO payments (user_id, bot_expires, trial_used) VALUES (?, NULL, 0)",
                        (user_id,)
                    )
                    await conn.commit()
            logger.info(f"Запись оплаты создана для user_id {user_id}")
            return
        except aiosqlite.Error as e:
            logger.error(f"Попытка {attempt + 1}/5: Ошибка для user_id {user_id}: {e}")
            if "database is locked" in str(e) and attempt < 4:
                await asyncio.sleep(2)
            else:
                raise

async def generate_item_id(counter_name: str, prefix: str) -> str:
    """Генерирует уникальный ID для элемента (product/request)."""
    if counter_name not in ("products", "requests"):
        raise ValueError(f"Недопустимое имя счетчика: {counter_name}. Ожидается 'products' или 'requests'")
    MAX_ATTEMPTS = 10000
    async with db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("SELECT value FROM counters WHERE name = ?", (counter_name,)) as cursor:
                result = await cursor.fetchone()
                current_value = result[0] if result else 0
            new_value = current_value
            table = "products" if counter_name == "products" else "requests"
            attempts = 0
            while attempts < MAX_ATTEMPTS:
                new_value += 1
                unique_id = f"{prefix}-{new_value:04d}"
                async with conn.execute(f"SELECT unique_id FROM {table} WHERE unique_id = ?", (unique_id,)) as cursor:
                    if not await cursor.fetchone():
                        await conn.execute(
                            "INSERT OR REPLACE INTO counters (name, value) VALUES (?, ?)",
                            (counter_name, new_value)
                        )
                        await conn.commit()
                        logger.debug(f"Сгенерирован unique_id: {unique_id}")
                        return unique_id
                attempts += 1
            logger.error(f"Не удалось сгенерировать уникальный ID для {counter_name} после {MAX_ATTEMPTS} попыток")
            raise ValueError(f"Не удалось сгенерировать уникальный ID после {MAX_ATTEMPTS} попыток")

async def grant_full_subscription(user_id: int) -> None:
    """Предоставляет полную подписку на 30 дней."""
    if not isinstance(user_id, int):
        raise ValueError(f"Ожидается целое число для user_id, получено: {type(user_id)}")
    full_expires = datetime.now() + timedelta(days=30)
    full_expires_str = format_uz_datetime(full_expires)
    async with db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO payments (user_id, bot_expires, channel_expires, trial_used) "
                "VALUES (?, ?, ?, COALESCE((SELECT trial_used FROM payments WHERE user_id = ?), FALSE))",
                (user_id, full_expires_str, full_expires_str, user_id)
            )
            await conn.commit()
            logger.info(f"Полная подписка для user_id {user_id} до {full_expires_str}")

async def activate_trial(user_id: int) -> None:
    """Активирует пробный период на 3 дня."""
    if not isinstance(user_id, int):
        raise ValueError(f"Ожидается целое число для user_id, получено: {type(user_id)}")
    trial_expires = datetime.now() + timedelta(days=3)
    trial_expires_str = format_uz_datetime(trial_expires)
    async with db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO payments (user_id, bot_expires, trial_used) "
                "VALUES (?, ?, 1)",
                (user_id, trial_expires_str)
            )
            await conn.commit()
            logger.info(f"Пробный период для user_id {user_id} до {trial_expires_str}")

async def register_user(user_id: int, phone_number: str, role: str) -> bool:
    """Регистрирует нового пользователя."""
    async with db_lock:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            # Проверка на блокировку
            async with conn.execute(
                    "SELECT deleted_at FROM deleted_users WHERE user_id = ? AND deleted_at IS NOT NULL", (user_id,)
            ) as cursor:
                if await cursor.fetchone():
                    logger.info(f"User {user_id} already deleted, allowing re-registration")
                    # Если пользователь удален, разрешаем повторную регистрацию
            # Проверка на существующего пользователя
            async with conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)) as cursor:
                if await cursor.fetchone():
                    logger.info(f"User {user_id} already exists in users table")
                    return True
            # Регистрация нового пользователя
            unique_id = await generate_user_id(role)
            await conn.execute(
                "INSERT INTO users (id, phone_number, role, unique_id) VALUES (?, ?, ?, ?)",
                (user_id, phone_number, role, unique_id)
            )
            await ensure_payment_record(user_id)
            await conn.commit()
            logger.info(f"User {user_id} registered with role {role} and unique_id {unique_id}")
            return True