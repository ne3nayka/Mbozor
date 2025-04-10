import asyncio
import logging
import os
import sys
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ReplyKeyboardMarkup, WebAppInfo, ReplyKeyboardRemove
from quart import Quart, jsonify, request, send_from_directory
from quart_cors import cors
import requests

from config import BOT_TOKEN, ROLES, SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE, LOG_LEVEL, ADMIN_IDS, ROLE_MAPPING, \
    WEBAPP_URL, DB_NAME
from database import init_db, register_user, activate_trial
from registration import register_handlers as register_registration_handlers
from profile import register_handlers as register_profile_handlers
from user_requests import register_handlers as register_request_handlers
from common import register_handlers as register_common_handlers
from admin import register_handlers as register_admin_handlers, AdminStates
from expiration_handler import check_expired_items
from utils import check_role, make_keyboard, MONTHS_UZ, check_subscription, format_uz_datetime, parse_uz_datetime

__all__ = ["get_main_menu", "get_admin_menu", "get_profile_menu", "get_requests_menu", "get_ads_menu"]

# Настройка логирования
logger = logging.getLogger(__name__)

class UzbekDateFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created)
        eng_date = dt.strftime("%d %B %Y йил %H:%M:%S")
        for eng, uz in MONTHS_UZ.items():
            eng_date = eng_date.replace(eng, uz)
        return eng_date

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "bot.log")),
        logging.StreamHandler(sys.stdout)
    ],
    force=True
)

formatter = UzbekDateFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
for handler in logging.getLogger().handlers:
    handler.setFormatter(formatter)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

# Инициализация веб-сервера
app = Quart(__name__)
app.config["PROVIDE_AUTOMATIC_OPTIONS"] = True  # Исправление ошибки
app = cors(app, allow_origin="*", allow_methods=["GET", "POST"])

# Состояния регистрации
class RegistrationStates(StatesGroup):
    awaiting_role = State()
    awaiting_phone = State()

# Middleware для проверки подписки
class SubscriptionCheckMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data: dict) -> None:
        user_id = event.from_user.id
        state: FSMContext = data["state"]
        current_state = await state.get_state()
        logger.debug(
            f"SubscriptionCheckMiddleware: Проверка подписки для {user_id}, текст сообщения: '{event.text}', текущее состояние: {current_state}")
        allowed, role = await check_role(event, allow_unregistered=True)

        if event.text in ["Рўйхатдан ўтиш", "Обуна", "/myid"] or current_state in [RegistrationStates.awaiting_role.state, RegistrationStates.awaiting_phone.state]:
            logger.debug(f"SubscriptionCheckMiddleware: Пропуск проверки подписки для команды '{event.text}' или состояния регистрации для {user_id}")
            await handler(event, data)
            return

        if not allowed or not role:
            logger.debug(f"SubscriptionCheckMiddleware: Пользователь {user_id} не зарегистрирован или роль не определена")
            await handler(event, data)
            return
        if role != ADMIN_ROLE:
            channel_active, bot_active, is_subscribed = await check_subscription(event.bot, user_id)
            logger.debug(
                f"SubscriptionCheckMiddleware: Подписка для {user_id}: channel_active={channel_active}, bot_active={bot_active}, is_subscribed={is_subscribed}")
            if not bot_active:
                await event.answer(
                    "Сизнинг обунангиз тугади. Обуна бўлиш учун 'Обуна' тугмасини босинг",
                    reply_markup=make_keyboard(["Обуна"], columns=1)
                )
                await state.clear()
                logger.info(f"SubscriptionCheckMiddleware: Доступ для {user_id} заблокирован из-за неактивной подписки")
                return
        logger.debug(f"SubscriptionCheckMiddleware: Проверка подписки пройдена для {user_id}, передаём управление дальше")
        await handler(event, data)

dp.message.middleware(SubscriptionCheckMiddleware())

# Функции меню
def get_main_menu(role: str | None) -> ReplyKeyboardMarkup:
    if role == SELLER_ROLE:
        return make_keyboard(["Менинг профилим", "Менинг эълонларим", "Эълон қўшиш", "Эълонлар доскаси"], columns=2)
    elif role == BUYER_ROLE:
        return make_keyboard(["Менинг профилим", "Менинг сўровларим", "Сўров юбориш", "Эълонлар доскаси"], columns=2)
    elif role == ADMIN_ROLE:
        return make_keyboard(["Админ панели"], columns=2)
    return make_keyboard(["Рўйхатдан ўтиш"], columns=1)

def get_admin_menu() -> ReplyKeyboardMarkup:
    return make_keyboard([
        "Фойдаланувчиларни бошқариш", "Эълонларни бошқариш", "Сўровларни бошқариш",
        "Обунани бошқариш", "Архивни бошқариш", "Статистика"
    ], columns=2)

