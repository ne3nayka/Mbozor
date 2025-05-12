import logging
import aiosqlite
from aiogram import types, Dispatcher, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from utils import check_role, make_keyboard, check_subscription, format_uz_datetime, parse_uz_datetime, notify_admin, get_main_menu
from config import DB_NAME, DB_TIMEOUT, SUBSCRIPTION_PRICES
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

ACTION_CANCELLED = "Амал бекор қилинди."
SUBSCRIPTION_ACTIVE = (
    "Ботга обуна: Фаол\n"
    "{trial_info}"
    "Тўлиқ обуна ({period_days} кун):\n"
    "1. Каналга обуна: {channel_price:,} сўм\n"
    "2. Бот + Канал: {bot_price:,} сўм/ой\n"
    "Тўловдан сўнг админга ёзинг (@ad_mbozor) ва user_id ни юборинг: /myid\n"
    "Тўлов усуллари: Click ёки Payme (админдан сўранг)."
)
SUBSCRIPTION_INACTIVE = (
    "Ботга обуна: Фаол эмас\n"
    "Тўлиқ обуна ({period_days} кун):\n"
    "1. Каналга обуна: {channel_price:,} сўм\n"
    "2. Бот + Канал: {bot_price:,} сўм/ой\n"
    "Тўловдан сўнг админга ёзинг (@ad_mbozor) ва user_id ни юборинг: /myid\n"
    "Тўлов усуллари: Click ёки Payme (админдан сўранг)."
)
SUBSCRIPTION_EXPIRED = "Сизнинг обунангиз муддати тугади. Обуна бўлиш учун 'Обуна' тугмасини босинг."
YOUR_ID = "Сизнинг Telegram ID: {telegram_id}"
NOT_REGISTERED = "Сиз рўйхатдан ўтмагансиз."
ERROR_MESSAGE = "Хатолик юз берди. Кейинроқ қайта уриниб кўринг ёки админ билан боғланинг (@ad_mbozor)."

async def get_subscription_info(user_id: int, bot: Bot) -> tuple[bool, bool, str | None, bool]:
    try:
        channel_active, bot_active, _ = await check_subscription(bot, user_id)
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                    "SELECT bot_expires, trial_used FROM payments WHERE user_id = ?",
                    (user_id,)
            ) as cursor:
                result = await cursor.fetchone()
        bot_expires = result[0] if result else None
        trial_used = bool(result[1]) if result else False
        logger.debug(f"get_subscription_info: user_id={user_id}, bot_expires={bot_expires}, trial_used={trial_used}")
        return channel_active, bot_active, bot_expires, trial_used
    except aiosqlite.Error as e:
        logger.error(f"Маълумотлар базаси хатолиги user_id={user_id}: {e}")
        await notify_admin(f"get_subscription_info да маълумотлар базаси хатолиги user_id={user_id}: {str(e)}", bot=bot)
        raise

