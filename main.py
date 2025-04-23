import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
import json
from typing import List, Dict, Any, Optional

import aiosqlite
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, ReplyKeyboardRemove
from aiogram.utils.web_app import check_webapp_signature
from quart import Quart, jsonify, request
from quart_cors import cors
import requests
from redis.asyncio import ConnectionError
import backoff
from logging.handlers import RotatingFileHandler
import pytz
import aiogram, quart, redis, hypercorn

from config import (
    BOT_TOKEN, ROLES, ADMIN_ROLE, LOG_LEVEL, ADMIN_IDS,
    ROLE_MAPPING, WEBAPP_URL, DB_NAME, DB_TIMEOUT, PORT,
    CERT_PATH, KEY_PATH, WEBHOOK_PATH
)
from database import init_db
from registration import router as registration_router
from products import router as products_router
from profile import register_handlers as register_profile_handlers
from requests import register_handlers as register_request_handlers
from common import register_handlers as register_common_handlers
from admin import register_handlers as register_admin_handlers, AdminStates
from expiration import check_expired_items
from utils import (
    check_role, make_keyboard, MONTHS_UZ, check_subscription,
    format_uz_datetime, parse_uz_datetime, ExpiredItem,
    get_main_menu, get_admin_menu
)

WEBHOOK_URL = f"https://mbozor.msma.uz:8443{WEBHOOK_PATH}"

logger = logging.getLogger(__name__)

# Настройка логирования
class UzbekDateFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=pytz.UTC)
        eng_date = dt.strftime("%d %B %Y йил %H:%M:%S")
        for eng, uz in MONTHS_UZ.items():
            eng_date = eng_date.replace(eng, uz)
        return eng_date

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler("bot.log", maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ],
)

formatter = UzbekDateFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
root_logger = logging.getLogger()
for handler in root_logger.handlers:
    handler.setFormatter(formatter)

# Инициализация Aiogram
@backoff.on_exception(backoff.expo, ConnectionError, max_tries=5)
async def connect_redis():
    try:
        return await RedisStorage.from_url("redis://localhost:6379/0")
    except ConnectionError as e:
        logger.warning(f"Не удалось подключиться к Redis: {e}. Используется MemoryStorage.")
        return MemoryStorage()

# Инициализация Quart
app = Quart(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB
app = cors(app, allow_origin=[WEBAPP_URL], allow_methods=["GET", "POST"])
logger.info(f"CORS настроен для: {WEBAPP_URL}")

# Добавление маршрута для /
@app.route('/')
async def index():
    return {"status": "Server is running", "version": "1.0"}

# Middleware для проверки подписки
class SubscriptionCheckMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message | types.CallbackQuery, data: dict) -> None:
        user_id = event.from_user.id
        state = data["state"]
        current_state = await state.get_state()
        event_text = event.text if isinstance(event, types.Message) else event.data
        logger.debug(
            f"SubscriptionCheckMiddleware: Проверка подписки для user_id={user_id}, event={event_text}, state={current_state}"
        )

        if user_id in ADMIN_IDS:
            logger.debug(f"Админ {user_id} пропущен")
            await handler(event, data)
            return

        allowed, role = await check_role(event, allow_unregistered=True)
        registration_states = ['Registration:phone', 'Registration:role', 'Registration:region',
                              'Registration:district', 'Registration:company_name']

        if current_state == ExpiredItem.choice:
            logger.debug(f"Пропуск проверки для состояния ExpiredItem.choice для {user_id}")
            await handler(event, data)
            return

        if (isinstance(event, types.Message) and event.text in ["/start", "/myid", "/subscribe", "Обуна", "Рўйхатдан ўтиш"]) or \
                current_state in registration_states:
            logger.debug(f"Пропуск проверки для команды '{event_text}' или состояния регистрации для {user_id}")
            await handler(event, data)
            return

        if not allowed or not role:
            logger.debug(f"Пользователь {user_id} не зарегистрирован")
            await event.answer(
                "Илтимос, аввал рўйхатдан ўтинг.",
                reply_markup=get_main_menu(None)
            )
            await state.clear()
            return

        if role != ADMIN_ROLE:
            channel_active, bot_active, is_subscribed = await check_subscription(bot, user_id)
            logger.debug(
                f"Подписка для {user_id}: channel_active={channel_active}, bot_active={bot_active}, is_subscribed={is_subscribed}"
            )
            if not is_subscribed:
                await event.answer(
                    "Сизнинг обунангиз тугади. Обуна бўлиш учун 'Обуна' тугмасини босинг",
                    reply_markup=make_keyboard(["Обуна"], columns=1)
                )
                await state.clear()
                logger.info(f"Доступ для {user_id} заблокирован из-за неактивной подписки")
                return

        logger.debug(f"Проверка подписки пройдена для {user_id}")
        await handler(event, data)

