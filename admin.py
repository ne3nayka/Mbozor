import aiosqlite
import logging
import pytz
import asyncio
from datetime import datetime, timedelta
from functools import wraps
from aiogram import types, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import DB_NAME, DB_TIMEOUT, ADMIN_ROLE, CHANNEL_ID, ADMIN_IDS
from utils import make_keyboard, format_uz_datetime, parse_uz_datetime, notify_admin, get_main_menu, get_ads_menu, get_requests_menu
from common import send_subscription_info

logger = logging.getLogger(__name__)

class AdminStates(StatesGroup):
    main_menu = State()
    users_menu = State()
    products_menu = State()
    requests_menu = State()
    subscription_menu = State()
    archives_menu = State()
    admin_delete_product = State()
    admin_delete_request = State()
    archive_product = State()
    archive_request = State()
    delete_user = State()
    confirm_delete_user = State()
    subscribe_30_days = State()
    cancel_subscription = State()
    delete_archive = State()
    broadcast_message = State()  # Ввод текста для рассылки
    confirm_broadcast = State()  # Подтверждение рассылки

def admin_only(handler):
    """Фақат администраторлар учун функцияларга киришни чеклайди."""
    @wraps(handler)
    async def wrapper(message: types.Message, state: FSMContext, *args, **kwargs):
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            logger.warning(f"Админга рухсатсиз кириш уриниши: user_id={user_id}")
            await message.answer("Бу буйруқ фақат админлар учун!", reply_markup=get_main_menu(None))
            return
        return await handler(message, state, *args, **kwargs)
    return wrapper

@admin_only
async def admin_command(message: types.Message, state: FSMContext):
    """Админ панелини очиш ва main_menu ҳолатини ўрнатиш."""
    user_id = message.from_user.id
    try:
        await message.answer("Админ панели:", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)
        logger.info(f"Админ {user_id} /admin орқали админ панелини очди")
    except Exception as e:
        logger.error(f"admin_command да хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"admin_command да хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def process_main_menu(message: types.Message, state: FSMContext, dp: Dispatcher):
    """Админ панелининг асосий менюсидаги танловни қайта ишлайди."""
    user_id = message.from_user.id
    text = message.text
    logger.debug(f"process_main_menu: user_id={user_id}, text={text}")

    if not text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)
        return

    menu_options = {
        "Фойдаланувчиларни бошқариш": (
            AdminStates.users_menu,
            ["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"]
        ),
        "Эълонларни бошқариш": (
            AdminStates.products_menu,
            ["Барча эълонлар рўйхати", "Эълонларни ўчириш", "Эълонларни архивга ўтказиш", "Орқага"]
        ),
        "Сўровларни бошқариш": (
            AdminStates.requests_menu,
            ["Барча сўровлар рўйхати", "Сўровларни ўчириш", "Сўровларни архивга ўтказиш", "Орқага"]
        ),
        "Обунани бошқариш": (
            AdminStates.subscription_menu,
            ["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"]
        ),
        "Архивни бошқариш": (
            AdminStates.archives_menu,
            ["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"]
        ),
        "Хабар юбориш": (
            AdminStates.broadcast_message,
            []
        ),
        "Статистика": (None, None)
    }

    option = menu_options.get(text)
    if option:
        if text == "Статистика":
            await statistics_command(message, state, dp)
        elif text == "Хабар юбориш":
            await message.answer(
                "Барча фойдаланувчиларга юбориладиган хабар матнини киритинг (ёки 'Орқага' босинг):",
                reply_markup=make_keyboard(["Орқага"], columns=1)
            )
            await state.set_state(AdminStates.broadcast_message)
            logger.info(f"Админ {user_id} хабар юбориш жараёнини бошлади")
        else:
            state_info, buttons = option
            await message.answer(f"{text}:", reply_markup=make_keyboard(buttons, columns=2))
            await state.set_state(state_info)
            logger.info(f"Админ {user_id} {text} ни танлади")
    else:
        logger.warning(f"Нотўғри меню танлови админ user_id={user_id}: {text}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def process_users_menu(message: types.Message, state: FSMContext):
    """Фойдаланувчиларни бошқариш менюсини қайта ишлайди."""
    user_id = message.from_user.id
    text = message.text
    logger.debug(f"process_users_menu: user_id={user_id}, text={text}")

    if not text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2))
        return

    if text == "Орқага":
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)
        logger.info(f"Админ {user_id} фойдаланувчилар менюсидан асосий менюга қайтди")
        return
    elif text == "Фойдаланувчилар рўйхати":
        await list_users_command(message, state)
    elif text == "Фойдаланувчини ўчириш":
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT id FROM users WHERE id != ?", (user_id,)) as cursor:
                    users = [str(row[0]) for row in await cursor.fetchall()]
            if not users:
                await message.answer("Фойдаланувчилар йўқ.", reply_markup=get_main_menu(ADMIN_ROLE))
                await state.set_state(AdminStates.main_menu)
                logger.info(f"Админ {user_id} ўчириш учун фойдаланувчиларни топмади")
                return
            await message.answer("Фойдаланувчини танланг:", reply_markup=make_keyboard(users, columns=2, with_back=True))
            await state.set_state(AdminStates.delete_user)
            logger.info(f"Админ {user_id} фойдаланувчини ўчириш жараёнини бошлади")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении пользователей для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении пользователей для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
    else:
        await message.answer(
            "Илтимос, менюдан танланг:",
            reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2)
        )
        logger.warning(f"Нотўғри танлов process_users_menu: user_id={user_id}, text={text}")

@admin_only
async def process_products_menu(message: types.Message, state: FSMContext):
    """Эълонларни бошқариш менюсини қайта ишлайди."""
    user_id = message.from_user.id
    text = message.text
    logger.debug(f"process_products_menu: user_id={user_id}, text={text}")

    if not text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_ads_menu(is_admin=True))
        return

    if text == "Орқага":
        await state.set_state(AdminStates.main_menu)
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Админ {user_id} эълонлар менюсидан асосий менюга қайтди")
        return
    elif text == "Барча эълонлар рўйхати":
        await list_products_command(message, state)
    elif text == "Эълонларни ўчириш":
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT unique_id FROM products WHERE TRIM(LOWER(status)) = 'active'") as cursor:
                    products = [row[0] for row in await cursor.fetchall()]
            if not products:
                await message.answer("Фаол эълонлар йўқ.", reply_markup=get_ads_menu(is_admin=True))
                await state.set_state(AdminStates.products_menu)
                logger.info(f"Админ {user_id} фаол эълонларни ўчириш учун топмади")
                return
            await message.answer(
                "Ўчириш учун эълонни танланг:",
                reply_markup=make_keyboard(products, columns=2, with_back=True)
            )
            await state.set_state(AdminStates.admin_delete_product)
            logger.info(f"Админ {user_id} эълонларни ўчириш жараёнини бошлади")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении эълонлар для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении эълонлар для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
    elif text == "Эълонларни архивга ўтказиш":
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT unique_id FROM products WHERE TRIM(LOWER(status)) = 'active'") as cursor:
                    products = [row[0] for row in await cursor.fetchall()]
            if not products:
                await message.answer("Фаол эълонлар йўқ.", reply_markup=get_ads_menu(is_admin=True))
                await state.set_state(AdminStates.products_menu)
                logger.info(f"Админ {user_id} фаол эълонларни архивга ўтказиш учун топмади")
                return
            await message.answer(
                "Архивга ўтказиш учун эълонни танланг:",
                reply_markup=make_keyboard(products, columns=2, with_back=True)
            )
            await state.set_state(AdminStates.archive_product)
            logger.info(f"Админ {user_id} эълонларни архивга ўтказиш жараёнини бошлади")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении эълонлар для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении эълонлар для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
    else:
        await message.answer(
            "Илтимос, менюдан танланг:",
            reply_markup=get_ads_menu(is_admin=True)
        )
        logger.warning(f"Нотўғри танлов process_products_menu: user_id={user_id}, text={text}")

