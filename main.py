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

from config import BOT_TOKEN, ROLES, SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE, LOG_LEVEL, ADMIN_IDS, ROLE_MAPPING, \
    WEBAPP_URL, DB_NAME
from database import init_db, register_user, activate_trial
from registration import register_handlers as register_registration_handlers
from profile import register_handlers as register_profile_handlers
from user_requests import register_handlers as register_request_handlers
from common import register_handlers as register_common_handlers
from admin import register_handlers as register_admin_handlers, AdminStates
from expiration_handler import check_expired_items
from utils import check_role, make_keyboard, MONTHS_UZ, check_subscription

__all__ = ["get_main_menu", "get_admin_menu", "get_profile_menu", "get_requests_menu", "get_ads_menu"]

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

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

class RegistrationStates(StatesGroup):
    awaiting_role = State()
    awaiting_phone = State()

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

@dp.message(F.text == "Эълонлар доскаси")
async def open_webapp(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    allowed, role = await check_role(message, allow_unregistered=True)
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role) if role else None
    reply_markup = make_keyboard(["Эълонлар доскаси", "Орқага"], columns=1)
    reply_markup.keyboard[0][0] = types.KeyboardButton(text="Эълонлар доскаси", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp.html"))
    await message.answer("Эълонлар доскаси:", reply_markup=reply_markup)
    logger.info(f"Пользователь {user_id} (роль: {display_role}) открыл веб-приложение")

async def set_bot_commands() -> None:
    commands = [
        BotCommand(command="/start", description="Ботни бошлаш"),
        BotCommand(command="/myid", description="Менинг ID имни кўриш"),
        BotCommand(command="/subscribe", description="Обуна маълумоти"),
    ]
    await bot.set_my_commands(commands)
    logger.info("Команды бота установлены")

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
        # Проверка на блокировку
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
        await activate_trial(user_id)  # Активируем пробный период
        await message.answer("Рўйхатдан ўтиш муваффақиятли yakunlandi!",
                             reply_markup=get_main_menu(role))
        logger.info(f"Пользователь {user_id} зарегистрирован с ролью {role}")
    else:
        await message.answer("Регистрацияда хатолик. Админга мурожаат қилинг (@MSMA_UZ).")
    await state.clear()

async def catch_all(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    allowed, role = await check_role(message, allow_unregistered=True)
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role) if role else "Нет роли"
    current_state = await state.get_state()
    logger.warning(f"catch_all: Сообщение '{message.text}' от {user_id} (роль: {display_role}, состояние: {current_state}) не обработано")
    await message.answer("Асосий меню:", reply_markup=get_main_menu(display_role))
    await state.clear()
    logger.info(f"catch_all: Пользователь {user_id} возвращён в главное меню")

def setup_handlers(dp: Dispatcher) -> None:
    from products import register_handlers as register_products_handlers
    dp.message.register(cmd_start, F.text == "/start")
    dp.message.register(open_webapp, F.text == "Эълонлар доскаси")
    dp.message.register(process_role_selection, RegistrationStates.awaiting_role)
    dp.message.register(process_phone_number, RegistrationStates.awaiting_phone)
    dp.message.register(process_phone_input, F.state == "awaiting_phone_number")
    register_profile_handlers(dp)
    register_request_handlers(dp)
    register_common_handlers(dp)
    register_registration_handlers(dp)
    register_admin_handlers(dp)
    register_products_handlers(dp)
    from webapp import handle_webapp_close
    dp.message.register(handle_webapp_close, F.web_app_data)
    dp.message.register(catch_all)
    logger.info("Обработчики зарегистрированы")

async def main() -> None:
    logger.info("Бот запускается...")
    try:
        await init_db()
        logger.info("База данных инициализирована")
        await set_bot_commands()
        logger.info("Команды бота установлены")
        setup_handlers(dp)
        logger.info("Обработчики зарегистрированы")
        logger.info("Создание фоновой задачи проверки истечения срока")
        asyncio.create_task(check_expired_items(bot, storage))
        logger.info("Фоновая задача создана, запуск polling")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {str(e)}", exc_info=True)
    finally:
        logger.info("Бот завершил работу")
        await bot.session.close()
        await dp.storage.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Ошибка запуска: {str(e)}", exc_info=True)