dp = Dispatcher()  # Инициализация Dispatcher без storage, будет установлено в on_startup

dp.message.middleware(SubscriptionCheckMiddleware())
dp.callback_query.middleware(SubscriptionCheckMiddleware())

# Обработчики
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает команду /start."""
    user_id = message.from_user.id
    logger.info(f"cmd_start: Начало обработки для {user_id}")
    allowed, role = await check_role(message, allow_unregistered=True)

    async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
        async with conn.execute(
                "SELECT blocked FROM deleted_users WHERE user_id = ? AND blocked = TRUE", (user_id,)
        ) as cursor:
            blocked = await cursor.fetchone()

    if blocked:
        await message.answer(
            "Сизнинг Telegram ID админ томонидан блокланган. Админга мурожаат қилинг (@MSMA_UZ).",
            reply_markup=ReplyKeyboardRemove()
        )
        logger.warning(f"Заблокированный пользователь {user_id} пытался запустить бот")
        await state.clear()
        return

    if user_id in ADMIN_IDS or role == ADMIN_ROLE:
        await message.answer("Админ панели:", reply_markup=get_admin_menu())
        await state.set_state(AdminStates.main_menu)
        logger.info(f"Админ {user_id} вошел в панель")
        return

    if not allowed or role not in ROLES:
        await message.answer("Хуш келибсиз! Рўйхатдан ўтиш:", reply_markup=get_main_menu(None))
        logger.info(f"Новый пользователь {user_id} получил приглашение к регистрации")
    else:
        display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role)
        await message.answer("Асосий меню:", reply_markup=get_main_menu(role))
        logger.info(f"Пользователь {user_id} (роль: {display_role}) получил главное меню")
    await state.clear()

@dp.message(F.text == "Эълонлар доскаси")
async def open_webapp(message: types.Message, state: FSMContext) -> None:
    """Открывает Web App."""
    user_id = message.from_user.id
    allowed, role = await check_role(message, allow_unregistered=True)
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role) if role else None
    reply_markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Эълонлар доскаси", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp.html"))],
            [KeyboardButton(text="Орқага")]
        ],
        resize_keyboard=True
    )
    await message.answer("Эълонлар доскаси:", reply_markup=reply_markup)
    logger.info(f"Пользователь {user_id} (роль: {display_role}) открыл веб-приложение")

@dp.message(F.web_app_data)
async def handle_webapp_close(message: types.Message, state: FSMContext):
    """Обрабатывает данные от Web App."""
    user_id = message.from_user.id
    web_app_data = message.web_app_data.data
    logger.info(f"Получены данные из Web App от {user_id}: '{web_app_data}'")

    if not web_app_data:
        logger.warning(f"Получены пустые данные из Web App от {user_id}, игнорируем.")
        return

    data = await state.get_data()
    role = data.get("role") or data.get("role_display")
    if role not in ROLES and role is not None:
        logger.warning(f"Некорректная роль для {user_id}: {role}")
        role = None

    if web_app_data == "closed":
        await message.answer(
            "Асосий менюга қайтинг:",
            reply_markup=get_main_menu(role)
        )
        await state.clear()
        logger.info(f"Пользователь {user_id} возвращён в главное меню из Web App")
    else:
        logger.warning(f"Неизвестные данные из Web App от {user_id}: '{web_app_data}'")
        await message.answer("Неверный формат данных из Web App.", reply_markup=get_main_menu(role))

# API-эндпоинты
async def get_all_data(table: str, status: Optional[str] = None, page: int = 1, per_page: int = 20) -> List[Dict[str, Any]]:
    """Получает данные из таблицы с пагинацией."""
    if table not in ["products", "requests"]:
        logger.error(f"Недопустимая таблица: {table}")
        return []
    offset = (page - 1) * per_page
    cache_key = f"cache:{table}:{status}:{page}:{per_page}"
    cached = await dp.storage.redis.get(cache_key)
    if cached:
        logger.debug(f"Данные из кэша для {cache_key}")
        return json.loads(cached)
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            if table == "products":
                query = """
                    SELECT p.*, u.region
                    FROM products p
                    JOIN users u ON p.user_id = u.id
                    WHERE p.status != 'hidden' AND p.created_at >= ?
                """
                params = [format_uz_datetime(datetime.now(pytz.UTC) - timedelta(days=30))]
                if status:
                    query += " AND p.status = ?"
                    params.append(status)
                query += " LIMIT ? OFFSET ?"
                params.extend([per_page, offset])
                cursor = await conn.execute(query, params)
            else:
                query = """
                    SELECT *
                    FROM requests
                    WHERE status != 'hidden' AND created_at >= ?
                """
                params = [format_uz_datetime(datetime.now(pytz.UTC) - timedelta(days=30))]
                if status:
                    query += " AND status = ?"
                    params.append(status)
                query += " LIMIT ? OFFSET ?"
                params.extend([per_page, offset])
                cursor = await conn.execute(query, params)
            items = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            result = [dict(zip(columns, item)) for item in items]

            for item in result:
                if "created_at" in item and item["created_at"]:
                    created_at_dt = parse_uz_datetime(item["created_at"])
                    item["created_at"] = format_uz_datetime(created_at_dt) if created_at_dt else "Не указано"
                if "archived_at" in item and item["archived_at"]:
                    archived_at_dt = parse_uz_datetime(item["archived_at"])
                    item["archived_at"] = format_uz_datetime(archived_at_dt) if archived_at_dt else "Не указано"
                if "notified" in item:
                    item["notified"] = bool(item["notified"])

            await dp.storage.redis.setex(cache_key, 300, json.dumps(result))
            logger.debug(f"Данные из таблицы {table} (status={status}, page={page}): {len(result)} записей")
            return result
    except aiosqlite.Error as e:
        logger.error(f"Ошибка при загрузке данных из {table}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Неожиданная ошибка в get_all_data для таблицы {table}: {e}", exc_info=True)
        return []

@app.route('/all_products')
async def get_all_products():
    """Возвращает активные продукты."""
    logger.info("Получен запрос на /all_products")
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data or not check_webapp_signature(BOT_TOKEN, init_data):
        logger.error("Недействительный initData")
        return jsonify({"error": "Invalid initData"}), 403
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    products = await get_all_data("products", "active", page, per_page)
    if not products:
        logger.error("Ошибка при получении продуктов")
        return jsonify({"error": "Failed to fetch products"}), 500
    logger.info(f"Успешно возвращено {len(products)} продуктов")
    return jsonify(products), 200

@app.route('/all_requests')
async def get_all_requests():
    """Возвращает активные запросы."""
    logger.info("Получен запрос на /all_requests")
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data or not check_webapp_signature(BOT_TOKEN, init_data):
        logger.error("Недействительный initData")
        return jsonify({"error": "Invalid initData"}), 403
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    requests_data = await get_all_data("requests", "active", page, per_page)
    if not requests_data:
        logger.error("Ошибка при получении запросов")
        return jsonify({"error": "Failed to fetch requests"}), 500
    logger.info(f"Успешно возвращено {len(requests_data)} запросов")
    return jsonify(requests_data), 200

@app.route('/archive')
async def get_archive():
    """Возвращает архивные продукты и запросы."""
    logger.info("Получен запрос на /archive")
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data or not check_webapp_signature(BOT_TOKEN, init_data):
        logger.error("Недействительный initData")
        return jsonify({"error": "Invalid initData"}), 403
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    archived_products = await get_all_data("products", "archived", page, per_page)
    archived_requests = await get_all_data("requests", "archived", page, per_page)
    archived = archived_products + archived_requests
    if not archived:
        logger.error("Ошибка при получении архивных данных")
        return jsonify({"error": "Failed to fetch archive"}), 500
    logger.info(f"Успешно возвращено {len(archived)} архивных записей")
    return jsonify(archived), 200

@app.route('/get_user_phone')
async def get_user_phone():
    """Возвращает номер телефона пользователя."""
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data or not check_webapp_signature(BOT_TOKEN, init_data):
        logger.error("Недействительный initData")
        return jsonify({"error": "Invalid initData"}), 403
    user_id = request.args.get('user_id')
    logger.info(f"Запрос номера телефона для user_id: {user_id}")
    if not user_id:
        return jsonify({"error": "User ID is required"}), 400

    try:
        user_id_int = int(user_id)
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                    "SELECT phone_number, region FROM users WHERE id = ?",
                    (user_id_int,)
            ) as cursor:
                result = await cursor.fetchone()
        if result:
            response = {"phone_number": result[0], "region": result[1] or "Не указан"}
            logger.info(f"Успешно возвращён номер телефона для user_id={user_id}: {response}")
            return jsonify(response), 200
        logger.warning(f"Пользователь с ID {user_id_int} не найден")
        return jsonify({"error": "User not found"}), 404
    except ValueError:
        logger.error(f"Некорректный user_id: {user_id}", exc_info=True)
        return jsonify({"error": "Invalid user_id format"}), 400
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при получении номера телефона для {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Database error"}), 500

@app.route('/photo/<file_id>')
async def get_photo(file_id: str):
    """Возвращает фото по file_id."""
    logger.info(f"Запрос фото с file_id: {file_id}")
    async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
        async with conn.execute(
            "SELECT unique_id FROM products WHERE photo_id = ? AND status = 'active'", (file_id,)
        ) as cursor:
            if not await cursor.fetchone():
                logger.warning(f"Фото {file_id} не найдено или не активно")
                return jsonify({"error": "File not found"}), 404
    get_file_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
    try:
        get_file_response = requests.get(get_file_url, timeout=10)
        get_file_response.raise_for_status()
        file_data = get_file_response.json()
        if not file_data.get("ok"):
            logger.error(f"Неверный ответ от Telegram getFile: {file_data}")
            return jsonify({"error": "Invalid file data", "details": file_data}), 500

        file_path = file_data["result"]["file_path"]
        if not file_path.endswith(('.jpg', '.jpeg', '.png')):
            logger.error(f"Неподдерживаемый тип файла: {file_path}")
            return jsonify({"error": "Unsupported file type"}), 400
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        response = requests.get(file_url, timeout=10)
        response.raise_for_status()
        if len(response.content) > 10 * 1024 * 1024:  # 10 MB
            logger.error(f"Слишком большой файл для {file_id}")
            return jsonify({"error": "File too large"}), 400
        logger.debug(f"Успешно загружено фото {file_id}")
        return response.content, 200, {'Content-Type': 'image/jpeg'}
    except requests.RequestException as e:
        logger.error(f"Ошибка при загрузке фото {file_id}: {e}", exc_info=True)
        return jsonify({"error": "Photo fetch error", "details": str(e)}), 500
    except Exception as e:
        logger.error(f"Неожиданная ошибка при загрузке фото {file_id}: {e}", exc_info=True)
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500

@app.route(WEBHOOK_PATH, methods=['POST'])
async def webhook_handler():
    """Обрабатывает вебхук от Telegram."""
    update_data = await request.get_json()
    if not update_data:
        logger.error("Webhook получил пустые данные")
        return jsonify({"ok": True}), 200

    update_id = update_data.get('update_id')
    logger.debug(f"Webhook получил update_id={update_id}")
    try:
        await dp.feed_raw_update(bot, update_data)
        return jsonify({"ok": True}), 200
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука update_id={update_id}: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500

async def catch_all(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает необработанные сообщения."""
    user_id = message.from_user.id
    allowed, role = await check_role(message, allow_unregistered=True)
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role) if role else "Нет роли"
    current_state = await state.get_state()
    logger.warning(
        f"catch_all: Сообщение типа {message.content_type} '{message.text}' от {user_id} (роль: {display_role}, состояние: {current_state}) не обработано"
    )
    await message.answer("Асосий меню:", reply_markup=get_main_menu(role))
    await state.clear()

