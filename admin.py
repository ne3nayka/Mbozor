import aiosqlite
import logging
from aiogram import types, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import DB_NAME, ADMIN_ROLE, CHANNEL_ID, ADMIN_IDS
from utils import check_role, make_keyboard, format_uz_datetime, parse_uz_datetime
from common import send_subscription_message
from datetime import datetime, timedelta
from functools import wraps
from profile import profile_menu

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
    @wraps(handler)
    async def wrapper(message: types.Message, state: FSMContext, *args, **kwargs):
        user_id = message.from_user.id
        allowed, role = await check_role(message)
        if not allowed or role != ADMIN_ROLE:
            logger.warning(f"Unauthorized admin access attempt by {user_id}, role={role}")
            await message.answer("Бу буйруқ фақат админлар учун!")
            return
        logger.debug(f"Admin {user_id} access granted, role={role}")
        return await handler(message, state, *args, **kwargs)
    return wrapper

@admin_only
async def admin_panel(message: types.Message, state: FSMContext):
    from main import get_admin_menu
    await state.set_state(AdminStates.main_menu)
    await message.answer("Админ панели:", reply_markup=get_admin_menu())
    logger.info(f"Admin {message.from_user.id} entered admin panel")

@admin_only
async def process_main_menu(message: types.Message, state: FSMContext):
    from main import get_main_menu, get_admin_menu
    user_id = message.from_user.id
    text = message.text

    if text == "Орқага":
        await state.clear()
        await message.answer("Асосий меню:", reply_markup=get_main_menu(ADMIN_ROLE))
        logger.info(f"Admin {user_id} returned to main menu")
        return
    elif text == "Менинг профилим":
        await profile_menu(message, state)
        return

    menu_options = {
        "Фойдаланувчиларни бошқариш": (AdminStates.users_menu, ["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"]),
        "Эълонларни бошқариш": (AdminStates.products_menu, ["Админ: Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни архивга ўтказиш", "Орқага"]),
        "Сўровларни бошқариш": (AdminStates.requests_menu, ["Админ: Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни архивга ўтказиш", "Орқага"]),
        "Обунани бошқариш": (AdminStates.subscription_menu, ["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"]),
        "Архивни бошқариш": (AdminStates.archives_menu, ["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"]),
        "Статистика": (None, None)
    }

    if text in menu_options:
        if text == "Статистика":
            await statistics_command(message, state)
        else:
            state_info, buttons = menu_options[text]
            await message.answer(f"{text}:", reply_markup=make_keyboard(buttons, columns=2))
            await state.set_state(state_info)
            logger.info(f"Admin {user_id} selected {text}")
    else:
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_admin_menu())

# User Management
@admin_only
async def process_users_menu(message: types.Message, state: FSMContext):
    from main import get_admin_menu
    text = message.text
    if text == "Орқага":
        await state.set_state(AdminStates.main_menu)
        await message.answer("Админ панели:", reply_markup=get_admin_menu())
        return
    elif text == "Фойдаланувчилар рўйхати":
        await list_users_command(message, state)
    elif text == "Фойдаланувчини ўчириш":
        await message.answer("Ўчириш учун фойдаланувчи Telegram ID ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AdminStates.delete_user)
    else:
        await message.answer("Илтимос, менюдан танланг:",
                             reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2))