@admin_only
async def process_requests_menu(message: types.Message, state: FSMContext):
    """Сўровларни бошқариш менюсини қайта ишлайди."""
    user_id = message.from_user.id
    text = message.text
    logger.debug(f"process_requests_menu: user_id={user_id}, text={text}")

    if not text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_requests_menu(is_admin=True))
        return

    if text == "Орқага":
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)
        logger.info(f"Админ {user_id} сўровлар менюсидан асосий менюга қайтди")
        return
    elif text == "Барча сўровлар рўйхати":
        await list_requests_command(message, state)
    elif text == "Сўровларни ўчириш":
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT unique_id FROM requests WHERE status = 'active'") as cursor:
                    requests = [row[0] for row in await cursor.fetchall()]
            if not requests:
                await message.answer("Фаол сўровлар йўқ.", reply_markup=get_requests_menu(is_admin=True))
                await state.set_state(AdminStates.requests_menu)
                logger.info(f"Админ {user_id} фаол сўровларни ўчириш учун топмади")
                return
            await message.answer(
                "Ўчириш учун сўровни танланг:",
                reply_markup=make_keyboard(requests, columns=2, with_back=True)
            )
            await state.set_state(AdminStates.admin_delete_request)
            logger.info(f"Админ {user_id} сўровларни ўчириш жараёнини бошлади")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении сўровлар для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении сўровлар для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
    elif text == "Сўровларни архивга ўтказиш":
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT unique_id FROM requests WHERE status = 'active'") as cursor:
                    requests = [row[0] for row in await cursor.fetchall()]
            if not requests:
                await message.answer("Фаол сўровлар йўқ.", reply_markup=get_requests_menu(is_admin=True))
                await state.set_state(AdminStates.requests_menu)
                logger.info(f"Админ {user_id} фаол сўровларни архивга ўтказиш учун топмади")
                return
            await message.answer(
                "Архивга ўтказиш учун сўровни танланг:",
                reply_markup=make_keyboard(requests, columns=2, with_back=True)
            )
            await state.set_state(AdminStates.archive_request)
            logger.info(f"Админ {user_id} сўровларни архивга ўтказиш жараёнини бошлади")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении сўровлар для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении сўровлар для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
    else:
        await message.answer(
            "Илтимос, менюдан танланг:",
            reply_markup=get_requests_menu(is_admin=True)
        )
        logger.warning(f"Нотўғри танлов process_requests_menu: user_id={user_id}, text={text}")