def get_profile_menu(role: str) -> ReplyKeyboardMarkup:
    return make_keyboard(["Профиль ҳақида маълумот", "Профильни таҳрирлаш", "Профильни ўчириш", "Орқага"], columns=2)

def get_requests_menu(role: str | None = None) -> ReplyKeyboardMarkup:
    return make_keyboard(["Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни ёпиш", "Орқага"], columns=2)

def get_ads_menu(role: str | None = None) -> ReplyKeyboardMarkup:
    items = ["Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни ёпиш", "Орқага"] if role == SELLER_ROLE else ["Орқага"]
    return make_keyboard(items, columns=2)

bot.get_main_menu = get_main_menu
bot.get_admin_menu = get_admin_menu
bot.get_profile_menu = get_profile_menu
bot.get_requests_menu = get_requests_menu
bot.get_ads_menu = get_ads_menu

# Обработчики Telegram
@dp.message(F.text == "Эълонлар доскаси")
async def open_webapp(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    allowed, role = await check_role(message, allow_unregistered=True)
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role) if role else None
    reply_markup = make_keyboard(["Эълонлар доскаси", "Орқага"], columns=1)
    reply_markup.keyboard[0][0] = types.KeyboardButton(text="Эълонлар доскаси", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp.html"))
    await message.answer("Эълонлар доскаси:", reply_markup=reply_markup)
    logger.info(f"Пользователь {user_id} (роль: {display_role}) открыл веб-приложение")

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    logger.debug(f"cmd_start: Начало обработки для {user_id}")
    allowed, role = await check_role(message, allow_unregistered=True)
    logger.debug(f"cmd_start: Роль для {user_id}: allowed={allowed}, role={role}")
    if user_id in ADMIN_IDS or role == ADMIN_ROLE:
        await message.answer("Админ панели:", reply_markup=get_admin_menu())
        await state.set_state(AdminStates.main_menu)
        logger.info(f"Админ {user_id} вошел в панель")
        return
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role) if role else None
    logger.debug(f"cmd_start: display_role={display_role}, allowed={allowed}, ROLES={ROLES}")
    if not allowed or display_role not in ROLES:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT blocked FROM deleted_users WHERE user_id = ? AND blocked = TRUE", (user_id,)
            ) as cursor:
                blocked = await cursor.fetchone()
        if blocked:
            await message.answer("Сизнинг Telegram ID админ томонидан блокланган. Админга мурожаат қилинг (@MSMA_UZ).")
            logger.warning(f"Заблокированный пользователь {user_id} пытался запустить бот")
            return
        await message.answer("Хуш келибсиз! Рўйхатдан ўтиш:", reply_markup=get_main_menu(None))
        await state.set_state(RegistrationStates.awaiting_role)
        logger.info(f"cmd_start: Новый пользователь {user_id} получил приглашение к регистрации")
    else:
        await message.answer("Асосий меню:", reply_markup=get_main_menu(display_role))
        logger.info(f"cmd_start: Пользователь {user_id} (роль: {display_role}) получил главное меню")
    await state.clear()

@dp.message(RegistrationStates.awaiting_role, F.text == "Рўйхатдан ўтиш")
async def process_role_selection(message: types.Message, state: FSMContext):
    await message.answer("Ролни танланг:", reply_markup=make_keyboard(["Харидор", "Сотувчи"], columns=2))
    await state.set_state(RegistrationStates.awaiting_phone)

