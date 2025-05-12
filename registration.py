import aiosqlite
import logging
import asyncio
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from config import DB_NAME, DB_TIMEOUT, SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE, ROLES, ROLE_MAPPING, ROLE_DISPLAY_NAMES, MAX_COMPANY_NAME_LENGTH, ADMIN_IDS
from database import register_user, activate_trial, init_db, clear_user_state, generate_user_id
from regions import get_all_regions, get_districts_for_region
from utils import make_keyboard, get_main_menu, check_subscription, format_uz_datetime, notify_admin, get_admin_menu, parse_uz_datetime
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)
router = Router()

class Registration(StatesGroup):
    start = State()
    phone = State()
    role = State()
    region = State()
    district = State()
    company_name = State()
    subscription = State()

async def process_start_registration(message: types.Message, state: FSMContext):
    """Рўйхатдан ўтишни бошлайди."""
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "Фойдаланувчи"
    current_state = await state.get_state()
    logger.info(f"process_start_registration: user_id={user_id}, first_name={first_name}, text='{message.text}', state={current_state}")

    # Очистка состояния FSM с повторной попыткой
    for attempt in range(3):
        try:
            logger.debug(f"Очистка состояния FSM для user_id={user_id}, попытка {attempt + 1}")
            await clear_user_state(user_id, state.storage, bot=message.bot)
            await state.clear()
            logger.debug(f"Состояние FSM успешно очищено для user_id={user_id}")
            break
        except Exception as e:
            logger.error(f"Ошибка очистки состояния FSM для user_id={user_id}, попытка {attempt + 1}: {e}", exc_info=True)
            if attempt < 2:
                await asyncio.sleep(2)
                continue
            await notify_admin(f"Ошибка очистки состояния FSM для user_id={user_id} после 3 попыток: {str(e)}", bot=message.bot)
            await message.answer(
                f"Хатолик юз берди: Номаълум хато. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            return

    # Проверка блокировки
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            logger.debug(f"Checking blocked status for user_id={user_id}")
            async with conn.execute(
                "SELECT blocked FROM deleted_users WHERE user_id = ? AND blocked = TRUE", (user_id,)
            ) as cursor:
                blocked = await cursor.fetchone()
            if blocked:
                await message.answer(
                    "Сизнинг Telegram ID блокланган. Админга мурожаат қилинг (@MSMA_UZ).",
                    reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
                )
                logger.warning(f"Заблокированный пользователь {user_id} пытался начать регистрацию")
                await state.clear()
                return
    except aiosqlite.Error as e:
        logger.error(f"Ошибка проверки блокировки user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка проверки блокировки user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        return

    # Проверка статуса админа
    if user_id in ADMIN_IDS:
        await message.answer("Админ панели:", reply_markup=get_admin_menu())
        await state.set_state("AdminStates:main_menu")
        logger.info(f"Админ {user_id} вошел в панель")
        return

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            logger.debug(f"Checking existing user for user_id={user_id}")
            async with conn.execute(
                "SELECT id, role, region, district, phone_number, unique_id FROM users WHERE id = ?", (user_id,)
            ) as cursor:
                existing_user = await cursor.fetchone()
            if existing_user and existing_user[1] and existing_user[2] and existing_user[3]:
                # Пользователь полностью зарегистрирован
                db_role = existing_user[1]
                display_role = ROLE_DISPLAY_NAMES.get(db_role, db_role)
                phone = existing_user[4]
                unique_id = existing_user[5]
                channel_active, bot_active, is_subscribed = await check_subscription(message.bot, user_id)
                logger.debug(f"User {user_id} subscription: channel_active={channel_active}, bot_active={bot_active}, is_subscribed={is_subscribed}")
                async with conn.execute(
                    "SELECT bot_expires, trial_used FROM payments WHERE user_id = ?", (user_id,)
                ) as cursor:
                    result = await cursor.fetchone()
                bot_expires = result[0] if result else None
                trial_used = bool(result[1]) if result else False
                logger.debug(f"Payment info for user_id={user_id}: bot_expires={bot_expires}, trial_used={trial_used}")

                if is_subscribed:
                    bot_expires_dt = parse_uz_datetime(bot_expires) if bot_expires else None
                    expires_formatted = format_uz_datetime(bot_expires_dt) if bot_expires_dt else "Кўрсатилмаган"
                    await message.answer(
                        f"Сиз рўйхатдан ўтгансиз!\nРоль: {display_role}\nТелефон: {phone}\nОбуна: Фаол (Тугайди: {expires_formatted})",
                        reply_markup=get_main_menu(db_role)
                    )
                else:
                    await message.answer(
                        f"Сиз рўйхатдан ўтгансиз!\nРоль: {display_role}\nТелефон: {phone}\nОбуна: Фаол эмас",
                        reply_markup=make_keyboard(["Обуна"], one_time=True)
                    )
                    await state.set_state(Registration.subscription)
                await state.clear()
                logger.info(f"Фойдаланувчи {user_id} аллақачон рўйхатдан ўтган, роль: {db_role}")
                return
    except aiosqlite.Error as e:
        logger.error(f"Фойдаланувчи {user_id} текширишда хатолик: {e}", exc_info=True)
        await notify_admin(f"Фойдаланувчи {user_id} текширишда маълумотлар базаси хатолиги: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        return

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            logger.debug(f"SQL: DELETE FROM users WHERE id = {user_id} AND (role IS NULL OR region IS NULL OR district IS NULL)")
            await conn.execute("DELETE FROM users WHERE id = ? AND (role IS NULL OR region IS NULL OR district IS NULL)", (user_id,))
            await conn.commit()
        await message.answer(
            f"Хуш келибсиз, {first_name}! Рўйхатдан ўтиш тугмасини босинг:",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        logger.info(f"Фойдаланувчи {user_id} рўйхатдан ўтишни бошлашга чақирилди, ҳолат Registration.start га ўрнатилди")
    except Exception as e:
        logger.error(f"Рўйхатдан ўтиш сўрови юборувда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Рўйхатдан ўтиш сўрови юборувда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        return

async def process_registration_start_state(message: types.Message, state: FSMContext):
    """Registration.start ҳолатида хабарларни қайта ишлайди."""
    user_id = message.from_user.id
    logger.debug(f"process_registration_start_state: user_id={user_id}, text='{message.text}'")
    if message.text != "Рўйхатдан ўтиш":
        try:
            await message.answer(
                "Илтимос, 'Рўйхатдан ўтиш' тугмасини босинг:",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            logger.warning(f"Фойдаланувчи {user_id} нотўғри матн юборди Registration.start да: {message.text}")
        except Exception as e:
            logger.error(f"Нотўғри матнга жавоб беришда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Нотўғри матнга жавоб беришда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        return
    try:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Телефон рақамини улашиш", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            "Илтимос, телефон рақамингизни улашинг:",
            reply_markup=keyboard
        )
        await state.set_state(Registration.phone)
        logger.info(f"Фойдаланувчи {user_id} рўйхатдан ўтишни тасдиқлади, Registration.phone га ўтилди")
    except Exception as e:
        logger.error(f"Рўйхатдан ўтиш бошланишида хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Рўйхатдан ўтиш бошланишида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return

async def process_phone(message: types.Message, state: FSMContext):
    """Телефон рақамини қайта ишлайди."""
    user_id = message.from_user.id
    logger.debug(f"process_phone: user_id={user_id}, text='{message.text}', contact={message.contact}")
    print(f"process_phone started for user_id={user_id}")  # Временное логирование

    if not message.contact:
        try:
            keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="📱 Телефон рақамини улашиш", request_contact=True)]
                ],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            await message.answer(
                "Илтимос, 'Телефон рақамини улашиш' тугмасини босинг:",
                reply_markup=keyboard
            )
            logger.warning(f"Фойдаланувчи {user_id} контакт ўрнига матн юборди: {message.text}")
        except Exception as e:
            logger.error(f"Контакт сўровида хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Контакт сўровда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        return

    contact = message.contact
    phone = contact.phone_number.strip()
    logger.debug(f"Получен телефон user_id={user_id}: {phone}")
    print(f"Phone number: {phone}, user_id: {user_id}")  # Временное логирование

    # Проверка, что пользователь отправил свой номер
    if contact.user_id != user_id:
        await message.answer(
            "Илтимос, фақат ўз телефон рақамингизни улашинг.",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        logger.warning(f"Фойдаланувчи {user_id} отправил чужой номер: {phone}")
        return

    try:
        logger.debug(f"Начало обработки номера для user_id={user_id}")

        # Проверка состояния Redis
        logger.debug(f"Проверка Redis для user_id={user_id}")
        storage = state.storage
        if hasattr(storage, 'redis'):
            try:
                await storage.redis.ping()
                logger.debug(f"Redis доступен для user_id={user_id}")
                print(f"Redis ping successful for user_id={user_id}")
            except Exception as redis_e:
                logger.warning(f"Redis недоступен для user_id={user_id}: {redis_e}")
                print(f"Redis ping failed for user_id={user_id}: {redis_e}")
        else:
            logger.warning(f"Redis не используется, storage={type(storage).__name__}")
            print(f"No Redis, storage type: {type(storage).__name__}")

        # Очистка состояния FSM
        logger.debug(f"Очистка состояния FSM для user_id={user_id}")
        await clear_user_state(user_id, state.storage, bot=message.bot)
        print(f"FSM state cleared for user_id={user_id}")  # Временное логирование

        # Регистрация пользователя
        logger.debug(f"Вызов register_user для user_id={user_id}, phone={phone}")
        print(f"Calling register_user for user_id={user_id}, phone={phone}")
        registered = await register_user(user_id, phone, bot=message.bot)
        logger.debug(f"Результат register_user: registered={registered}")
        print(f"register_user result for user_id={user_id}: {registered}")  # Временное логирование

        if not isinstance(registered, bool):
            logger.error(f"register_user вернул неверный тип: {type(registered).__name__}, значение: {registered}")
            raise ValueError(f"register_user вернул неверный тип: {type(registered).__name__}")

        if not registered:
            # Проверка причины отказа
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                logger.debug(f"SQL: SELECT blocked FROM deleted_users WHERE user_id = {user_id} AND blocked = 1")
                async with conn.execute(
                    "SELECT blocked FROM deleted_users WHERE user_id = ? AND blocked = 1", (user_id,)
                ) as cursor:
                    blocked = await cursor.fetchone()
                if blocked:
                    await message.answer(
                        "Сиз блоклангансиз. Админ билан боғланинг (@MSMA_UZ).",
                        reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
                    )
                    await state.clear()
                    logger.warning(f"Блокланган фойдаланувчи {user_id} рўйхатдан ўтишга уринди")
                    print(f"User {user_id} is blocked")
                    return
                logger.debug(f"SQL: SELECT id FROM users WHERE phone_number = '{phone}'")
                async with conn.execute(
                    "SELECT id FROM users WHERE phone_number = ?", (phone,)
                ) as cursor:
                    existing_phone = await cursor.fetchone()
                    if existing_phone and existing_phone[0] != user_id:
                        await message.answer(
                            "Бу телефон рақами бошқа фойдаланувчи билан рўйхатдан ўтган. Админ билан боғланинг (@MSMA_UZ).",
                            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
                        )
                        await state.clear()
                        logger.warning(f"Телефон рақами {phone} бошқа фойдаланувчи (id={existing_phone[0]}) билан рўйхатдан ўтган")
                        print(f"Phone {phone} already registered with user_id={existing_phone[0]}")
                        return
                await message.answer(
                    "Рўйхатдан ўтиш имконсиз. База данных хатоси. Админ билан боғланинг (@MSMA_UZ).",
                    reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
                )
                await state.clear()
                logger.warning(f"Фойдаланувчи {user_id} рўйхатдан ўтишда рад этилди, сабаб: неизвестная ошибка")
                print(f"Registration failed for user_id={user_id}: unknown error")
                return

        # Сохранение номера в состояние
        logger.debug(f"Сохранение phone={phone} в состояние для user_id={user_id}")
        await state.update_data(phone=phone)
        print(f"Phone {phone} saved to FSM state for user_id={user_id}")

        # Переход к выбору роли без кнопки "Орқага"
        logger.debug(f"Переход к выбору роли для user_id={user_id}")
        role_buttons = [key for key in ROLE_MAPPING.keys() if ROLE_MAPPING[key] != ADMIN_ROLE]
        await message.answer(
            "Рольни танланг:",
            reply_markup=make_keyboard(role_buttons, columns=2, one_time=True)
        )
        await state.set_state(Registration.role)
        logger.info(f"Фойдаланувчи {user_id} телефон {phone} улашди, Registration.role га ўтилди")
        print(f"Registration.role set for user_id={user_id}")  # Временное логирование

    except aiosqlite.Error as db_e:
        logger.error(f"Маълумотлар базаси хатолиги телефон қайта ишлашда user_id={user_id}: {db_e}", exc_info=True)
        await notify_admin(f"Маълумотлар базаси хатолиги телефон қайта ишлашда user_id={user_id}: {str(db_e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: База данных хатоси ({str(db_e)}). Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        print(f"Database error in process_phone for user_id={user_id}: {db_e}")
        return
    except Exception as e:
        logger.error(f"Кутмаган хатолик в process_phone для user_id={user_id}: {e} (type: {type(e).__name__})", exc_info=True)
        await notify_admin(f"Кутмаган хатолик в process_phone для user_id={user_id}: {str(e)} (type: {type(e).__name__})", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: Номаълум хато ({str(e)}). Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        print(f"Unexpected error in process_phone for user_id={user_id}: {e} (type: {type(e).__name__})")
        return

async def process_role(message: types.Message, state: FSMContext):
    """Роль танлашни қайта ишлайди."""
    user_id = message.from_user.id
    logger.debug(f"process_role: user_id={user_id}, text='{message.text}'")
    role_buttons = [key for key in ROLE_MAPPING.keys() if ROLE_MAPPING[key] != ADMIN_ROLE]
    if message.text not in role_buttons:
        try:
            await message.answer(
                "Илтимос, рўйхатдан роль танланг:",
                reply_markup=make_keyboard(role_buttons, columns=2, one_time=True)
            )
            logger.warning(f"Фойдаланувчи {user_id} нотўғри роль танлади: {message.text}")
        except Exception as e:
            logger.error(f"Нотўғри роль жавобида хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Нотўғри роль жавобида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    role = ROLE_MAPPING.get(message.text)
    try:
        # Обновление роли в базе данных
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            logger.debug(f"SQL: UPDATE users SET role = '{role}' WHERE id = {user_id}")
            await conn.execute(
                "UPDATE users SET role = ? WHERE id = ?",
                (role, user_id)
            )
            await conn.commit()
        await state.update_data(role=role)
        regions = get_all_regions()
        if not regions:
            await message.answer(
                "Вилоятлар рўйхати бўш. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
            logger.warning(f"Бўш вилоятлар рўйхати user_id={user_id}")
            return
        await message.answer(
            "Вилоятни танланг:",
            reply_markup=make_keyboard(regions, columns=2, with_back=True)
        )
        await state.set_state(Registration.region)
        logger.info(f"Фойдаланувчи {user_id} роль {role} танлади, Registration.region га ўтилди")
    except Exception as e:
        logger.error(f"Вилоят сўровида хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Вилоят сўровида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return

async def process_region(message: types.Message, state: FSMContext):
    """Вилоят танлашни қайта ишлайди."""
    user_id = message.from_user.id
    logger.debug(f"process_region: user_id={user_id}, text='{message.text}'")
    regions = get_all_regions()
    if message.text == "Орқага":
        role_buttons = [key for key in ROLE_MAPPING.keys() if ROLE_MAPPING[key] != ADMIN_ROLE]
        try:
            await message.answer(
                "Рольни танланг:",
                reply_markup=make_keyboard(role_buttons, columns=2, one_time=True)
            )
            await state.set_state(Registration.role)
            logger.info(f"Фойдаланувчи {user_id} Registration.role га қайтди (вилоятдан)")
        except Exception as e:
            logger.error(f"'Орқага' жавобида вилоят танлашда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"'Орқага' жавобида вилоят танлашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    if message.text not in regions:
        try:
            await message.answer(
                "Илтимос, рўйхатдан вилоят танланг:",
                reply_markup=make_keyboard(regions, columns=2, with_back=True)
            )
            logger.warning(f"Фойдаланувчи {user_id} нотўғри вилоят танлади: {message.text}")
        except Exception as e:
            logger.error(f"Нотўғри вилоят жавобида хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Нотўғри вилоят жавобида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    try:
        await state.update_data(region=message.text)
        districts = get_districts_for_region(message.text)
        if not districts:
            await message.answer(
                "Туманлар рўйхати бўш. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
            logger.warning(f"Бўш туманлар рўйхати вилоят {message.text} учун user_id={user_id}")
            return
        await message.answer(
            "Туманни танланг:",
            reply_markup=make_keyboard(districts, columns=2, with_back=True)
        )
        await state.set_state(Registration.district)
        logger.info(f"Фойдаланувчи {user_id} вилоят {message.text} танлади, Registration.district га ўтилди")
    except Exception as e:
        logger.error(f"Туман сўровида хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Туман сўровида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return

async def process_district(message: types.Message, state: FSMContext):
    """Туман танлашни қайта ишлайди."""
    user_id = message.from_user.id
    logger.debug(f"process_district: user_id={user_id}, text='{message.text}'")
    data = await state.get_data()
    region = data.get("region")
    districts = get_districts_for_region(region)
    if message.text == "Орқага":
        regions = get_all_regions()
        try:
            await message.answer(
                "Вилоятни танланг:",
                reply_markup=make_keyboard(regions, columns=2, with_back=True)
            )
            await state.set_state(Registration.region)
            logger.info(f"Фойдаланувчи {user_id} Registration.region га қайтди (тумандан)")
        except Exception as e:
            logger.error(f"'Орқага' жавобида туман танлашда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"'Орқага' жавобида туман танлашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    if message.text not in districts:
        try:
            await message.answer(
                "Илтимос, рўйхатдан туман танланг:",
                reply_markup=make_keyboard(districts, columns=2, with_back=True)
            )
            logger.warning(f"Фойдаланувчи {user_id} нотўғри туман танлади: {message.text}")
        except Exception as e:
            logger.error(f"Нотўғри туман жавобида хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Нотўғри туман жавобида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    try:
        await state.update_data(district=message.text)
        if data.get("role") == SELLER_ROLE:
            await message.answer(
                f"Фирма номини киритинг (макс. {MAX_COMPANY_NAME_LENGTH} белги):",
                reply_markup=make_keyboard(["Орқага"], one_time=True)
            )
            await state.set_state(Registration.company_name)
            logger.info(f"Фойдаланувчи {user_id} туман {message.text} танлади, Registration.company_name га ўтилди")
        else:
            await complete_registration(message, state)
    except Exception as e:
        logger.error(f"Туман қайта ишлашда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Туман қайта ишлашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return

async def process_company_name(message: types.Message, state: FSMContext):
    """Фирма номини қайта ишлайди."""
    user_id = message.from_user.id
    logger.debug(f"process_company_name: user_id={user_id}, text='{message.text}'")
    data = await state.get_data()
    if message.text == "Орқага":
        districts = get_districts_for_region(data.get("region"))
        try:
            await message.answer(
                "Туманни танланг:",
                reply_markup=make_keyboard(districts, columns=2, with_back=True)
            )
            await state.set_state(Registration.district)
            logger.info(f"Фойдаланувчи {user_id} Registration.district га қайтди (фирма номидан)")
        except Exception as e:
            logger.error(f"'Орқага' жавобида фирма номида хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"'Орқага' жавобида фирма номида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    company_name = message.text.strip()
    if len(company_name) > MAX_COMPANY_NAME_LENGTH:
        try:
            await message.answer(
                f"Фирма номи {MAX_COMPANY_NAME_LENGTH} белгидан ошмаслиги керак. Қайта киритинг:",
                reply_markup=make_keyboard(["Орқага"], one_time=True)
            )
            logger.warning(f"Фойдаланувчи {user_id} жуда узун фирма номи киритди: {len(company_name)} белги")
        except Exception as e:
            logger.error(f"Жуда узун фирма номи жавобида хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Жуда узун фирма номи жавобида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    try:
        await state.update_data(company_name=company_name)
        await complete_registration(message, state)
    except Exception as e:
        logger.error(f"Фирма номи қайта ишлашда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Фирма номи қайта ишлашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return

async def complete_registration(message: types.Message, state: FSMContext):
    """Рўйхатдан ўтишни якунлайди."""
    user_id = message.from_user.id
    logger.debug(f"complete_registration: user_id={user_id}")
    data = await state.get_data()
    phone = data.get("phone")
    role = data.get("role")
    region = data.get("region")
    district = data.get("district")
    company_name = data.get("company_name") if role == SELLER_ROLE else "Йўқ"
    display_role = ROLE_DISPLAY_NAMES.get(role, role)

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            # Генерация unique_id
            unique_id = await generate_user_id(role, bot=message.bot)
            logger.debug(f"SQL: UPDATE users SET phone_number = '{phone}', region = '{region}', district = '{district}', company_name = '{company_name}', unique_id = '{unique_id}' WHERE id = {user_id}")
            await conn.execute(
                "UPDATE users SET phone_number = ?, region = ?, district = ?, company_name = ?, unique_id = ? WHERE id = ?",
                (phone, region, district, company_name, unique_id, user_id)
            )
            await conn.commit()
            logger.debug(f"Фойдаланувчи {user_id} маълумотлари янгиланди: unique_id={unique_id}")

        channel_active, bot_active, is_subscribed = await check_subscription(message.bot, user_id)
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT bot_expires, trial_used FROM payments WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                result = await cursor.fetchone()
            bot_expires = result[0] if result else None
            trial_used = bool(result[1]) if result else False
            logger.debug(f"Обуна маълумоти user_id={user_id}: is_subscribed={is_subscribed}, trial_used={trial_used}, bot_expires={bot_expires}")

        if is_subscribed:
            bot_expires_dt = parse_uz_datetime(bot_expires) if bot_expires else None
            expires_formatted = format_uz_datetime(bot_expires_dt) if bot_expires_dt else "Кўрсатилмаган"
            message_text = (
                f"✅ Рўйхатдан ўтиш якунланди!\n"
                f"Телефон: {phone}\n"
                f"Роль: {display_role}\n"
                f"Вилоят: {region}\n"
                f"Туман: {district}\n"
                f"Компания: {company_name}\n"
                f"ID: {unique_id}\n"
                f"Сизда фаол обуна мавжуд. Тугайди: {expires_formatted}"
            )
            reply_markup = get_main_menu(role)
        else:
            if not trial_used:
                await activate_trial(user_id, bot=message.bot)
                trial_expires = datetime.now(pytz.UTC) + timedelta(days=3)
                trial_expires_formatted = format_uz_datetime(trial_expires)
                message_text = (
                    f"✅ Рўйхатдан ўтиш якунланди!\n"
                    f"Телефон: {phone}\n"
                    f"Роль: {display_role}\n"
                    f"Вилоят: {region}\n"
                    f"Туман: {district}\n"
                    f"Компания: {company_name}\n"
                    f"ID: {unique_id}\n"
                    f"Сизга 3 кунлик тест даври берилди. Тугайди: {trial_expires_formatted}"
                )
                reply_markup = get_main_menu(role)
            else:
                message_text = (
                    f"✅ Рўйхатдан ўтиш якунланди!\n"
                    f"Телефон: {phone}\n"
                    f"Роль: {display_role}\n"
                    f"Вилоят: {region}\n"
                    f"Туман: {district}\n"
                    f"Компания: {company_name}\n"
                    f"ID: {unique_id}\n"
                    f"Сизда фаол обуна мавжуд эмас. Ботдан фойдаланиш учун 'Обуна' тугмасини босинг."
                )
                reply_markup = make_keyboard(["Обуна"], one_time=True)
                await state.set_state(Registration.subscription)

        await message.answer(
            message_text,
            reply_markup=reply_markup
        )
        await state.clear()
        logger.info(f"Фойдаланувчи {user_id} рўйхатдан ўтишни якунлади: роль={role}, телефон={phone}, вилоят={region}, туман={district}, компания={company_name}, ID={unique_id}")
    except aiosqlite.Error as e:
        logger.error(f"Рўйхатдан ўтишни якунлашда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Рўйхатдан ўтишни якунлашда маълумотлар базаси хатолиги user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return
    except Exception as e:
        logger.error(f"Кутмаган хатолик рўйхатдан ўтишни якунлашда user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Кутмаган хатолик рўйхатдан ўтишни якунлашда user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return

async def process_subscription(message: types.Message, state: FSMContext):
    """Обуна танлашни қайта ишлайди."""
    user_id = message.from_user.id
    logger.debug(f"process_subscription: user_id={user_id}, text='{message.text}'")
    if message.text == "Обуна":
        try:
            await message.answer(
                "Тўлиқ обуна (30 кун):\n"
                "1. Каналга обуна: 10,000 сўм\n"
                "2. Бот + Канал: 50,000 сўм/ой\n"
                "Тўловдан сўнг админга ёзинг (@MSMA_UZ) ва user_id ни юборинг: /myid\n"
                "Тўлов усуллари: Click ёки Payme (админдан сўранг).",
                reply_markup=make_keyboard(["Асосий меню"], one_time=True)
            )
            await state.clear()
            logger.info(f"Фойдаланувчи {user_id} обуна маълумотини сўради")
        except Exception as e:
            logger.error(f"Обуна маълумоти юборувда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Обуна маълумоти юборувда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    try:
        await message.answer(
            "Илтимос, 'Обуна' тугмасини босинг:",
            reply_markup=make_keyboard(["Обуна"], one_time=True)
        )
        logger.warning(f"Фойдаланувчи {user_id} нотўғри обуна амали юборди: {message.text}")
    except Exception as e:
        logger.error(f"Нотўғри обуна амали жавобида хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Нотўғри обуна амали жавобида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)

@router.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message, state: FSMContext):
    """Обуна командасини қайта ишлайди."""
    user_id = message.from_user.id
    logger.debug(f"cmd_subscribe: user_id={user_id}")
    try:
        await message.answer(
            "Обуна бўлиш учун тугмани босинг:",
            reply_markup=make_keyboard(["Обуна"], one_time=True)
        )
        await state.set_state(Registration.subscription)
        logger.info(f"Фойдаланувчи {user_id} обуна жараёнини бошлади")
    except Exception as e:
        logger.error(f"/subscribe командасида хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"/subscribe командасида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )

@router.message(Registration.start)
async def handle_registration_start(message: types.Message, state: FSMContext):
    """Registration.start ҳолатида хабарларни қайта ишлайди."""
    logger.debug(f"handle_registration_start: user_id={message.from_user.id}, text='{message.text}'")
    await process_registration_start_state(message, state)

@router.message(Registration.phone)
async def handle_phone(message: types.Message, state: FSMContext):
    """Registration.phone ҳолатида хабарларни қайта ишлайди."""
    logger.debug(f"handle_phone: user_id={message.from_user.id}, text='{message.text}', contact={message.contact}")
    await process_phone(message, state)

@router.message(Registration.role)
async def handle_role(message: types.Message, state: FSMContext):
    """Registration.role ҳолатида хабарларни қайта ишлайди."""
    logger.debug(f"handle_role: user_id={message.from_user.id}, text='{message.text}'")
    await process_role(message, state)

@router.message(Registration.region)
async def handle_region(message: types.Message, state: FSMContext):
    """Registration.region ҳолатида хабарларни қайта ишлайди."""
    logger.debug(f"handle_region: user_id={message.from_user.id}, text='{message.text}'")
    await process_region(message, state)

@router.message(Registration.district)
async def handle_district(message: types.Message, state: FSMContext):
    """Registration.district ҳолатида хабарларни қайта ишлайди."""
    logger.debug(f"handle_district: user_id={message.from_user.id}, text='{message.text}'")
    await process_district(message, state)

@router.message(Registration.company_name)
async def handle_company_name(message: types.Message, state: FSMContext):
    """Registration.company_name ҳолатида хабарларни қайта ишлайди."""
    logger.debug(f"handle_company_name: user_id={message.from_user.id}, text='{message.text}'")
    await process_company_name(message, state)

@router.message(Registration.subscription)
async def handle_subscription(message: types.Message, state: FSMContext):
    """Registration.subscription ҳолатида хабарларни қайта ишлайди."""
    logger.debug(f"handle_subscription: user_id={message.from_user.id}, text='{message.text}'")
    await process_subscription(message, state)