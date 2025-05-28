import logging
import aiosqlite
from aiogram import types, Dispatcher, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage

from utils import check_role, make_keyboard, check_subscription, format_uz_datetime, parse_uz_datetime, notify_admin, get_main_menu, get_admin_menu
from config import DB_NAME, DB_TIMEOUT, SUBSCRIPTION_PRICES, ROLE_MAPPING, ADMIN_IDS, ADMIN_ROLE
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

ACTION_CANCELLED = "Амал бекор қилинди."
YOUR_ID = "Сизнинг Telegram ID: {telegram_id}"
NOT_REGISTERED = "Сиз рўйхатдан ўтмагансиз."
ERROR_MESSAGE = "Хатолик юз берди. Кейинроқ қайта уриниб кўринг ёки админ билан боғланинг (@ad_mbozor)."

async def get_subscription_expiry(user_id: int) -> str:
    """Получает дату истечения подписки для пользователя."""
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT bot_expires FROM payments WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                result = await cursor.fetchone()
        if result and result[0]:
            bot_expires_dt = parse_uz_datetime(result[0])
            return format_uz_datetime(bot_expires_dt) if bot_expires_dt else "Кўрсатилмаган"
        return "Кўрсатилмаган"
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных в get_subscription_expiry для user_id={user_id}: {e}")
        return "Кўрсатилмаган"

async def send_subscription_info(message: types.Message, user_id: int, role: str, storage: BaseStorage):
    """Отправляет информацию о статусе подписки."""
    try:
        _, bot_active, is_subscribed = await check_subscription(message.bot, user_id, storage)
        if is_subscribed:
            expires = await get_subscription_expiry(user_id)
            await message.answer(
                f"Сизнинг обунангиз {expires} гача амал қилади.",
                reply_markup=get_main_menu(role) if role else make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
        else:
            await message.answer(
                f"Бот обунаси ({SUBSCRIPTION_PRICES['period_days']} кун): {SUBSCRIPTION_PRICES['bot']:,} сўм\n"
                "Тўлов учун @ad_mbozor га ёзинг.",
                reply_markup=make_keyboard(["Орқага"], one_time=True)
            )
        logger.info(f"Пользователь {user_id} запросил информацию о подписке: is_subscribed={is_subscribed}")
    except Exception as e:
        logger.error(f"Ошибка в send_subscription_info для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в send_subscription_info для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            ERROR_MESSAGE,
            reply_markup=get_main_menu(role) if role else make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )

async def start_command(message: types.Message, state: FSMContext, dp: Dispatcher):
    user_id = message.from_user.id
    current_state = await state.get_state()
    username = message.from_user.first_name
    logger.debug(f"start_command: user_id={user_id}, text='{message.text}', state={current_state}")
    try:
        # Очистка текущего состояния
        if current_state:
            await state.clear()
            logger.debug(f"Состояние {current_state} очищено для user_id={user_id} при /start")

        # Проверка, является ли пользователь админом
        if user_id in ADMIN_IDS:
            logger.debug(f"Админ {user_id} перенаправлен в админ-панель")
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                await conn.execute(
                    "INSERT OR REPLACE INTO users (id, role, phone_number, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, ADMIN_ROLE, f"admin_{user_id}", format_uz_datetime(datetime.now(pytz.UTC)))
                )
                await conn.commit()
            await message.answer(
                "Админ панели:",
                reply_markup=get_admin_menu()
            )
            await state.set_state("AdminStates:main_menu")
            logger.info(f"Админ {user_id} вошёл в админ-панель")
            return

        # Проверка, зарегистрирован ли пользователь
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT role, region, district, phone_number FROM users WHERE id = ?",
                (user_id,)
            ) as cursor:
                user = await cursor.fetchone()

        # Новый пользователь
        if not user or not all([user[0], user[1], user[3]]):
            logger.debug(f"Новый пользователь user_id={user_id} перенаправлен на регистрацию")
            await message.answer(
                f"Хуш келибсиз, {username}! Рўйхатдан ўтиш тугмасини босинг:",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state("Registration:start")
            logger.info(f"Новый пользователь {user_id} перенаправлен на регистрацию")
            return

        # Зарегистрированный пользователь
        role = user[0]
        _, bot_active, is_subscribed = await check_subscription(message.bot, user_id, dp.storage)
        logger.debug(f"Подписка: user_id={user_id}, bot_active={bot_active}, is_subscribed={is_subscribed}")

        if not is_subscribed:
            logger.debug(f"Пользователь user_id={user_id} без подписки перенаправлен на подписку")
            await message.answer(
                "Сизнинг обунангиз тугаган. Обуна тугмасини босинг:",
                reply_markup=make_keyboard(["Обуна", "Орқага"], columns=2, one_time=True)
            )
            await state.set_state("Registration:subscription")
            logger.info(f"Пользователь {user_id} перенаправлен на подписку")
            return

        # Пользователь с активной подпиской
        logger.debug(f"Отправка главного меню для user_id={user_id}, role={role}")
        await message.answer(
            "Асосий меню",
            reply_markup=get_main_menu(role)
        )
        logger.info(f"Пользователь {user_id} с ролью {role} вошёл в главное меню")

    except Exception as e:
        logger.error(f"Ошибка в start_command для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в start_command для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            ERROR_MESSAGE,
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )

async def cancel_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        await state.clear()
        allowed, role = await check_role(message, allow_unregistered=True)
        if allowed and role:
            await message.answer(ACTION_CANCELLED, reply_markup=get_main_menu(role))
        else:
            await message.answer(ACTION_CANCELLED, reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True))
        logger.info(f"Пользователь {user_id} отменил действие")
    except Exception as e:
        logger.error(f"Ошибка в cancel_command для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в cancel_command для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            ERROR_MESSAGE,
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )

