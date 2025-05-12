import aiosqlite
import logging
from aiogram import types, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import DB_NAME, DB_TIMEOUT, ADMIN_ROLE, CHANNEL_ID, ADMIN_IDS
from utils import check_role, make_keyboard, format_uz_datetime, parse_uz_datetime, notify_admin, get_main_menu
from common import send_subscription_message
from datetime import datetime, timedelta
from functools import wraps
from profile import profile_menu
import pytz

logger = logging.getLogger(__name__)

class AdminStates(StatesGroup):
    main_menu = State()
    users_menu = State()
    products_menu = State()
    requests_menu = State()
    subscription_menu = State()
    archives_menu = State()
    subscribe_30_days = State()
    cancel_subscription = State()
    delete_product = State()
    archive_product = State()
    delete_request = State()
    archive_request = State()
    delete_user = State()
    delete_archive = State()

def admin_only(handler):
    """Ограничивает доступ к функциям только для администраторов."""
    @wraps(handler)
    async def wrapper(message: types.Message, state: FSMContext, *args, **kwargs):
        user_id = message.from_user.id
        allowed, role = await check_role(message)
        logger.debug(f"Admin check: user_id={user_id}, allowed={allowed}, role={role}, in_ADMIN_IDS={user_id in ADMIN_IDS}")
        if not allowed or role != ADMIN_ROLE or user_id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin access attempt by {user_id}, role={role}, in_ADMIN_IDS={user_id in ADMIN_IDS}")
            await message.answer("Бу буйруқ фақат админлар учун!", reply_markup=get_main_menu(None))
            return
        logger.debug(f"Admin {user_id} access granted, role={role}")
        return await handler(message, state, *args, **kwargs)
    return wrapper

