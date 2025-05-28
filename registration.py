import aiosqlite
import logging
import asyncio
import json
from aiogram import Router, types, F, Dispatcher
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from config import DB_NAME, DB_TIMEOUT, SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE, ROLES, ROLE_MAPPING, ROLE_DISPLAY_NAMES, MAX_COMPANY_NAME_LENGTH, ADMIN_IDS
from database import register_user, activate_trial, init_db, clear_user_state, generate_user_id
from regions import get_all_regions, get_districts_for_region
from utils import make_keyboard, get_main_menu, check_subscription, format_uz_datetime, notify_admin, get_admin_menu, parse_uz_datetime, validate_phone, save_registration_state
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

async def process_start_registration(message: types.Message, state: FSMContext, dp: Dispatcher):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "Фойдаланувчи"
    current_state = await state.get_state()
    logger.info(f"process_start_registration: user_id={user_id}, first_name={first_name}, text='{message.text}', state={current_state}")

    # Проверка статуса админа
    if user_id in ADMIN_IDS:
        try:
            await state.clear()
            await clear_user_state(user_id, state.storage, bot=message.bot)
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                await conn.execute(
                    "INSERT OR REPLACE INTO users (id, role, created_at) VALUES (?, ?, ?)",
                    (user_id, ADMIN_ROLE, format_uz_datetime(datetime.now(pytz.UTC)))
                )
                await conn.commit()
            await message.answer("Админ панели:", reply_markup=get_admin_menu())
            await state.set_state("AdminStates:main_menu")
            logger.info(f"Админ {user_id} панельга кирди")
            return
        except Exception as e:
            logger.error(f"Админ {user_id} панельга киришда хатолик: {e}", exc_info=True)
            await notify_admin(f"Админ {user_id} панельга киришда хатолик: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            return

    # Очистка состояния FSM с повторной попыткой
    for attempt in range(3):
        try:
            logger.debug(f"FSM ҳолатини тозалаш user_id={user_id}, уриниш {attempt + 1}")
            await clear_user_state(user_id, state.storage, bot=message.bot)
            await state.clear()
            logger.debug(f"FSM ҳолати user_id={user_id} учун муваффақиятли тозаланди")
            break
        except Exception as e:
            logger.error(f"FSM ҳолатини тозалашда хатолик user_id={user_id}, уриниш {attempt + 1}: {e}", exc_info=True)
            if attempt < 2:
                await asyncio.sleep(2)
                continue
            await notify_admin(f"3 уринишдан сўнг FSM ҳолатини тозалашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди: Номаълум хато. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            return

    # Проверка блокировки
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            logger.debug(f"Блокировка ҳолатини текшириш user_id={user_id}")
            async with conn.execute(
                "SELECT blocked FROM deleted_users WHERE user_id = ? AND blocked = TRUE", (user_id,)
            ) as cursor:
                blocked = await cursor.fetchone()
            if blocked:
                await message.answer(
                    "Сизнинг Telegram ID блокланган. Админга мурожаат қилинг (@ad_mbozor).",
                    reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
                )
                logger.warning(f"Блокланган фойдаланувчи {user_id} рўйхатдан ўтишга уринди")
                await state.clear()
                return
    except aiosqlite.Error as e:
        logger.error(f"Блокировка текширишда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Блокировка текширишда маълумотлар базаси хатолиги user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        return

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            logger.debug(f"Мавжуд фойдаланувчи текширилмоқда user_id={user_id}")
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
                _, bot_active, is_subscribed = await check_subscription(message.bot, user_id, dp.storage)
                logger.debug(f"Фойдаланувчи {user_id} обунаси: bot_active={bot_active}, is_subscribed={is_subscribed}")
                async with conn.execute(
                    "SELECT bot_expires, trial_used FROM payments WHERE user_id = ?", (user_id,)
                ) as cursor:
                    result = await cursor.fetchone()
                bot_expires = result[0] if result else None
                trial_used = bool(result[1]) if result else False
                logger.debug(f"Тўлов маълумоти user_id={user_id}: bot_expires={bot_expires}, trial_used={trial_used}")

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
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@ad_mbozor).",
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
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@ad_mbozor).",
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
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return

async def process_phone(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"process_phone: user_id={user_id}, text='{message.text}', contact={message.contact}")

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
    # Добавление префикса "+" если отсутствует
    if not phone.startswith('+'):
        phone = f"+{phone}"
    logger.debug(f"Получен телефон user_id={user_id}: {phone}")

    # Проверка, что пользователь отправил свой номер
    if contact.user_id != user_id:
        await message.answer(
            "Илтимос, фақат ўз телефон рақамингизни улашинг.",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        logger.warning(f"Фойдаланувчи {user_id} бегона рақам юборди: {phone}")
        return

    try:
        # Сохранение состояния в Redis
        await state.update_data(phone=phone)
        await save_registration_state(state.storage, user_id, await state.get_data())

        # Очистка состояния FSM
        await clear_user_state(user_id, state.storage, bot=message.bot)

        # Регистрация пользователя
        registered = await register_user(user_id, phone, bot=message.bot)
        if not isinstance(registered, bool):
            logger.error(f"register_user нотўғри тип қайтарди: {type(registered).__name__}, қиймат: {registered}")
            raise ValueError(f"register_user нотўғри тип қайтарди: {type(registered).__name__}")

        if not registered:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute(
                    "SELECT blocked FROM deleted_users WHERE user_id = ? AND blocked = 1", (user_id,)
                ) as cursor:
                    blocked = await cursor.fetchone()
                if blocked:
                    await message.answer(
                        "Сиз блоклангансиз. Админ билан боғланинг (@ad_mbozor).",
                        reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
                    )
                    await state.clear()
                    logger.warning(f"Блокланган фойдаланувчи {user_id} рўйхатдан ўтишга уринди")
                    return
                async with conn.execute(
                    "SELECT id FROM users WHERE phone_number = ?", (phone,)
                ) as cursor:
                    existing_phone = await cursor.fetchone()
                    if existing_phone and existing_phone[0] != user_id:
                        await message.answer(
                            "Бу телефон рақами бошқа фойдаланувчи билан рўйхатдан ўтган. Админ билан боғланинг (@ad_mbozor).",
                            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
                        )
                        await state.clear()
                        logger.warning(f"Телефон рақами {phone} бошқа фойдаланувчи (id={existing_phone[0]}) билан рўйхатдан ўтган")
                        return
                await message.answer(
                    "Рўйхатдан ўтиш имконсиз. Маълумотлар базаси хатоси. Админ билан боғланинг (@ad_mbozor).",
                    reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
                )
                await state.clear()
                logger.warning(f"Фойдаланувчи {user_id} рўйхатдан ўтишда рад этилди, сабаб: ноаниқ хато")
                return

        # Переход к выбору роли без кнопки "Орқага"
        role_buttons = [key for key in ROLE_MAPPING.keys() if ROLE_MAPPING[key] != ADMIN_ROLE]
        await message.answer(
            "Рольни танланг:",
            reply_markup=make_keyboard(role_buttons, columns=2, one_time=True)
        )
        await state.set_state(Registration.role)
        logger.info(f"Фойдаланувчи {user_id} телефон {phone} улашди, Registration.role га ўтилди")
    except aiosqlite.Error as db_e:
        logger.error(f"Телефон қайта ишлашда маълумотлар базаси хатолиги user_id={user_id}: {db_e}", exc_info=True)
        await notify_admin(f"Телефон қайта ишлашда маълумотлар базаси хатолиги user_id={user_id}: {str(db_e)}", bot=message.bot)
        try:
            if hasattr(state.storage, 'redis'):
                saved_data = await state.storage.redis.get(f"reg:{user_id}")
                if saved_data:
                    await state.set_data(json.loads(saved_data))
                    await message.answer(
                        "Хатолик юз берди. Илтимос, телефон рақамингизни қайта улашинг:",
                        reply_markup=ReplyKeyboardMarkup(
                            keyboard=[[KeyboardButton(text="📱 Телефон рақамини улашиш", request_contact=True)]],
                            resize_keyboard=True,
                            one_time_keyboard=True
                        )
                    )
                    await state.set_state(Registration.phone)
                    logger.info(f"Registration.phone ҳолати user_id={user_id} учун қайта тикланди")
                    return
        except Exception as redis_e:
            logger.error(f"Redis дан ҳолатни қайта тиклашда хатолик user_id={user_id}: {redis_e}", exc_info=True)
        await message.answer(
            f"Хатолик юз берди: Маълумотлар базаси хатоси ({str(db_e)}). Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return
    except Exception as e:
        logger.error(f"process_phone да кутилмаган хато user_id={user_id}: {e} (type: {type(e).__name__})", exc_info=True)
        await notify_admin(f"process_phone да кутилмаган хато user_id={user_id}: {str(e)} (type: {type(e).__name__})", bot=message.bot)
        try:
            if hasattr(state.storage, 'redis'):
                saved_data = await state.storage.redis.get(f"reg:{user_id}")
                if saved_data:
                    await state.set_data(json.loads(saved_data))
                    await message.answer(
                        "Хатолик юз берди. Илтимос, телефон рақамингизни қайта улашинг:",
                        reply_markup=ReplyKeyboardMarkup(
                            keyboard=[[KeyboardButton(text="📱 Телефон рақамини улашиш", request_contact=True)]],
                            resize_keyboard=True,
                            one_time_keyboard=True
                        )
                    )
                    await state.set_state(Registration.phone)
                    logger.info(f"Registration.phone ҳолати user_id={user_id} учун қайта тикланди")
                    return
        except Exception as redis_e:
            logger.error(f"Redis дан ҳолатни қайта тиклашда хатолик user_id={user_id}: {redis_e}", exc_info=True)
        await message.answer(
            f"Хатолик юз берди: Номаълум хато ({str(e)}). Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return

async def process_role(message: types.Message, state: FSMContext):
    """Роль танлашни қайта ишлайди."""
    user_id = message.from_user.id
    logger.debug(f"process_role: user_id={user_id}, text='{message.text}'")
    role_buttons = [key for key in ROLE_MAPPING.keys() if ROLE_MAPPING[key] != ADMIN_ROLE]
    role_text = message.text
    if role_text not in role_buttons:
        try:
            await message.answer(
                "Илтимос, рўйхатдан роль танланг:",
                reply_markup=make_keyboard(role_buttons, columns=2, one_time=True)
            )
            logger.warning(f"Фойдаланувчи {user_id} нотўғри роль танлади: {role_text}")
        except Exception as e:
            logger.error(f"Нотўғри роль жавобида хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Нотўғри роль жавобида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            try:
                if hasattr(state.storage, 'redis'):
                    saved_data = await state.storage.redis.get(f"reg:{user_id}")
                    if saved_data:
                        await state.set_data(json.loads(saved_data))
                        await message.answer(
                            "Хатолик юз берди. Рольни қайта танланг:",
                            reply_markup=make_keyboard(role_buttons, columns=2, one_time=True)
                        )
                        await state.set_state(Registration.role)
                        logger.info(f"Registration.role ҳолати user_id={user_id} учун қайта тикланди")
                        return
            except Exception as redis_e:
                logger.error(f"Redis дан ҳолатни қайта тиклашда хатолик user_id={user_id}: {redis_e}", exc_info=True)
            await message.answer(
                f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    role = ROLE_MAPPING.get(role_text)
    try:
        await state.update_data(role=role)
        await save_registration_state(state.storage, user_id, await state.get_data())
        regions = get_all_regions()
        if not regions:
            await message.answer(
                "Вилоятлар рўйхати бўш. Админ билан боғланинг (@ad_mbozor).",
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
        try:
            if hasattr(state.storage, 'redis'):
                saved_data = await state.storage.redis.get(f"reg:{user_id}")
                if saved_data:
                    await state.set_data(json.loads(saved_data))
                    await message.answer(
                        "Хатолик юз берди. Рольни қайта танланг:",
                        reply_markup=make_keyboard(role_buttons, columns=2, one_time=True)
                    )
                    await state.set_state(Registration.role)
                    logger.info(f"Registration.role ҳолати user_id={user_id} учун қайта тикланди")
                    return
        except Exception as redis_e:
            logger.error(f"Redis дан ҳолатни қайта тиклашда хатолик user_id={user_id}: {redis_e}", exc_info=True)
        await message.answer(
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
        return

async def process_region(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"process_region: user_id={user_id}, text='{message.text}'")
    regions = get_all_regions()
    region_text = message.text
    if region_text == "Орқага":
        role_buttons = [key for key in ROLE_MAPPING.keys() if ROLE_MAPPING[key] != ADMIN_ROLE]
        try:
            await message.answer(
                "Рольни танланг:",
                reply_markup=make_keyboard(role_buttons, columns=2, one_time=True)
            )
            await state.set_state(Registration.role)
            logger.info(f"Фойдаланувчи {user_id} Registration.role га қайтди (вилоятдан)")
        except Exception as e:
            logger.error(f"Роль танлашга қайтишда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Роль танлашга қайтишда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    if region_text not in regions:
        try:
            await message.answer(
                "Илтимос, рўйхатдан вилоят танланг:",
                reply_markup=make_keyboard(regions, columns=2, with_back=True)
            )
            logger.warning(f"Фойдаланувчи {user_id} нотўғри вилоят танлади: {region_text}")
        except Exception as e:
            logger.error(f"Нотўғри вилоят қайта ишлашда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Нотўғри вилоят қайта ишлашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    try:
        await state.update_data(region=region_text)
        await save_registration_state(state.storage, user_id, await state.get_data())
        data = await state.get_data()
        if region_text == "Ташкент шахри":
            # Для Ташкент шахри пропускаем выбор района
            await state.update_data(district="Йўқ")
            if data.get("role") == BUYER_ROLE:
                await complete_registration(message, state)
            else:  # SELLER_ROLE
                await message.answer(
                    f"Ташкилот номини киритинг (макс. {MAX_COMPANY_NAME_LENGTH} белги):",
                    reply_markup=make_keyboard(["Орқага"], one_time=True)
                )
                await state.set_state(Registration.company_name)
            logger.info(f"Фойдаланувчи {user_id} вилоят Ташкент шахри танлади, район Йўқ, роль {data.get('role')}")
        else:
            districts = get_districts_for_region(region_text)
            if not districts:
                # Для регионов без районов (кроме Ташкент шахри) устанавливаем district="Йўқ"
                await state.update_data(district="Йўқ")
                if data.get("role") == BUYER_ROLE:
                    await complete_registration(message, state)
                else:  # SELLER_ROLE
                    await message.answer(
                        f"Ташкилот номини киритинг (макс. {MAX_COMPANY_NAME_LENGTH} белги):",
                        reply_markup=make_keyboard(["Орқага"], one_time=True)
                    )
                    await state.set_state(Registration.company_name)
                logger.info(f"Фойдаланувчи {user_id} вилоят {region_text} танлади, район Йўқ, роль {data.get('role')}")
            else:
                await message.answer(
                    "Туманни танланг:",
                    reply_markup=make_keyboard(districts, columns=2, with_back=True)
                )
                await state.set_state(Registration.district)
                logger.info(f"Фойдаланувчи {user_id} вилоят {region_text} танлади, Registration.district га ўтилди")
    except Exception as e:
        logger.error(f"Вилоят қайта ишлашда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Вилоят қайта ишлашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)

async def process_district(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"process_district: user_id={user_id}, text='{message.text}'")
    data = await state.get_data()
    region = data.get("region")
    districts = get_districts_for_region(region)
    district_text = message.text

    if district_text == "Орқага":
        regions = get_all_regions()
        try:
            await message.answer(
                "Вилоятни танланг:",
                reply_markup=make_keyboard(regions, columns=2, with_back=True)
            )
            await state.set_state(Registration.region)
            logger.info(f"Фойдаланувчи {user_id} Registration.region га қайтди (тумандан)")
        except Exception as e:
            logger.error(f"Вилоят танлашга қайтишда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Вилоят танлашга қайтишда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return

    if not districts:
        logger.debug(f"Вилоят {region} учун туманлар йўқ, рўйхатдан ўтиш якунланмоқда user_id={user_id}")
        try:
            await state.update_data(district="Йўқ")
            await save_registration_state(state.storage, user_id, await state.get_data())
            await complete_registration(message, state)
        except Exception as e:
            logger.error(f"Тумансиз рўйхатдан ўтишни якунлашда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Тумансиз рўйхатдан ўтишни якунлашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return

    if district_text not in districts:
        try:
            await message.answer(
                "Илтимос, рўйхатдан туман танланг:",
                reply_markup=make_keyboard(districts, columns=2, with_back=True)
            )
            logger.warning(f"Фойдаланувчи {user_id} нотўғри туман танлади: {district_text}")
        except Exception as e:
            logger.error(f"Нотўғри туман қайта ишлашда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Нотўғри туман қайта ишлашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return

    try:
        await state.update_data(district=district_text)
        await save_registration_state(state.storage, user_id, await state.get_data())
        if data.get("role") == SELLER_ROLE:
            await message.answer(
                f"Ташкилот номини киритинг (макс. {MAX_COMPANY_NAME_LENGTH} белги):",
                reply_markup=make_keyboard(["Орқага"], one_time=True)
            )
            await state.set_state(Registration.company_name)
            logger.info(f"Фойдаланувчи {user_id} туман {district_text} танлади, Registration.company_name га ўтилди")
        else:
            await complete_registration(message, state)
    except Exception as e:
        logger.error(f"Туман қайта ишлашда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Туман қайта ишлашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)

async def process_company_name(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"process_company_name: user_id={user_id}, text='{message.text}'")
    data = await state.get_data()
    region = data.get("region")
    if message.text == "Орқага":
        districts = get_districts_for_region(region)
        try:
            if not districts:
                regions = get_all_regions()
                await message.answer(
                    "Вилоятни танланг:",
                    reply_markup=make_keyboard(regions, columns=2, with_back=True)
                )
                await state.set_state(Registration.region)
                logger.info(f"Фойдаланувчи {user_id} Registration.region га қайтди (ташкилот номидан, бўш туманлар)")
            else:
                await message.answer(
                    "Туманни танланг:",
                    reply_markup=make_keyboard(districts, columns=2, with_back=True)
                )
                await state.set_state(Registration.district)
                logger.info(f"Фойдаланувчи {user_id} Registration.district га қайтди (ташкилот номидан)")
        except Exception as e:
            logger.error(f"Туман/вилоят танлашга қайтишда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Туман/вилоят танлашга қайтишда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    company_name = message.text.strip()
    if len(company_name) > MAX_COMPANY_NAME_LENGTH:
        try:
            await message.answer(
                f"Ташкилот номи {MAX_COMPANY_NAME_LENGTH} белгидан ошмаслиги керак. Қайта киритинг:",
                reply_markup=make_keyboard(["Орқага"], one_time=True)
            )
            logger.warning(f"Фойдаланувчи {user_id} жуда узун ташкилот номи киритди: {len(company_name)} белги")
        except Exception as e:
            logger.error(f"Узун ташкилот номини қайта ишлашда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Узун ташкилот номини қайта ишлашда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state(Registration.start)
        return
    try:
        await state.update_data(company_name=company_name)
        await save_registration_state(state.storage, user_id, await state.get_data())
        logger.debug(f"Ташкилот номи сақланди user_id={user_id}: {company_name}")
        await complete_registration(message, state)
    except aiosqlite.Error as db_e:
        logger.error(f"Ташкилот номини сақлашда маълумотлар базаси хатоси user_id={user_id}: {db_e}", exc_info=True)
        await notify_admin(f"Ташкилот номини сақлашда маълумотлар базаси хатоси user_id={user_id}: {str(db_e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди: Маълумотлар базаси хатоси. Ташкилот номини қайта киритинг:",
            reply_markup=make_keyboard(["Орқага"], one_time=True)
        )
        await state.set_state(Registration.company_name)
    except Exception as e:
        logger.error(f"Ташкилот номини сақлашда кутилмаган хато user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ташкилот номини сақлашда кутилмаган хато user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди: Номаълум хато. Ташкилот номини қайта киритинг:",
            reply_markup=make_keyboard(["Орқага"], one_time=True)
        )
        await state.set_state(Registration.company_name)

async def complete_registration(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"complete_registration: user_id={user_id}")
    data = await state.get_data()
    phone = data.get("phone")
    role = data.get("role")
    region = data.get("region")
    district = data.get("district") if role == SELLER_ROLE else "Йўқ"
    company_name = data.get("company_name") if role == SELLER_ROLE else "Йўқ"
    display_role = ROLE_DISPLAY_NAMES.get(role, role)

    # Отладка: проверяем данные перед обновлением
    logger.debug(f"complete_registration: user_id={user_id}, phone={phone}, role={role}, region={region}, district={district}, company_name={company_name}")

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            unique_id = await generate_user_id(role, bot=message.bot)
            logger.debug(f"SQL: UPDATE users SET phone_number = ?, role = ?, region = ?, district = ?, company_name = ?, unique_id = ? WHERE id = ?")
            await conn.execute(
                "UPDATE users SET phone_number = ?, role = ?, region = ?, district = ?, company_name = ?, unique_id = ? WHERE id = ?",
                (phone, role, region, district, company_name, unique_id, user_id)
            )
            await conn.commit()
            logger.debug(f"Фойдаланувчи {user_id} маълумотлари янгиланди: unique_id={unique_id}")

        if not hasattr(state.storage, 'redis'):
            logger.error(f"Storage Redis ни қўллаб-қувватламайди user_id={user_id}")
            raise ValueError("Storage Redis ни қўллаб-қувватламайди")
        _, bot_active, is_subscribed = await check_subscription(message.bot, user_id, state.storage)
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
                f"Ташкилот: {company_name}\n"
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
                    f"Ташкилот: {company_name}\n"
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
                    f"Ташкилот: {company_name}\n"
                    f"ID: {unique_id}\n"
                    f"Сизда фаол обуна мавжуд эмас. Ботдан фойдаланиш учун 'Обуна' тугмасини босинг."
                )
                reply_markup = make_keyboard(["Обуна"], one_time=True)
                await state.set_state(Registration.subscription)

        await message.answer(
            message_text,
            reply_markup=reply_markup
        )
        if hasattr(state.storage, 'redis'):
            await state.storage.redis.delete(f"reg:{user_id}")
        await state.clear()
        logger.info(f"Фойдаланувчи {user_id} рўйхатдан ўтишни якунлади: роль={role}, телефон={phone}, вилоят={region}, туман={district}, ташкилот={company_name}, ID={unique_id}")
    except aiosqlite.Error as e:
        logger.error(f"Рўйхатдан ўтишни якунлашда маълумотлар базаси хатоси user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Рўйхатдан ўтишни якунлашда маълумотлар базаси хатоси user_id={user_id}: {str(e)}", bot=message.bot)
        try:
            if hasattr(state.storage, 'redis'):
                saved_data = await state.storage.redis.get(f"reg:{user_id}")
                if saved_data:
                    await state.set_data(json.loads(saved_data))
                    role = data.get("role")
                    if role == SELLER_ROLE:
                        await message.answer(
                            "Хатолик юз берди. Ташкилот номини қайта киритинг:",
                            reply_markup=make_keyboard(["Орқага"], one_time=True)
                        )
                        await state.set_state(Registration.company_name)
                        logger.info(f"Registration.company_name ҳолати user_id={user_id} учун қайта тикланди")
                    else:
                        regions = get_all_regions()
                        await message.answer(
                            "Хатолик юз берди. Вилоятни қайта танланг:",
                            reply_markup=make_keyboard(regions, columns=2, with_back=True)
                        )
                        await state.set_state(Registration.region)
                        logger.info(f"Registration.region ҳолати user_id={user_id} учун қайта тикланди")
                    return
        except Exception as redis_e:
            logger.error(f"Redis дан ҳолатни қайта тиклашда хатолик user_id={user_id}: {redis_e}", exc_info=True)
        await message.answer(
            f"Хатолик юз берди: Маълумотлар базаси хатоси. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)
    except Exception as e:
        logger.error(f"Рўйхатдан ўтишни якунлашда кутилмаган хато user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Рўйхатдан ўтишни якунлашда кутилмаган хато user_id={user_id}: {str(e)}", bot=message.bot)
        try:
            if hasattr(state.storage, 'redis'):
                saved_data = await state.storage.redis.get(f"reg:{user_id}")
                if saved_data:
                    await state.set_data(json.loads(saved_data))
                    role = data.get("role")
                    if role == SELLER_ROLE:
                        await message.answer(
                            "Хатолик юз берди. Ташкилот номини қайта киритинг:",
                            reply_markup=make_keyboard(["Орқага"], one_time=True)
                        )
                        await state.set_state(Registration.company_name)
                        logger.info(f"Registration.company_name ҳолати user_id={user_id} учун қайта тикланди")
                    else:
                        regions = get_all_regions()
                        await message.answer(
                            "Хатолик юз берди. Вилоятни қайта танланг:",
                            reply_markup=make_keyboard(regions, columns=2, with_back=True)
                        )
                        await state.set_state(Registration.region)
                        logger.info(f"Registration.region ҳолати user_id={user_id} учун қайта тикланди")
                    return
        except Exception as redis_e:
            logger.error(f"Redis дан ҳолатни қайта тиклашда хатолик user_id={user_id}: {redis_e}", exc_info=True)
            await notify_admin(f"Redis дан ҳолатни қайта тиклашда хатолик user_id={user_id}: {str(redis_e)}", bot=message.bot)
        await message.answer(
            f"Хатолик юз берди: Номаълум хато. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state(Registration.start)

async def process_subscription(message: types.Message, state: FSMContext):
    """Обуна танлашни қайта ишлайди."""
    user_id = message.from_user.id
    logger.debug(f"process_subscription: user_id={user_id}, text='{message.text}'")
    if message.text == "Обуна":
        try:
            await message.answer(
                "Тўлиқ обуна (30 кун):\n"
                "Бот: 100,000 сўм/ой\n"
                "Тўловдан сўнг админга ёзинг (@ad_mbozor) ва user_id ни юборинг: /myid\n"
                "Тўлов усуллари: Click ёки Payme (админдан сўранг).",
                reply_markup=make_keyboard(["Асосий меню"], one_time=True)
            )
            await state.clear()
            logger.info(f"Фойдаланувчи {user_id} обуна маълумотини сўради")
        except Exception as e:
            logger.error(f"Обуна маълумоти юборувда хатолик user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Обуна маълумоти юборувда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@ad_mbozor).",
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
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@ad_mbozor).",
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
            f"Хатолик юз берди: {str(e)}. Админ билан боғланинг (@ad_mbozor).",
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