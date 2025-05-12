import aiosqlite
import logging

import pytz
from aiogram import types, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import DB_NAME, BUYER_ROLE, CATEGORIES, MAX_SORT_LENGTH, MAX_VOLUME_TON, CHANNEL_ID, ADMIN_IDS, SELLER_ROLE, ADMIN_ROLE
from utils import check_role, make_keyboard, validate_number, check_subscription, format_uz_datetime, parse_uz_datetime, has_pending_items, get_requests_menu, get_ads_menu, get_main_menu, notify_admin
from profile import normalize_text
from database import generate_item_id
from regions import get_all_regions
from datetime import datetime, timedelta
from functools import wraps

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
        current_state = await state.get_state()
        logger.debug(f"buyer_only: Проверка для user_id={user_id}, текст: '{message.text}', state={current_state}")
        try:
            has_pending = await has_pending_items(user_id)
            if has_pending:
                await notify_next_pending_item(message, state)
                logger.info(f"User {user_id} has pending items, notifying")
                return
            channel_active, bot_active, is_subscribed = await check_subscription(message.bot, user_id)
            if not bot_active:
                await message.answer("Сизнинг обунангиз тугади.", reply_markup=make_keyboard(["Обуна"], columns=1, one_time=True))
                await state.clear()
                await state.set_state("Registration:subscription")
                logger.info(f"Доступ для {user_id} заблокирован из-за неактивной подписки")
                return
            allowed, role = await check_role(message, allow_unregistered=True)
            if not allowed or (role != BUYER_ROLE and role != ADMIN_ROLE):
                await message.answer("Бу буйруқ фақат харидорлар учун!", reply_markup=get_main_menu(BUYER_ROLE))
                await state.clear()
                logger.warning(f"Пользователь {user_id} не прошёл проверку роли: {role}")
                return
            logger.debug(f"buyer_only passed for user_id={user_id}, role={role}, pending_items={has_pending}, bot_active={bot_active}, is_subscribed={is_subscribed}")
            return await handler(message, state, role=role, **kwargs)
        except aiosqlite.Error as e:
            logger.error(f"Database error in buyer_only for user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Database error in buyer_only for user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            return
        except Exception as e:
            logger.error(f"Unexpected error in buyer_only for user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Unexpected error in buyer_only for user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            return
    return wrapper

@buyer_only
async def send_request_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"send_request_start: user_id={user_id}, text='{message.text}', state={current_state}")
    try:
        await message.answer("Маҳсулот турини танланг:", reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
        await state.set_state(SendRequest.category)
        logger.info(f"Пользователь {user_id} начал создание запроса через 'Сўров юбормоқ'")
    except Exception as e:
        logger.error(f"Ошибка в send_request_start для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в send_request_start для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(role))
        await state.clear()

@buyer_only
async def requests_menu(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"requests_menu: user_id={user_id}, text='{message.text}', current_state={current_state}")
    try:
        if current_state != RequestsMenu.menu.state:
            await state.clear()
            logger.debug(f"Состояние очищено для user_id={user_id}, было: {current_state}")
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        logger.info(f"Пользователь {user_id} вошёл в меню сўровларим")
    except Exception as e:
        logger.error(f"Ошибка в requests_menu для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в requests_menu для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(role))
        await state.clear()

@buyer_only
async def handle_requests_back_button(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"handle_requests_back_button: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state != RequestsMenu.menu.state:
        logger.warning(f"Unexpected state for handle_requests_back_button: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    if normalized_text != "Орқага":
        logger.debug(f"handle_requests_back_button skipped: text='{normalized_text}', state={current_state}")
        return
    try:
        channel_active, bot_active, is_subscribed = await check_subscription(message.bot, user_id)
        logger.debug(f"Subscription check in handle_requests_back_button: user_id={user_id}, is_subscribed={is_subscribed}, bot_active={bot_active}")
        if not is_subscribed and role != ADMIN_ROLE:
            await message.answer(
                "Сизда фаол обуна мавжуд эмас. 'Обуна' тугмасини босинг:",
                reply_markup=make_keyboard(["Обуна"], one_time=True)
            )
            await state.set_state("Registration:subscription")
            logger.info(f"User {user_id} redirected to subscription from handle_requests_back_button")
            return
        await message.answer("Асосий меню:", reply_markup=get_main_menu(role))
        await state.clear()
        logger.info(f"Пользователь {user_id} вернулся в главное меню из подменю 'Менинг сўровларим'")
    except Exception as e:
        logger.error(f"Ошибка в handle_requests_back_button для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в handle_requests_back_button для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(role))
        await state.clear()

@buyer_only
async def process_requests_menu(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    normalized_text = normalize_text(message.text)
    logger.debug(f"process_requests_menu: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state != RequestsMenu.menu.state:
        logger.warning(f"Unexpected state for process_requests_menu: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    if message.text.startswith('/'):
        logger.debug(f"Игнорируется команда '{message.text}' в состоянии RequestsMenu.menu для user_id={user_id}")
        return
    options = {
        "сўровлар рўйхати": (get_requests_menu(), requests_list, "просмотрел список сўровларим"),
        "сўров қўшиш": (None, add_request_start, "начал добавление сўров"),
        "сўровни ўчириш": (None, requests_delete_start, "начал удаление сўров"),
        "сўровни ёпиш": (None, close_request_start, "начал закрытие сўров")
    }
    option = options.get(normalized_text)
    if option:
        logger.debug(f"process_requests_menu: user_id={user_id}, selected option='{normalized_text}', expected_state='RequestsMenu:menu'")
        try:
            if option[1]:
                await option[1](message, state)
            if option[0]:
                await message.answer("Менинг сўровларим:", reply_markup=option[0])
            if not option[1] or option[2].startswith("просмотрел"):
                await state.set_state(RequestsMenu.menu)
            logger.info(f"Пользователь {user_id} {option[2]}")
        except Exception as e:
            logger.error(f"Ошибка в process_requests_menu для user_id={user_id}, option={normalized_text}: {e}", exc_info=True)
            await notify_admin(f"Ошибка в process_requests_menu для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
    else:
        logger.warning(f"Неверный выбор в process_requests_menu: user_id={user_id}, text='{normalized_text}', state={current_state}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def add_request_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"add_request_start: user_id={user_id}, text='{message.text}', state={current_state}")
    if current_state != RequestsMenu.menu.state:
        logger.warning(f"Unexpected state for add_request_start: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    try:
        await message.answer("Маҳсулот турини танланг:", reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
        await state.set_state(AddRequest.category)
        logger.info(f"Пользователь {user_id} начал добавление запроса из 'Менинг сўровларим'")
    except Exception as e:
        logger.error(f"Ошибка в add_request_start для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в add_request_start для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_category(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    normalized_text = normalize_text(message.text)
    logger.debug(f"process_category: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state not in [SendRequest.category.state, AddRequest.category.state]:
        logger.warning(f"Unexpected state for process_category: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    back_menu = get_requests_menu() if current_state.startswith("AddRequest") else get_main_menu(role)
    back_state = RequestsMenu.menu if current_state.startswith("AddRequest") else None
    try:
        if normalized_text == "орқага":
            await message.answer("Менинг сўровларим:" if current_state.startswith("AddRequest") else "Асосий меню:", reply_markup=back_menu)
            await state.set_state(back_state) if back_state else await state.clear()
            logger.info(f"Пользователь {user_id} вернулся из выбора категории")
            return
        category = message.text.strip()
        if category not in CATEGORIES:
            await message.answer("Нотўғри категория! Рўйхатдан танланг:", reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
            logger.warning(f"Неверная категория от {user_id}: {category}")
            return
        await state.update_data(category=category)
        await message.answer("Маҳсулот сортни киритинг:", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(SendRequest.sort if current_state.startswith("SendRequest") else AddRequest.sort)
        logger.debug(f"Пользователь {user_id} выбрал категорию: {category}")
    except Exception as e:
        logger.error(f"Ошибка в process_category для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в process_category для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_sort(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    normalized_text = normalize_text(message.text)
    back_state = AddRequest.category if current_state.startswith("AddRequest") else SendRequest.category
    logger.debug(f"process_sort: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state not in [SendRequest.sort.state, AddRequest.sort.state]:
        logger.warning(f"Unexpected state for process_sort: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    try:
        if normalized_text == "орқага":
            await message.answer("Маҳсулот турини танланг:", reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
            await state.set_state(back_state)
            logger.info(f"Пользователь {user_id} вернулся к выбору категории из сорта")
            return
        sort = message.text.strip()
        if len(sort) > MAX_SORT_LENGTH:
            await message.answer(f"Сорт {MAX_SORT_LENGTH} белгидан узун бўлмаслиги керак. Қайта киритинг:")
            logger.warning(f"Сорт слишком длинный от {user_id}: {len(sort)} символов")
            return
        await state.update_data(sort=sort)
        await message.answer("Вилоятни танланг:", reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True))
        await state.set_state(SendRequest.region if current_state.startswith("SendRequest") else AddRequest.region)
        logger.debug(f"Пользователь {user_id} ввёл сорт: {sort}")
    except Exception as e:
        logger.error(f"Ошибка в process_sort для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в process_sort для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_region(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    normalized_text = normalize_text(message.text)
    logger.debug(f"process_region: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state not in [SendRequest.region.state, AddRequest.region.state]:
        logger.warning(f"Unexpected state for process_region: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    try:
        if normalized_text == "орқага":
            await message.answer("Маҳсулот сортни киритинг:", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
            await state.set_state(SendRequest.sort if current_state.startswith("SendRequest") else AddRequest.sort)
            logger.info(f"Пользователь {user_id} вернулся к вводу сорта из региона")
            return
        region = message.text.strip()
        if region not in get_all_regions():
            await message.answer("Нотўғри область! Рўйхатдан танланг:", reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True))
            logger.warning(f"Неверный регион от {user_id}: {region}")
            return
        await state.update_data(region=region)
        await message.answer("Маҳсулот ҳажмни киритинг (тоннада):", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(SendRequest.volume_ton if current_state.startswith("SendRequest") else AddRequest.volume_ton)
        logger.debug(f"Пользователь {user_id} выбрал регион: {region}")
    except Exception as e:
        logger.error(f"Ошибка в process_region для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в process_region для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_volume_ton(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    normalized_text = normalize_text(message.text)
    back_state = SendRequest.region if current_state.startswith("SendRequest") else AddRequest.region
    logger.debug(f"process_volume_ton: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state not in [SendRequest.volume_ton.state, AddRequest.volume_ton.state]:
        logger.warning(f"Unexpected state for process_volume_ton: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    try:
        if normalized_text == "орқага":
            await message.answer("Вилоятни танланг:", reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True))
            await state.set_state(back_state)
            logger.info(f"Пользователь {user_id} вернулся к выбору региона из объёма")
            return
        valid, volume_ton = validate_number(message.text, min_value=0)
        if not valid or volume_ton > MAX_VOLUME_TON:
            await message.answer(f"Ҳажм мусбат рақам бўлиши ва {MAX_VOLUME_TON} тоннадан ошмаслиги керак:")
            logger.warning(f"Неверный объём от {user_id}: {message.text}")
            return
        await state.update_data(volume_ton=volume_ton)
        await message.answer("Нархни киритинг (сўмда):", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(SendRequest.price if current_state.startswith("SendRequest") else AddRequest.price)
        logger.debug(f"Пользователь {user_id} ввёл объём: {volume_ton} тонн")
    except Exception as e:
        logger.error(f"Ошибка в process_volume_ton для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в process_volume_ton для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_price(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    normalized_text = normalize_text(message.text)
    back_state = SendRequest.volume_ton if current_state.startswith("SendRequest") else AddRequest.volume_ton
    finish_menu = get_main_menu(role) if current_state.startswith("SendRequest") else get_requests_menu()
    finish_state = None if current_state.startswith("SendRequest") else RequestsMenu.menu
    logger.debug(f"process_price: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state not in [SendRequest.price.state, AddRequest.price.state]:
        logger.warning(f"Unexpected state for process_price: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    try:
        if normalized_text == "орқага":
            await message.answer("Маҳсулот ҳажмни киритинг (тоннада):", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
            await state.set_state(back_state)
            logger.info(f"Пользователь {user_id} вернулся к вводу объёма из цены")
            return
        valid, price = validate_number(message.text, min_value=0)
        if not valid or price > 1_000_000_000:
            await message.answer("Нарх мусбат рақам бўлиши ва 1 миллиард сумдан ошмаслиги керак:")
            logger.warning(f"Неверная цена от {user_id}: {message.text}")
            return
        data = await state.get_data()
        item_id = await generate_item_id("requests", "S")
        created_at = format_uz_datetime(datetime.now())
        async with aiosqlite.connect(DB_NAME) as conn:
            await conn.execute(
                "INSERT INTO requests (unique_id, user_id, category, region, sort, volume_ton, price, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (item_id, user_id, data["category"], data["region"], data["sort"], data["volume_ton"], price, created_at)
            )
            await conn.commit()

        info = (
            f"Сўров {item_id}\n"
            f"Категория: {data['category']}\n"
            f"Сорт: {data['sort']}\n"
            f"Вилоят: {data['region']}\n"
            f"Ҳажм: {data['volume_ton']} тонна\n"
            f"Нарх: {price} сум"
        )
        channel_msg = None
        try:
            channel_msg = await message.bot.send_message(chat_id=CHANNEL_ID, text=info)
            logger.debug(f"Сўров {item_id} отправлен в канал, message_id={channel_msg.message_id}")
        except types.TelegramError as e:
            logger.error(f"Ошибка отправки сўрова {item_id} в канал: {e}")
            for admin_id in ADMIN_IDS:
                await message.bot.send_message(admin_id, f"Сўров {item_id} канлага юборилмади: {e}")
            await message.answer("Сўров каналга юборилмади. Админ билан боғланинг.", reply_markup=finish_menu)
            await state.set_state(finish_state) if finish_state else await state.clear()
            return

        try:
            async with aiosqlite.connect(DB_NAME) as conn:
                await conn.execute("UPDATE requests SET channel_message_id = ? WHERE unique_id = ?",
                                  (channel_msg.message_id, item_id))
                await conn.commit()
        except aiosqlite.Error as e:
            logger.error(f"Ошибка обновления channel_message_id для {item_id}: {e}")
            for admin_id in ADMIN_IDS:
                await message.bot.send_message(admin_id, f"Сўров {item_id} каналга юборилди, лекин базада хатолик: {e}")

        await message.answer(
            f"Сизнинг сўровингиз юборилди. Сўров рақами {item_id}. Танишиш учун Сўровлар доскаси ёки <a href=\"https://t.me/+6WXzyGqqotgzODM6\">Каналга</a> ўтинг.",
            reply_markup=finish_menu,
            parse_mode="HTML"
        )
        await state.set_state(finish_state) if finish_state else await state.clear()
        logger.info(f"Сўров {item_id} успешно добавлен пользователем {user_id}")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при добавлении сўрова {item_id or 'unknown'}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных при добавлении сўрова {item_id or 'unknown'} для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровни қўшишда хатолик юз берди! Админга хабар беринг.", reply_markup=finish_menu)
        await state.set_state(finish_state) if finish_state else await state.clear()
    except Exception as e:
        logger.error(f"Неожиданная ошибка в process_price для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка в process_price для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def requests_list(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"requests_list: user_id={user_id}, text='{message.text}', state={current_state}")
    if current_state != RequestsMenu.menu.state:
        logger.warning(f"Unexpected state for requests_list: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                    "SELECT unique_id, category, region, sort, volume_ton, price, created_at "
                    "FROM requests WHERE user_id = ? AND status = 'active'", (user_id,)
            ) as cursor:
                requests = await cursor.fetchall()
        if not requests:
            await message.answer("Сизда сўровлар йўқ.", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            logger.info(f"Пользователь {user_id} не имеет активных запросов")
            return
        now = datetime.now(pytz.UTC)
        for unique_id, category, region, sort, volume_ton, price, created_at in requests:
            created_at_dt = parse_uz_datetime(created_at)
            if not created_at_dt:
                logger.warning(f"Неверный формат created_at для сўрова {unique_id}: {created_at}")
                continue
            expiration = created_at_dt + timedelta(hours=24)
            status = "Фаол" if now < expiration else "Муддати тугаган"
            info = (
                f"Сўров {unique_id}\n"
                f"Категория: {category}\n"
                f"Сорт: {sort}\n"
                f"Вилоят: {region}\n"
                f"Ҳажм: {volume_ton} тонна\n"
                f"Нарх: {price} сум\n"
                f"Ҳолат: {status} ({format_uz_datetime(expiration)} гача)"
            )
            await message.answer(info)
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        logger.info(f"Пользователь {user_id} просмотрел список своих запросов")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка загрузки списка сўровларим для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка загрузки списка сўровларим для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровлар рўйхатини юклашда хатолик юз берди!", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Неожиданная ошибка в requests_list для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка в requests_list для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def requests_delete_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"requests_delete_start: user_id={user_id}, text='{message.text}', state={current_state}")
    if current_state != RequestsMenu.menu.state:
        logger.warning(f"Unexpected state for requests_delete_start: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT unique_id FROM requests WHERE user_id = ? AND status = 'active'", (user_id,)) as cursor:
                requests = await cursor.fetchall()
        if not requests:
            await message.answer("Ўчириш учун сўровлар йўқ.", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            logger.info(f"Пользователь {user_id} не имеет запросов для удаления")
            return
        request_ids = [r[0] for r in requests]
        await message.answer("Ўчириш учун сўров танланг:",
                             reply_markup=make_keyboard(request_ids, columns=2, with_back=True))
        await state.set_state(DeleteRequest.delete_request)
        logger.info(f"Пользователь {user_id} начал процесс удаления запроса")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка загрузки сўровларим для удаления для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка загрузки сўровларим для удаления для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровларни юклашда хатолик юз берди!", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Неожиданная ошибка в requests_delete_start для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка в requests_delete_start для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_delete_request(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_delete_request: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state != DeleteRequest.delete_request.state:
        logger.warning(f"Unexpected state for process_delete_request: user_id={user_id}, state={current_state}")
        await state.set_state(RequestsMenu.menu)
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        return
    try:
        if normalized_text == "орқага":
            await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            logger.info(f"Пользователь {user_id} вернулся в меню сўровларим из удаления")
            return
        item_id = message.text.strip()
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT channel_message_id FROM requests WHERE unique_id = ? AND user_id = ? AND status = 'active'",
                (item_id, user_id)
            ) as cursor:
                request = await cursor.fetchone()
            if not request:
                await message.answer(f"Сўров {item_id} топилмади ёки у сизга тегишли эмас!", reply_markup=get_requests_menu())
                await state.set_state(RequestsMenu.menu)
                logger.warning(f"Пользователь {user_id} пытался удалить несуществующий/чужой сўров {item_id}")
                return
            if request[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=request[0])
                    logger.debug(f"Сообщение {request[0]} удалено из канала {CHANNEL_ID}")
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {request[0]} из канала: {e}")
            await conn.execute(
                "UPDATE requests SET status = 'deleted' WHERE unique_id = ? AND user_id = ?",
                (item_id, user_id)
            )
            await conn.commit()
        await message.answer(f"Сўров {item_id} ўчирилди!", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        logger.info(f"Пользователь {user_id} успешно удалил сўров {item_id}")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при удалении сўрова {item_id or 'unknown'} для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных при удалении сўрова {item_id or 'unknown'} для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровни ўчиришда хатолик юз берди!", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Неожиданная ошибка в process_delete_request для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка в process_delete_request для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def close_request_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"close_request_start: user_id={user_id}, text='{message.text}', state={current_state}")
    if current_state != RequestsMenu.menu.state:
        logger.warning(f"Unexpected state for close_request_start: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        return
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT id, unique_id, category, sort, volume_ton, price FROM requests WHERE user_id = ? AND status = 'active'",
                (user_id,)
            ) as cursor:
                requests = await cursor.fetchall()
        if not requests:
            await message.answer("Ёпиш учун сўровлар йўқ.", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            logger.info(f"Пользователь {user_id} не имеет запросов для закрытия")
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
        logger.error(f"Ошибка загрузки сўровларим для закрытия для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка загрузки сўровларим для закрытия для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровларни юклашда хатолик юз берди!", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Неожиданная ошибка в close_request_start для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка в close_request_start для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_close_request(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_close_request: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state != CloseRequest.select_request.state:
        logger.warning(f"Unexpected state for process_close_request: user_id={user_id}, state={current_state}")
        await state.set_state(RequestsMenu.menu)
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        return
    try:
        if normalized_text == "орқага":
            await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            logger.info(f"Пользователь {user_id} вернулся в меню сўровларим из выбора для закрытия")
            return
        selected_unique_id = message.text.strip()
        data = await state.get_data()
        requests = data.get("requests", [])
        selected_request = next((req for req in requests if req[1] == selected_unique_id), None)
        if selected_request:
            await state.update_data(selected_request_id=selected_request[0], selected_unique_id=selected_unique_id)
            await message.answer("Якуний нарҳни киритинг (сум):", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
            await state.set_state(CloseRequest.awaiting_final_price)
            logger.debug(f"Пользователь {user_id} выбрал сўров {selected_unique_id} для закрытия")
        else:
            await message.answer("Ното‘г‘ри танлов. Илтимос, рўйхатдан сўров танланг:", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            logger.warning(f"Пользователь {user_id} выбрал неверный запрос: {selected_unique_id}")
    except Exception as e:
        logger.error(f"Ошибка в process_close_request для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в process_close_request для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_final_price(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_final_price: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state != CloseRequest.awaiting_final_price.state:
        logger.warning(f"Unexpected state for process_final_price: user_id={user_id}, state={current_state}")
        await state.set_state(RequestsMenu.menu)
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        return
    try:
        if normalized_text == "орқага":
            await close_request_start(message, state)
            logger.info(f"Пользователь {user_id} вернулся к выбору запроса для закрытия")
            return
        valid, final_price = validate_number(message.text, min_value=0)
        if not valid or final_price > 1_000_000_000:
            await message.answer("Нарх мусбат рақам бўлиши ва 1 миллиард сумдан ошмаслиги керак:", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
            logger.warning(f"Неверная цена от {user_id}: {message.text}")
            return
        data = await state.get_data()
        request_id = data.get("selected_request_id")
        unique_id = data.get("selected_unique_id")
        if not request_id or not unique_id:
            await message.answer("Ошибка: запрос не выбран. Начните заново.", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            logger.warning(f"Ошибка в process_final_price для user_id={user_id}: request_id или unique_id отсутствует")
            return
        archived_at = format_uz_datetime(datetime.now())
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT channel_message_id FROM requests WHERE id = ? AND user_id = ? AND status = 'active'",
                (request_id, user_id)
            ) as cursor:
                request = await cursor.fetchone()
            if not request:
                await message.answer(f"Сўров {unique_id} топилмади!", reply_markup=get_requests_menu())
                await state.set_state(RequestsMenu.menu)
                logger.warning(f"Сўров {unique_id} не найден для user_id={user_id}")
                return
            await conn.execute(
                "UPDATE requests SET status = 'archived', final_price = ?, archived_at = ? WHERE id = ? AND user_id = ?",
                (final_price, archived_at, request_id, user_id)
            )
            await conn.commit()
            if request[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=request[0])
                    logger.debug(f"Сообщение {request[0]} удалено из канала")
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {request[0]} из канала: {e}")
        await message.answer(
            f"✅ Сўров {unique_id} архивига ўтказилди. Якуний нарҳ: {final_price} сум.",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)
        logger.info(f"Пользователь {user_id} закрыл сўров {unique_id} с окончательной ценой {final_price} сум")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при закрытии сўрова {unique_id or 'unknown'} для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных при закрытии сўрова {unique_id or 'unknown'} для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Сўровни ёпишда хатолик юз берди!", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Неожиданная ошибка в process_final_price для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка в process_final_price для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

async def notify_next_pending_item(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"notify_next_pending_item: user_id={user_id}")
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT unique_id, type FROM pending_items WHERE user_id = ? ORDER BY created_at LIMIT 1",
                (user_id,)
            ) as cursor:
                item = await cursor.fetchone()
            if not item:
                logger.debug(f"Нет незавершённых элементов для user_id={user_id}")
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
                    reply_markup=menu_func()
                )
            await conn.execute("DELETE FROM pending_items WHERE unique_id = ? AND user_id = ?", (unique_id, user_id))
            await conn.commit()
        logger.info(f"Уведомление о незавершённом {item_type} {unique_id} отправлено пользователю {user_id}")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка уведомления о незавершённом элементе для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка уведомления о незавершённом элементе для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(BUYER_ROLE))
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Неожиданная ошибка в notify_next_pending_item для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Неожиданная ошибка в notify_next_pending_item для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer("Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).", reply_markup=get_main_menu(BUYER_ROLE))
        await state.set_state(RequestsMenu.menu)

def register_handlers(dp: Dispatcher):
    logger.debug("Registering request handlers")
    dp.message.register(handle_requests_back_button, RequestsMenu.menu, F.text == "Орқага")
    dp.message.register(process_requests_menu, RequestsMenu.menu)
    dp.message.register(send_request_start, F.text == "Сўров юбормоқ")
    dp.message.register(requests_menu, F.text == "Менинг сўровларим")
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