@admin_only
async def process_main_menu(message: types.Message, state: FSMContext):
    """Обрабатывает выбор в главном меню админ-панели."""
    user_id = message.from_user.id
    text = message.text
    current_state = await state.get_state()
    logger.debug(f"process_main_menu: user_id={user_id}, text={text}, state={current_state}")

    if not isinstance(text, str):
        logger.error(f"Invalid message.text type: {type(text)}, value: {text}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_main_menu(ADMIN_ROLE))
        return

    if text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} returned to main menu")
        return
    elif text == "Менинг профилим":
        logger.debug(f"Calling profile_menu for user_id={user_id} with text={text}")
        try:
            await profile_menu(message, state)
        except Exception as e:
            logger.error(f"Error calling profile_menu for user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Error in profile_menu for user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        return

    menu_options = {
        "Фойдаланувчиларни бошқариш": (AdminStates.users_menu, ["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"]),
        "Эълонларни бошқариш": (AdminStates.products_menu, ["Админ: Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни архивга ўтказиш", "Орқага"]),
        "Сўровларни бошқариш": (AdminStates.requests_menu, ["Админ: Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни архивга ўтказиш", "Орқага"]),
        "Обунани бошқариш": (AdminStates.subscription_menu, ["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"]),
        "Архивни бошқариш": (AdminStates.archives_menu, ["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"]),
        "Статистика": (None, None)
    }

    option = menu_options.get(text)
    if option:
        if text == "Статистика":
            await statistics_command(message, state)
        else:
            state_info, buttons = option
            await message.answer(f"{text}:", reply_markup=make_keyboard(buttons, columns=2, with_back=True))
            await state.set_state(state_info)
            logger.info(f"Admin {user_id} selected {text}")
    else:
        logger.warning(f"Invalid menu option selected by user_id={user_id}: {text}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_main_menu(ADMIN_ROLE))

# User Management
@admin_only
async def process_users_menu(message: types.Message, state: FSMContext):
    """Обрабатывает меню управления пользователями."""
    user_id = message.from_user.id
    text = message.text
    current_state = await state.get_state()
    logger.debug(f"process_users_menu: user_id={user_id}, text={text}, state={current_state}")
    if current_state != AdminStates.users_menu.state:
        logger.warning(f"Unexpected state for process_users_menu: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} returned to main menu from users menu")
        return
    elif text == "Фойдаланувчилар рўйхати":
        await list_users_command(message, state)
    elif text == "Фойдаланувчини ўчириш":
        await message.answer("Ўчириш учун фойдаланувчи Telegram ID ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(AdminStates.delete_user)
        logger.info(f"Admin {user_id} started user deletion process")
    else:
        await message.answer("Илтимос, менюдан танланг:",
                             reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш"], columns=2, with_back=True))
        logger.warning(f"Invalid option in process_users_menu: user_id={user_id}, text={text}")

@admin_only
async def list_users_command(message: types.Message, state: FSMContext):
    """Отображает список пользователей."""
    user_id = message.from_user.id
    try:
        now = format_uz_datetime(datetime.now(pytz.UTC))
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("""
                SELECT u.id, u.phone_number, u.role, u.unique_id, 
                       (p.bot_expires > ?) as has_active_sub,
                       p.trial_used
                FROM users u
                LEFT JOIN payments p ON u.id = p.user_id
            """, (now,)) as cursor:
                active_users = await cursor.fetchall()
            async with conn.execute("SELECT COUNT(*) FROM deleted_users") as cursor:
                deleted_count = (await cursor.fetchone())[0]

        admins, sellers, buyers = [], [], []
        active_sub_count, trial_sub_count = 0, 0

        for user in active_users:
            user_id, phone, role, unique_id, has_sub, trial_used = user
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

        response_parts = ["Фойдаланувчилар рўйхати:"]
        response_parts.append(f"\nАдминлар ({len(admins)}):")
        if admins:
            response_parts.extend(f"ID: {a[0]}, Телефон: {a[1]}, Уникал ID: {a[2]}" for a in admins)
        response_parts.append(f"\n\nСотувчилар ({len(sellers)}):")
        if sellers:
            response_parts.extend(f"ID: {s[0]}, Телефон: {s[1]}, Уникал ID: {s[2]}" for s in sellers)
        response_parts.append(f"\n\nХаридорлар ({len(buyers)}):")
        if buyers:
            response_parts.extend(f"ID: {b[0]}, Телефон: {b[1]}, Уникал ID: {b[2]}" for b in buyers)
        response_parts.append(f"\n\nАктив обуналар (жами: {active_sub_count}, тестовые: {trial_sub_count})")
        response_parts.append(f"Ўчирилган фойдаланувчилар (жами: {deleted_count})")

        await message.answer(
            "\n".join(response_parts),
            reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш"], columns=2, with_back=True)
        )
        logger.info(f"Admin {user_id} listed users")
    except aiosqlite.Error as e:
        logger.error(f"Error listing users for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка при получении списка пользователей для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Рўйхатни юклашда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
    except Exception as e:
        logger.error(f"Unexpected error listing users for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка при получении списка пользователей для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))

@admin_only
async def process_delete_user(message: types.Message, state: FSMContext):
    """Удаляет пользователя по Telegram ID."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"process_delete_user: user_id={user_id}, text={message.text}, state={current_state}")
    if current_state != AdminStates.delete_user.state:
        logger.warning(f"Unexpected state for process_delete_user: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if message.text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} canceled user deletion")
        return
    try:
        delete_user_id = int(message.text.strip())
        admin_id = message.from_user.id
        if delete_user_id == admin_id:
            await message.answer("Сиз ўзингизни ўчира олмайсиз!", reply_markup=get_main_menu(ADMIN_ROLE))
            await state.clear()
            return
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute(
                "SELECT id, phone_number, role, region, district, company_name, unique_id FROM users WHERE id = ?",
                (delete_user_id,)
            ) as cursor:
                user = await cursor.fetchone()
            if not user:
                await message.answer(f"Фойдаланувчи с ID {delete_user_id} топилмади!", reply_markup=get_main_menu(ADMIN_ROLE))
                await conn.execute("ROLLBACK")
                await state.clear()
                return
            async with conn.execute(
                "SELECT user_id FROM deleted_users WHERE user_id = ? AND blocked = 1",
                (delete_user_id,)
            ) as cursor:
                if await cursor.fetchone():
                    await message.answer(f"Фойдаланувчи с ID {delete_user_id} аллақачон блокланган!", reply_markup=get_main_menu(ADMIN_ROLE))
                    await conn.execute("ROLLBACK")
                    await state.clear()
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
            f"Фойдаланувчи с ID {delete_user_id} ўчирилди!",
            reply_markup=get_main_menu(ADMIN_ROLE)
        )
        await state.clear()
        logger.info(f"Admin {admin_id} deleted user {delete_user_id}")
    except ValueError:
        await message.answer("Тўғри Telegram ID киритинг (фақат рақамлар)!",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        logger.warning(f"Invalid user ID input by admin {user_id}: {message.text}")
    except aiosqlite.Error as e:
        logger.error(f"Error deleting user {delete_user_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка удаления пользователя {delete_user_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Фойдаланувчини ўчиришда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
    except Exception as e:
        logger.error(f"Unexpected error deleting user {delete_user_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка удаления пользователя {delete_user_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()

# Products Management
@admin_only
async def process_products_menu(message: types.Message, state: FSMContext):
    """Обрабатывает меню управления продуктами."""
    user_id = message.from_user.id
    text = message.text
    current_state = await state.get_state()
    logger.debug(f"process_products_menu: user_id={user_id}, text={text}, state={current_state}")
    if current_state != AdminStates.products_menu.state:
        logger.warning(f"Unexpected state for process_products_menu: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} returned to main menu from products menu")
        return
    elif text == "Админ: Эълонлар рўйхати":
        await list_products_command(message, state)
    elif text == "Эълонни ўчириш":
        await message.answer("Ўчириш учун эълон unique_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(AdminStates.delete_product)
        logger.info(f"Admin {user_id} started product deletion process")
    elif text == "Эълонни архивга ўтказиш":
        await message.answer("Архивга ўтказиш учун эълон unique_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(AdminStates.archive_product)
        logger.info(f"Admin {user_id} started product archiving process")
    else:
        await message.answer("Илтимос, менюдан танланг:",
                             reply_markup=make_keyboard(["Админ: Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни архивга ўтказиш"], columns=2, with_back=True))
        logger.warning(f"Invalid option in process_products_menu: user_id={user_id}, text={text}")

@admin_only
async def list_products_command(message: types.Message, state: FSMContext):
    """Отображает список продуктов."""
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("SELECT unique_id, user_id, category, sort, status, created_at FROM products") as cursor:
                products = await cursor.fetchall()
        if not products:
            await message.answer("Эълонлар йўқ.", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
            logger.info(f"Admin {user_id} found no products")
            return
        response = "Эълонлар рўйхати:\n\n"
        for unique_id, user_id, category, sort, status, created_at in products:
            response += f"ID: {unique_id}, User ID: {user_id}, Тур: {category}, Нав: {sort}, Ҳолат: {status}, Яратилган: {created_at}\n"
        await message.answer(
            response,
            reply_markup=make_keyboard(["Админ: Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни архивга ўтказиш"], columns=2, with_back=True)
        )
        logger.info(f"Admin {user_id} listed products")
    except aiosqlite.Error as e:
        logger.error(f"Error listing products for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка при получении списка продуктов для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Эълонларни юклашда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
    except Exception as e:
        logger.error(f"Unexpected error listing products for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка при получении списка продуктов для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))

@admin_only
async def process_delete_product(message: types.Message, state: FSMContext):
    """Удаляет продукт по unique_id."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"process_delete_product: user_id={user_id}, text={message.text}, state={current_state}")
    if current_state != AdminStates.delete_product.state:
        logger.warning(f"Unexpected state for process_delete_product: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if message.text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} canceled product deletion")
        return
    unique_id = message.text.strip()
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute("SELECT channel_message_id, user_id FROM products WHERE unique_id = ?", (unique_id,)) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(f"Эълон {unique_id} топилмади!", reply_markup=get_main_menu(ADMIN_ROLE))
                await conn.execute("ROLLBACK")
                await state.clear()
                return
            if product[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=product[0])
                    logger.debug(f"Deleted channel message {product[0]} for product {unique_id}")
                except Exception as e:
                    logger.warning(f"Failed to delete channel message {product[0]} for product {unique_id}: {e}", exc_info=True)
            await conn.execute("UPDATE products SET status = 'deleted' WHERE unique_id = ?", (unique_id,))
            await conn.commit()
            if product[1]:
                await message.bot.send_message(product[1], f"Сизнинг эълонингиз {unique_id} админ томонидан ўчирилди.")
        await message.answer(
            f"Эълон {unique_id} ўчирилди!",
            reply_markup=get_main_menu(ADMIN_ROLE)
        )
        await state.clear()
        logger.info(f"Admin {user_id} deleted product {unique_id}")
    except aiosqlite.Error as e:
        logger.error(f"Error deleting product {unique_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка удаления продукта {unique_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Эълонни ўчиришда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
    except Exception as e:
        logger.error(f"Unexpected error deleting product {unique_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка удаления продукта {unique_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()

@admin_only
async def process_archive_product(message: types.Message, state: FSMContext):
    """Архивирует продукт по unique_id."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"process_archive_product: user_id={user_id}, text={message.text}, state={current_state}")
    if current_state != AdminStates.archive_product.state:
        logger.warning(f"Unexpected state for process_archive_product: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if message.text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} canceled product archiving")
        return
    unique_id = message.text.strip()
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute("SELECT unique_id, user_id FROM products WHERE unique_id = ? AND status = 'active'", (unique_id,)) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(f"Актив эълон {unique_id} топилмади!", reply_markup=get_main_menu(ADMIN_ROLE))
                await conn.execute("ROLLBACK")
                await state.clear()
                return
            archived_at = format_uz_datetime(datetime.now(pytz.UTC))
            await conn.execute("UPDATE products SET status = 'archived', archived_at = ? WHERE unique_id = ?",
                               (archived_at, unique_id))
            await conn.commit()
            if product[1]:
                await message.bot.send_message(product[1], f"Сизнинг эълонингиз {unique_id} админ томонидан архивга ўтказилди.")
        await message.answer(
            f"Эълон {unique_id} архивга ўтказилди!",
            reply_markup=get_main_menu(ADMIN_ROLE)
        )
        await state.clear()
        logger.info(f"Admin {user_id} archived product {unique_id}")
    except aiosqlite.Error as e:
        logger.error(f"Error archiving product {unique_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка архивирования продукта {unique_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Эълонни архивга ўтказишда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
    except Exception as e:
        logger.error(f"Unexpected error archiving product {unique_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка архивирования продукта {unique_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()

# Requests Management
@admin_only
async def process_requests_menu(message: types.Message, state: FSMContext):
    """Обрабатывает меню управления запросами."""
    user_id = message.from_user.id
    text = message.text
    current_state = await state.get_state()
    logger.debug(f"process_requests_menu: user_id={user_id}, text={text}, state={current_state}")
    if current_state != AdminStates.requests_menu.state:
        logger.warning(f"Unexpected state for process_requests_menu: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} returned to main menu from requests menu")
        return
    elif text == "Админ: Сўровлар рўйхати":
        await list_requests_command(message, state)
    elif text == "Сўровни ўчириш":
        await message.answer("Ўчириш учун сўров unique_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(AdminStates.delete_request)
        logger.info(f"Admin {user_id} started request deletion process")
    elif text == "Сўровни архивга ўтказиш":
        await message.answer("Архивга ўтказиш учун сўров unique_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(AdminStates.archive_request)
        logger.info(f"Admin {user_id} started request archiving process")
    else:
        await message.answer("Илтимос, менюдан танланг:",
                             reply_markup=make_keyboard(["Админ: Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни архивга ўтказиш"], columns=2, with_back=True))
        logger.warning(f"Invalid option in process_requests_menu: user_id={user_id}, text={text}")

@admin_only
async def list_requests_command(message: types.Message, state: FSMContext):
    """Отображает список активных запросов."""
    user_id = message.from_user.id
    if message.text != "Админ: Сўровлар рўйхати":
        logger.warning(f"Unexpected text in list_requests_command: user_id={user_id}, text={message.text}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        return
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT unique_id, user_id, category, sort, status FROM requests WHERE status = 'active' LIMIT 50"
            ) as cursor:
                requests = await cursor.fetchall()
        if not requests:
            await message.answer("Фаол сўровлар йўқ.", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
            logger.info(f"Admin {user_id} found no active requests")
            return
        response = "Сўровлар рўйхати:\n\n"
        status_map = {
            "active": "Фаол",
            "archived": "Архивланган",
            "deleted": "Ўчирилган"
        }
        for unique_id, user_id, category, sort, status in requests:
            status_uz = status_map.get(status, status)
            response += f"ID: {unique_id}, User ID: {user_id}, Тур: {category}, Нав: {sort}, Ҳолат: {status_uz}\n"
        await message.answer(
            response,
            reply_markup=make_keyboard(["Админ: Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни архивга ўтказиш"], columns=2, with_back=True)
        )
        logger.info(f"Admin {user_id} listed requests")
    except aiosqlite.Error as e:
        logger.error(f"Error listing requests for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка при получении списка запросов для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровларни юклашда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
    except Exception as e:
        logger.error(f"Unexpected error listing requests for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка при получении списка запросов для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))

@admin_only
async def process_delete_request(message: types.Message, state: FSMContext):
    """Удаляет запрос по unique_id."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"process_delete_request: user_id={user_id}, text={message.text}, state={current_state}")
    if current_state != AdminStates.delete_request.state:
        logger.warning(f"Unexpected state for process_delete_request: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if message.text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} canceled request deletion")
        return
    unique_id = message.text.strip()
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute("SELECT channel_message_id, user_id FROM requests WHERE unique_id = ?", (unique_id,)) as cursor:
                request = await cursor.fetchone()
            if not request:
                await message.answer(f"Сўров {unique_id} топилмади!", reply_markup=get_main_menu(ADMIN_ROLE))
                await conn.execute("ROLLBACK")
                await state.clear()
                return
            if request[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=request[0])
                    logger.debug(f"Deleted channel message {request[0]} for request {unique_id}")
                except Exception as e:
                    logger.warning(f"Failed to delete channel message {request[0]} for request {unique_id}: {e}", exc_info=True)
            await conn.execute("UPDATE requests SET status = 'deleted' WHERE unique_id = ?", (unique_id,))
            await conn.commit()
            if request[1]:
                await message.bot.send_message(request[1], f"Сизнинг сўровингиз {unique_id} админ томонидан ўчирилди.")
        await message.answer(
            f"Сўров {unique_id} ўчирилди!",
            reply_markup=get_main_menu(ADMIN_ROLE)
        )
        await state.clear()
        logger.info(f"Admin {user_id} deleted request {unique_id}")
    except aiosqlite.Error as e:
        logger.error(f"Error deleting request {unique_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка удаления запроса {unique_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровни ўчиришда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
    except Exception as e:
        logger.error(f"Unexpected error deleting request {unique_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка удаления запроса {unique_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()

@admin_only
async def process_archive_request(message: types.Message, state: FSMContext):
    """Архивирует запрос по unique_id."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"process_archive_request: user_id={user_id}, text={message.text}, state={current_state}")
    if current_state != AdminStates.archive_request.state:
        logger.warning(f"Unexpected state for process_archive_request: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if message.text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} canceled request archiving")
        return
    unique_id = message.text.strip()
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute("SELECT unique_id, user_id FROM requests WHERE unique_id = ? AND status = 'active'", (unique_id,)) as cursor:
                request = await cursor.fetchone()
            if not request:
                await message.answer(f"Актив сўров {unique_id} топилмади!", reply_markup=get_main_menu(ADMIN_ROLE))
                await conn.execute("ROLLBACK")
                await state.clear()
                return
            archived_at = format_uz_datetime(datetime.now(pytz.UTC))
            await conn.execute("UPDATE requests SET status = 'archived', archived_at = ? WHERE unique_id = ?",
                               (archived_at, unique_id))
            await conn.commit()
            if request[1]:
                await message.bot.send_message(request[1], f"Сизнинг сўровингиз {unique_id} админ томонидан архивга ўтказилди.")
        await message.answer(
            f"Сўров {unique_id} архивга ўтказилди!",
            reply_markup=get_main_menu(ADMIN_ROLE)
        )
        await state.clear()
        logger.info(f"Admin {user_id} archived request {unique_id}")
    except aiosqlite.Error as e:
        logger.error(f"Error archiving request {unique_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка архивирования запроса {unique_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровни архивга ўтказишда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
    except Exception as e:
        logger.error(f"Unexpected error archiving request {unique_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка архивирования запроса {unique_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()

# Subscription Management
@admin_only
async def process_subscription_menu(message: types.Message, state: FSMContext):
    """Обрабатывает меню управления подписками."""
    user_id = message.from_user.id
    text = message.text
    current_state = await state.get_state()
    logger.debug(f"process_subscription_menu: user_id={user_id}, text={text}, state={current_state}")
    if current_state != AdminStates.subscription_menu.state:
        logger.warning(f"Unexpected state for process_subscription_menu: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} returned to main menu from subscription menu")
        return
    elif text == "Обуналар рўйхати":
        await subscription_list_command(message, state)
    elif text == "30 кунлик обуна бериш":
        await message.answer("30 кунлик обуна бериш учун user_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(AdminStates.subscribe_30_days)
        logger.info(f"Admin {user_id} started 30-day subscription process")
    elif text == "Обунани бекор қилиш":
        await message.answer("Обунани бекор қилиш учун user_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(AdminStates.cancel_subscription)
        logger.info(f"Admin {user_id} started subscription cancellation process")
    else:
        await message.answer("Илтимос, менюдан танланг:",
                             reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш"], columns=2, with_back=True))
        logger.warning(f"Invalid option in process_subscription_menu: user_id={user_id}, text={text}")

@admin_only
async def subscription_list_command(message: types.Message, state: FSMContext):
    """Отображает список активных подписок."""
    user_id = message.from_user.id
    try:
        now = format_uz_datetime(datetime.now(pytz.UTC))
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("""
                SELECT p.user_id, p.bot_expires, u.phone_number, u.role, p.trial_used 
                FROM payments p
                JOIN users u ON p.user_id = u.id
                WHERE p.bot_expires > ?
            """, (now,)) as cursor:
                subscriptions = await cursor.fetchall()
        if not subscriptions:
            await message.answer(
                "Актив обуналар йўқ.",
                reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш"], columns=2, with_back=True)
            )
            logger.info(f"Admin {user_id} found no active subscriptions")
            return
        response = "Актив обуналар рўйхати:\n\n"
        for sub in subscriptions:
            user_id, bot_expires, phone, role, trial_used = sub
            expires_at = parse_uz_datetime(bot_expires).strftime("%d.%m.%Y %H:%M")
            sub_type = "Тестовая" if trial_used else "Тўловли"
            response += f"ID: {user_id}, Телефон: {phone}, Рол: {role}\nТип: {sub_type}\nОбуна тугаши: {expires_at}\n\n"
        await message.answer(
            response,
            reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш"], columns=2, with_back=True)
        )
        logger.info(f"Admin {user_id} listed subscriptions")
    except aiosqlite.Error as e:
        logger.error(f"Error listing subscriptions for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка при получении списка подписок для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Обуналарни юклашда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
    except Exception as e:
        logger.error(f"Unexpected error listing subscriptions for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка при получении списка подписок для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))

@admin_only
async def process_subscribe_30_days(message: types.Message, state: FSMContext):
    """Предоставляет 30-дневную подписку пользователю."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"process_subscribe_30_days: user_id={user_id}, text={message.text}, state={current_state}")
    if current_state != AdminStates.subscribe_30_days.state:
        logger.warning(f"Unexpected state for process_subscribe_30_days: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if message.text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} canceled 30-day subscription process")
        return
    try:
        target_user_id = int(message.text.strip())
        expires_at = datetime.now(pytz.UTC) + timedelta(days=30)
        expires_at_str = format_uz_datetime(expires_at)
        if await manage_subscription(message, target_user_id, expires_at_str):
            await message.answer(
                f"{target_user_id} учун 30 кунлик обуна {expires_at.strftime('%d.%m.%Y')} гача берилди!",
                reply_markup=get_main_menu(ADMIN_ROLE)
            )
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT role FROM users WHERE id = ?", (target_user_id,)) as cursor:
                    result = await cursor.fetchone()
                    role = result[0] if result else None
            if role:
                await send_subscription_message(message.bot, target_user_id, role)
            await state.clear()
            logger.info(f"Admin {user_id} granted 30-day subscription to {target_user_id}")
    except ValueError:
        await message.answer("Тўғри user_id киритинг (фақат рақамлар)!",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        logger.warning(f"Invalid user ID input by admin {user_id}: {message.text}")
    except Exception as e:
        logger.error(f"Unexpected error granting subscription for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка при выдаче подписки для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()

@admin_only
async def process_cancel_subscription(message: types.Message, state: FSMContext):
    """Отменяет подписку пользователя."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"process_cancel_subscription: user_id={user_id}, text={message.text}, state={current_state}")
    if current_state != AdminStates.cancel_subscription.state:
        logger.warning(f"Unexpected state for process_cancel_subscription: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if message.text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} canceled subscription cancellation process")
        return
    try:
        target_user_id = int(message.text.strip())
        if await manage_subscription(message, target_user_id, None):
            await message.answer(
                f"{target_user_id} учун обуна бекор қилинди!",
                reply_markup=get_main_menu(ADMIN_ROLE)
            )
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute("SELECT role FROM users WHERE id = ?", (target_user_id,)) as cursor:
                    result = await cursor.fetchone()
                    role = result[0] if result else None
            if role:
                await send_subscription_message(message.bot, target_user_id, role)
            await state.clear()
            logger.info(f"Admin {user_id} canceled subscription for {target_user_id}")
    except ValueError:
        await message.answer("Тўғри user_id киритинг (фақат рақамлар)!",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        logger.warning(f"Invalid user ID input by admin {user_id}: {message.text}")
    except Exception as e:
        logger.error(f"Unexpected error canceling subscription for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка при отмене подписки для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()

async def manage_subscription(message: types.Message, user_id: int, bot_expires: str | None) -> bool:
    """Управляет подпиской пользователя."""
    admin_id = message.from_user.id
    if user_id == admin_id:
        await message.answer("Сиз ўзингизга обуна бера/бекор қила олмайсиз!", reply_markup=get_main_menu(ADMIN_ROLE))
        return False
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute("BEGIN TRANSACTION")
            async with conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)) as cursor:
                if not await cursor.fetchone():
                    await message.answer(f"Фойдаланувчи с ID {user_id} топилмади!", reply_markup=get_main_menu(ADMIN_ROLE))
                    await conn.execute("ROLLBACK")
                    return False
            if bot_expires is None:
                await conn.execute("DELETE FROM payments WHERE user_id = ?", (user_id,))
            else:
                await conn.execute("INSERT OR REPLACE INTO payments (user_id, bot_expires) VALUES (?, ?)",
                                   (user_id, bot_expires))
            await conn.commit()
            logger.debug(f"Subscription managed for user_id={user_id}, bot_expires={bot_expires}")
            return True
    except aiosqlite.Error as e:
        logger.error(f"Error managing subscription for user_id={user_id} by admin {admin_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка управления подпиской для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Обунани бошқаришда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        return False
    except Exception as e:
        logger.error(f"Unexpected error managing subscription for user_id={user_id} by admin {admin_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка управления подпиской для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        return False

# Archives Management
@admin_only
async def process_archives_menu(message: types.Message, state: FSMContext):
    """Обрабатывает меню управления архивами."""
    user_id = message.from_user.id
    text = message.text
    current_state = await state.get_state()
    logger.debug(f"process_archives_menu: user_id={user_id}, text={text}, state={current_state}")
    if current_state != AdminStates.archives_menu.state:
        logger.warning(f"Unexpected state for process_archives_menu: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} returned to main menu from archives menu")
        return
    elif text == "Архивлар рўйхати":
        await list_archives_command(message, state)
    elif text == "Архивларни ўчириш":
        await message.answer("Ўчириш учун архив unique_id ни киритинг (эълон ёки сўров):",
                             reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(AdminStates.delete_archive)
        logger.info(f"Admin {user_id} started archive deletion process")
    else:
        await message.answer("Илтимос, менюдан танланг:",
                             reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш"], columns=2, with_back=True))
        logger.warning(f"Invalid option in process_archives_menu: user_id={user_id}, text={text}")

@admin_only
async def list_archives_command(message: types.Message, state: FSMContext):
    """Отображает список архивов."""
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
            await message.answer("Архивлар йўқ.", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
            logger.info(f"Admin {user_id} found no archives")
            return
        response = "Архивлар рўйхати:\n\n"
        for unique_id, category, sort, user_id, archive_type in archives:
            response += f"Тип: {archive_type}, ID: {unique_id}, Тур: {category}, Нав: {sort}, User ID: {user_id}\n"
        await message.answer(
            response,
            reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш"], columns=2, with_back=True)
        )
        logger.info(f"Admin {user_id} listed archives")
    except aiosqlite.Error as e:
        logger.error(f"Error listing archives for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка при получении списка архивов для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Архивларни юклашда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
    except Exception as e:
        logger.error(f"Unexpected error listing archives for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка при получении списка архивов для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))

@admin_only
async def process_delete_archive(message: types.Message, state: FSMContext):
    """Удаляет архив (продукт или запрос) по unique_id."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"process_delete_archive: user_id={user_id}, text={message.text}, state={current_state}")
    if current_state != AdminStates.delete_archive.state:
        logger.warning(f"Unexpected state for process_delete_archive: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        return
    if message.text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} canceled archive deletion")
        return
    unique_id = message.text.strip()
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
                        logger.debug(f"Deleted channel message {product[0]} for archived product {unique_id}")
                    except Exception as e:
                        logger.warning(f"Failed to delete channel message {product[0]} for archived product {unique_id}: {e}", exc_info=True)
                await conn.execute("DELETE FROM products WHERE unique_id = ? AND status = 'archived'", (unique_id,))
                await conn.commit()
                if product[1]:
                    await message.bot.send_message(product[1], f"Сизнинг архивланган эълонингиз {unique_id} админ томонидан ўчирилди.")
                await message.answer(
                    f"Архивланган эълон {unique_id} ўчирилди!",
                    reply_markup=get_main_menu(ADMIN_ROLE)
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
                            logger.debug(f"Deleted channel message {request[0]} for archived request {unique_id}")
                        except Exception as e:
                            logger.warning(f"Failed to delete channel message {request[0]} for archived request {unique_id}: {e}", exc_info=True)
                    await conn.execute("DELETE FROM requests WHERE unique_id = ? AND status = 'archived'", (unique_id,))
                    await conn.commit()
                    if request[1]:
                        await message.bot.send_message(request[1], f"Сизнинг архивланган сўровингиз {unique_id} админ томонидан ўчирилди.")
                    await message.answer(
                        f"Архивланган сўров {unique_id} ўчирилди!",
                        reply_markup=get_main_menu(ADMIN_ROLE)
                    )
                else:
                    await message.answer(f"Архивда {unique_id} топилмади!",
                                         reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
                    await conn.execute("ROLLBACK")
                    return
        await state.clear()
        logger.info(f"Admin {user_id} deleted archive {unique_id}")
    except aiosqlite.Error as e:
        logger.error(f"Error deleting archive {unique_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка удаления архива {unique_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Архивни ўчиришда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
    except Exception as e:
        logger.error(f"Unexpected error deleting archive {unique_id} by admin {user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка удаления архива {unique_id} для admin_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()

# Statistics
@admin_only
async def statistics_command(message: types.Message, state: FSMContext):
    """Отображает статистику системы."""
    user_id = message.from_user.id
    try:
        now = format_uz_datetime(datetime.now(pytz.UTC))
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
            f"• Фаол: {active_subs}"
        )
        await message.answer(response, reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
        logger.info(f"Admin {user_id} viewed statistics")
    except aiosqlite.Error as e:
        logger.error(f"Error generating statistics for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка генерации статистики для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Статистикани юклашда хатолик.", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()
    except Exception as e:
        logger.error(f"Unexpected error generating statistics for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка генерации статистики для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(ADMIN_ROLE))
        await state.clear()

def register_handlers(dp: Dispatcher):
    """Регистрирует обработчики для админ-панели."""
    logger.info("Registering admin handlers")
    dp.message.register(process_main_menu, F.text.in_([
        "Фойдаланувчиларни бошқариш", "Эълонларни бошқариш", "Сўровларни бошқариш",
        "Обунани бошқариш", "Архивни бошқариш", "Статистика", "Менинг профилим", "Орқага"
    ]), AdminStates.main_menu)
    dp.message.register(process_users_menu, AdminStates.users_menu)
    dp.message.register(process_delete_user, AdminStates.delete_user)
    dp.message.register(process_products_menu, AdminStates.products_menu)
    dp.message.register(process_delete_product, AdminStates.delete_product)
    dp.message.register(process_archive_product, AdminStates.archive_product)
    dp.message.register(process_requests_menu, AdminStates.requests_menu)
    dp.message.register(process_delete_request, AdminStates.delete_request)
    dp.message.register(process_archive_request, AdminStates.archive_request)
    dp.message.register(process_subscription_menu, AdminStates.subscription_menu)
    dp.message.register(process_subscribe_30_days, AdminStates.subscribe_30_days)
    dp.message.register(process_cancel_subscription, AdminStates.cancel_subscription)
    dp.message.register(process_archives_menu, AdminStates.archives_menu)
    dp.message.register(process_delete_archive, AdminStates.delete_archive)
    logger.info("Admin handlers registered")