async def send_subscription_message(message: types.Message, user_id: int, role: str):
    try:
        channel_active, bot_active, bot_expires, trial_used = await get_subscription_info(user_id, message.bot)
        subscription_prices = {
            "period_days": SUBSCRIPTION_PRICES["period_days"],
            "channel_price": SUBSCRIPTION_PRICES.get("channel", 10000),
            "bot_price": SUBSCRIPTION_PRICES.get("bot_and_channel", 50000)
        }

        bot_expires_dt = parse_uz_datetime(bot_expires) if bot_expires else None
        if bot_expires_dt and bot_expires_dt < datetime.now(pytz.UTC):
            await message.answer(
                SUBSCRIPTION_EXPIRED,
                reply_markup=make_keyboard(["Обуна"], one_time=True)
            )
            logger.info(f"Фойдаланувчи {user_id} обуна муддати тугаганлиги ҳақида маълумот сўради")
        else:
            bot_expires_formatted = format_uz_datetime(bot_expires_dt) if bot_expires_dt else "Кўрсатилмаган"
            if bot_active:
                trial_info = (
                    f"Сизга 3 кунлик синов муддати берилган. Синов муддати {bot_expires_formatted} да тугайди.\n"
                    if not trial_used and bot_expires_dt and (bot_expires_dt - datetime.now(pytz.UTC)).days <= 3
                    else f"Обуна {bot_expires_formatted} гача фаол.\n"
                )
                text = SUBSCRIPTION_ACTIVE.format(trial_info=trial_info, **subscription_prices)
            else:
                text = SUBSCRIPTION_INACTIVE.format(**subscription_prices)

            await message.answer(
                text,
                reply_markup=get_main_menu(role) if role else make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            logger.info(f"Фойдаланувчи {user_id} обунани текширди: фаол={bot_active}, муддати={bot_expires}")
    except Exception as e:
        logger.error(f"Обуна маълумотини юборишда хатолик user_id={user_id}: {e}")
        await notify_admin(f"send_subscription_message да хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            ERROR_MESSAGE,
            reply_markup=get_main_menu(role) if role else make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )

async def start_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"start_command: user_id={user_id}, text='{message.text}', state={current_state}")
    try:
        # Очистка текущего состояния
        if current_state:
            await state.clear()
            logger.debug(f"Состояние {current_state} очищено для user_id={user_id} при /start")

        allowed, role = await check_role(message, allow_unregistered=True)
        if not allowed or not role:
            await message.answer(
                "Илтимос, рўйхатдан ўтинг:",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state("Registration:start")
            logger.info(f"Незарегистрированный пользователь {user_id} перенаправлен на регистрацию")
            return

        channel_active, bot_active, is_subscribed = await check_subscription(message.bot, user_id)
        logger.debug(f"Подписка: user_id={user_id}, channel_active={channel_active}, bot_active={bot_active}, is_subscribed={is_subscribed}")
        if not is_subscribed and role != "admin":
            await message.answer(
                "Сизда фаол обуна мавжуд эмас. 'Обуна' тугмасини босинг:",
                reply_markup=make_keyboard(["Обуна", "Орқага"], columns=2, one_time=True)
            )
            await state.set_state("Registration:subscription")
            logger.info(f"Пользователь {user_id} перенаправлен на подписку")
            return

        await message.answer("Асосий меню:", reply_markup=get_main_menu(role))
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
        logger.info(f"Фойдаланувчи {user_id} амални бекор қилди")
    except Exception as e:
        logger.error(f"/cancel командасида хатолик user_id={user_id}: {e}")
        await notify_admin(f"cancel_command да хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            ERROR_MESSAGE,
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )

async def my_id(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        logger.debug(f"Ҳолат {current_state} /myid дан олдин user_id={user_id} учун тозаланди")
    allowed, role = await check_role(message, allow_unregistered=True)
    try:
        _, bot_active, _ = await check_subscription(message.bot, user_id)
        await message.answer(
            YOUR_ID.format(telegram_id=user_id),
            reply_markup=make_keyboard(["Обуна"], one_time=True) if not bot_active else get_main_menu(role)
        )
        logger.info(f"Фойдаланувчи {user_id} ўз ID сини сўради")
    except Exception as e:
        logger.error(f"/myid командасида хатолик user_id={user_id}: {e}")
        await notify_admin(f"my_id да хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            ERROR_MESSAGE,
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )

async def handle_subscription_request(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    if current_state and current_state != "Registration:subscription":
        await state.clear()
        logger.debug(f"Ҳолат {current_state} /subscribe ёки 'Обуна' дан олдин user_id={user_id} учун тозаланди")
    allowed, role = await check_role(message, allow_unregistered=True)
    if not allowed or not role:
        await message.answer(
            NOT_REGISTERED,
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        logger.info(f"Рўйхатдан ўтмаган фойдаланувчи {user_id} обуна сўради, роль: None")
        return
    logger.info(f"Фойдаланувчи {user_id} обуна сўради, роль: {role}")
    await message.answer(
        "Тўлиқ обуна (30 кун):\n"
        "1. Каналга обуна: 10,000 сўм\n"
        "2. Бот + Канал: 50,000 сўм/ой\n"
        "Тўловдан сўнг админга ёзинг (@ad_mbozor) ва user_id ни юборинг: /myid\n"
        "Тўлов усуллари: Click ёки Payme (админдан сўранг).",
        reply_markup=make_keyboard(["Асосий меню"], one_time=True)
    )
    await state.clear()

async def reset_state(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"reset_state: user_id={user_id}, current_state={current_state}")
    await state.clear()
    allowed, role = await check_role(message, allow_unregistered=True)
    reply_markup = get_main_menu(role) if allowed else make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
    await message.answer("Состояние сброшено. Выберите действие:", reply_markup=reply_markup)
    logger.info(f"Состояние сброшено для user_id={user_id}")

def register_handlers(dp: Dispatcher):
    dp.message.register(start_command, Command("start"))
    dp.message.register(cancel_command, Command("cancel"))
    dp.message.register(my_id, Command("myid"))
    dp.message.register(handle_subscription_request, Command("subscribe"))
    dp.message.register(handle_subscription_request, F.text == "Обуна")
    dp.message.register(reset_state, Command("reset"))
    logger.info("Умумий обработчиклар рўйхатдан ўтказилди")