@admin_only
async def list_users_command(message: types.Message, state: FSMContext):
    try:
        now = format_uz_datetime(datetime.now())
        async with aiosqlite.connect(DB_NAME) as conn:
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
            if role.lower() == 'admin':
                admins.append(user_info)
            elif role.lower() == 'seller':
                sellers.append(user_info)
            elif role.lower() == 'buyer':
                buyers.append(user_info)

        response_parts = ["Фойдаланувчилар руйхати:"]
        response_parts.append(f"\nАдминлар ({len(admins)}):")
        if admins:
            response_parts.extend(f"ID: {a[0]}, Телефон: {a[1]}, Уникал ID: {a[2]}" for a in admins)
        response_parts.append(f"\n\nСотувчилар ({len(sellers)}):")
        if sellers:
            response_parts.extend(f"ID: {s[0]}, Телефон: {s[1]}, Уникал ID: {s[2]}" for s in sellers)
        response_parts.append(f"\n\nХаридорлар ({len(buyers)}):")
        if buyers:
            response_parts.extend(f"ID: {b[0]}, Телефон: {b[1]}, Уникал ID: {b[2]}" for b in buyers)
        response_parts.append(f"\n\nАктив обуналар (всего: {active_sub_count}, тестовые: {trial_sub_count})")
        response_parts.append(f"Ўчирилган фойдаланувчилар (всего: {deleted_count})")

        await message.answer(
            "\n".join(response_parts),
            reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2)
        )
    except Exception as e:
        logger.error(f"Error listing users: {e}", exc_info=True)
        await message.answer("Рўйхатни юклашда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

@admin_only
async def process_delete_user(message: types.Message, state: FSMContext):
    if message.text == "Орқага":
        await state.set_state(AdminStates.users_menu)
        await message.answer("Фойдаланувчиларни бошқариш:",
                             reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2))
        return
    try:
        delete_user_id = int(message.text.strip())
        admin_id = message.from_user.id
        if delete_user_id == admin_id:
            await message.answer("Сиз ўзингизни ўчира олмайсиз!")
            return
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT id, phone_number, role FROM users WHERE id = ?", (delete_user_id,)) as cursor:
                user = await cursor.fetchone()
            if not user:
                await message.answer(f"Фойдаланувчи с ID {delete_user_id} топилмади!")
                return
            await conn.execute(
                "INSERT INTO deleted_users (user_id, phone_number, role, deleted_at) VALUES (?, ?, ?, datetime('now'))",
                (user[0], user[1], user[2])
            )
            await conn.execute("DELETE FROM users WHERE id = ?", (delete_user_id,))
            await conn.commit()
        await message.answer(f"Фойдаланувчи с ID {delete_user_id} ўчирилди!",
                             reply_markup=make_keyboard(["Фойдаланувчилар рўйхати", "Фойдаланувчини ўчириш", "Орқага"], columns=2))
        await state.set_state(AdminStates.users_menu)
    except ValueError:
        await message.answer("Тўғри Telegram ID киритинг (рақам)!",
                             reply_markup=make_keyboard(["Орқага"], columns=1))
    except Exception as e:
        logger.error(f"Error deleting user: {e}")
        await message.answer("Фойдаланувчини ўчиришда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

# Products Management
@admin_only
async def process_products_menu(message: types.Message, state: FSMContext):
    from main import get_admin_menu
    text = message.text
    if text == "Орқага":
        await state.set_state(AdminStates.main_menu)
        await message.answer("Админ панели:", reply_markup=get_admin_menu())
        return
    elif text == "Админ: Эълонлар рўйхати":
        await list_products_command(message, state)
    elif text == "Эълонни ўчириш":
        await message.answer("Ўчириш учун эълон unique_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AdminStates.delete_product)
    elif text == "Эълонни архивга ўтказиш":
        await message.answer("Архивга ўтказиш учун эълон unique_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AdminStates.archive_product)
    else:
        await message.answer("Илтимос, менюдан танланг:",
                             reply_markup=make_keyboard(["Админ: Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни архивга ўтказиш", "Орқага"], columns=2))

@admin_only
async def list_products_command(message: types.Message, state: FSMContext):
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT unique_id, user_id, category, sort, status, created_at FROM products") as cursor:
                products = await cursor.fetchall()
        if not products:
            await message.answer("Эълонлар йўқ.", reply_markup=make_keyboard(["Орқага"], columns=1))
            return
        response = "Эълонлар рўйхати:\n\n"
        for unique_id, user_id, category, sort, status, created_at in products:
            response += f"ID: {unique_id}, User ID: {user_id}, Категория: {category}, Сорт: {sort}, Статус: {status}, Создано: {created_at}\n"
        await message.answer(response, reply_markup=make_keyboard(["Админ: Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни архивга ўтказиш", "Орқага"], columns=2))
    except Exception as e:
        logger.error(f"Error listing products: {e}")
        await message.answer("Эълонларни юклашда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

@admin_only
async def process_delete_product(message: types.Message, state: FSMContext):
    if message.text == "Орқага":
        await state.set_state(AdminStates.products_menu)
        await message.answer("Эълонларни бошқариш:",
                             reply_markup=make_keyboard(["Админ: Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни архивга ўтказиш", "Орқага"], columns=2))
        return
    unique_id = message.text.strip()
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT channel_message_id, user_id FROM products WHERE unique_id = ?", (unique_id,)) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(f"Эълон {unique_id} топилмади!")
                return
            if product[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=product[0])
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {product[0]} из канала: {e}")
            await conn.execute("UPDATE products SET status = 'deleted' WHERE unique_id = ?", (unique_id,))
            await conn.commit()
            # Уведомляем пользователя
            if product[1]:
                await message.bot.send_message(product[1], f"Сизнинг эълонингиз {unique_id} админ томонидан ўчирилди.")
        await message.answer(f"Эълон {unique_id} ўчирилди!",
                             reply_markup=make_keyboard(["Админ: Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни архивга ўтказиш", "Орқага"], columns=2))
        await state.set_state(AdminStates.products_menu)
    except Exception as e:
        logger.error(f"Error deleting product {unique_id}: {e}")
        await message.answer("Эълонни ўчиришда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

@admin_only
async def process_archive_product(message: types.Message, state: FSMContext):
    if message.text == "Орқага":
        await state.set_state(AdminStates.products_menu)
        await message.answer("Эълонларни бошқариш:",
                             reply_markup=make_keyboard(["Админ: Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни архивга ўтказиш", "Орқага"], columns=2))
        return
    unique_id = message.text.strip()
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT unique_id, user_id FROM products WHERE unique_id = ? AND status = 'active'", (unique_id,)) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(f"Актив эълон {unique_id} топилмади!")
                return
            archived_at = format_uz_datetime(datetime.now())
            await conn.execute("UPDATE products SET status = 'archived', archived_at = ? WHERE unique_id = ?",
                               (archived_at, unique_id))
            await conn.commit()
            # Уведомляем пользователя
            if product[1]:
                await message.bot.send_message(product[1], f"Сизнинг эълонингиз {unique_id} админ томонидан архивга ўтказилди.")
        await message.answer(f"Эълон {unique_id} архивга ўтказилди!",
                             reply_markup=make_keyboard(["Админ: Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни архивга ўтказиш", "Орқага"], columns=2))
        await state.set_state(AdminStates.products_menu)
    except Exception as e:
        logger.error(f"Error archiving product {unique_id}: {e}")
        await message.answer("Эълонни архивга ўтказишда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

# Requests Management
@admin_only
async def process_requests_menu(message: types.Message, state: FSMContext):
    from main import get_admin_menu
    text = message.text
    if text == "Орқага":
        await state.set_state(AdminStates.main_menu)
        await message.answer("Админ панели:", reply_markup=get_admin_menu())
        return
    elif text == "Админ: Сўровлар рўйхати":
        await list_requests_command(message, state)
    elif text == "Сўровни ўчириш":
        await message.answer("Ўчириш учун сўров unique_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AdminStates.delete_request)
    elif text == "Сўровни архивга ўтказиш":
        await message.answer("Архивга ўтказиш учун сўров unique_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AdminStates.archive_request)
    else:
        await message.answer("Илтимос, менюдан танланг:",
                             reply_markup=make_keyboard(["Админ: Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни архивга ўтказиш", "Орқага"], columns=2))

@admin_only
async def list_requests_command(message: types.Message, state: FSMContext):
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT unique_id, user_id, category, sort, status FROM requests") as cursor:
                requests = await cursor.fetchall()
        if not requests:
            await message.answer("Сўровлар йўқ.", reply_markup=make_keyboard(["Орқага"], columns=1))
            return
        response = "Сўровлар рўйхати:\n\n"
        for unique_id, user_id, category, sort, status in requests:
            response += f"ID: {unique_id}, User ID: {user_id}, Категория: {category}, Сорт: {sort}, Статус: {status}\n"
        await message.answer(response,
                             reply_markup=make_keyboard(["Админ: Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни архивга ўтказиш", "Орқага"], columns=2))
    except Exception as e:
        logger.error(f"Error listing requests: {e}")
        await message.answer("Сўровларни юклашда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

@admin_only
async def process_delete_request(message: types.Message, state: FSMContext):
    if message.text == "Орқага":
        await state.set_state(AdminStates.requests_menu)
        await message.answer("Сўровларни бошқариш:",
                             reply_markup=make_keyboard(["Админ: Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни архивга ўтказиш", "Орқага"], columns=2))
        return
    unique_id = message.text.strip()
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT channel_message_id, user_id FROM requests WHERE unique_id = ?", (unique_id,)) as cursor:
                request = await cursor.fetchone()
            if not request:
                await message.answer(f"Сўров {unique_id} топилмади!")
                return
            if request[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=request[0])
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {request[0]} из канала: {e}")
            await conn.execute("UPDATE requests SET status = 'deleted' WHERE unique_id = ?", (unique_id,))
            await conn.commit()
            # Уведомляем пользователя
            if request[1]:
                await message.bot.send_message(request[1], f"Сизнинг сўровингиз {unique_id} админ томонидан ўчирилди.")
        await message.answer(f"Сўров {unique_id} ўчирилди!",
                             reply_markup=make_keyboard(["Админ: Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни архивга ўтказиш", "Орқага"], columns=2))
        await state.set_state(AdminStates.requests_menu)
    except Exception as e:
        logger.error(f"Error deleting request {unique_id}: {e}")
        await message.answer("Сўровни ўчиришда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

@admin_only
async def process_archive_request(message: types.Message, state: FSMContext):
    if message.text == "Орқага":
        await state.set_state(AdminStates.requests_menu)
        await message.answer("Сўровларни бошқариш:",
                             reply_markup=make_keyboard(["Админ: Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни архивга ўтказиш", "Орқага"], columns=2))
        return
    unique_id = message.text.strip()
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT unique_id, user_id FROM requests WHERE unique_id = ? AND status = 'active'", (unique_id,)) as cursor:
                request = await cursor.fetchone()
            if not request:
                await message.answer(f"Актив сўров {unique_id} топилмади!")
                return
            archived_at = format_uz_datetime(datetime.now())
            await conn.execute("UPDATE requests SET status = 'archived', archived_at = ? WHERE unique_id = ?",
                               (archived_at, unique_id))
            await conn.commit()
            # Уведомляем пользователя
            if request[1]:
                await message.bot.send_message(request[1], f"Сизнинг сўровингиз {unique_id} админ томонидан архивга ўтказилди.")
        await message.answer(f"Сўров {unique_id} архивга ўтказилди!",
                             reply_markup=make_keyboard(["Админ: Сўровлар рўйхати", "Сўровни ўчириш", "Сўровни архивга ўтказиш", "Орқага"], columns=2))
        await state.set_state(AdminStates.requests_menu)
    except Exception as e:
        logger.error(f"Error archiving request {unique_id}: {e}")
        await message.answer("Сўровни архивга ўтказишда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

# Subscription Management
@admin_only
async def process_subscription_menu(message: types.Message, state: FSMContext):
    from main import get_admin_menu
    text = message.text
    if text == "Орқага":
        await state.set_state(AdminStates.main_menu)
        await message.answer("Админ панели:", reply_markup=get_admin_menu())
        return
    elif text == "Обуналар рўйхати":
        await subscription_list_command(message, state)
    elif text == "30 кунлик обуна бериш":
        await message.answer("30 кунлик обуна бериш учун user_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AdminStates.subscribe_30_days)
    elif text == "Обунани бекор қилиш":
        await message.answer("Обунани бекор қилиш учун user_id ни киритинг:",
                             reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AdminStates.cancel_subscription)
    else:
        await message.answer("Илтимос, менюдан танланг:",
                             reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2))

@admin_only
async def subscription_list_command(message: types.Message, state: FSMContext):
    try:
        now = format_uz_datetime(datetime.now())
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("""
                SELECT p.user_id, p.bot_expires, u.phone_number, u.role, p.trial_used 
                FROM payments p
                JOIN users u ON p.user_id = u.id
                WHERE p.bot_expires > ?
            """, (now,)) as cursor:
                subscriptions = await cursor.fetchall()
        if not subscriptions:
            await message.answer("Актив обуналар йўқ.",
                                 reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2))
            return
        response = "Актив обуналар рўйхати:\n\n"
        for sub in subscriptions:
            user_id, bot_expires, phone, role, trial_used = sub
            expires_at = parse_uz_datetime(bot_expires).strftime("%d.%m.%Y %H:%M")
            sub_type = "Тестовая" if trial_used else "Тўловли"
            response += f"ID: {user_id}, Телефон: {phone}, Роль: {role}\nТип: {sub_type}\nОбуна тугаши: {expires_at}\n\n"
        await message.answer(response,
                             reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2))
    except Exception as e:
        logger.error(f"Error listing subscriptions: {e}")
        await message.answer("Обуналарни юклашда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

@admin_only
async def process_subscribe_30_days(message: types.Message, state: FSMContext):
    if message.text == "Орқага":
        await state.set_state(AdminStates.subscription_menu)
        await message.answer("Обунани бошқариш:",
                             reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2))
        return
    try:
        target_user_id = int(message.text.strip())
        expires_at = datetime.now() + timedelta(days=30)
        expires_at_str = format_uz_datetime(expires_at)
        if await manage_subscription(message, target_user_id, expires_at_str):
            await message.answer(
                f"{target_user_id} учун 30 кунлик обуна {expires_at.strftime('%d.%m.%Y')} гача берилди!",
                reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2)
            )
            _, role = await check_role(message, target_user_id)
            if role:
                # Исправленный вызов
                await send_subscription_message(message, target_user_id, role)
            await state.set_state(AdminStates.subscription_menu)
    except ValueError:
        await message.answer("Тўғри user_id киритинг (фақат рақамлар)!",
                             reply_markup=make_keyboard(["Орқага"], columns=1))

@admin_only
async def process_cancel_subscription(message: types.Message, state: FSMContext):
    if message.text == "Орқага":
        await state.set_state(AdminStates.subscription_menu)
        await message.answer("Обунани бошқариш:",
                             reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2))
        return
    try:
        target_user_id = int(message.text.strip())
        if await manage_subscription(message, target_user_id, None):
            await message.answer(
                f"{target_user_id} учун обуна бекор қилинди!",
                reply_markup=make_keyboard(["Обуналар рўйхати", "30 кунлик обуна бериш", "Обунани бекор қилиш", "Орқага"], columns=2)
            )
            _, role = await check_role(message, target_user_id)
            if role:
                await send_subscription_message(message.bot, target_user_id, role)
            await state.set_state(AdminStates.subscription_menu)
    except ValueError:
        await message.answer("Тўғри user_id киритинг!",
                             reply_markup=make_keyboard(["Орқага"], columns=1))

async def manage_subscription(message: types.Message, user_id: int, bot_expires: str | None) -> bool:
    admin_id = message.from_user.id
    if user_id == admin_id:
        await message.answer("Сиз ўзингизга обуна бера/бекор қила олмайсиз!")
        return False
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)) as cursor:
                if not await cursor.fetchone():
                    await message.answer(f"Фойдаланувчи с ID {user_id} топилмади!")
                    return False
            if bot_expires is None:
                await conn.execute("DELETE FROM payments WHERE user_id = ?", (user_id,))
            else:
                await conn.execute("INSERT OR REPLACE INTO payments (user_id, bot_expires) VALUES (?, ?)",
                                   (user_id, bot_expires))
            await conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error managing subscription for {user_id}: {e}")
        await message.answer("Обунани бошқаришда хатолик.")
        return False

# Archives Management
@admin_only
async def process_archives_menu(message: types.Message, state: FSMContext):
    from main import get_admin_menu
    text = message.text
    if text == "Орқага":
        await state.set_state(AdminStates.main_menu)
        await message.answer("Админ панели:", reply_markup=get_admin_menu())
        return
    elif text == "Архивлар рўйхати":
        await list_archives_command(message, state)
    elif text == "Архивларни ўчириш":
        await message.answer("Ўчириш учун архив unique_id ни киритинг (эълон ёки сўров):",
                             reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AdminStates.delete_archive)
    else:
        await message.answer("Илтимос, менюдан танланг:",
                             reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2))

@admin_only
async def list_archives_command(message: types.Message, state: FSMContext):
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT unique_id, category, sort, user_id, 'Эълон' as type FROM products WHERE status = 'archived' "
                                    "UNION ALL "
                                    "SELECT unique_id, category, sort, user_id, 'Сўров' as type FROM requests WHERE status = 'archived'") as cursor:
                archives = await cursor.fetchall()
        if not archives:
            await message.answer("Архивлар йўқ.", reply_markup=make_keyboard(["Орқага"], columns=1))
            return
        response = "Архивлар рўйхати:\n\n"
        for unique_id, category, sort, user_id, archive_type in archives:
            response += f"Тип: {archive_type}, ID: {unique_id}, Категория: {category}, Сорт: {sort}, User ID: {user_id}\n"
        await message.answer(response,
                             reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2))
    except Exception as e:
        logger.error(f"Error listing archives: {e}")
        await message.answer("Архивларни юклашда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

@admin_only
async def process_delete_archive(message: types.Message, state: FSMContext):
    if message.text == "Орқага":
        await state.set_state(AdminStates.archives_menu)
        await message.answer("Архивни бошқариш:",
                             reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2))
        return
    unique_id = message.text.strip()
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT channel_message_id, user_id FROM products WHERE unique_id = ? AND status = 'archived'", (unique_id,)) as cursor:
                product = await cursor.fetchone()
            if product:
                if product[0]:
                    try:
                        await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=product[0])
                    except Exception as e:
                        logger.warning(f"Не удалось удалить сообщение {product[0]} из канала: {e}")
                await conn.execute("DELETE FROM products WHERE unique_id = ? AND status = 'archived'", (unique_id,))
                await conn.commit()
                if product[1]:
                    await message.bot.send_message(product[1], f"Сизнинг архивланган эълонингиз {unique_id} админ томонидан ўчирилди.")
                await message.answer(f"Архивланган эълон {unique_id} ўчирилди!",
                                     reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2))
            else:
                async with conn.execute("SELECT channel_message_id, user_id FROM requests WHERE unique_id = ? AND status = 'archived'", (unique_id,)) as cursor:
                    request = await cursor.fetchone()
                if request:
                    if request[0]:
                        try:
                            await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=request[0])
                        except Exception as e:
                            logger.warning(f"Не удалось удалить сообщение {request[0]} из канала: {e}")
                    await conn.execute("DELETE FROM requests WHERE unique_id = ? AND status = 'archived'", (unique_id,))
                    await conn.commit()
                    if request[1]:
                        await message.bot.send_message(request[1], f"Сизнинг архивланган сўровингиз {unique_id} админ томонидан ўчирилди.")
                    await message.answer(f"Архивланган сўров {unique_id} ўчирилди!",
                                         reply_markup=make_keyboard(["Архивлар рўйхати", "Архивларни ўчириш", "Орқага"], columns=2))
                else:
                    await message.answer(f"Архивда {unique_id} топилмади!",
                                         reply_markup=make_keyboard(["Орқага"], columns=1))
                    return
        await state.set_state(AdminStates.archives_menu)
    except Exception as e:
        logger.error(f"Error deleting archive {unique_id}: {e}")
        await message.answer("Архивни ўчиришда хатолик.", reply_markup=make_keyboard(["Орқага"], columns=1))

