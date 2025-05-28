import asyncio
import aiosqlite
import logging
import shutil
from config import DB_NAME, DB_TIMEOUT, CATEGORIES, SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE, SELLER_BASE_ID, BUYER_BASE_ID, ADMIN_BASE_ID
from datetime import datetime, timedelta
import pytz
from utils import format_uz_datetime, notify_admin, parse_uz_datetime
from aiogram.fsm.storage.redis import RedisStorage
from aiogram import Bot

logger = logging.getLogger(__name__)
db_lock = asyncio.Lock()

VALID_ROLES = (SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE)
VALID_STATUSES = ('active', 'pending_response', 'archived', 'deleted')

async def backup_db() -> None:
    """Создаёт резервную копию базы данных."""
    try:
        backup_path = f"{DB_NAME}.bak"
        shutil.copyfile(DB_NAME, backup_path)
        logger.info(f"Резервная копия базы данных создана: {backup_path}")
    except FileNotFoundError:
        logger.warning(f"База данных {DB_NAME} не найдена, пропуск резервного копирования")
    except Exception as e:
        logger.error(f"Ошибка создания резервной копии базы данных: {e}")
        raise

async def init_db(bot: Bot = None) -> None:
    """Инициализирует базу данных."""
    try:
        logger.info("Маълумотлар базасини инициализация қилиш")
        await backup_db()

        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            logger.debug("Создание таблицы users")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users
                (
                    id INTEGER PRIMARY KEY,
                    phone_number TEXT NOT NULL UNIQUE,
                    role TEXT,
                    region TEXT,
                    district TEXT,
                    company_name TEXT,
                    unique_id TEXT UNIQUE,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)

            # Миграция: убрать NOT NULL из role, если оно присутствует
            logger.debug("Проверка и миграция таблицы users для удаления NOT NULL из role")
            async with conn.execute("PRAGMA table_info(users)") as cursor:
                columns = await cursor.fetchall()
                role_column = next((col for col in columns if col[1] == 'role'), None)
                if role_column and role_column[2] == 'TEXT' and role_column[3] == 1:  # NOT NULL присутствует
                    logger.info("Обнаружено NOT NULL на поле role, выполняется миграция")
                    await conn.execute("""
                        CREATE TABLE users_temp
                        (
                            id INTEGER PRIMARY KEY,
                            phone_number TEXT NOT NULL UNIQUE,
                            role TEXT,
                            region TEXT,
                            district TEXT,
                            company_name TEXT,
                            unique_id TEXT UNIQUE,
                            created_at TEXT DEFAULT (datetime('now'))
                        )
                    """)
                    await conn.execute("""
                        INSERT INTO users_temp (id, phone_number, role, region, district, company_name, unique_id, created_at)
                        SELECT id, phone_number, role, region, district, company_name, unique_id, created_at FROM users
                    """)
                    await conn.execute("DROP TABLE users")
                    await conn.execute("ALTER TABLE users_temp RENAME TO users")
                    await conn.commit()
                    logger.info("Миграция таблицы users завершена: NOT NULL удалён из role")

            logger.debug("Создание таблицы products")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS products
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    sort TEXT NOT NULL,
                    volume_ton REAL NOT NULL CHECK (volume_ton > 0),
                    price REAL NOT NULL CHECK (price > 0),
                    photos TEXT,
                    unique_id TEXT UNIQUE NOT NULL,
                    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'pending_response', 'archived', 'deleted')),
                    created_at TEXT DEFAULT (datetime('now')),
                    channel_message_id INTEGER,
                    channel_message_ids TEXT,
                    final_price REAL,
                    archived_at TEXT,
                    region TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )
            """)

            logger.debug("Создание таблицы requests")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS requests
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    sort TEXT NOT NULL,
                    volume_ton REAL NOT NULL CHECK (volume_ton > 0),
                    price REAL NOT NULL CHECK (price > 0),
                    region TEXT NOT NULL,
                    unique_id TEXT UNIQUE NOT NULL,
                    channel_message_id INTEGER,
                    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'pending_response', 'archived', 'deleted')),
                    created_at TEXT DEFAULT (datetime('now')),
                    final_price REAL,
                    archived_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )
            """)

            logger.debug("Создание таблицы counters")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS counters
                (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0
                )
            """)

            logger.debug("Создание таблицы categories")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS categories
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            """)

            logger.debug("Создание таблицы payments")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS payments
                (
                    user_id INTEGER PRIMARY KEY,
                    channel_expires TEXT,
                    bot_expires TEXT,
                    trial_used INTEGER DEFAULT 0
                )
            """)

            logger.debug("Создание таблицы deleted_users")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS deleted_users
                (
                    deleted_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    phone_number TEXT,
                    role TEXT,
                    region TEXT,
                    district TEXT,
                    company_name TEXT,
                    unique_id TEXT,
                    deleted_at TEXT DEFAULT (datetime('now')),
                    blocked INTEGER DEFAULT 0
                )
            """)

            logger.debug("Создание таблицы pending_items")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_items
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    item_type TEXT NOT NULL CHECK (item_type IN ('product', 'request')),
                    unique_id TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )
            """)

            logger.debug("Создание таблицы processed_updates")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_updates
                (
                    update_id INTEGER PRIMARY KEY
                )
            """)

            logger.debug("Создание индексов")
            await conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_products_user_id ON products(user_id);
                CREATE INDEX IF NOT EXISTS idx_products_unique_id ON products(unique_id);
                CREATE INDEX IF NOT EXISTS idx_products_created_at ON products(created_at);
                CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
                CREATE INDEX IF NOT EXISTS idx_requests_user_id ON requests(user_id);
                CREATE INDEX IF NOT EXISTS idx_requests_unique_id ON requests(unique_id);
                CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at);
                CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
                CREATE INDEX IF NOT EXISTS idx_pending_items_user_id ON pending_items(user_id);
                CREATE INDEX IF NOT EXISTS idx_users_phone_number ON users(phone_number);
                CREATE INDEX IF NOT EXISTS idx_users_id ON users(id);
            """)

            await _migrate_table(conn, "products", {
                "channel_message_id": "INTEGER",
                "channel_message_ids": "TEXT",
                "status": "TEXT DEFAULT 'active'",
                "final_price": "REAL",
                "created_at": "TEXT",
                "archived_at": "TEXT",
                "region": "TEXT NOT NULL DEFAULT 'Не указан'",
                "archived_photos": "TEXT",
                "completed_at": "TEXT"
            }, bot=bot)
            await _migrate_table(conn, "requests", {
                "channel_message_id": "INTEGER",
                "status": "TEXT DEFAULT 'active'",
                "final_price": "REAL",
                "created_at": "TEXT",
                "archived_at": "TEXT",
                "region": "TEXT NOT NULL DEFAULT 'Не указан'"
            }, bot=bot)
            await _migrate_table(conn, "deleted_users", {
                "blocked": "INTEGER DEFAULT 0"
            }, bot=bot)
            await _migrate_table(conn, "pending_items", {
                "item_type": "TEXT NOT NULL",
                "unique_id": "TEXT NOT NULL",
                "created_at": "TEXT"
            }, bot=bot)

            await _migrate_dates(conn, bot=bot)

            logger.debug("Инициализация счетчиков")
            await conn.executemany(
                "INSERT OR IGNORE INTO counters (name, value) VALUES (?, ?)",
                [("products", 0), ("requests", 0), ("sellers", 0), ("buyers", 0), ("admins", 0)]
            )
            logger.debug("Инициализация категорий")
            await conn.executemany(
                "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                [(cat,) for cat in CATEGORIES]
            )
            await conn.commit()

            async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
                tables = [row[0] for row in await cursor.fetchall()]
                logger.debug(f"Созданы таблицы: {tables}")
                if 'users' not in tables or 'payments' not in tables:
                    logger.error("Таблицы users или payments не созданы")
                    await notify_admin("Таблицы users или payments не созданы", bot=bot)
                    raise aiosqlite.Error("Таблицы users или payments не созданы")
        logger.info("Маълумотлар базаси муваффақиятли инициализация қилинди")
    except aiosqlite.Error as e:
        logger.error(f"Хатолик: Маълумотлар базасини инициализация қилишда: {e}")
        await notify_admin(f"Хатолик: Маълумотлар базасини инициализация қилишда: {str(e)}", bot=bot)
        raise
    except Exception as e:
        logger.error(f"Кутмаган хатолик: Маълумотлар базасини инициализация қилишда: {e}")
        await notify_admin(f"Кутмаган хатолик: Маълумотлар базасини инициализация қилишда: {str(e)}", bot=bot)
        raise

async def _migrate_table(conn: aiosqlite.Connection, table_name: str, columns: dict, bot: Bot = None) -> None:
    """Устунлар мавжуд бўлмаса, таблицага қўшади."""
    try:
        async with conn.execute(f"PRAGMA table_info({table_name})") as cursor:
            existing_columns = {row[1] for row in await cursor.fetchall()}

        for col_name, col_type in columns.items():
            if col_name not in existing_columns:
                logger.info(f"Таблица {table_name} га {col_name} устуни қўшиляпти")
                await conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")
                if col_name == "created_at":
                    await conn.execute(
                        f"UPDATE {table_name} SET created_at = datetime('now') WHERE created_at IS NULL"
                    )
        await conn.commit()
    except aiosqlite.Error as e:
        logger.error(f"Хатолик: {table_name} таблицасини миграция қилишда: {e}")
        await notify_admin(f"Хатолик: {table_name} таблицасини миграция қилишда: {str(e)}", bot=bot)
        raise

async def _migrate_dates(conn: aiosqlite.Connection, bot: Bot = None) -> None:
    """Саналарни ўзбек форматидан SQLite форматига ўтказади."""
    try:
        tables = [
            {"name": "users", "columns": ["created_at"]},
            {"name": "products", "columns": ["created_at", "archived_at"]},
            {"name": "requests", "columns": ["created_at", "archived_at"]},
            {"name": "pending_items", "columns": ["created_at"]},
            {"name": "deleted_users", "columns": ["deleted_at"]}
        ]
        for table in tables:
            table_name = table["name"]
            columns = table["columns"]
            logger.info(f"Таблица {table_name} учун саналар миграцияси")
            async with conn.execute(f"PRAGMA table_info({table_name})") as cursor:
                existing_columns = {row[1] for row in await cursor.fetchall()}

            select_columns = [col for col in columns if col in existing_columns]
            if not select_columns:
                logger.debug(f"Таблица {table_name} да миграция қилинадиган устунлар йўқ")
                continue

            query = f"SELECT rowid, {', '.join(select_columns)} FROM {table_name} WHERE {' OR '.join(f'{col} IS NOT NULL' for col in select_columns)}"
            async with conn.execute(query) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    rowid = row[0]
                    for i, col in enumerate(select_columns, 1):
                        value = row[i]
                        if value and not value.startswith('20'):  # Фильтрация не-ISO дат
                            try:
                                parsed = parse_uz_datetime(value)
                                if parsed:
                                    new_value = parsed.strftime('%Y-%m-%d %H:%M:%S')
                                    logger.debug(f"SQL: UPDATE {table_name} SET {col} = '{new_value}' WHERE rowid = {rowid}")
                                    await conn.execute(
                                        f"UPDATE {table_name} SET {col} = ? WHERE rowid = ?",
                                        (new_value, rowid)
                                    )
                            except ValueError:
                                logger.warning(f"Сана {value} ни {table_name} таблицасида, rowid={rowid} да ўзгартириб бўлмади")
            await conn.commit()
        logger.info("Саналар миграцияси якунланди")
    except aiosqlite.Error as e:
        logger.error(f"Хатолик: Саналар миграциясида: {e}")
        await notify_admin(f"Хатолик: Саналар миграциясида: {str(e)}", bot=bot)
        raise

async def generate_user_id(role: str, bot: Bot = None) -> str:
    """Генерирует уникальный ID для пользователя в формате S00001, B00002, A00003."""
    if role not in VALID_ROLES:
        raise ValueError(f"Йўқ роль: {role}. Керакли роллар: {VALID_ROLES}")

    role_config = {
        BUYER_ROLE: ("buyers", BUYER_BASE_ID),
        SELLER_ROLE: ("sellers", SELLER_BASE_ID),
        ADMIN_ROLE: ("admins", ADMIN_BASE_ID)
    }

    counter_name, base_id = role_config[role]
    logger.debug(f"Роль учун ID яратиш: {role}, счетчик: {counter_name}, базовый ID: {base_id}")
    try:
        async with db_lock:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                await conn.execute("BEGIN TRANSACTION")
                logger.debug(f"SQL: SELECT value FROM counters WHERE name = '{counter_name}'")
                async with conn.execute("SELECT value FROM counters WHERE name = ?", (counter_name,)) as cursor:
                    row = await cursor.fetchone()
                    current_value = row[0] if row else 0
                    new_value = current_value + 1

                numeric_part = str(new_value).zfill(5)  # Фиксированная длина 5 цифр
                unique_id = f"{base_id.rstrip('0123456789')}{numeric_part}"
                if len(unique_id) > 12:
                    logger.error(f"Сгенерированный unique_id слишком длинный: {unique_id}")
                    await conn.execute("ROLLBACK")
                    raise ValueError(f"unique_id превышает допустимую длину: {unique_id}")
                logger.debug(f"SQL: INSERT OR REPLACE INTO counters (name, value) VALUES ('{counter_name}', {new_value})")
                await conn.execute(
                    "INSERT OR REPLACE INTO counters (name, value) VALUES (?, ?)",
                    (counter_name, new_value)
                )
                await conn.commit()
                logger.debug(f"Яратилган unique_id: {unique_id}")
                return unique_id
    except aiosqlite.Error as e:
        logger.error(f"Роль {role} учун user_id яратишда хатолик: {e}")
        await notify_admin(f"Роль {role} учун user_id яратишда хатолик: {str(e)}", bot=bot)
        raise
    except Exception as e:
        logger.error(f"Роль {role} учун user_id яратишда кутмаган хатолик: {e}")
        await notify_admin(f"Роль {role} учун user_id яратишда кутмаган хатолик: {str(e)}", bot=bot)
        raise

async def generate_item_id(counter_name: str, prefix: str, bot: Bot = None) -> str:
    """Элемент (product/request) учун уникал ID яратади."""
    if counter_name not in ("products", "requests"):
        raise ValueError(f"Йўқ счетчик номи: {counter_name}. Керакли: 'products' ёки 'requests'")
    MAX_ATTEMPTS = 10000
    try:
        async with db_lock:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                await conn.execute("BEGIN TRANSACTION")
                logger.debug(f"SQL: SELECT value FROM counters WHERE name = '{counter_name}'")
                async with conn.execute("SELECT value FROM counters WHERE name = ?", (counter_name,)) as cursor:
                    result = await cursor.fetchone()
                    current_value = result[0] if result else 0
                new_value = current_value
                table = "products" if counter_name == "products" else "requests"
                attempts = 0
                while attempts < MAX_ATTEMPTS:
                    new_value += 1
                    unique_id = f"{prefix}-{new_value:04d}"
                    logger.debug(f"SQL: SELECT unique_id FROM {table} WHERE unique_id = '{unique_id}'")
                    async with conn.execute(f"SELECT unique_id FROM {table} WHERE unique_id = ?", (unique_id,)) as cursor:
                        if not await cursor.fetchone():
                            logger.debug(f"SQL: INSERT OR REPLACE INTO counters (name, value) VALUES ('{counter_name}', {new_value})")
                            await conn.execute(
                                "INSERT OR REPLACE INTO counters (name, value) VALUES (?, ?)",
                                (counter_name, new_value)
                            )
                            await conn.commit()
                            logger.debug(f"Яратилган unique_id: {unique_id} за {attempts + 1} уриниш")
                            return unique_id
                    attempts += 1
                logger.error(f"{counter_name} учун уникал ID ни {MAX_ATTEMPTS} уринишдан кейин яратиб бўлмади")
                await conn.execute("ROLLBACK")
                await notify_admin(f"{counter_name} учун уникал ID ни {MAX_ATTEMPTS} уринишдан кейин яратиб бўлмади", bot=bot)
                raise ValueError(f"{MAX_ATTEMPTS} уринишдан кейин уникал ID яратиб бўлмади")
    except aiosqlite.Error as e:
        logger.error(f"{counter_name} учун item_id яратишда хатолик: {e}")
        await notify_admin(f"{counter_name} учун item_id яратишда хатолик: {str(e)}", bot=bot)
        raise
    except Exception as e:
        logger.error(f"{counter_name} учун item_id яратишда кутмаган хатолик: {e}")
        await notify_admin(f"{counter_name} учун item_id яратишда кутмаган хатолик: {str(e)}", bot=bot)
        raise

async def ensure_payment_record(user_id: int, bot: Bot = None) -> None:
    """Фойдаланувчи учун тўлов ёзувини таъминлайди."""
    if not isinstance(user_id, int):
        logger.error(f"Неверный тип user_id: {type(user_id)}")
        await notify_admin(f"Неверный тип user_id: {type(user_id)}", bot=bot)
        raise ValueError(f"User_id учун бутун сон керак, олинди: {type(user_id)}")
    logger.debug(f"Фойдаланувчи user_id={user_id} учун тўлов ёзувини таъминлаш")
    for attempt in range(3):
        try:
            async with db_lock:
                async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                    await conn.execute("BEGIN TRANSACTION")
                    logger.debug(f"SQL: SELECT name FROM sqlite_master WHERE type='table' AND name='payments'")
                    async with conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='payments'") as cursor:
                        if not await cursor.fetchone():
                            logger.error("Таблица payments не существует")
                            await notify_admin("Таблица payments не существует при создании записи оплаты", bot=bot)
                            await conn.execute("ROLLBACK")
                            raise aiosqlite.Error("Таблица payments не существует")
                    logger.debug(f"SQL: INSERT OR IGNORE INTO payments (user_id, bot_expires, trial_used) VALUES ({user_id}, NULL, 0)")
                    await conn.execute(
                        "INSERT OR IGNORE INTO payments (user_id, bot_expires, trial_used) VALUES (?, NULL, 0)",
                        (user_id,)
                    )
                    await conn.commit()
            logger.info(f"Фойдаланувчи user_id={user_id} учун тўлов ёзуви яратилди")
            return
        except aiosqlite.Error as e:
            logger.error(f"Попытка {attempt + 1}/3: user_id={user_id} хатолик в ensure_payment_record: {e}")
            if "database is locked" in str(e) and attempt < 2:
                await asyncio.sleep(10)
            else:
                await notify_admin(f"Фойдаланувчи user_id={user_id} тўлов ёзувини яратиб бўлмади (3 попытки): {str(e)}", bot=bot)
                raise
        except Exception as e:
            logger.error(f"Фойдаланувчи user_id={user_id} тўлов ёзувини яратишда кутмаган хатолик: {e}")
            await notify_admin(f"Фойдаланувчи user_id={user_id} тўлов ёзувини яратишда кутмаган хатолик: {str(e)}", bot=bot)
            raise

async def activate_trial(user_id: int, bot: Bot = None) -> None:
    """Фойдаланувчига 3 кунлик синов муддатини фаоллаштиради."""
    if not isinstance(user_id, int):
        logger.error(f"Неверный тип user_id: {type(user_id)}")
        await notify_admin(f"Неверный тип user_id: {type(user_id)}", bot=bot)
        raise ValueError(f"User_id учун бутун сон керак, олинди: {type(user_id)}")
    logger.debug(f"Фойдаланувчи user_id={user_id} учун синов муддатини фаоллаштириш")
    for attempt in range(3):
        try:
            async with db_lock:
                async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                    await conn.execute("BEGIN TRANSACTION")
                    trial_expires = datetime.now(pytz.timezone('Asia/Tashkent')) + timedelta(days=3)
                    trial_expires_str = format_uz_datetime(trial_expires)
                    logger.debug(f"SQL: INSERT OR REPLACE INTO payments (user_id, bot_expires, trial_used) VALUES ({user_id}, '{trial_expires_str}', 1)")
                    await conn.execute(
                        "INSERT OR REPLACE INTO payments (user_id, bot_expires, trial_used) VALUES (?, ?, 1)",
                        (user_id, trial_expires_str)
                    )
                    await conn.commit()
            logger.info(f"Фойдаланувчи user_id={user_id} учун синов муддати активирован до {trial_expires_str}")
            return
        except aiosqlite.Error as e:
            logger.error(f"Попытка {attempt + 1}/3: user_id={user_id} хатолик в activate_trial: {e}")
            if "database is locked" in str(e) and attempt < 2:
                await asyncio.sleep(10)
            else:
                await notify_admin(f"Фойдаланувчи user_id={user_id} синов муддатини активировать бўлмади (3 попытки): {str(e)}", bot=bot)
                raise
        except Exception as e:
            logger.error(f"Фойдаланувчи user_id={user_id} синов муддатини активировать бўлмади: {e}")
            await notify_admin(f"Фойдаланувчи user_id={user_id} синов муддатини активировать бўлмади: {str(e)}", bot=bot)
            raise

async def grant_full_subscription(user_id: int, bot: Bot = None) -> None:
    """Фойдаланувчига 30 кунлик тўлиқ обуна беради."""
    if not isinstance(user_id, int):
        raise ValueError(f"User_id учун бутун сон керак, олинди: {type(user_id)}")
    try:
        full_expires = datetime.now(pytz.timezone('Asia/Tashkent')) + timedelta(days=30)
        full_expires_str = format_uz_datetime(full_expires)
        async with db_lock:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                await conn.execute("BEGIN TRANSACTION")
                logger.debug(f"SQL: INSERT OR REPLACE INTO payments (user_id, bot_expires, channel_expires, trial_used) VALUES ({user_id}, '{full_expires_str}', '{full_expires_str}', COALESCE((SELECT trial_used FROM payments WHERE user_id = {user_id}), FALSE))")
                await conn.execute(
                    "INSERT OR REPLACE INTO payments (user_id, bot_expires, channel_expires, trial_used) "
                    "VALUES (?, ?, ?, COALESCE((SELECT trial_used FROM payments WHERE user_id = ?), FALSE))",
                    (user_id, full_expires_str, full_expires_str, user_id)
                )
                await conn.commit()
                logger.info(f"Фойдаланувчи user_id={user_id} учун тўлиқ обуна {full_expires_str} гача")
    except aiosqlite.Error as e:
        logger.error(f"Фойдаланувчи user_id={user_id} учун тўлиқ обуна беришда хатолик: {e}")
        await notify_admin(f"Фойдаланувчи user_id={user_id} учун тўлиқ обуна беришда хатолик: {str(e)}", bot=bot)
        raise
    except Exception as e:
        logger.error(f"Фойдаланувчи user_id={user_id} учун тўлиқ обуна беришда кутмаган хатолик: {e}")
        await notify_admin(f"Фойдаланувчи user_id={user_id} учун тўлиқ обуна беришда кутмаган хатолик: {str(e)}", bot=bot)
        raise

async def register_user(user_id: int, phone_number: str, bot: Bot = None) -> bool:
    """Регистрирует нового пользователя без указания роли."""
    if not phone_number.strip():
        logger.error(f"Фойдаланувчи user_id={user_id} учун телефон рақами бўш")
        await notify_admin(f"Фойдаланувчи user_id={user_id} рўйхатдан ўтишда телефон рақами бўш", bot=bot)
        raise ValueError("Телефон рақами бўш бўлиши мумкин эмас")

    logger.info(f"[register_user] Попытка регистрации user_id={user_id}, phone={phone_number}")
    for attempt in range(3):
        try:
            async with db_lock:
                async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                    await conn.execute("BEGIN TRANSACTION")
                    # Проверка на блокировку
                    logger.debug(f"SQL: SELECT blocked FROM deleted_users WHERE user_id = {user_id} AND blocked = 1")
                    async with conn.execute(
                            "SELECT blocked FROM deleted_users WHERE user_id = ? AND blocked = 1", (user_id,)
                    ) as cursor:
                        blocked = await cursor.fetchone()
                        if blocked:
                            logger.warning(f"Блокланган фойдаланувчи user_id={user_id} рўйхатдан ўтишга уринди")
                            await conn.execute("ROLLBACK")
                            return False

                    # Проверка на существующего пользователя по user_id или phone_number
                    logger.debug(f"SQL: SELECT id, phone_number FROM users WHERE id = {user_id} OR phone_number = '{phone_number}'")
                    async with conn.execute(
                            "SELECT id, phone_number FROM users WHERE id = ? OR phone_number = ?",
                            (user_id, phone_number)
                    ) as cursor:
                        existing_user = await cursor.fetchone()
                        if existing_user:
                            if existing_user[0] == user_id:
                                logger.info(f"Фойдаланувчи user_id={user_id} уже существует, phone_number={existing_user[1]}")
                                await conn.execute("ROLLBACK")
                                return True
                            else:
                                logger.warning(f"Телефон рақами {phone_number} уже зарегистрирован другим пользователем user_id={existing_user[0]}")
                                await conn.execute("ROLLBACK")
                                return False

                    # Регистрация нового пользователя
                    logger.debug(f"SQL: INSERT INTO users (id, phone_number) VALUES ({user_id}, '{phone_number}')")
                    await conn.execute(
                        "INSERT INTO users (id, phone_number) VALUES (?, ?)",
                        (user_id, phone_number)
                    )

                    # Временно отключено для теста
                    # logger.debug(f"Фойдаланувчи user_id={user_id} учун тўлов ёзуви таъминланмоқда")
                    # await ensure_payment_record(user_id, bot=bot)

                    await conn.commit()
                    logger.info(f"Фойдаланувчи user_id={user_id} телефон {phone_number} билан рўйхатдан ўтди")
                    return True
        except aiosqlite.IntegrityError as e:
            logger.error(f"Попытка {attempt + 1}/3: Фойдаланувчи user_id={user_id} рўйхатдан ўтишда IntegrityError: {e}")
            await notify_admin(f"Попытка {attempt + 1}/3: Фойдаланувчи user_id={user_id} рўйхатдан ўтишда IntegrityError: {str(e)}", bot=bot)
            if attempt < 2:
                await asyncio.sleep(10)
                continue
            return False
        except aiosqlite.Error as e:
            logger.error(f"Попытка {attempt + 1}/3: user_id={user_id} рўйхатдан ўтишда хатолик: {e}")
            if "database is locked" in str(e) and attempt < 2:
                await asyncio.sleep(10)
            else:
                await notify_admin(f"Фойдаланувчи user_id={user_id} рўйхатдан ўтишда хатолик (3 попытки): {str(e)}", bot=bot)
                raise
        except Exception as e:
            logger.error(f"Фойдаланувчи user_id={user_id} рўйхатдан ўтишда кутмаган хатолик: {e}")
            await notify_admin(f"Фойдаланувчи user_id={user_id} рўйхатдан ўтишда кутмаган хатолик: {str(e)}", bot=bot)
            raise

async def clear_user_state(user_id: int, storage: RedisStorage, bot: Bot = None) -> None:
    """Принудительно очищает состояние пользователя в Redis."""
    try:
        if hasattr(storage, 'redis'):
            redis_keys = await storage.redis.keys(f"aiogram:*:{user_id}:*")
            if redis_keys:
                await storage.redis.delete(*redis_keys)
                logger.info(f"Фойдаланувчи user_id={user_id} учун {len(redis_keys)} Redis ҳолат калитлари тозаланди")
            else:
                logger.debug(f"Фойдаланувчи user_id={user_id} учун Redis ҳолат калитлари топилмади")
        else:
            logger.debug(f"Фойдаланувчи user_id={user_id} учун сақлаш Redis эмас")
    except Exception as e:
        logger.error(f"Фойдаланувчи user_id={user_id} учун Redis ҳолатини тозалашда хатолик: {e}")
        await notify_admin(f"Фойдаланувчи user_id={user_id} учун Redis ҳолатини тозалашда хатолик: {str(e)}", bot=bot)