# Настройка команд и обработчиков
async def set_bot_commands() -> None:
    """Устанавливает команды бота."""
    commands = [
        BotCommand(command="/start", description="Ботни бошлаш"),
        BotCommand(command="/myid", description="Менинг ID имни кўриш"),
        BotCommand(command="/subscribe", description="Обуна маълумоти"),
    ]
    await bot.set_my_commands(commands)
    logger.info("Команды бота установлены")

def setup_handlers(dp: Dispatcher) -> None:
    """Регистрирует обработчики."""
    dp.message.register(cmd_start, F.text == "/start")
    dp.message.register(open_webapp, F.text == "Эълонлар доскаси")
    dp.message.register(handle_webapp_close, F.web_app_data)
    dp.include_router(registration_router)
    dp.include_router(products_router)
    register_profile_handlers(dp)
    register_request_handlers(dp)
    register_common_handlers(dp)
    register_admin_handlers(dp)
    dp.message.register(catch_all)
    logger.info("Обработчики зарегистрированы")

# Запуск и завершение
async def on_startup(dispatcher: Dispatcher, bot_instance: Bot):
    """Выполняется при запуске бота."""
    logger.info("Запуск бота...")
    try:
        # Инициализация Redis
        try:
            storage = await connect_redis()
            dispatcher.storage = storage
            logger.info("Успешно подключено к RedisStorage.")
        except ConnectionError as e:
            logger.critical(f"Не удалось подключиться к Redis: {e}", exc_info=True)
            sys.exit("Ошибка подключения к Redis")

        if not os.path.exists(CERT_PATH) or not os.path.exists(KEY_PATH):
            raise FileNotFoundError(f"Certificate or key not found: {CERT_PATH}, {KEY_PATH}")
        await init_db()
        logger.info("База данных инициализирована")
        await set_bot_commands()
        logger.info("Команды бота установлены")

        logger.info(f"Установка вебхука: {WEBHOOK_URL}")
        await bot_instance.delete_webhook(drop_pending_updates=True)
        webhook_set = await bot_instance.set_webhook(
            url=WEBHOOK_URL,
            drop_pending_updates=True
        )
        if webhook_set:
            logger.info(f"Вебхук успешно установлен: {WEBHOOK_URL}")
            webhook_info = await bot_instance.get_webhook_info()
            logger.info(f"Информация о вебхуке: url={webhook_info.url}, pending={webhook_info.pending_update_count}")
        else:
            logger.error("Не удалось установить вебхук")
            sys.exit("Ошибка установки вебхука")

        asyncio.create_task(check_expired_items(bot_instance, dispatcher.storage))
        logger.info("Фоновая задача проверки истечения срока создана")
    except Exception as e:
        logger.critical(f"Ошибка при запуске: {e}", exc_info=True)
        sys.exit("Ошибка запуска")