async def my_id(message: types.Message, state: FSMContext, dp: Dispatcher):
    user_id = message.from_user.id
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        logger.debug(f"Состояние {current_state} очищено для user_id={user_id} при /myid")
    allowed, role = await check_role(message, allow_unregistered=True)
    try:
        _, bot_active, is_subscribed = await check_subscription(message.bot, user_id, dp.storage)
        await message.answer(
            YOUR_ID.format(telegram_id=user_id),
            reply_markup=make_keyboard(["Обуна"], one_time=True) if not is_subscribed else get_main_menu(role)
        )
        logger.info(f"Пользователь {user_id} запросил свой ID")
    except Exception as e:
        logger.error(f"Ошибка в my_id для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в my_id для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            ERROR_MESSAGE,
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )

async def handle_subscription_request(message: types.Message, state: FSMContext, dp: Dispatcher):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"handle_subscription_request: user_id={user_id}, text='{message.text}', state={current_state}")
    try:
        if current_state and current_state != "Registration:subscription":
            await state.clear()
            logger.debug(f"Состояние {current_state} очищено для user_id={user_id} при запросе подписки")
        if message.text not in ["Обуна", "/subscribe"]:
            await message.answer(
                "Илтимос, 'Обуна' тугмасини босинг:",
                reply_markup=make_keyboard(["Обуна"], one_time=True)
            )
            logger.info(f"Пользователь {user_id} отправил некорректный текст для подписки: {message.text}")
            return
        allowed, role = await check_role(message, allow_unregistered=True)
        if not allowed or not role:
            await message.answer(
                NOT_REGISTERED,
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            logger.info(f"Незарегистрированный пользователь {user_id} запросил подписку, роль: None")
            return
        await send_subscription_info(message, user_id, role, dp.storage)
        await state.clear()
        logger.info(f"Пользователь {user_id} с ролью {role} запросил подписку")
    except Exception as e:
        logger.error(f"Ошибка в handle_subscription_request для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в handle_subscription_request для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            ERROR_MESSAGE,
            reply_markup=make_keyboard(["Орқага"], one_time=True)
        )

async def reset_state(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"reset_state: user_id={user_id}, current_state={current_state}")
    await state.clear()
    allowed, role = await check_role(message, allow_unregistered=True)
    reply_markup = get_main_menu(role) if allowed else make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
    await message.answer("Ҳолат тозаланди. Амални танланг:", reply_markup=reply_markup)
    logger.info(f"Состояние сброшено для user_id={user_id}")

def register_handlers(dp: Dispatcher):
    logger.info("Registering common handlers")
    dp.message.register(start_command, Command("start"))
    dp.message.register(cancel_command, Command("cancel"))
    dp.message.register(my_id, Command("myid"))
    dp.message.register(handle_subscription_request, Command("subscribe"))
    dp.message.register(handle_subscription_request, F.text == "Обуна")
    dp.message.register(reset_state, Command("reset"))
    logger.info("Common handlers registered")