# Statistics
@admin_only
async def statistics_command(message: types.Message, state: FSMContext):
    from main import get_admin_menu
    try:
        now = format_uz_datetime(datetime.now())
        async with aiosqlite.connect(DB_NAME) as conn:
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

        response = (
            "📊 Статистика:\n\n"
            f"👥 Фойдаланувчилар:\n"
            f"• Админлар: {role_counts.get('admin', 0)}\n"
            f"• Сотувчилар: {role_counts.get('seller', 0)}\n"
            f"• Харидорлар: {role_counts.get('buyer', 0)}\n"
            f"• Ўчирилганлар: {deleted_users}\n\n"
            f"📦 Эълонлар:\n"
            f"• Актив: {product_stats.get('active', 0)}\n"
            f"• Архив: {product_stats.get('archived', 0)}\n"
            f"• Ўчирилган: {product_stats.get('deleted', 0)}\n\n"
            f"📝 Сўровлар:\n"
            f"• Актив: {request_stats.get('active', 0)}\n"
            f"• Архив: {request_stats.get('archived', 0)}\n"
            f"• Ўчирилган: {request_stats.get('deleted', 0)}\n\n"
            f"🔔 Обуналар:\n"
            f"• Актив: {active_subs}"
        )
        await message.answer(response, reply_markup=get_admin_menu())
        await state.set_state(AdminStates.main_menu)
    except Exception as e:
        logger.error(f"Error generating statistics: {e}")
        await message.answer("Статистикани юклашда хатолик.", reply_markup=get_admin_menu())

def register_handlers(dp: Dispatcher):
    dp.message.register(admin_panel, F.text == "Админ панели")
    dp.message.register(process_main_menu, AdminStates.main_menu)
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