@dp.message(RegistrationStates.awaiting_phone)
async def process_phone_number(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.strip()
    role_display = text
    role = ROLE_MAPPING.get(role_display)
    if role not in [BUYER_ROLE, SELLER_ROLE]:
        await message.answer("Илтимос, 'Харидор' ёки 'Сотувчи' танланг:",
                             reply_markup=make_keyboard(["Харидор", "Сотувчи"], columns=2))
        return
    await state.update_data(role=role, role_display=role_display)
    await message.answer("Телефон рақамингизни киритинг (масалан, +998901234567):",
                         reply_markup=ReplyKeyboardRemove())
    await state.set_state("awaiting_phone_number")

@dp.message(F.state == "awaiting_phone_number")
async def process_phone_input(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    phone_number = message.text.strip()
    if not phone_number.startswith("+998") or len(phone_number) != 13 or not phone_number[1:].isdigit():
        await message.answer("Ното‘г‘ри формат. Илтимос, +998901234567 форматида киритинг:")
        return
    data = await state.get_data()
    role = data["role"]
    role_display = data["role_display"]
    if await register_user(user_id, phone_number, role):
        await activate_trial(user_id)
        await message.answer("Рўйхатдан ўтиш муваффақиятли yakunlandi!",
                             reply_markup=get_main_menu(role))
        logger.info(f"Пользователь {user_id} зарегистрирован с ролью {role}")
    else:
        await message.answer("Регистрацияда хатолик. Админга мурожаат қилинг (@MSMA_UZ).")
    await state.clear()

@dp.message(F.web_app_data)
async def handle_webapp_close(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    web_app_data = message.web_app_data.data
    logger.info(f"Получены данные из Web App от {user_id}: '{web_app_data}'")
    if not web_app_data or web_app_data == "":
        logger.warning(f"Получены пустые данные из Web App от {user_id}, игнорируем.")
        return
    data = await state.get_data()
    role = data.get("user_role") or data.get("role_display")
    if web_app_data == "closed":
        await message.answer(
            "Асосий менюга қайтинг:",
            reply_markup=get_main_menu(role)
        )
        await state.clear()
        logger.info(f"Пользователь {user_id} возвращён в главное меню из Web App")
    else:
        logger.warning(f"Неизвестные данные из Web App от {user_id}: '{web_app_data}'")
        await message.answer("Неверный формат данных из Web App.")

async def catch_all(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    allowed, role = await check_role(message, allow_unregistered=True)
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role) if role else "Нет роли"
    current_state = await state.get_state()
    logger.warning(f"catch_all: Сообщение '{message.text}' от {user_id} (роль: {display_role}, состояние: {current_state}) не обработано")
    await message.answer("Асосий меню:", reply_markup=get_main_menu(display_role))
    await state.clear()
    logger.info(f"catch_all: Пользователь {user_id} возвращён в главное меню")

# Веб-маршруты
async def get_all_data(table: str, status: str | None = None) -> list[dict[str, any]]:
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            if table == "products":
                query = "SELECT p.*, u.region FROM products p JOIN users u ON p.user_id = u.id WHERE p.status != 'hidden'"
                if status:
                    query += " AND p.status = ?"
                    cursor = await conn.execute(query, (status,))
                else:
                    cursor = await conn.execute(query)
            else:
                query = "SELECT * FROM requests WHERE status != 'hidden'"
                if status:
                    query += " AND status = ?"
                    cursor = await conn.execute(query, (status,))
                else:
                    cursor = await conn.execute(query)
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
            logger.debug(f"Данные из таблицы {table} (status={status}): {len(result)} записей")
            return result
    except aiosqlite.Error as e:
        logger.error(f"Ошибка при загрузке данных из {table}: {e}")
        return []
    except Exception as e:
        logger.error(f"Неожиданная ошибка в get_all_data для таблицы {table}: {e}", exc_info=True)
        return []

@app.route('/all_products')
async def get_all_products():
    logger.info("Получен запрос на /all_products")
    products = await get_all_data("products", "active")
    if not products and products != []:
        logger.error("Ошибка при получении продуктов: результат None или некорректен")
        return jsonify({"error": "Failed to fetch products"}), 500
    return jsonify(products), 200

@app.route('/all_requests')
async def get_all_requests():
    logger.info("Получен запрос на /all_requests")
    requests_data = await get_all_data("requests", "active")
    if not requests_data and requests_data != []:
        logger.error("Ошибка при получении запросов: результат None или некорректен")
        return jsonify({"error": "Failed to fetch requests"}), 500
    return jsonify(requests_data), 200

@app.route('/archive')
async def get_archive():
    logger.info("Получен запрос на /archive")
    archived_products = await get_all_data("products", "archived")
    archived_requests = await get_all_data("requests", "archived")
    if (not archived_products and archived_products != []) or (not archived_requests and archived_requests != []):
        logger.error("Ошибка при получении архивных данных")
        return jsonify({"error": "Failed to fetch archive"}), 500
    archived = archived_products + archived_requests
    return jsonify(archived), 200

@app.route('/get_user_phone')
async def get_user_phone():
    user_id = request.args.get('user_id')
    logger.info(f"Запрос номера телефона для user_id: {user_id}")
    if not user_id:
        return jsonify({"error": "User ID is required"}), 400
    try:
        user_id_int = int(user_id)
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                    "SELECT phone_number, region FROM users WHERE id = ?",
                    (user_id_int,)
            ) as cursor:
                result = await cursor.fetchone()
        if result:
            response = {"phone_number": result[0], "region": result[1] or "Не указан"}
            logger.debug(f"Ответ для get_user_phone: {response}")
            return jsonify(response), 200
        logger.warning(f"Пользователь с ID {user_id_int} не найден")
        return jsonify({"error": "User not found"}), 404
    except ValueError:
        logger.error(f"Некорректный user_id: {user_id}")
        return jsonify({"error": "Invalid user_id format"}), 400
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при получении номера телефона для {user_id}: {e}")
        return jsonify({"error": "Database error"}), 500

@app.route('/photo/<file_id>')
async def get_photo(file_id: str):
    logger.info(f"Запрос фото с file_id: {file_id}")
    get_file_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
    try:
        get_file_response = requests.get(get_file_url, timeout=10)
        get_file_response.raise_for_status()
        file_data = get_file_response.json()
        if not file_data.get("ok"):
            logger.error(f"Неверный ответ от Telegram getFile: {file_data}")
            return jsonify({"error": "Invalid file data", "details": file_data}), 500
        file_path = file_data["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        response = requests.get(file_url, timeout=10)
        response.raise_for_status()
        logger.debug(f"Успешно загружено фото {file_id}")
        return response.content, 200, {'Content-Type': 'image/jpeg'}
    except requests.RequestException as e:
        logger.error(f"Ошибка при загрузке фото {file_id}: {e}")
        return jsonify({"error": "Photo fetch error", "details": str(e)}), 500
    except Exception as e:
        logger.error(f"Неожиданная ошибка при загрузке фото {file_id}: {e}", exc_info=True)
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500

@app.route('/webapp.html')
async def serve_webapp():
    logger.info("Запрос на /webapp.html")
    try:
        response = await send_from_directory('.', 'webapp.html')
        logger.debug("Файл webapp.html успешно отправлен")
        return response
    except FileNotFoundError:
        logger.error("Файл webapp.html не найден в текущей директории")
        return jsonify({"error": "Webapp file not found"}), 404
    except Exception as e:
        logger.error(f"Ошибка при отправке webapp.html: {e}", exc_info=True)
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.post('/webhook')
async def telegram_webhook():
    update = await request.get_json()
    if update:
        logger.info(f"Webhook received: {update}")
        await dp.process_update(types.Update(**update))
        return jsonify({"status": "ok"}), 200
    logger.warning("Получен пустой или некорректный webhook-запрос")
    return jsonify({"error": "Invalid update"}), 400

# Настройка обработчиков
def setup_handlers(dp: Dispatcher) -> None:
    from products import register_handlers as register_products_handlers
    dp.message.register(cmd_start, F.text == "/start")
    dp.message.register(open_webapp, F.text == "Эълонлар доскаси")
    dp.message.register(process_role_selection, RegistrationStates.awaiting_role)
    dp.message.register(process_phone_number, RegistrationStates.awaiting_phone)
    dp.message.register(process_phone_input, F.state == "awaiting_phone_number")
    dp.message.register(handle_webapp_close, F.web_app_data)
    dp.message.register(catch_all)
    register_profile_handlers(dp)
    register_request_handlers(dp)
    register_common_handlers(dp)
    register_registration_handlers(dp)
    register_admin_handlers(dp)
    register_products_handlers(dp)
    logger.info("Обработчики зарегистрированы")

# Главная функция
async def main() -> None:
    logger.info("Бот и веб-сервер запускаются...")
    try:
        await init_db()
        logger.info("База данных инициализирована")
        await bot.set_my_commands([
            BotCommand(command="/start", description="Ботни бошлаш"),
            BotCommand(command="/myid", description="Менинг ID имни кўриш"),
            BotCommand(command="/subscribe", description="Обуна маълумоти"),
        ])
        logger.info("Команды бота установлены")
        setup_handlers(dp)
        logger.info("Обработчики зарегистрированы")
        asyncio.create_task(check_expired_items(bot, storage))
        logger.info("Фоновая задача проверки истечения срока создана")

        webhook_url = f"{WEBAPP_URL}/webhook"
        logger.info(f"Установка webhook на {webhook_url}")
        await bot.set_webhook(url=webhook_url)
        logger.info("Webhook успешно установлен")

        from hypercorn.config import Config
        from hypercorn.asyncio import serve
        config = Config()
        config.bind = ["0.0.0.0:5001"]
        logger.info("Запуск веб-сервера на 0.0.0.0:5001")
        await serve(app, config)
    except Exception as e:
        logger.error(f"Ошибка при запуске: {str(e)}", exc_info=True)
        try:
            await bot.delete_webhook()
            logger.info("Webhook удалён из-за ошибки")
        except Exception as cleanup_error:
            logger.error(f"Ошибка при удалении webhook: {str(cleanup_error)}")
    finally:
        logger.info("Бот и сервер завершили работу")
        await bot.session.close()
        await dp.storage.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот и сервер остановлены пользователем")
    except Exception as e:
        logger.error(f"Ошибка запуска: {str(e)}", exc_info=True)
