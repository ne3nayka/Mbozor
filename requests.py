import aiosqlite
import logging
from aiogram import types, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from config import DB_NAME, BUYER_ROLE, CATEGORIES, MAX_SORT_LENGTH, MAX_VOLUME_TON, CHANNEL_ID, ADMIN_IDS, SELLER_ROLE
from utils import check_role, make_keyboard, validate_number, format_uz_datetime, parse_uz_datetime, has_pending_items, get_main_menu, get_ads_menu, get_requests_menu
from database import generate_item_id
from regions import get_all_regions
from datetime import datetime, timedelta
from functools import wraps
from html import escape

logger = logging.getLogger(__name__)

class RequestsMenu(StatesGroup):
    menu = State()

class SendRequest(StatesGroup):
    category = State()
    sort = State()
    region = State()
    volume_ton = State()
    price = State()

class AddRequest(StatesGroup):
    category = State()
    sort = State()
    region = State()
    volume_ton = State()
    price = State()

class DeleteRequest(StatesGroup):
    delete_request = State()

class CloseRequest(StatesGroup):
    select_request = State()
    awaiting_final_price = State()

def buyer_only(handler):
    @wraps(handler)
    async def wrapper(message: types.Message, state: FSMContext, *args, **kwargs):
        user_id = message.from_user.id
        logger.debug(f"buyer_only: Проверка для {user_id}, текст: '{message.text}'")
        if await has_pending_items(user_id):
            await notify_next_pending_item(message, state)
            return
        allowed, role = await check_role(message, allow_unregistered=False)
        if not allowed or role != BUYER_ROLE:
            await message.answer("Бу буйруқ фақат харидорлар учун!", reply_markup=get_main_menu(BUYER_ROLE))
            await state.clear()
            logger.warning(f"Пользователь {user_id} не прошёл проверку роли: {role}")
            return
        return await handler(message, state, *args, **kwargs)
    return wrapper

async def safe_send_message(bot, chat_id, text, parse_mode=None):
    try:
        message = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        logger.debug(f"Сообщение отправлено в канал {chat_id}: {message.message_id}")
        return message
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        logger.error(f"Не удалось отправить сообщение в канал {chat_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения в канал {chat_id}: {e}", exc_info=True)
        return None

async def safe_delete_message(bot, chat_id, message_id):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.debug(f"Сообщение {message_id} удалено из канала {chat_id}")
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        logger.warning(f"Не удалось удалить сообщение {message_id} из канала {chat_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения {message_id}: {e}", exc_info=True)

async def handle_back(message: types.Message, state: FSMContext, back_state: State | None, back_text: str, back_keyboard: types.ReplyKeyboardMarkup):
    await message.answer(back_text, reply_markup=back_keyboard)
    if back_state:
        await state.set_state(back_state)
    else:
        await state.clear()
    logger.info(f"Пользователь {message.from_user.id} вернулся назад из {await state.get_state()}")

@buyer_only
async def send_request_start(message: types.Message, state: FSMContext):
    await message.answer("Маҳсулот турини танланг:", reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
    await state.set_state(SendRequest.category)
    logger.info(f"Пользователь {message.from_user.id} начал создание запроса через 'Сўров юбормоқ'")

@buyer_only
async def requests_menu(message: types.Message, state: FSMContext):
    await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu(BUYER_ROLE))
    await state.set_state(RequestsMenu.menu)
    logger.info(f"Пользователь {message.from_user.id} вошёл в меню сўровларим")

@buyer_only
async def process_requests_menu(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    options = {
        "Орқага": (get_main_menu(BUYER_ROLE), None, "вернулся в главное меню"),
        "Сўровлар рўйхати": (get_requests_menu(BUYER_ROLE), requests_list, "просмотрел список сўровларим"),
        "Сўров қўшиш": (None, add_request_start, "начал добавление сўров"),
        "Сўровни ўчириш": (None, requests_delete_start, "начал удаление сўров"),
        "Сўровни ёпиш": (None, close_request_start, "начал закрытие сўров")
    }
    option = options.get(message.text)
    if option:
        if option[1]:
            await option[1](message, state)
        if option[0]:
            await message.answer("Менинг сўровларим:" if option[2].startswith("просмотрел") else "Асосий меню:", reply_markup=option[0])
        if not option[1] or option[2].startswith("просмотрел"):
            await state.set_state(RequestsMenu.menu)
        logger.info(f"Пользователь {user_id} {option[2]}")
    else:
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_requests_menu(BUYER_ROLE))
        logger.warning(f"Неизвестная опция от {user_id}: '{message.text}'")