async def on_shutdown(dispatcher: Dispatcher, bot_instance: Bot):
    """Выполняется при завершении работы бота."""
    logger.info("Завершение работы бота...")
    try:
        await bot_instance.delete_webhook(drop_pending_updates=True)
        logger.info("Вебхук удалён")
        await dispatcher.storage.redis.delete(*await dispatcher.storage.redis.keys("aiogram:*"))
        logger.info("Очищены состояния в Redis")
        await dispatcher.storage.close()
        logger.info("Хранилище закрыто")
        await bot_instance.session.close()
        logger.info("Сессия бота закрыта")
    except Exception as e:
        logger.error(f"Ошибка при завершении: {e}", exc_info=True)

async def main() -> None:
    """Главная функция запуска."""
    global bot
    logger.info("Начало выполнения main()")
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    setup_handlers(dp)
    logger.info("Обработчики установлены")
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    logger.info(f"Запуск Quart сервера на порту {PORT}...")
    config = hypercorn.config.Config()
    config.bind = [f"0.0.0.0:{PORT}"]
    config.certfile = CERT_PATH
    config.keyfile = KEY_PATH
    config.graceful_timeout = 30
    config.keep_alive_timeout = 75
    config.workers = 1  # Ограничить для экономии памяти
    logger.info(f"Запуск сервера: bind={config.bind}, certfile={config.certfile}, keyfile={config.keyfile}")
    try:
        await hypercorn.asyncio.serve(app, config)
    except Exception as e:
        logger.critical(f"Ошибка запуска сервера: {e}", exc_info=True)
        sys.exit("Ошибка запуска сервера")

if __name__ == "__main__":
    logger.info("Запуск main.py")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}", exc_info=True)