@admin_only
async def list_products_command(message: types.Message, state: FSMContext):
    """Барча эълонлар рўйхатини кўрсатади."""
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("SELECT unique_id, user_id, category, sort, status, created_at FROM products") as cursor:
                products = await cursor.fetchall()
        if not products:
            await message.answer("Эълонлар йўқ.", reply_markup=get_ads_menu(is_admin=True))
            await state.set_state(AdminStates.products_menu)
            logger.info(f"Админ {user_id} эълонларни топмади")
            return
        response = "Барча эълонлар рўйхати:\n\n"
        for unique_id, user_id, category, sort, status, created_at in products:
            response += f"ID: {unique_id}, User ID: {user_id}, Тур: {category}, Нав: {sort}, Ҳолат: {status}, Яратилган: {created_at}\n"
        await message.answer(response, reply_markup=get_ads_menu(is_admin=True))
        logger.info(f"Админ {user_id} барча эълонлар рўйхатини кўрди")
    except aiosqlite.Error as e:
        logger.error(f"Эълонлар рўйхатини олишда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Эълонлар рўйхатини олишда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Эълонларни юклашда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def list_requests_command(message: types.Message, state: FSMContext):
    """Барча сўровлар рўйхатини кўрсатади."""
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT unique_id, user_id, category, sort, status FROM requests WHERE status = 'active'"
            ) as cursor:
                requests = await cursor.fetchall()
        if not requests:
            await message.answer("Фаол сўровлар йўқ.", reply_markup=get_requests_menu(is_admin=True))
            await state.set_state(AdminStates.requests_menu)
            logger.info(f"Админ {user_id} фаол сўровларни топмади")
            return
        response = "Барча сўровлар рўйхати:\n\n"
        status_map = {
            "active": "Фаол",
            "archived": "Архивланган",
            "deleted": "Ўчирилган"
        }
        for unique_id, user_id, category, sort, status in requests:
            status_uz = status_map.get(status, status)
            response += f"ID: {unique_id}, User ID: {user_id}, Тур: {category}, Нав: {sort}, Ҳолат: {status_uz}\n"
        await message.answer(response, reply_markup=get_requests_menu(is_admin=True))
        logger.info(f"Админ {user_id} барча сўровлар рўйхатини кўрди")
    except aiosqlite.Error as e:
        logger.error(f"Сўровлар рўйхатини олишда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Сўровлар рўйхатини олишда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровларни юклашда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def admin_delete_product(message: types.Message, state: FSMContext):
    """unique_id бўйича эълонни админ томонидан ўчиради."""
    user_id = message.from_user.id
    if not message.text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT unique_id FROM products WHERE status = 'active'") as cursor:
                    products = [row[0] for row in await cursor.fetchall()]
            if not products:
                await message.answer("Фаол эълонлар йўқ.", reply_markup=get_ads_menu(is_admin=True))
                await state.set_state(AdminStates.products_menu)
                return
            await message.answer(
                "Ўчириш учун эълонни танланг:",
                reply_markup=make_keyboard(products, columns=2, with_back=True)
            )
            return
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении эълонлар для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении эълонлар для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
            return

    if message.text == "Орқага":
        await message.answer("Эълонларни бошқариш:", reply_markup=get_ads_menu(is_admin=True))
        await state.set_state(AdminStates.products_menu)
        logger.info(f"Админ {user_id} эълонларни ўчиришни бекор қилди")
        return

    unique_id = message.text
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute("SELECT channel_message_id, user_id FROM products WHERE unique_id = ?", (unique_id,)) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(f"Эълон {unique_id} топилмади!", reply_markup=get_ads_menu(is_admin=True))
                await conn.execute("ROLLBACK")
                await state.set_state(AdminStates.products_menu)
                return
            if product[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=product[0])
                except TelegramBadRequest as e:
                    logger.warning(f"Канал хабари {product[0]} ни эълон {unique_id} учун ўчиришда хатолик: {e}")
            await conn.execute("UPDATE products SET status = 'deleted' WHERE unique_id = ?", (unique_id,))
            await conn.commit()
            if product[1]:
                try:
                    await message.bot.send_message(product[1], f"Сизнинг эълонингиз {unique_id} админ томонидан ўчирилди.")
                except TelegramBadRequest as e:
                    logger.warning(f"Фойдаланувчи {product[1]} га хабар юборишда хатолик: {e}")
        await message.answer(f"Эълон {unique_id} ўчирилди!", reply_markup=get_ads_menu(is_admin=True))
        await state.set_state(AdminStates.products_menu)
        logger.info(f"Админ {user_id} эълон {unique_id} ни ўчирди")
    except aiosqlite.Error as e:
        logger.error(f"Эълон {unique_id} ни ўчиришда хатолик админ {user_id}: {e}", exc_info=True)
        await notify_admin(f"Эълон {unique_id} ни ўчиришда хатолик admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Эълонларни ўчиришда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def admin_delete_request(message: types.Message, state: FSMContext):
    """unique_id бўйича сўровни админ томонидан ўчиради."""
    user_id = message.from_user.id
    if not message.text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT unique_id FROM requests WHERE status = 'active'") as cursor:
                    requests = [row[0] for row in await cursor.fetchall()]
            if not requests:
                await message.answer("Фаол сўровлар йўқ.", reply_markup=get_requests_menu(is_admin=True))
                await state.set_state(AdminStates.requests_menu)
                return
            await message.answer(
                "Ўчириш учун сўровни танланг:",
                reply_markup=make_keyboard(requests, columns=2, with_back=True)
            )
            return
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении сўровлар для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении сўровлар для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
            return

    if message.text == "Орқага":
        await message.answer("Сўровларни бошқариш:", reply_markup=get_requests_menu(is_admin=True))
        await state.set_state(AdminStates.requests_menu)
        logger.info(f"Админ {user_id} сўровларни ўчиришни бекор қилди")
        return

    unique_id = message.text
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute("SELECT channel_message_id, user_id FROM requests WHERE unique_id = ?", (unique_id,)) as cursor:
                request = await cursor.fetchone()
            if not request:
                await message.answer(f"Сўров {unique_id} топилмади!", reply_markup=get_requests_menu(is_admin=True))
                await conn.execute("ROLLBACK")
                await state.set_state(AdminStates.requests_menu)
                return
            if request[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=request[0])
                except TelegramBadRequest as e:
                    logger.warning(f"Канал хабари {request[0]} ни сўров {unique_id} учун ўчиришда хатолик: {e}")
            await conn.execute("UPDATE requests SET status = 'deleted' WHERE unique_id = ?", (unique_id,))
            await conn.commit()
            if request[1]:
                try:
                    await message.bot.send_message(request[1], f"Сизнинг сўровингиз {unique_id} админ томонидан ўчирилди.")
                except TelegramBadRequest as e:
                    logger.warning(f"Фойдаланувчи {request[1]} га хабар юборишда хатолик: {e}")
        await message.answer(f"Сўров {unique_id} ўчирилди!", reply_markup=get_requests_menu(is_admin=True))
        await state.set_state(AdminStates.requests_menu)
        logger.info(f"Админ {user_id} сўров {unique_id} ни ўчирди")
    except aiosqlite.Error as e:
        logger.error(f"Сўров {unique_id} ни ўчиришда хатолик админ {user_id}: {e}", exc_info=True)
        await notify_admin(f"Сўров {unique_id} ни ўчиришда хатолик admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровларни ўчиришда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def process_archive_product(message: types.Message, state: FSMContext):
    """unique_id бўйича эълонни архивга ўтказади."""
    user_id = message.from_user.id
    if not message.text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT unique_id FROM products WHERE status = 'active'") as cursor:
                    products = [row[0] for row in await cursor.fetchall()]
            if not products:
                await message.answer("Фаол эълонлар йўқ.", reply_markup=get_ads_menu(is_admin=True))
                await state.set_state(AdminStates.products_menu)
                return
            await message.answer(
                "Архивга ўтказиш учун эълонни танланг:",
                reply_markup=make_keyboard(products, columns=2, with_back=True)
            )
            return
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении эълонлар для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении эълонлар для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
            return

    if message.text == "Орқага":
        await message.answer("Эълонларни бошқариш:", reply_markup=get_ads_menu(is_admin=True))
        await state.set_state(AdminStates.products_menu)
        logger.info(f"Админ {user_id} эълонларни архивга ўтказишни бекор қилди")
        return

    unique_id = message.text
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute("SELECT unique_id, user_id FROM products WHERE unique_id = ? AND status = 'active'", (unique_id,)) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(f"Фаол эълон {unique_id} топилмади!", reply_markup=get_ads_menu(is_admin=True))
                await conn.execute("ROLLBACK")
                await state.set_state(AdminStates.products_menu)
                return
            archived_at = format_uz_datetime(datetime.now(pytz.UTC))
            await conn.execute("UPDATE products SET status = 'archived', archived_at = ? WHERE unique_id = ?",
                              (archived_at, unique_id))
            await conn.commit()
            if product[1]:
                try:
                    await message.bot.send_message(product[1], f"Сизнинг эълонингиз {unique_id} админ томонидан архивга ўтказилди.")
                except TelegramBadRequest as e:
                    logger.warning(f"Фойдаланувчи {product[1]} га хабар юборишда хатолик: {e}")
        await message.answer(f"Эълон {unique_id} архивга ўтказилди!", reply_markup=get_ads_menu(is_admin=True))
        await state.set_state(AdminStates.products_menu)
        logger.info(f"Админ {user_id} эълон {unique_id} ни архивга ўтказди")
    except aiosqlite.Error as e:
        logger.error(f"Эълон {unique_id} ни архивга ўтказишда хатолик админ {user_id}: {e}", exc_info=True)
        await notify_admin(f"Эълон {unique_id} ни архивга ўтказишда хатолик admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Эълонларни архивга ўтказишда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def process_archive_request(message: types.Message, state: FSMContext):
    """unique_id бўйича сўровни архивга ўтказади."""
    user_id = message.from_user.id
    if not message.text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT unique_id FROM requests WHERE status = 'active'") as cursor:
                    requests = [row[0] for row in await cursor.fetchall()]
            if not requests:
                await message.answer("Фаол сўровлар йўқ.", reply_markup=get_requests_menu(is_admin=True))
                await state.set_state(AdminStates.requests_menu)
                return
            await message.answer(
                "Архивга ўтказиш учун сўровни танланг:",
                reply_markup=make_keyboard(requests, columns=2, with_back=True)
            )
            return
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении сўровлар для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении сўровлар для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
            return

    if message.text == "Орқага":
        await message.answer("Сўровларни бошқариш:", reply_markup=get_requests_menu(is_admin=True))
        await state.set_state(AdminStates.requests_menu)
        logger.info(f"Админ {user_id} сўровларни архивга ўтказишни бекор қилди")
        return

    unique_id = message.text
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute("SELECT unique_id, user_id FROM requests WHERE unique_id = ? AND status = 'active'", (unique_id,)) as cursor:
                request = await cursor.fetchone()
            if not request:
                await message.answer(f"Фаол сўров {unique_id} топилмади!", reply_markup=get_requests_menu(is_admin=True))
                await conn.execute("ROLLBACK")
                await state.set_state(AdminStates.requests_menu)
                return
            archived_at = format_uz_datetime(datetime.now(pytz.UTC))
            await conn.execute("UPDATE requests SET status = 'archived', archived_at = ? WHERE unique_id = ?",
                              (archived_at, unique_id))
            await conn.commit()
            if request[1]:
                try:
                    await message.bot.send_message(request[1], f"Сизнинг сўровингиз {unique_id} админ томонидан архивга ўтказилди.")
                except TelegramBadRequest as e:
                    logger.warning(f"Фойдаланувчи {request[1]} га хабар юборишда хатолик: {e}")
        await message.answer(f"Сўров {unique_id} архивга ўтказилди!", reply_markup=get_requests_menu(is_admin=True))
        await state.set_state(AdminStates.requests_menu)
        logger.info(f"Админ {user_id} сўров {unique_id} ни архивга ўтказди")
    except aiosqlite.Error as e:
        logger.error(f"Сўров {unique_id} ни архивга ўтказишда хатолик админ {user_id}: {e}", exc_info=True)
        await notify_admin(f"Сўров {unique_id} ни архивга ўтказишда хатолик admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровларни архивга ўтказишда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def process_delete_user(message: types.Message, state: FSMContext):
    """Фойдаланувчини ўчириш учун танлашни сўрайди."""
    user_id = message.from_user.id
    logger.debug(f"process_delete_user: user_id={user_id}, text={message.text}")

    if not message.text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT id FROM users WHERE id != ?", (user_id,)) as cursor:
                    users = [str(row[0]) for row in await cursor.fetchall()]
            if not users:
                await message.answer("Фойдаланувчилар йўқ.", reply_markup=get_main_menu(ADMIN_ROLE))
                await state.set_state(AdminStates.main_menu)
                logger.info(f"Админ {user_id} ўчириш учун фойдаланувчиларни топмади")
                return
            await message.answer("Фойдаланувчини танланг:", reply_markup=make_keyboard(users, columns=2, with_back=True))
            await state.set_state(AdminStates.delete_user)
            logger.info(f"Админ {user_id} фойдаланувчини ўчириш жараёнини бошлади")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении пользователей для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении пользователей для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
        return

    if message.text == "Орқага":
        await message.answer("Фойдаланувчиларни бошқариш:", reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2))
        await state.set_state(AdminStates.users_menu)
        logger.info(f"Админ {user_id} фойдаланувчини ўчиришни бекор қилди")
        return

    delete_user_id = message.text
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("SELECT id FROM users WHERE id = ?", (delete_user_id,)) as cursor:
                user = await cursor.fetchone()
            if not user:
                await message.answer(f"Фойдаланувчи ID {delete_user_id} топилмади!", reply_markup=get_main_menu(ADMIN_ROLE))
                await state.set_state(AdminStates.main_menu)
                logger.info(f"Админ {user_id} ID {delete_user_id} билан фойдаланувчи топмади")
                return
        if delete_user_id == str(user_id):
            await message.answer("Сиз ўзингизни ўчира олмайсиз!", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
            logger.info(f"Админ {user_id} ўзини ўчиришга уринди")
            return
        await state.update_data(delete_user_id=delete_user_id)
        await message.answer(
            f"Фойдаланувчи {delete_user_id} ни ўчиришни тасдиқланг:",
            reply_markup=make_keyboard(["Тасдиқлаш", "Бекор қилиш", "Орқага"], columns=2, one_time=True)
        )
        await state.set_state(AdminStates.confirm_delete_user)
        logger.info(f"Админ {user_id} фойдаланувчи {delete_user_id} ни ўчиришни тасдиқлашни сўради")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при проверке пользователя user_id={user_id}, delete_user_id={delete_user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных при проверке пользователя user_id={user_id}, delete_user_id={delete_user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def confirm_delete_user(message: types.Message, state: FSMContext):
    """Фойдаланувчини ўчиришни тасдиқлайди ёки бекор қилади."""
    user_id = message.from_user.id
    logger.debug(f"confirm_delete_user: user_id={user_id}, text={message.text}")

    if not message.text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        await message.answer("Илтимос, тугмани танланг:", reply_markup=make_keyboard(["Тасдиқлаш", "Бекор қилиш", "Орқага"], columns=2, one_time=True))
        return

    if message.text == "Бекор қилиш" or message.text == "Орқага":
        await message.answer("Фойдаланувчиларни бошқариш:", reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2))
        await state.set_state(AdminStates.users_menu)
        logger.info(f"Админ {user_id} фойдаланувчини ўчиришни бекор қилди")
        return

    if message.text != "Тасдиқлаш":
        await message.answer(
            "Илтимос, тугмани танланг:",
            reply_markup=make_keyboard(["Тасдиқлаш", "Бекор қилиш", "Орқага"], columns=2, one_time=True)
        )
        logger.warning(f"confirm_delete_user да нотўғри танлов: user_id={user_id}, text={message.text}")
        return

    try:
        data = await state.get_data()
        delete_user_id = data.get("delete_user_id")
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute(
                "SELECT id, phone_number, role, region, district, company_name, unique_id FROM users WHERE id = ?",
                (delete_user_id,)
            ) as cursor:
                user = await cursor.fetchone()
            if not user:
                await message.answer(f"Фойдаланувчи ID {delete_user_id} топилмади!", reply_markup=get_main_menu(ADMIN_ROLE))
                await conn.execute("ROLLBACK")
                await state.set_state(AdminStates.main_menu)
                return
            async with conn.execute(
                "SELECT user_id FROM deleted_users WHERE user_id = ? AND blocked = 1",
                (delete_user_id,)
            ) as cursor:
                if await cursor.fetchone():
                    await message.answer(f"Фойдаланувчи ID {delete_user_id} аллақачон блокланган!", reply_markup=get_main_menu(ADMIN_ROLE))
                    await conn.execute("ROLLBACK")
                    await state.set_state(AdminStates.main_menu)
                    return
            deleted_at = format_uz_datetime(datetime.now(pytz.UTC))
            await conn.execute(
                """INSERT INTO deleted_users 
                (user_id, phone_number, role, region, district, company_name, unique_id, deleted_at, blocked) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user[0], user[1], user[2], user[3], user[4], user[5], user[6], deleted_at, False)
            )
            await conn.execute("DELETE FROM users WHERE id = ?", (delete_user_id,))
            await conn.commit()
        await message.answer(
            f"Фойдаланувчи ID {delete_user_id} ўчирилди!",
            reply_markup=get_main_menu(ADMIN_ROLE)
        )
        await state.set_state(AdminStates.main_menu)
        logger.info(f"Админ {user_id} фойдаланувчи {delete_user_id} ни ўчирди")
    except aiosqlite.Error as e:
        logger.error(f"Фойдаланувчи {delete_user_id} ни ўчиришда хатолик админ {user_id}: {e}", exc_info=True)
        await notify_admin(f"Фойдаланувчи {delete_user_id} ни ўчиришда хатолик admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Фойдаланувчини ўчиришда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def subscription_list_command(message: types.Message, state: FSMContext):
    """Фаол обуналар рўйхатини кўрсатади."""
    user_id = message.from_user.id
    try:
        now = datetime.now(pytz.UTC)
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("""
                SELECT p.user_id, p.bot_expires, u.phone_number, u.role, p.trial_used
                FROM payments p
                JOIN users u ON p.user_id = u.id
                WHERE p.bot_expires > ?
            """, (format_uz_datetime(now),)) as cursor:
                subscriptions = await cursor.fetchall()
        if not subscriptions:
            await message.answer(
                "Фаол обуналар йўқ.",
                reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2)
            )
            logger.info(f"Админ {user_id} фаол обуналарни топмади")
            return
        response = "Фаол обуналар рўйхати:\n\n"
        for sub in subscriptions:
            user_id, bot_expires, phone, role, trial_used = sub
            expires_at = parse_uz_datetime(bot_expires).strftime("%d.%m.%Y %H:%M")
            sub_type = "Тест" if trial_used else "Тўловли"
            response += f"ID: {user_id}, Телефон: {phone or 'Йўқ'}, Рол: {role}\nТип: {sub_type}\nОбуна тугаши: {expires_at}\n\n"
        await message.answer(
            response,
            reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2)
        )
        logger.info(f"Админ {user_id} обуналар рўйхатини кўрди")
    except aiosqlite.Error as e:
        logger.error(f"Обуналар рўйхатини олишда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Обуналар рўйхатини олишда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Обуналарни юклашда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def process_subscribe_30_days(message: types.Message, state: FSMContext):
    """Фойдаланувчига 30 кунлик обуна беради."""
    user_id = message.from_user.id
    logger.debug(f"process_subscribe_30_days: user_id={user_id}, text={message.text}")

    if not message.text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT id FROM users WHERE id != ?", (user_id,)) as cursor:
                    users = [str(row[0]) for row in await cursor.fetchall()]
            if not users:
                await message.answer("Фойдаланувчилар йўқ.", reply_markup=get_main_menu(ADMIN_ROLE))
                await state.set_state(AdminStates.main_menu)
                logger.info(f"Админ {user_id} обуна бериш учун фойдаланувчиларни топмади")
                return
            await message.answer(
                "30 кунлик обуна бериш учун user_id ни танланг:",
                reply_markup=make_keyboard(users, columns=2, with_back=True)
            )
            await state.set_state(AdminStates.subscribe_30_days)
            logger.info(f"Админ {user_id} 30 кунлик обуна бериш жараёнини бошлади")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении пользователей для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении пользователей для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
        return

    if message.text == "Орқага":
        await message.answer("Обунани бошқариш:", reply_markup=make_keyboard(
            ["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2))
        await state.set_state(AdminStates.subscription_menu)
        logger.info(f"Админ {user_id} 30 кунлик обуна бериш жараёнини бекор қилди")
        return

    target_user_id = message.text
    expires_at = datetime.now(pytz.UTC) + timedelta(days=30)
    expires_at_str = format_uz_datetime(expires_at)
    try:
        if await manage_subscription(message, target_user_id, expires_at_str):
            await message.answer(
                f"{target_user_id} учун 30 кунлик обуна {expires_at.strftime('%d.%m.%Y')} гача берилди!",
                reply_markup=get_main_menu(ADMIN_ROLE)
            )
            await state.set_state(AdminStates.main_menu)
            logger.info(f"Админ {user_id} фойдаланувчи {target_user_id} га 30 кунлик обуна берди")
        else:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT id FROM users WHERE id != ?", (user_id,)) as cursor:
                    users = [str(row[0]) for row in await cursor.fetchall()]
            await message.answer(
                "Илтимос, рўйхатдан user_id танланг:",
                reply_markup=make_keyboard(users, columns=2, with_back=True)
            )
            logger.warning(f"Нотўғри user_id киритилди админ {user_id}: {message.text}")
    except aiosqlite.Error as e:
        logger.error(f"Обуна беришда хатолик user_id={user_id}, target_user_id={target_user_id}: {e}", exc_info=True)
        await notify_admin(f"Обуна беришда хатолик user_id={user_id}, target_user_id={target_user_id}: {str(e)}", bot=message.bot)
        await message.answer("Обуна беришда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def process_cancel_subscription(message: types.Message, state: FSMContext):
    """Фойдаланувчи обунасини бекор қилади."""
    user_id = message.from_user.id
    logger.debug(f"process_cancel_subscription: user_id={user_id}, text={message.text}")

    if not message.text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT id FROM users WHERE id != ?", (user_id,)) as cursor:
                    users = [str(row[0]) for row in await cursor.fetchall()]
            if not users:
                await message.answer("Фойдаланувчилар йўқ.", reply_markup=get_main_menu(ADMIN_ROLE))
                await state.set_state(AdminStates.main_menu)
                logger.info(f"Админ {user_id} обуна бекор қилиш учун фойдаланувчиларни топмади")
                return
            await message.answer(
                "Обунани бекор қилиш учун user_id ни танланг:",
                reply_markup=make_keyboard(users, columns=2, with_back=True)
            )
            await state.set_state(AdminStates.cancel_subscription)
            logger.info(f"Админ {user_id} обуна бекор қилиш жараёнини бошлади")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении пользователей для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении пользователей для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
        return

    if message.text == "Орқага":
        await message.answer("Обунани бошқариш:", reply_markup=make_keyboard(
            ["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2))
        await state.set_state(AdminStates.subscription_menu)
        logger.info(f"Админ {user_id} обуна бекор қилиш жараёнини бекор қилди")
        return

    target_user_id = message.text
    try:
        if await manage_subscription(message, target_user_id, None):
            await message.answer(
                f"{target_user_id} учун обуна бекор қилинди!",
                reply_markup=get_main_menu(ADMIN_ROLE)
            )
            await state.set_state(AdminStates.main_menu)
            logger.info(f"Админ {user_id} фойдаланувчи {target_user_id} обунасини бекор қилди")
        else:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT id FROM users WHERE id != ?", (user_id,)) as cursor:
                    users = [str(row[0]) for row in await cursor.fetchall()]
            await message.answer(
                "Илтимос, рўйхатдан user_id танланг:",
                reply_markup=make_keyboard(users, columns=2, with_back=True)
            )
            logger.warning(f"Нотўғри user_id киритилди админ {user_id}: {message.text}")
    except aiosqlite.Error as e:
        logger.error(f"Обуна бекор қилишда хатолик user_id={user_id}, target_user_id={target_user_id}: {e}", exc_info=True)
        await notify_admin(f"Обуна бекор қилишда хатолик user_id={user_id}, target_user_id={target_user_id}: {str(e)}", bot=message.bot)
        await message.answer("Обуна бекор қилишда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

async def manage_subscription(message: types.Message, user_id: str, bot_expires: str | None) -> bool:
    """Фойдаланувчи обунасини бошқаради."""
    admin_id = message.from_user.id
    if user_id == str(admin_id):
        await message.answer("Сиз ўзингизга обуна бера/бекор қила олмайсиз!", reply_markup=get_main_menu(ADMIN_ROLE))
        return False
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)) as cursor:
                if not await cursor.fetchone():
                    await message.answer(f"Фойдаланувчи ID {user_id} топилмади!", reply_markup=get_main_menu(ADMIN_ROLE))
                    await conn.execute("ROLLBACK")
                    return False
            if bot_expires is None:
                await conn.execute("DELETE FROM payments WHERE user_id = ?", (user_id,))
            else:
                await conn.execute("INSERT OR REPLACE INTO payments (user_id, bot_expires) VALUES (?, ?)",
                                  (user_id, bot_expires))
            await conn.commit()
            logger.debug(f"Обуна user_id={user_id} учун янгиланди, bot_expires={bot_expires}")
            return True
    except aiosqlite.Error as e:
        logger.error(f"Обуна бошқарувида хатолик user_id={user_id} админ {admin_id}: {e}", exc_info=True)
        await notify_admin(f"Обуна бошқарувида хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Обуна бошқарувида хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        return False

@admin_only
async def process_archives_menu(message: types.Message, state: FSMContext):
    """Архивларни бошқариш менюсини қайта ишлайди."""
    user_id = message.from_user.id
    text = message.text
    logger.debug(f"process_archives_menu: user_id={user_id}, text={text}")

    if not text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2))
        return

    if text == "Орқага":
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)
        logger.info(f"Админ {user_id} архивлар менюсидан асосий менюга қайтди")
        return
    elif text == "Архивлар рўйхати":
        await list_archives_command(message, state)
    elif text == "Архивларни ўчириш":
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute(
                        "SELECT unique_id FROM products WHERE status = 'archived' "
                        "UNION ALL "
                        "SELECT unique_id FROM requests WHERE status = 'archived'"
                ) as cursor:
                    archives = [row[0] for row in await cursor.fetchall()]
            if not archives:
                await message.answer("Архивлар йўқ.", reply_markup=get_main_menu(ADMIN_ROLE))
                await state.set_state(AdminStates.main_menu)
                logger.info(f"Админ {user_id} ўчириш учун архивларни топмади")
                return
            await message.answer(
                "Ўчириш учун архивни танланг (эълон ёки сўров):",
                reply_markup=make_keyboard(archives, columns=2, with_back=True)
            )
            await state.set_state(AdminStates.delete_archive)
            logger.info(f"Админ {user_id} архивни ўчириш жараёнини бошлади")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении архивов для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении архивов для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
    else:
        await message.answer(
            "Илтимос, менюдан танланг:",
            reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2)
        )
        logger.warning(f"Нотўғри танлов process_archives_menu: user_id={user_id}, text={text}")

@admin_only
async def list_archives_command(message: types.Message, state: FSMContext):
    """Архивлар рўйхатини кўрсатади."""
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                    "SELECT unique_id, category, sort, user_id, 'Эълон' as type FROM products WHERE status = 'archived' "
                    "UNION ALL "
                    "SELECT unique_id, category, sort, user_id, 'Сўров' as type FROM requests WHERE status = 'archived'"
            ) as cursor:
                archives = await cursor.fetchall()
        if not archives:
            await message.answer("Архивлар йўқ.", reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2))
            await state.set_state(AdminStates.archives_menu)
            logger.info(f"Админ {user_id} архивларни топмади")
            return
        response = "Архивлар рўйхати:\n\n"
        for unique_id, category, sort, user_id, archive_type in archives:
            response += f"Тип: {archive_type}, ID: {unique_id}, Тур: {category}, Нав: {sort}, User ID: {user_id}\n"
        await message.answer(
            response,
            reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2)
        )
        logger.info(f"Админ {user_id} архивлар рўйхатини кўрди")
    except aiosqlite.Error as e:
        logger.error(f"Архивлар рўйхатини олишда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Архивлар рўйхатини олишда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Архивларни юклашда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def process_delete_archive(message: types.Message, state: FSMContext):
    """unique_id бўйича архивни (эълон ёки сўров) ўчиради."""
    user_id = message.from_user.id
    logger.debug(f"process_delete_archive: user_id={user_id}, text={message.text}")

    if not message.text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute(
                        "SELECT unique_id FROM products WHERE status = 'archived' "
                        "UNION ALL "
                        "SELECT unique_id FROM requests WHERE status = 'archived'"
                ) as cursor:
                    archives = [row[0] for row in await cursor.fetchall()]
            if not archives:
                await message.answer("Архивлар йўқ.", reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2))
                await state.set_state(AdminStates.archives_menu)
                logger.info(f"Админ {user_id} ўчириш учун архивларни топмади")
                return
            await message.answer(
                "Ўчириш учун архивни танланг (эълон ёки сўров):",
                reply_markup=make_keyboard(archives, columns=2, with_back=True)
            )
            await state.set_state(AdminStates.delete_archive)
            logger.info(f"Админ {user_id} архивни ўчириш жараёнини бошлади")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при получении архивов для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных при получении архивов для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.set_state(AdminStates.main_menu)
        return

    if message.text == "Орқага":
        await message.answer("Архивни бошқариш:", reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2))
        await state.set_state(AdminStates.archives_menu)
        logger.info(f"Админ {user_id} архивни ўчиришни бекор қилди")
        return

    unique_id = message.text
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute(
                    "SELECT channel_message_id, user_id FROM products WHERE unique_id = ? AND status = 'archived'",
                    (unique_id,)
            ) as cursor:
                product = await cursor.fetchone()
            if product:
                if product[0]:
                    try:
                        await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=product[0])
                        logger.debug(f"Канал хабари {product[0]} архив эълони {unique_id} учун ўчирилди")
                    except TelegramBadRequest as e:
                        logger.warning(f"Канал хабари {product[0]} ни архив эълони {unique_id} учун ўчиришда хатолик: {e}")
                await conn.execute("DELETE FROM products WHERE unique_id = ? AND status = 'archived'", (unique_id,))
                await conn.commit()
                if product[1]:
                    try:
                        await message.bot.send_message(product[1], f"Сизнинг архивланган эълонингиз {unique_id} админ томонидан ўчирилди.")
                    except TelegramBadRequest as e:
                        logger.warning(f"Фойдаланувчи {product[1]} га хабар юборишда хатолик: {e}")
                await message.answer(
                    f"Архивланган эълон {unique_id} ўчирилди!",
                    reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2)
                )
            else:
                async with conn.execute(
                        "SELECT channel_message_id, user_id FROM requests WHERE unique_id = ? AND status = 'archived'",
                        (unique_id,)
                ) as cursor:
                    request = await cursor.fetchone()
                if request:
                    if request[0]:
                        try:
                            await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=request[0])
                            logger.debug(f"Канал хабари {request[0]} архив сўрови {unique_id} учун ўчирилди")
                        except TelegramBadRequest as e:
                            logger.warning(f"Канал хабари {request[0]} ни архив сўрови {unique_id} учун ўчиришда хатолик: {e}")
                    await conn.execute("DELETE FROM requests WHERE unique_id = ? AND status = 'archived'", (unique_id,))
                    await conn.commit()
                    if request[1]:
                        try:
                            await message.bot.send_message(request[1], f"Сизнинг архивланган сўровингиз {unique_id} админ томонидан ўчирилди.")
                        except TelegramBadRequest as e:
                            logger.warning(f"Фойдаланувчи {request[1]} га хабар юборишда хатолик: {e}")
                    await message.answer(
                        f"Архивланган сўров {unique_id} ўчирилди!",
                        reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2)
                    )
                else:
                    await message.answer(f"Архивда {unique_id} топилмади!", reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2))
                    await conn.execute("ROLLBACK")
                    await state.set_state(AdminStates.archives_menu)
                    return
            await state.set_state(AdminStates.archives_menu)
            logger.info(f"Админ {user_id} архив {unique_id} ни ўчирди")
    except aiosqlite.Error as e:
        logger.error(f"Архив {unique_id} ни ўчиришда хатолик админ {user_id}: {e}", exc_info=True)
        await notify_admin(f"Архив {unique_id} ни ўчиришда хатолик admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Архивни ўчиришда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def statistics_command(message: types.Message, state: FSMContext, dp: Dispatcher):
    """Система статистикасини ҳафта ва ойлик трендлар билан кўрсатади."""
    user_id = message.from_user.id
    try:
        now = format_uz_datetime(datetime.now(pytz.UTC))
        week_ago = format_uz_datetime(datetime.now(pytz.UTC) - timedelta(days=7))
        month_ago = format_uz_datetime(datetime.now(pytz.UTC) - timedelta(days=30))
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("SELECT role, COUNT(*) FROM users GROUP BY role") as cursor:
                role_counts = {row[0]: row[1] for row in await cursor.fetchall()}
            async with conn.execute("SELECT status, COUNT(*) FROM products GROUP BY status") as cursor:
                product_stats = {row[0]: row[1] for row in await cursor.fetchall()}
            async with conn.execute("SELECT status, COUNT(*) FROM requests GROUP BY status") as cursor:
                request_stats = {row[0]: row[1] for row in await cursor.fetchall()}
            async with conn.execute("SELECT COUNT(*) FROM payments WHERE bot_expires > ?", (now,)) as cursor:
                active_subs = (await cursor.fetchone())[0]
            async with conn.execute("SELECT COUNT(*) FROM deleted_users") as cursor:
                deleted_users = (await cursor.fetchone())[0]
            async with conn.execute("SELECT COUNT(*) FROM products WHERE status = 'archived'") as cursor:
                archived_products = (await cursor.fetchone())[0]
            async with conn.execute("SELECT COUNT(*) FROM requests WHERE status = 'archived'") as cursor:
                archived_requests = (await cursor.fetchone())[0]
            async with conn.execute("SELECT COUNT(*) FROM users WHERE created_at > ?", (week_ago,)) as cursor:
                new_users_week = (await cursor.fetchone())[0]
            async with conn.execute("SELECT COUNT(*) FROM products WHERE created_at > ?", (week_ago,)) as cursor:
                new_products_week = (await cursor.fetchone())[0]
            async with conn.execute("SELECT COUNT(*) FROM requests WHERE created_at > ?", (week_ago,)) as cursor:
                new_requests_week = (await cursor.fetchone())[0]
            async with conn.execute("SELECT COUNT(*) FROM users WHERE created_at > ?", (month_ago,)) as cursor:
                new_users_month = (await cursor.fetchone())[0]
            async with conn.execute("SELECT COUNT(*) FROM products WHERE created_at > ?", (month_ago,)) as cursor:
                new_products_month = (await cursor.fetchone())[0]
            async with conn.execute("SELECT COUNT(*) FROM requests WHERE created_at > ?", (month_ago,)) as cursor:
                new_requests_month = (await cursor.fetchone())[0]

        response = (
            "📊 Статистика:\n\n"
            f"👥 Фойдаланувчилар:\n"
            f"• Админлар: {role_counts.get('admin', 0)}\n"
            f"• Сотувчилар: {role_counts.get('seller', 0)}\n"
            f"• Харидорлар: {role_counts.get('buyer', 0)}\n"
            f"• Ўчирилганлар: {deleted_users}\n\n"
            f"📦 Эълонлар:\n"
            f"• Фаол: {product_stats.get('active', 0)}\n"
            f"• Архив: {product_stats.get('archived', 0)}\n"
            f"• Ўчирилган: {product_stats.get('deleted', 0)}\n\n"
            f"📝 Сўровлар:\n"
            f"• Фаол: {request_stats.get('active', 0)}\n"
            f"• Архив: {request_stats.get('archived', 0)}\n"
            f"• Ўчирилган: {request_stats.get('deleted', 0)}\n\n"
            f"📜 Архивлар:\n"
            f"• Эълонлар: {archived_products}\n"
            f"• Сўровлар: {archived_requests}\n"
            f"• Жами: {archived_products + archived_requests}\n\n"
            f"🔔 Обуналар:\n"
            f"• Фаол: {active_subs}\n\n"
            f"📈 Трендлар:\n"
            f"• Ҳафтада янги фойдаланувчилар: {new_users_week}\n"
            f"• Ойда янги фойдаланувчилар: {new_users_month}\n"
            f"• Ҳафтада янги эълонлар: {new_products_week}\n"
            f"• Ойда янги эълонлар: {new_products_month}\n"
            f"• Ҳафтада янги сўровлар: {new_requests_week}\n"
            f"• Ойда янги сўровлар: {new_requests_month}"
        )
        await message.answer(response, reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)
        logger.info(f"Админ {user_id} статистикани кўрди")
    except aiosqlite.Error as e:
        logger.error(f"Статистикани олишда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Статистикани олишда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Статистикани юклашда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def list_users_command(message: types.Message, state: FSMContext):
    """Фойдаланувчилар рўйхатини кўрсатади, разделяя по ролям и ограничивая 25 пользователей на сообщение."""
    user_id = message.from_user.id
    logger.debug(f"list_users_command: user_id={user_id}")

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("""
                SELECT u.id, u.phone_number, u.role, u.unique_id, p.bot_expires, p.trial_used
                FROM users u
                LEFT JOIN payments p ON u.id = p.user_id
            """) as cursor:
                active_users = await cursor.fetchall()
            async with conn.execute("SELECT COUNT(*) FROM deleted_users") as cursor:
                deleted_count = (await cursor.fetchone())[0]

        admins, sellers, buyers = [], [], []
        active_sub_count, trial_sub_count = 0, 0
        now = datetime.now(pytz.timezone('Asia/Tashkent'))

        for user in active_users:
            user_id, phone, role, unique_id, bot_expires, trial_used = user
            has_sub = bot_expires and parse_uz_datetime(bot_expires) > now
            user_info = (user_id, phone, unique_id)
            if has_sub:
                active_sub_count += 1
                if trial_used:
                    trial_sub_count += 1
            if role == 'admin':
                admins.append(user_info)
            elif role == 'seller':
                sellers.append(user_info)
            elif role == 'buyer':
                buyers.append(user_info)

        # Функция для отправки списка пользователей по частям
        async def send_user_list(role_name: str, users: list, role_count: int):
            if not users:
                await message.answer(
                    f"{role_name} ({role_count}): Йўқ",
                    reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2)
                )
                return
            chunk_size = 25
            for i in range(0, len(users), chunk_size):
                chunk = users[i:i + chunk_size]
                response = f"{role_name} ({role_count}, {i+1}-{min(i+len(chunk), role_count)}):\n\n"
                response += "\n".join(f"ID: {u[0]}, Телефон: {u[1] or 'Йўқ'}, Уникал ID: {u[2]}" for u in chunk)
                try:
                    await message.answer(
                        response,
                        reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2)
                    )
                    await asyncio.sleep(0.5)  # Задержка для избежания ограничений Telegram
                except TelegramBadRequest as e:
                    logger.error(f"Ошибка отправки списка {role_name} для user_id={user_id}: {e}", exc_info=True)
                    await notify_admin(f"Ошибка отправки списка {role_name} для user_id={user_id}: {str(e)}", bot=message.bot)
                    await message.answer(
                        f"{role_name} рўйхатини юклашда хатолик!",
                        reply_markup=get_main_menu(ADMIN_ROLE)
                    )
                    return

        # Отправка списков по ролям
        await send_user_list("Админлар", admins, len(admins))
        await send_user_list("Сотувчилар", sellers, len(sellers))
        await send_user_list("Харидорлар", buyers, len(buyers))

        # Отправка статистики
        stats_response = (
            f"\nАктив обуналар (жами: {active_sub_count}, тест: {trial_sub_count})\n"
            f"Ўчирилган фойдаланувчилар (жами: {deleted_count})"
        )
        await message.answer(
            stats_response,
            reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2)
        )

        logger.info(f"Админ {user_id} фойдаланувчилар рўйхатини кўрди")
        await state.set_state(AdminStates.users_menu)
    except aiosqlite.Error as e:
        logger.error(f"Фойдаланувчилар рўйхатини олишда хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Фойдаланувчилар рўйхатини олишда хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Рўйхатни юклашда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)

@admin_only
async def process_broadcast_message(message: types.Message, state: FSMContext):
    """Обрабатывает ввод текста для рассылки."""
    user_id = message.from_user.id
    text = message.text
    logger.debug(f"process_broadcast_message: user_id={user_id}, text={text}")

    if not text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        await message.answer("Илтимос, хабар матнини киритинг:", reply_markup=make_keyboard(["Орқага"], columns=1))
        return

    if text == "Орқага":
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.set_state(AdminStates.main_menu)
        logger.info(f"Админ {user_id} рассылку бекор қилди")
        return

    if len(text) > 4096:
        await message.answer(
            "Хабар жуда узун! Илтимос, 4096 та белгидан камроқ матн киритинг.",
            reply_markup=make_keyboard(["Орқага"], columns=1)
        )
        logger.warning(f"Слишком длинное сообщение для рассылки user_id={user_id}: {len(text)} символов")
        return

    await state.update_data(broadcast_text=text)
    await message.answer(
        f"Ушбу хабар барча фойдаланувчиларга юборилади:\n\n{text}\n\nТасдиқлайсизми?",
        reply_markup=make_keyboard(["Тасдиқлаш", "Бекор қилиш", "Орқага"], columns=2, one_time=True)
    )
    await state.set_state(AdminStates.confirm_broadcast)
    logger.info(f"Админ {user_id} хабар матнини киритди ва тасдиқлашга ўтди")

@admin_only
async def confirm_broadcast_message(message: types.Message, state: FSMContext):
    """Подтверждает и отправляет рассылку всем пользователям."""
    user_id = message.from_user.id
    text = message.text
    logger.debug(f"confirm_broadcast_message: user_id={user_id}, text={text}")

    if not text:
        logger.warning(f"Матнсиз хабар user_id={user_id}")
        await message.answer(
            "Илтимос, тугмани танланг:",
            reply_markup=make_keyboard(["Тасдиқлаш", "Бекор қилиш", "Орқага"], columns=2, one_time=True)
        )
        return

    if text in ["Бекор қилиш", "Орқага"]:
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
        await state.set_state(AdminStates.main_menu)
        logger.info(f"Админ {user_id} рассылку бекор қилди")
        return

    if text != "Тасдиқлаш":
        await message.answer(
            "Илтимос, тугмани танланг:",
            reply_markup=make_keyboard(["Тасдиқлаш", "Бекор қилиш", "Орқага"], columns=2, one_time=True)
        )
        logger.warning(f"Нотўғри танлов confirm_broadcast_message: user_id={user_id}, text={text}")
        return

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await message.answer("Хабар матни топилмади! Жараённи қайтадан бошланг.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
        await state.set_state(AdminStates.main_menu)
        logger.warning(f"Отсутствует текст рассылки для user_id={user_id}")
        return

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("SELECT id FROM users") as cursor:
                users = [row[0] for row in await cursor.fetchall()]

        if not users:
            await message.answer("Фойдаланувчилар йўқ.", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.clear()
            await state.set_state(AdminStates.main_menu)
            logger.info(f"Админ {user_id} рассылку бекор қилди: пользователи не найдены")
            return

        success_count = 0
        failed_count = 0
        failed_users = []

        await message.answer("Рассылка бошланди...", reply_markup=get_main_menu(ADMIN_ROLE))

        for target_user_id in users:
            try:
                await message.bot.send_message(target_user_id, broadcast_text)
                success_count += 1
                logger.debug(f"Хабар user_id={target_user_id} га муваффақиятли юборилди")
            except TelegramBadRequest as e:
                failed_count += 1
                failed_users.append(target_user_id)
                logger.warning(f"Хабар user_id={target_user_id} га юборилмади: {e}")
            except Exception as e:
                failed_count += 1
                failed_users.append(target_user_id)
                logger.error(f"Неожиданная ошибка при отправке user_id={target_user_id}: {e}", exc_info=True)
            await asyncio.sleep(0.1)  # Задержка для избежания ограничений Telegram

        response = (
            f"Рассылка якунланди!\n"
            f"Жами фойдаланувчилар: {len(users)}\n"
            f"Муваффақиятли юборилди: {success_count}\n"
            f"Юборилмади: {failed_count}"
        )
        if failed_count > 0:
            response += f"\nЮборилмаган user_id'лар: {', '.join(map(str, failed_users[:10]))}"
            if len(failed_users) > 10:
                response += f" ва яна {len(failed_users) - 10} та фойдаланувчи"

        await message.answer(response, reply_markup=get_main_menu(ADMIN_ROLE))
        await notify_admin(
            f"Админ {user_id} рассылку якунлади: {success_count} муваффақиятли, {failed_count} хатолик",
            bot=message.bot
        )
        logger.info(f"Админ {user_id} рассылку якунлади: {success_count} успехов, {failed_count} ошибок")
        await state.clear()
        await state.set_state(AdminStates.main_menu)
    except aiosqlite.Error as e:
        logger.error(f"Рассылкада хатолик user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Рассылкада хатолик user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Рассылкада хатолик юз берди!", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
        await state.set_state(AdminStates.main_menu)

def register_handlers(dp: Dispatcher):
    """Админ панели учун обработчикларни рўйхатга олади."""
    logger.info("Админ панели обработчиклари рўйхатга олинмоқда")

    dp.message.register(admin_command, F.text == "/admin", F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(
        process_main_menu,
        AdminStates.main_menu,
        F.text.in_(["Фойдаланувчиларни бошқариш", "Эълонларни бошқариш", "Сўровларни бошқариш",
                    "Обунани бошқариш", "Архивни бошқариш", "Хабар юбориш", "Статистика", "Орқага"]),
        F.from_user.id.in_(ADMIN_IDS)
    )
    dp.message.register(process_users_menu, AdminStates.users_menu, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_products_menu, AdminStates.products_menu, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_requests_menu, AdminStates.requests_menu, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_subscription_menu, AdminStates.subscription_menu, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_archives_menu, AdminStates.archives_menu, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(admin_delete_product, AdminStates.admin_delete_product, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(admin_delete_request, AdminStates.admin_delete_request, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_archive_product, AdminStates.archive_product, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_archive_request, AdminStates.archive_request, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_delete_user, AdminStates.delete_user, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(confirm_delete_user, AdminStates.confirm_delete_user, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_subscribe_30_days, AdminStates.subscribe_30_days, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_cancel_subscription, AdminStates.cancel_subscription, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_delete_archive, AdminStates.delete_archive, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(process_broadcast_message, AdminStates.broadcast_message, F.from_user.id.in_(ADMIN_IDS))
    dp.message.register(confirm_broadcast_message, AdminStates.confirm_broadcast, F.from_user.id.in_(ADMIN_IDS))

    logger.info("Админ панели обработчиклари рўйхатга олинди")