@buyer_only
async def add_request_start(message: types.Message, state: FSMContext):
    await message.answer("Маҳсулот турини танланг:", reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
    await state.set_state(AddRequest.category)
    logger.debug(f"Пользователь {message.from_user.id} начал добавление запроса из 'Менинг сўровларим'")

async def process_category_common(message: types.Message, state: FSMContext, is_add_request: bool):
    user_id = message.from_user.id
    back_menu = get_requests_menu(BUYER_ROLE) if is_add_request else get_main_menu(BUYER_ROLE)
    back_state = RequestsMenu.menu if is_add_request else None
    if message.text == "Орқага":
        await handle_back(message, state, back_state, "Менинг сўровларим:" if is_add_request else "Асосий меню:", back_menu)
        return
    category = message.text.strip()
    if category not in CATEGORIES:
        await message.answer("Нотўғри категория! Рўйхатдан танланг:", reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
        logger.warning(f"Неверная категория от {user_id}: {category}")
        return
    await state.update_data(category=category)
    await message.answer("Маҳсулот сортни киритинг:", reply_markup=make_keyboard(["Орқага"], columns=1))
    await state.set_state(AddRequest.sort if is_add_request else SendRequest.sort)

@buyer_only
async def process_category(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    await process_category_common(message, state, current_state.startswith("AddRequest"))

async def process_sort_common(message: types.Message, state: FSMContext, is_add_request: bool):
    user_id = message.from_user.id
    back_menu = get_requests_menu(BUYER_ROLE) if is_add_request else get_main_menu(BUYER_ROLE)
    back_state = AddRequest.category if is_add_request else SendRequest.category
    if message.text == "Орқага":
        await handle_back(message, state, back_state, "Маҳсулот турини танланг:", make_keyboard(CATEGORIES, columns=2, with_back=True))
        return
    sort = escape(message.text.strip())[:MAX_SORT_LENGTH]
    if not sort:
        await message.answer("Сорт бўш бўлиши мумкин эмас!", reply_markup=make_keyboard(["Орқага"], columns=1))
        logger.warning(f"Пустой сорт от {user_id}")
        return
    if len(sort) > MAX_SORT_LENGTH:
        await message.answer(f"Сорт {MAX_SORT_LENGTH} белгидан узун бўлмаслиги керак. Қайта киритинг:")
        logger.warning(f"Сорт слишком длинный от {user_id}: {len(sort)} символов")
        return
    await state.update_data(sort=sort)
    await message.answer("Вилоятни танланг:", reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True))
    await state.set_state(AddRequest.region if is_add_request else SendRequest.region)

@buyer_only
async def process_sort(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    await process_sort_common(message, state, current_state.startswith("AddRequest"))

async def process_region_common(message: types.Message, state: FSMContext, is_add_request: bool):
    user_id = message.from_user.id
    back_state = AddRequest.sort if is_add_request else SendRequest.sort
    if message.text == "Орқага":
        await handle_back(message, state, back_state, "Маҳсулот сортни киритинг:", make_keyboard(["Орқага"], columns=1))
        return
    region = message.text.strip()
    if region not in get_all_regions():
        await message.answer("Нотўғри область! Рўйхатдан танланг:", reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True))
        logger.warning(f"Неверный регион от {user_id}: {region}")
        return
    await state.update_data(region=region)
    await message.answer("Маҳсулот ҳажмни киритинг (тоннада):", reply_markup=make_keyboard(["Орқага"], columns=1))
    await state.set_state(AddRequest.volume_ton if is_add_request else SendRequest.volume_ton)

@buyer_only
async def process_region(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    await process_region_common(message, state, current_state.startswith("AddRequest"))

async def process_volume_ton_common(message: types.Message, state: FSMContext, is_add_request: bool):
    user_id = message.from_user.id
    back_state = AddRequest.region if is_add_request else SendRequest.region
    if message.text == "Орқага":
        await handle_back(message, state, back_state, "Вилоятни танланг:", make_keyboard(get_all_regions(), columns=2, with_back=True))
        return
    volume_ton = validate_number(message.text, MAX_VOLUME_TON, allow_float=True)
    if not volume_ton:
        await message.answer(f"Ҳажм 0.01 тоннадан кам бўлмаслиги ва {MAX_VOLUME_TON} тоннадан ошмаслиги керак:")
        logger.warning(f"Неверный объём от {user_id}: {message.text}")
        return
    await state.update_data(volume_ton=volume_ton)
    await message.answer("Нархни киритинг (сўмда):", reply_markup=make_keyboard(["Орқага"], columns=1))
    await state.set_state(AddRequest.price if is_add_request else SendRequest.price)

@buyer_only
async def process_volume_ton(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    await process_volume_ton_common(message, state, current_state.startswith("AddRequest"))

async def process_price_common(message: types.Message, state: FSMContext, is_add_request: bool):
    user_id = message.from_user.id
    back_state = AddRequest.volume_ton if is_add_request else SendRequest.volume_ton
    finish_menu = get_requests_menu(BUYER_ROLE) if is_add_request else get_main_menu(BUYER_ROLE)
    finish_state = RequestsMenu.menu if is_add_request else None
    if message.text == "Орқага":
        await handle_back(message, state, back_state, "Маҳсулот ҳажмни киритинг (тоннада):", make_keyboard(["Орқага"], columns=1))
        return
    price = validate_number(message.text, 1_000_000_000)
    if not price:
        await message.answer("Нарх мусбат рақам бўлиши ва 1 миллиард сумдан ошмаслиги керак:")
        logger.warning(f"Неверная цена от {user_id}: {message.text}")
        return
    data = await state.get_data()
    item_id = await generate_item_id("requests", "S")
    created_at = format_uz_datetime(datetime.now())
    if not created_at:
        logger.error(f"Ошибка форматирования даты для сўрова {item_id}")
        await message.answer("Хатолик: ваqt форматини олиш мумкин эмас.", reply_markup=finish_menu)
        await state.clear()
        return
    info = (
        f"Сўров {item_id}\n"
        f"Категория: {data['category']}\n"
        f"Сорт: {data['sort']}\n"
        f"Вилоят: {data['region']}\n"
        f"Ҳажм: {data['volume_ton']} тонна\n"
        f"Нарх: {price} сум"
    )
    async with aiosqlite.connect(DB_NAME) as conn:
        await conn.execute("BEGIN TRANSACTION")
        try:
            await conn.execute(
                "INSERT INTO requests (unique_id, user_id, category, region, sort, volume_ton, price, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (item_id, user_id, data["category"], data["region"], data["sort"], data["volume_ton"], price, created_at)
            )
            channel_msg = await safe_send_message(message.bot, CHANNEL_ID, info, parse_mode="HTML")
            if not channel_msg:
                raise Exception(f"Failed to send сўров {item_id} to channel")
            await conn.execute("UPDATE requests SET channel_message_id = ? WHERE unique_id = ?",
                              (channel_msg.message_id, item_id))
            await conn.commit()
        except Exception as e:
            await conn.execute("ROLLBACK")
            logger.error(f"Ошибка базы данных при добавлении сўрова {item_id}: {e}", exc_info=True)
            for admin_id in ADMIN_IDS:
                await message.bot.send_message(admin_id, f"Сўров {item_id} канлага юборилмади или базада хатолик: {e}")
            await message.answer("Сўров каналга юборилмади. Админ билан боғланинг.", reply_markup=finish_menu)
            await state.clear()
            return
    await message.answer(
        f"Сизнинг сўровингиз юборилди. Сўров рақами {item_id}. Танишиш учун Сўровлар доскаси ёки <a href=\"https://t.me/+6WXzyGqqotgzODM6\">Каналга</a> ўтинг.",
        reply_markup=finish_menu,
        parse_mode="HTML"
    )
    await state.clear() if not finish_state else await state.set_state(finish_state)
    logger.info(f"Сўров {item_id} успешно добавлен пользователем {user_id}")

@buyer_only
async def process_price(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    await process_price_common(message, state, current_state.startswith("AddRequest"))

@buyer_only
async def requests_list(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT unique_id, category, region, sort, volume_ton, price, created_at "
                "FROM requests WHERE user_id = ? AND status = 'active'", (user_id,)
            ) as cursor:
                requests = await cursor.fetchall()
        now = datetime.now(pytz.UTC)
        response = ["Сизнинг актив сўровларингиз:"]
        for unique_id, category, region, sort, volume_ton, price, created_at in requests:
            created_at_dt = parse_uz_datetime(created_at)
            if not created_at_dt:
                logger.error(f"Ошибка парсинга даты для сўрова {unique_id}: {created_at}")
                response.append(f"Сўров {unique_id}: Ошибка даты, обратитесь к администратору")
                continue
            expiration = created_at_dt + timedelta(hours=24)
            status = "Фаол" if now < expiration else "Муддати тугаган"
            response.append(
                f"Сўров {unique_id}\n"
                f"Категория: {category}\n"
                f"Сорт: {sort}\n"
                f"Вилоят: {region}\n"
                f"Ҳажм: {volume_ton} тонна\n"
                f"Нарх: {price} сум\n"
                f"Ҳолат: {status} ({format_uz_datetime(expiration)} гача)\n"
            )
        await message.answer("\n".join(response) or "Сизда сўровлар йўқ.", reply_markup=get_requests_menu(BUYER_ROLE))
        logger.info(f"Пользователь {user_id} просмотрел список своих запросов")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка загрузки списка сўровларим для {user_id}: {e}", exc_info=True)
        await message.answer("Сўровлар рўйхатини юклашда хатолик юз берди!", reply_markup=get_requests_menu(BUYER_ROLE))

@buyer_only
async def requests_delete_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT unique_id FROM requests WHERE user_id = ? AND status = 'active'", (user_id,)) as cursor:
                requests = await cursor.fetchall()
        if not requests:
            await message.answer("Ўчириш учун сўровлар йўқ.", reply_markup=get_requests_menu(BUYER_ROLE))
            await state.set_state(RequestsMenu.menu)
            return
        request_ids = [r[0] for r in requests]
        await message.answer("Ўчириш учун сўров танланг:",
                             reply_markup=make_keyboard(request_ids, columns=2, with_back=True))
        await state.set_state(DeleteRequest.delete_request)
        logger.info(f"Пользователь {user_id} начал процесс удаления запроса")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка загрузки сўровларим для удаления для {user_id}: {e}", exc_info=True)
        await message.answer("Сўровларни юклашда хатолик юз берди!", reply_markup=get_requests_menu(BUYER_ROLE))
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_delete_request(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == "Орқага":
        await handle_back(message, state, RequestsMenu.menu, "Менинг сўровларим:", get_requests_menu(BUYER_ROLE))
        return
    item_id = message.text.strip()
    if not item_id.startswith("S-"):
        await message.answer("Ното‘г‘ри сўров ID формати!", reply_markup=get_requests_menu(BUYER_ROLE))
        logger.warning(f"Неверный формат ID от {user_id}: {item_id}")
        return
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            await conn.execute("BEGIN TRANSACTION")
            try:
                async with conn.execute(
                    "SELECT channel_message_id FROM requests WHERE unique_id = ? AND user_id = ? AND status = 'active'",
                    (item_id, user_id)
                ) as cursor:
                    request = await cursor.fetchone()
                if not request:
                    await message.answer(f"Сўров {item_id} топилмади ёки у сизга тегишли эмас!", reply_markup=get_requests_menu(BUYER_ROLE))
                    await state.set_state(RequestsMenu.menu)
                    logger.warning(f"Пользователь {user_id} пытался удалить несуществующий/чужой сўров {item_id}")
                    return
                if request[0]:
                    await safe_delete_message(message.bot, CHANNEL_ID, request[0])
                await conn.execute(
                    "UPDATE requests SET status = 'deleted' WHERE unique_id = ? AND user_id = ?",
                    (item_id, user_id)
                )
                await conn.commit()
                await message.answer(f"Сўров {item_id} ўчирилди!", reply_markup=get_requests_menu(BUYER_ROLE))
                await state.set_state(RequestsMenu.menu)
                logger.info(f"Пользователь {user_id} успешно удалил сўров {item_id}")
            except Exception as e:
                await conn.execute("ROLLBACK")
                logger.error(f"Ошибка базы данных при удалении сўрова {item_id} для {user_id}: {e}", exc_info=True)
                await message.answer("Сўровни ўчиришда хатолик юз берди!", reply_markup=get_requests_menu(BUYER_ROLE))
                await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Ошибка при удалении сўрова {item_id} для {user_id}: {e}", exc_info=True)
        await message.answer("Сўровни ўчиришда хатолик юз берди!", reply_markup=get_requests_menu(BUYER_ROLE))
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def close_request_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT id, unique_id, category, sort, volume_ton, price FROM requests WHERE user_id = ? AND status = 'active'",
                (user_id,)
            ) as cursor:
                requests = await cursor.fetchall()
        if not requests:
            await message.answer("Ёпиш учун сўровлар йўқ.", reply_markup=get_requests_menu(BUYER_ROLE))
            await state.set_state(RequestsMenu.menu)
            return
        request_buttons = [req[1] for req in requests]
        keyboard = make_keyboard(request_buttons, columns=2, with_back=True)
        request_list = [
            f"{req[1]} - {req[2]} ({req[3]}), {req[4]} тонна, {req[5]} сум"
            for req in requests
        ]
        response = "Ёпиш учун сўров танланг:\n" + "\n".join(request_list)
        await message.answer(response, reply_markup=keyboard)
        await state.set_state(CloseRequest.select_request)
        await state.update_data(requests=requests)
        logger.info(f"Пользователь {user_id} начал процесс закрытия запроса")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка загрузки сўровларим для закрытия для {user_id}: {e}", exc_info=True)
        await message.answer("Сўровларни юклашда хатолик юз берди!", reply_markup=get_requests_menu(BUYER_ROLE))
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_close_request(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == "Орқага":
        await handle_back(message, state, RequestsMenu.menu, "Менинг сўровларим:", get_requests_menu(BUYER_ROLE))
        return
    selected_unique_id = message.text.strip()
    if not selected_unique_id.startswith("S-"):
        await message.answer("Ното‘г‘ри сўров ID формати!", reply_markup=get_requests_menu(BUYER_ROLE))
        logger.warning(f"Неверный формат ID от {user_id}: {selected_unique_id}")
        return
    data = await state.get_data()
    requests = data.get("requests", [])
    selected_request = next((req for req in requests if req[1] == selected_unique_id), None)
    if selected_request:
        await state.update_data(selected_request_id=selected_request[0], selected_unique_id=selected_unique_id)
        await message.answer("Якуний нарҳни киритинг (сум):", reply_markup=types.ReplyKeyboardRemove())
        await state.set_state(CloseRequest.awaiting_final_price)
    else:
        await message.answer("Ното‘г‘ри танлов. Илтимос, рўйхатдан сўров танланг:", reply_markup=get_requests_menu(BUYER_ROLE))
        await state.set_state(RequestsMenu.menu)
        logger.warning(f"Пользователь {user_id} выбрал неверный запрос: {selected_unique_id}")

@buyer_only
async def process_final_price(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == "Орқага":
        await close_request_start(message, state)
        return
    try:
        final_price = validate_number(message.text, 1_000_000_000)
        if not final_price:
            await message.answer("Нарх мусбат рақам бўлиши ва 1 миллиард сумдан ошмаслиги керак:")
            logger.warning(f"Неверная цена от {user_id}: {message.text}")
            return
        data = await state.get_data()
        request_id = data.get("selected_request_id")
        unique_id = data.get("selected_unique_id")
        if not request_id or not unique_id:
            await message.answer("Ошибка: запрос не выбран. Начните заново.", reply_markup=get_requests_menu(BUYER_ROLE))
            await state.clear()
            return
        archived_at = format_uz_datetime(datetime.now())
        if not archived_at:
            logger.error(f"Ошибка форматирования даты для сўрова {unique_id}")
            await message.answer("Хатолик: ваqt форматини олиш мумкин эмас.", reply_markup=get_requests_menu(BUYER_ROLE))
            await state.clear()
            return
        async with aiosqlite.connect(DB_NAME) as conn:
            await conn.execute("BEGIN TRANSACTION")
            try:
                async with conn.execute(
                    "SELECT channel_message_id FROM requests WHERE id = ? AND user_id = ? AND status = 'active'",
                    (request_id, user_id)
                ) as cursor:
                    request = await cursor.fetchone()
                if not request:
                    await message.answer(f"Сўров {unique_id} топилмади!", reply_markup=get_requests_menu(BUYER_ROLE))
                    await state.set_state(RequestsMenu.menu)
                    return
                if request[0]:
                    await safe_delete_message(message.bot, CHANNEL_ID, request[0])
                await conn.execute(
                    "UPDATE requests SET status = 'archived', final_price = ?, archived_at = ? WHERE id = ? AND user_id = ?",
                    (final_price, archived_at, request_id, user_id)
                )
                await conn.commit()
                await message.answer(
                    f"✅ Сўров {unique_id} архивига ўтказилди. Якуний нарҳ: {final_price} сум.",
                    reply_markup=get_requests_menu(BUYER_ROLE)
                )
                await state.set_state(RequestsMenu.menu)
                logger.info(f"Пользователь {user_id} закрыл сўров {unique_id} с окончательной ценой {final_price} сум")
            except Exception as e:
                await conn.execute("ROLLBACK")
                logger.error(f"Ошибка базы данных при закрытии сўрова {unique_id} для {user_id}: {e}", exc_info=True)
                await message.answer("Сўровни ёпишда хатолик юз берди!", reply_markup=get_requests_menu(BUYER_ROLE))
                await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Ошибка при закрытии сўрова {unique_id} для {user_id}: {e}", exc_info=True)
        await message.answer("Сўровни ёпишда хатолик юз берди!", reply_markup=get_requests_menu(BUYER_ROLE))
        await state.set_state(RequestsMenu.menu)

async def notify_next_pending_item(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            await conn.execute("BEGIN TRANSACTION")
            try:
                async with conn.execute(
                    "SELECT unique_id, type FROM pending_items WHERE user_id = ? ORDER BY created_at LIMIT 1",
                    (user_id,)
                ) as cursor:
                    item = await cursor.fetchone()
                if not item:
                    await conn.commit()
                    return
                unique_id, item_type = item
                if item_type == "product":
                    table, menu_func = "products", get_ads_menu
                else:
                    table, menu_func = "requests", get_requests_menu
                async with conn.execute(f"SELECT category, region FROM {table} WHERE unique_id = ?", (unique_id,)) as cursor:
                    details = await cursor.fetchone()
                if details:
                    category, region = details
                    await message.answer(
                        f"Сизда тасдиқланмаган {item_type} мавжуд: {unique_id}\n"
                        f"Категория: {category}\n"
                        f"Вилоят: {region}\n"
                        f"Тасдиқлашни кутмоқда...",
                        reply_markup=menu_func(BUYER_ROLE if item_type == "request" else SELLER_ROLE)
                    )
                    await conn.execute("DELETE FROM pending_items WHERE unique_id = ? AND user_id = ?", (unique_id, user_id))
                    await conn.commit()
                else:
                    await conn.commit()
                logger.info(f"Уведомление о незавершённом {item_type} {unique_id} отправлено пользователю {user_id}")
            except Exception as e:
                await conn.execute("ROLLBACK")
                logger.error(f"Ошибка уведомления о незавершённом элементе для {user_id}: {e}", exc_info=True)
                await message.answer("Хатолик юз берди!", reply_markup=get_main_menu(BUYER_ROLE))
    except Exception as e:
        logger.error(f"Ошибка уведомления о незавершённом элементе для {user_id}: {e}", exc_info=True)
        await message.answer("Хатолик юз берди!", reply_markup=get_main_menu(BUYER_ROLE))

def register_handlers(dp: Dispatcher):
    dp.message.register(send_request_start, F.text == "Сўров юбориш")
    dp.message.register(requests_menu, F.text == "Менинг сўровларим")
    dp.message.register(process_requests_menu, RequestsMenu.menu)
    dp.message.register(add_request_start, F.text == "Сўров қўшиш")
    dp.message.register(process_category, AddRequest.category)
    dp.message.register(process_category, SendRequest.category)
    dp.message.register(process_sort, AddRequest.sort)
    dp.message.register(process_sort, SendRequest.sort)
    dp.message.register(process_region, AddRequest.region)
    dp.message.register(process_region, SendRequest.region)
    dp.message.register(process_volume_ton, AddRequest.volume_ton)
    dp.message.register(process_volume_ton, SendRequest.volume_ton)
    dp.message.register(process_price, AddRequest.price)
    dp.message.register(process_price, SendRequest.price)
    dp.message.register(requests_list, F.text == "Сўровлар рўйхати")
    dp.message.register(requests_delete_start, F.text == "Сўровни ўчириш")
    dp.message.register(process_delete_request, DeleteRequest.delete_request)
    dp.message.register(close_request_start, F.text == "Сўровни ёпиш")
    dp.message.register(process_close_request, CloseRequest.select_request)
    dp.message.register(process_final_price, CloseRequest.awaiting_final_price)
    logger.debug("Request handlers registered successfully")
