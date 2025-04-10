# common.py
import logging
import aiosqlite
from aiogram import types, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from utils import check_role, make_keyboard, check_subscription, format_uz_datetime, parse_uz_datetime
from config import DB_NAME
from datetime import datetime

logger = logging.getLogger(__name__)

# Константы для текстовых сообщений
WELCOME_NEW_USER = "Хуш келибсиз, {username}! Ботдан фойдаланиш учун рўйхатдан ўтишингиз керак."
WELCOME_EXISTING_USER = "Хуш келибсиз, {username}! Сиз аллақачон рўтгансиз."
ACTION_CANCELLED = "Амал бекор қилинди."
SUBSCRIPTION_ACTIVE = (
    "Ботга обуна: Фаол\n"
    "{trial_info}"
    "Тўлиқ обуна (30 кун):\n"
    "1. Каналга обуна: 10,000 сўм\n"
    "2. Бот + Канал: 50,000 сўм/ой\n"
    "Тўловдан сўнг админга ёзинг (@MSMA_UZ) ва user_id ни юборинг: /myid\n"
    "Тўлов усуллари: Click ёки Payme (админдан сўранг)."
)
SUBSCRIPTION_INACTIVE = (
    "Ботга обуна: Фаол эмас\n"
    "Тўлиқ обуна (30 кун):\n"
    "1. Каналга обуна: 10,000 сўм\n"
    "2. Бот + Канал: 50,000 сўм/ой\n"
    "Тўловдан сўнг админга ёзинг (@MSMA_UZ) ва user_id ни юборинг: /myid\n"
    "Тўлов усуллари: Click ёки Payme (админдан сўранг)."
)
SUBSCRIPTION_EXPIRED = "Сизнинг обунангиз тугади. Обуна бўлиш учун 'Обуна' тугмасини босинг."
YOUR_ID = "Сизнинг Telegram ID: {telegram_id}"
NOT_REGISTERED = "Сиз рўйхатдан ўтмагансиз."
ERROR_MESSAGE = "Хатолик юз берди. Кейинроқ уриниб кўринг ёки админ билан боғланинг."

async def get_subscription_info(user_id: int, bot):
    """Общая функция для получения информации о подписке"""
    try:
        channel_active, bot_active, is_subscribed = await check_subscription(bot, user_id)
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                    "SELECT bot_expires, trial_used FROM payments WHERE user_id = ?",
                    (user_id,)
            ) as cursor:
                result = await cursor.fetchone()

        bot_expires = result[0] if result else None
        trial_used = result[1] if result else False
        return channel_active, bot_active, is_subscribed, bot_expires, trial_used
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных для пользователя {user_id}: {e}")
        raise

async def send_subscription_message(message: types.Message, user_id: int, role: str):
    """Общая функция для отправки информации о подписке"""
    try:
        channel_active, bot_active, _, bot_expires, trial_used = await get_subscription_info(user_id, message.bot)

        if not bot_active and bot_expires and parse_uz_datetime(bot_expires) < datetime.now():
            await message.answer(SUBSCRIPTION_EXPIRED, reply_markup=make_keyboard(["Обуна"], columns=1))
            logger.info(f"Пользователь {user_id} с истёкшей подпиской запросил информацию")
        else:
            bot_expires_formatted = format_uz_datetime(parse_uz_datetime(bot_expires)) if bot_expires else "Не указано"
            if bot_active:
                trial_info = (
                    f"Сизга 3 кунлик тест даври берилган. Тест даври {bot_expires_formatted} да тугайди.\n"
                    if not trial_used and bot_expires and (parse_uz_datetime(bot_expires) - datetime.now()).days <= 3
                    else f"Обуна {bot_expires_formatted} гача фаол.\n"
                )
                text = SUBSCRIPTION_ACTIVE.format(trial_info=trial_info)
            else:
                text = SUBSCRIPTION_INACTIVE

            await message.answer(text, reply_markup=message.bot.get_main_menu(role))
            logger.info(f"Пользователь {user_id} проверил подписку: active={bot_active}, expires={bot_expires}")
    except Exception as e:
        logger.error(f"Ошибка при отправке информации о подписке: {e}")
        await message.answer(
            ERROR_MESSAGE,
            reply_markup=message.bot.get_main_menu(role) if role else make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )

async def start_command(message: types.Message):
    username = message.from_user.first_name or message.from_user.username or "Фойдаланувчи"
    user_id = message.from_user.id
    allowed, role = await check_role(message, allow_unregistered=True)
    if allowed and role:
        await message.answer(WELCOME_EXISTING_USER.format(username=username),
                             reply_markup=message.bot.get_main_menu(role))
        logger.info(f"Существующий пользователь {user_id} (роль: {role}) запустил бот")
    else:
        await message.answer(WELCOME_NEW_USER.format(username=username),
                             reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True))
        logger.info(f"Новый пользователь {user_id} запустил бот")

async def cancel_command(message: types.Message, state: FSMContext):
    await state.clear()
    allowed, role = await check_role(message, allow_unregistered=True)
    if allowed and role:
        await message.answer(ACTION_CANCELLED, reply_markup=message.bot.get_main_menu(role))
    else:
        await message.answer(ACTION_CANCELLED, reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True))
    logger.info(f"Пользователь {message.from_user.id} отменил действие")

async def my_id(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        logger.debug(f"Состояние {current_state} сброшено для {user_id} перед выполнением /myid")
    allowed, role = await check_role(message, allow_unregistered=True)
    try:
        subscription_status = await check_subscription(message.bot, user_id)
        bot_active = subscription_status[1]
        await message.answer(
            YOUR_ID.format(telegram_id=user_id),
            reply_markup=make_keyboard(["Обуна"], columns=1) if not bot_active else message.bot.get_main_menu(role)
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке команды /myid: {e}")
        await message.answer(ERROR_MESSAGE, reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True))

async def handle_subscription_request(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        logger.debug(f"Состояние {current_state} сброшено для {user_id} перед выполнением /subscribe или 'Обуна'")
    allowed, role = await check_role(message, allow_unregistered=True)
    if not allowed or not role:
        await message.answer(NOT_REGISTERED, reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True))
        logger.info(f"Незарегистрированный пользователь {user_id} запросил подписку")
        return
    await send_subscription_message(message, user_id, role)



def register_handlers(dp: Dispatcher):
    dp.message.register(start_command, Command("start"))
    dp.message.register(cancel_command, Command("cancel"))
    dp.message.register(my_id, Command("myid"))
    dp.message.register(handle_subscription_request, Command("subscribe"))
    dp.message.register(handle_subscription_request, F.text == "Обуна")
