import aiosqlite
import logging
import pytz
from aiogram import types, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from config import DB_NAME, BUYER_ROLE, CATEGORIES, MAX_SORT_LENGTH, MAX_VOLUME_TON, CHANNEL_ID, ADMIN_IDS, SELLER_ROLE, ADMIN_ROLE, DB_TIMEOUT
from utils import check_role, make_keyboard, validate_number_minimal, validate_sort, check_subscription, format_uz_datetime, parse_uz_datetime, has_pending_items, get_requests_menu, get_ads_menu, get_main_menu, notify_admin
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
        logger.info(f"buyer_only: user_id={user_id}, text='{message.text}', state={current_state}")
        try:
            logger.debug(f"Проверка незавершённых элементов для user_id={user_id}")
            has_pending = await has_pending_items(user_id)
            if has_pending:
                await notify_next_pending_item(message, state)
                logger.info(f"Пользователь {user_id} имеет незавершённые элементы")
                return

            logger.debug(f"Очистка кэша подписки для user_id={user_id}")
            try:
                await state.storage.redis.delete(f"sub:{user_id}")
                logger.debug(f"Очищен кэш подписки для user_id={user_id}")
            except Exception as e:
                logger.warning(f"Ошибка очистки кэша подписки для user_id={user_id}: {e}")

            logger.debug(f"Проверка подписки для user_id={user_id}")
            success, bot_active, is_subscribed = await check_subscription(message.bot, user_id, state.storage)
            logger.debug(f"check_subscription: user_id={user_id}, success={success}, bot_active={bot_active}, is_subscribed={is_subscribed}")
            if not is_subscribed:
                await message.answer(
                    "Сизда фаол обуна мавжуд эмас. Админ билан боғланинг (@ad_mbozor).",
                    reply_markup=get_main_menu(BUYER_ROLE)
                )
                await state.set_state("Registration:subscription")
                logger.info(f"Пользователь {user_id} перенаправлен на подписку")
                return

            logger.debug(f"Проверка регистрации для user_id={user_id}")
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute(
                    "SELECT id FROM users WHERE id = ?",
                    (user_id,)
                ) as cursor:
                    user_exists = await cursor.fetchone()
            if not user_exists:
                await message.answer(
                    "Сиз рўйхатдан ўтмагансиз. Илтимос, рўйхатдан ўтинг.",
                    reply_markup=get_main_menu(None)
                )
                await state.set_state("Registration:start")
                logger.warning(f"Пользователь {user_id} не зарегистрирован")
                return

            logger.debug(f"Проверка роли для user_id={user_id}")
            allowed, role = await check_role(message, allow_unregistered=True)
            logger.debug(f"check_role: user_id={user_id}, allowed={allowed}, role={role}")
            if not allowed or (role != BUYER_ROLE and role != ADMIN_ROLE):
                await message.answer(
                    "Бу буйруқ фақат харидорлар учун!",
                    reply_markup=get_main_menu(BUYER_ROLE)
                )
                await state.clear()
                logger.warning(f"Пользователь {user_id} не прошёл проверку роли: {role}")
                return

            logger.debug(f"Передача управления обработчику {handler.__name__} для user_id={user_id}")
            return await handler(message, state, role=role)
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных в buyer_only для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных в buyer_only для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_requests_menu()
            )
            await state.set_state(RequestsMenu.menu)
            return
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в buyer_only для user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Непредвиденная ошибка в buyer_only для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_requests_menu()
            )
            await state.set_state(RequestsMenu.menu)
            return
    return wrapper

@buyer_only
async def send_request_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"send_request_start: user_id={user_id}, text='{message.text}'")
    try:
        await message.answer(
            "Маҳсулот турини танланг:",
            reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True)
        )
        await state.set_state(SendRequest.category)
        logger.info(f"Пользователь {user_id} начал создание запроса через 'Сўров юбориш'")
    except Exception as e:
        logger.error(f"Ошибка в send_request_start для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в send_request_start для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_main_menu(role)
        )
        await state.clear()

@buyer_only
async def requests_menu(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"requests_menu: user_id={user_id}, text='{message.text}'")
    try:
        await state.clear()
        await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        logger.info(f"Пользователь {user_id} вошёл в меню 'Менинг сўровларим'")
    except Exception as e:
        logger.error(f"Ошибка в requests_menu для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в requests_menu для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_main_menu(role)
        )
        await state.clear()

@buyer_only
async def handle_requests_back_button(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"handle_requests_back_button: user_id={user_id}, text='{message.text}'")
    if message.text != "Орқага":
        logger.debug(f"Пропущен handle_requests_back_button: text='{message.text}'")
        return
    try:
        success, bot_active, is_subscribed = await check_subscription(message.bot, user_id, state.storage)
        logger.debug(f"check_subscription в handle_requests_back_button: user_id={user_id}, success={success}, is_subscribed={is_subscribed}")
        if not is_subscribed and role != ADMIN_ROLE:
            await message.answer(
                "Сизда фаол обуна мавжуд эмас. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_main_menu(role)
            )
            await state.set_state("Registration:subscription")
            logger.info(f"Пользователь {user_id} перенаправлен на подписку")
            return
        await message.answer("Асосий меню:", reply_markup=get_main_menu(role))
        await state.clear()
        logger.info(f"Пользователь {user_id} вернулся в главное меню")
    except Exception as e:
        logger.error(f"Ошибка в handle_requests_back_button для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в handle_requests_back_button для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_main_menu(role)
        )
        await state.clear()

@buyer_only
async def process_requests_menu(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"process_requests_menu: user_id={user_id}, text='{message.text}'")
    if message.text.startswith('/'):
        logger.debug(f"Игнорируется команда '{message.text}' для user_id={user_id}")
        return
    options = {
        "Сўровлар рўйхати": (None, requests_list, "просмотрел список сўровларим"),
        "Сўровни ўчириш": (None, requests_delete_start, "начал удаление сўров"),
        "Сўровни ёпиш": (None, close_request_start, "начал закрытие сўров")
    }
    option = options.get(message.text)
    if option:
        try:
            if option[1]:
                await option[1](message, state, role)
            if option[0]:
                await message.answer("Менинг сўровларим:", reply_markup=option[0])
                await state.set_state(RequestsMenu.menu)
            logger.info(f"Пользователь {user_id} {option[2]}")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных в process_requests_menu, опция={message.text}: {e}", exc_info=True)
            await notify_admin(f"Ошибка базы данных в process_requests_menu для user_id={user_id}, опция={message.text}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_requests_menu()
            )
            await state.set_state(RequestsMenu.menu)
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в process_requests_menu, опция={message.text}: {e}", exc_info=True)
            await notify_admin(f"Непредвиденная ошибка в process_requests_menu для user_id={user_id}, опция={message.text}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_requests_menu()
            )
            await state.set_state(RequestsMenu.menu)
    else:
        logger.warning(f"Некорректный выбор в process_requests_menu: user_id={user_id}, text='{message.text}'")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_category(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    category = message.text
    logger.debug(f"process_category: user_id={user_id}, text='{category}'")
    current_state = await state.get_state()
    back_menu = get_main_menu(role)
    back_state = None
    try:
        if category == "Орқага":
            await message.answer(
                "Асосий меню:", reply_markup=back_menu
            )
            await state.clear()
            logger.info(f"Пользователь {user_id} вернулся из выбора категории")
            return
        if category not in CATEGORIES:
            await message.answer(
                "Илтимос, категориядан танланг:",
                reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True)
            )
            logger.warning(f"Некорректная категория от user_id={user_id}: {category}")
            return
        await state.update_data(category=category)
        await message.answer(
            "Маҳсулот сортни киритинг:",
            reply_markup=make_keyboard([], with_back=True)
        )
        await state.set_state(SendRequest.sort)
        logger.info(f"Пользователь {user_id} выбрал категорию: {category}")
    except Exception as e:
        logger.error(f"Ошибка в process_category для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в process_category для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_sort(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    sort = message.text
    current_state = await state.get_state()
    back_state = SendRequest.category
    logger.debug(f"process_sort: user_id={user_id}, text='{sort}'")
    try:
        if sort == "Орқага":
            await message.answer(
                "Маҳсулот турини танланг:",
                reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True)
            )
            await state.set_state(back_state)
            logger.info(f"Пользователь {user_id} вернулся к выбору категории")
            return
        if not await validate_sort(sort):
            await message.answer(f"Сорт {MAX_SORT_LENGTH} белгидан узун бўлмаслиги керак:")
            logger.warning(f"Сорт слишком длинный от user_id={user_id}: {len(sort)}")
            return
        await state.update_data(sort=sort)
        regions = get_all_regions() + ["Бутун Ўзбекистон"]
        if not regions:
            await message.answer(
                "Вилоятлар рўйхати бўш. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_requests_menu()
            )
            await state.set_state(RequestsMenu.menu)
            logger.warning(f"Пустой список регионов для user_id={user_id}")
            return
        await message.answer(
            "Вилоятни танланг:",
            reply_markup=make_keyboard(regions, columns=2, with_back=True)
        )
        await state.set_state(SendRequest.region)
        logger.info(f"Пользователь {user_id} ввёл сорт: {sort}")
    except Exception as e:
        logger.error(f"Ошибка в process_sort для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в process_sort для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_region(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    region = message.text
    current_state = await state.get_state()
    back_state = SendRequest.sort
    logger.debug(f"process_region: user_id={user_id}, text='{region}'")
    try:
        if region == "Орқага":
            await message.answer(
                "Маҳсулот сортни киритинг:",
                reply_markup=make_keyboard([], with_back=True)
            )
            await state.set_state(back_state)
            logger.info(f"Пользователь {user_id} вернулся к вводу сорта")
            return
        regions = get_all_regions() + ["Бутун Ўзбекистон"]
        if region not in regions:
            await message.answer(
                "Илтимос, рўйхатдан вилоят танланг:",
                reply_markup=make_keyboard(regions, columns=2, with_back=True)
            )
            logger.warning(f"Некорректный регион от user_id={user_id}: {region}")
            return
        await state.update_data(region=region)
        await message.answer(
            "Маҳсулот ҳажмни киритинг (тоннада):",
            reply_markup=make_keyboard([], with_back=True)
        )
        await state.set_state(SendRequest.volume_ton)
        logger.info(f"Пользователь {user_id} выбрал регион: {region}")
    except Exception as e:
        logger.error(f"Ошибка в process_region для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в process_region для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_volume_ton(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    volume = message.text
    current_state = await state.get_state()
    back_state = SendRequest.region
    logger.debug(f"process_volume_ton: user_id={user_id}, text='{volume}'")
    try:
        if volume == "Орқага":
            regions = get_all_regions() + ["Бутун Ўзбекистон"]
            await message.answer(
                "Вилоятни танланг:",
                reply_markup=make_keyboard(regions, columns=2, with_back=True)
            )
            await state.set_state(back_state)
            logger.info(f"Пользователь {user_id} вернулся к выбору региона")
            return
        valid, volume_ton = await validate_number_minimal(volume)
        if not valid or volume_ton <= 0 or volume_ton > MAX_VOLUME_TON:
            await message.answer(f"Ҳажм мусбат рақам бўлиши ва {MAX_VOLUME_TON} тоннадан ошмаслиги керак:")
            logger.warning(f"Некорректный объём от user_id={user_id}: {volume}")
            return
        await state.update_data(volume_ton=volume_ton)
        await message.answer(
            "Нархни киритинг (сўмда):",
            reply_markup=make_keyboard([], with_back=True)
        )
        await state.set_state(SendRequest.price)
        logger.info(f"Пользователь {user_id} ввёл объём: {volume_ton} тонн")
    except Exception as e:
        logger.error(f"Ошибка в process_volume_ton для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в process_volume_ton для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_price(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    price = message.text
    current_state = await state.get_state()
    back_state = SendRequest.volume_ton
    finish_menu = get_main_menu(role)
    finish_state = None
    logger.debug(f"process_price: user_id={user_id}, text='{price}'")
    try:
        if price == "Орқага":
            await message.answer(
                "Маҳсулот ҳажмни киритинг (тоннада):",
                reply_markup=make_keyboard([], with_back=True)
            )
            await state.set_state(back_state)
            logger.info(f"Пользователь {user_id} вернулся к вводу объёма")
            return
        valid, price_value = await validate_number_minimal(price)
        if not valid or price_value <= 0 or price_value > 1_000_000_000:
            await message.answer("Нарх мусбат рақам бўлиши ва 1 миллиард сумдан ошмаслиги керак:")
            logger.warning(f"Некорректная цена от user_id={user_id}: {price}")
            return
        data = await state.get_data()
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='requests'"
            ) as cursor:
                if not await cursor.fetchone():
                    raise aiosqlite.Error("Table 'requests' does not exist")
            async with conn.execute(
                "SELECT unique_id FROM requests WHERE user_id = ? AND category = ? AND sort = ? AND region = ? AND status = 'active'",
                (user_id, data["category"], data["sort"], data["region"])
            ) as cursor:
                if await cursor.fetchone():
                    await message.answer("Бундай сўров аллақачон мавжуд!", reply_markup=finish_menu)
                    await state.clear()
                    logger.warning(f"Дубликат запроса для user_id={user_id}: category={data['category']}, sort={data['sort']}, region={data['region']}")
                    return
            item_id = await generate_item_id("requests", "S")
            created_at = format_uz_datetime(datetime.now(pytz.UTC))
            await conn.execute(
                "INSERT INTO requests (unique_id, user_id, category, region, sort, volume_ton, price, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (item_id, user_id, data["category"], data["region"], data["sort"], data["volume_ton"], price_value, created_at)
            )
            await conn.commit()
        info = (
            f"Сўров {item_id}\n"
            f"Категория: {data['category']}\n"
            f"Сорт: {data['sort']}\n"
            f"Вилоят: {data['region']}\n"
            f"Ҳажм: {data['volume_ton']} тонна\n"
            f"Нарх: {price_value:,.0f} сўм"
        )
        try:
            channel_msg = await message.bot.send_message(chat_id=CHANNEL_ID, text=info)
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                await conn.execute(
                    "UPDATE requests SET channel_message_id = ? WHERE unique_id = ?",
                    (channel_msg.message_id, item_id)
                )
                await conn.commit()
            logger.info(f"Запрос {item_id} отправлен в канал, message_id={channel_msg.message_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки запроса {item_id} в канал: {e}", exc_info=True)
            await notify_admin(f"Ошибка отправки запроса {item_id} в канал для user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Сўров каналга юборилмади. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=finish_menu
            )
            await state.clear()
            return
        await message.answer(
            f"Сизнинг сўровингиз юборилди. Сўров рақами {item_id}. Танишиш учун Сўровлар доскаси ёки <a href=\"https://t.me/+6WXzyGqqotgzODM6\">Каналга</a> ўтинг.",
            reply_markup=finish_menu,
            parse_mode="HTML"
        )
        await state.clear()
        logger.info(f"Пользователь {user_id} добавил запрос {item_id}")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при добавлении запроса {item_id or 'неизвестно'}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных при добавлении запроса для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Сўровни қўшишда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=finish_menu
        )
        await state.clear()
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в process_price для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Непредвиденная ошибка в process_price для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def requests_list(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"requests_list: user_id={user_id}, text='{message.text}'")
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='requests'"
            ) as cursor:
                if not await cursor.fetchone():
                    raise aiosqlite.Error("Table 'requests' does not exist")
            async with conn.execute(
                "SELECT unique_id, category, region, sort, volume_ton, price, created_at "
                "FROM requests WHERE user_id = ? AND status = 'active'",
                (user_id,)
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
                logger.warning(f"Некорректный формат created_at для запроса {unique_id}: {created_at}")
                continue
            expiration = created_at_dt + timedelta(hours=24)
            status = "Фаол" if now < expiration else "Муддати тугаган"
            info = (
                f"Сўров {unique_id}\n"
                f"Категория: {category}\n"
                f"Сорт: {sort}\n"
                f"Вилоят: {region}\n"
                f"Ҳажм: {volume_ton} тонна\n"
                f"Нарх: {price:,.0f} сўм\n"
                f"Ҳолат: {status} ({format_uz_datetime(expiration)} гача)"
            )
            await message.answer(info)
        await state.set_state(RequestsMenu.menu)
        logger.info(f"Пользователь {user_id} просмотрел список запросов")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных в requests_list для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных в requests_list для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Сўровлар рўйхатини юклашда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в requests_list для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Непредвиденная ошибка в requests_list для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def requests_delete_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"requests_delete_start: user_id={user_id}, text='{message.text}'")
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='requests'"
            ) as cursor:
                if not await cursor.fetchone():
                    raise aiosqlite.Error("Table 'requests' does not exist")
            async with conn.execute(
                "SELECT unique_id FROM requests WHERE user_id = ? AND status = 'active'",
                (user_id,)
            ) as cursor:
                requests = await cursor.fetchall()
        if not requests:
            await message.answer("Ўчириш учун сўровлар йўқ.", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            logger.info(f"Пользователь {user_id} не имеет запросов для удаления")
            return
        request_ids = [r[0] for r in requests]
        await message.answer(
            "Ўчириш учун сўров танланг:",
            reply_markup=make_keyboard(request_ids, columns=2, with_back=True)
        )
        await state.set_state(DeleteRequest.delete_request)
        logger.info(f"Пользователь {user_id} начал удаление запроса")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных в requests_delete_start для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных в requests_delete_start для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Сўровларни юклашда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в requests_delete_start для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Непредвиденная ошибка в requests_delete_start для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_delete_request(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    item_id = message.text
    logger.debug(f"process_delete_request: user_id={user_id}, text='{item_id}'")
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT unique_id FROM requests WHERE user_id = ? AND status = 'active'",
                (user_id,)
            ) as cursor:
                requests = [row[0] for row in await cursor.fetchall()]
        if item_id == "Орқага":
            await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            logger.info(f"Пользователь {user_id} вернулся в меню сўровларим")
            return
        if item_id not in requests:
            await message.answer(
                "Илтимос, рўйхатдан сўров танланг:",
                reply_markup=make_keyboard(requests, columns=2, with_back=True)
            )
            logger.warning(f"Некорректный выбор запроса для удаления: {item_id}")
            return
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT channel_message_id FROM requests WHERE unique_id = ? AND user_id = ? AND status = 'active'",
                (item_id, user_id)
            ) as cursor:
                request = await cursor.fetchone()
            if not request:
                await message.answer(
                    f"Сўров {item_id} топилмади ёки у сизга тегишли эмас!",
                    reply_markup=get_requests_menu()
                )
                await state.set_state(RequestsMenu.menu)
                logger.warning(f"Запрос {item_id} не найден для user_id={user_id}")
                return
            if request[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=request[0])
                    logger.debug(f"Сообщение {request[0]} удалено из канала")
                except TelegramBadRequest as e:
                    if "message can't be deleted" in str(e) or "message to delete not found" in str(e):
                        logger.warning(f"Сообщение {request[0]} для запроса {item_id} уже удалено или не найдено: {e}")
                        await conn.execute(
                            "UPDATE requests SET channel_message_id = NULL WHERE unique_id = ? AND user_id = ?",
                            (item_id, user_id)
                        )
                        await conn.commit()
                        logger.info(f"Сброшено channel_message_id для запроса {item_id}")
                    else:
                        logger.error(f"Не удалось удалить сообщение {request[0]} для запроса {item_id}: {e}", exc_info=True)
                        await notify_admin(f"Не удалось удалить сообщение {request[0]} для запроса {item_id}: {str(e)}", bot=message.bot)
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {request[0]}: {e}", exc_info=True)
                    await notify_admin(f"Не удалось удалить сообщение {request[0]} для запроса {item_id}: {str(e)}", bot=message.bot)
            await conn.execute(
                "UPDATE requests SET status = 'deleted' WHERE unique_id = ? AND user_id = ?",
                (item_id, user_id)
            )
            await conn.commit()
        await message.answer(f"Сўров {item_id} ўчирилди!", reply_markup=get_requests_menu())
        await state.set_state(RequestsMenu.menu)
        logger.info(f"Пользователь {user_id} удалил запрос {item_id}")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при удалении запроса {item_id or 'неизвестно'}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных при удалении запроса для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Сўровни ўчиришда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в process_delete_request для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Непредвиденная ошибка в process_delete_request для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def close_request_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"close_request_start: user_id={user_id}, text='{message.text}'")
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='requests'"
            ) as cursor:
                if not await cursor.fetchone():
                    raise aiosqlite.Error("Table 'requests' does not exist")
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
            f"{req[1]} - {req[2]} ({req[3]}), {req[4]} тонна, {req[5]:,.0f} сўм"
            for req in requests
        ]
        response = "Ёпиш учун сўров танланг:\n" + "\n".join(request_list)
        await message.answer(response, reply_markup=keyboard)
        await state.set_state(CloseRequest.select_request)
        await state.update_data(requests=requests)
        logger.info(f"Пользователь {user_id} начал закрытие запроса")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных в close_request_start для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных в close_request_start для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Сўровларни юклашда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в close_request_start для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Непредвиденная ошибка в close_request_start для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_close_request(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    unique_id = message.text
    logger.debug(f"process_close_request: user_id={user_id}, text='{unique_id}'")
    try:
        if unique_id == "Орқага":
            await message.answer("Менинг сўровларим:", reply_markup=get_requests_menu())
            await state.set_state(RequestsMenu.menu)
            logger.info(f"Пользователь {user_id} вернулся в меню сўровларим")
            return
        data = await state.get_data()
        requests = data.get("requests", [])
        selected_request = next((req for req in requests if req[1] == unique_id), None)
        if selected_request:
            await state.update_data(selected_request_id=selected_request[0], selected_unique_id=unique_id)
            await message.answer(
                "Якуний нарҳни киритинг (сўмда):",
                reply_markup=make_keyboard([], with_back=True)
            )
            await state.set_state(CloseRequest.awaiting_final_price)
            logger.info(f"Пользователь {user_id} выбрал запрос {unique_id} для закрытия")
        else:
            await message.answer(
                "Ното‘г‘ри танлов. Илтимос, рўйхатдан сўров танланг:",
                reply_markup=make_keyboard([req[1] for req in requests], columns=2, with_back=True)
            )
            logger.warning(f"Некорректный выбор запроса: {unique_id}")
    except Exception as e:
        logger.error(f"Ошибка в process_close_request для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в process_close_request для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

@buyer_only
async def process_final_price(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    final_price_str = message.text
    logger.debug(f"process_final_price: user_id={user_id}, text='{final_price_str}'")
    try:
        if final_price_str == "Орқага":
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
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
                f"{req[1]} - {req[2]} ({req[3]}), {req[4]} тонна, {req[5]:,.0f} сўм"
                for req in requests
            ]
            response = "Ёпиш учун сўров танланг:\n" + "\n".join(request_list)
            await message.answer(response, reply_markup=keyboard)
            await state.set_state(CloseRequest.select_request)
            await state.update_data(requests=requests)
            logger.info(f"Пользователь {user_id} вернулся к выбору запроса")
            return
        valid, final_price = await validate_number_minimal(final_price_str)
        if not valid or final_price <= 0 or final_price > 1_000_000_000:
            await message.answer(
                "Нарх мусбат рақам бўлиши ва 1 миллиард сумдан ошмаслиги керак:",
                reply_markup=make_keyboard([], with_back=True)
            )
            logger.warning(f"Некорректная финальная цена от user_id={user_id}: {final_price_str}")
            return
        data = await state.get_data()
        request_id = data.get("selected_request_id")
        unique_id = data.get("selected_unique_id")
        if not request_id or not unique_id:
            await message.answer(
                "Ошибка: запрос не выбран. Начните заново.",
                reply_markup=get_requests_menu()
            )
            await state.set_state(RequestsMenu.menu)
            logger.warning(f"Отсутствует request_id или unique_id для user_id={user_id}")
            return
        archived_at = format_uz_datetime(datetime.now(pytz.UTC))
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT channel_message_id FROM requests WHERE id = ? AND user_id = ? AND status = 'active'",
                (request_id, user_id)
            ) as cursor:
                request = await cursor.fetchone()
            if not request:
                await message.answer(
                    f"Сўров {unique_id} топилмади!",
                    reply_markup=get_requests_menu()
                )
                await state.set_state(RequestsMenu.menu)
                logger.warning(f"Запрос {unique_id} не найден для user_id={user_id}")
                return
            if request[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=request[0])
                    logger.debug(f"Сообщение {request[0]} удалено из канала")
                except TelegramBadRequest as e:
                    if "message can't be deleted" in str(e) or "message to delete not found" in str(e):
                        logger.warning(f"Сообщение {request[0]} для запроса {unique_id} уже удалено или не найдено: {e}")
                        await conn.execute(
                            "UPDATE requests SET channel_message_id = NULL WHERE unique_id = ? AND user_id = ?",
                            (unique_id, user_id)
                        )
                        await conn.commit()
                        logger.info(f"Сброшено channel_message_id для запроса {unique_id}")
                    else:
                        logger.error(f"Не удалось удалить сообщение {request[0]} для запроса {unique_id}: {e}", exc_info=True)
                        await notify_admin(f"Не удалось удалить сообщение {request[0]} для запроса {unique_id}: {str(e)}", bot=message.bot)
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {request[0]}: {e}", exc_info=True)
                    await notify_admin(f"Не удалось удалить сообщение {request[0]} для запроса {unique_id}: {str(e)}", bot=message.bot)
            await conn.execute(
                "UPDATE requests SET status = 'archived', final_price = ?, archived_at = ? WHERE id = ? AND user_id = ?",
                (final_price, archived_at, request_id, user_id)
            )
            await conn.commit()
        await message.answer(
            f"✅ Сўров {unique_id} архивига ўтказилди. Якуний нарх: {final_price:,.0f} сўм.",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)
        logger.info(f"Пользователь {user_id} закрыл запрос {unique_id} с ценой {final_price} сўм")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при закрытии запроса {unique_id or 'неизвестно'}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных при закрытии запроса для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Сўровни ёпишда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в process_final_price для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Непредвиденная ошибка в process_final_price для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_requests_menu()
        )
        await state.set_state(RequestsMenu.menu)

async def notify_next_pending_item(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"notify_next_pending_item: user_id={user_id}")
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT unique_id, type FROM pending_items WHERE user_id = ? ORDER BY created_at LIMIT 1",
                (user_id,)
            ) as cursor:
                item = await cursor.fetchone()
            if not item:
                logger.debug(f"Нет незавершённых элементов для user_id={user_id}")
                return
            unique_id, item_type = item
            table = "products" if item_type == "product" else "requests"
            menu_func = get_ads_menu if item_type == "product" else get_requests_menu
            async with conn.execute(
                f"SELECT category, region FROM {table} WHERE unique_id = ?",
                (unique_id,)
            ) as cursor:
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
            await conn.execute(
                "DELETE FROM pending_items WHERE unique_id = ? AND user_id = ?",
                (unique_id, user_id)
            )
            await conn.commit()
        logger.info(f"Уведомление о незавершённом {item_type} {unique_id} отправлено пользователю {user_id}")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных в notify_next_pending_item для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных в notify_next_pending_item для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_main_menu(BUYER_ROLE)
        )
        await state.set_state(RequestsMenu.menu)
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в notify_next_pending_item для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Непредвиденная ошибка в notify_next_pending_item для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_main_menu(BUYER_ROLE)
        )
        await state.set_state(RequestsMenu.menu)

def register_handlers(dp: Dispatcher):
    logger.info("Регистрация обработчиков запросов")
    dp.message.register(handle_requests_back_button, RequestsMenu.menu, F.text == "Орқага")
    dp.message.register(process_requests_menu, RequestsMenu.menu)
    dp.message.register(send_request_start, F.text == "Сўров юбориш")
    dp.message.register(requests_menu, F.text == "Менинг сўровларим")
    dp.message.register(process_category, SendRequest.category)
    dp.message.register(process_sort, SendRequest.sort)
    dp.message.register(process_region, SendRequest.region)
    dp.message.register(process_volume_ton, SendRequest.volume_ton)
    dp.message.register(process_price, SendRequest.price)
    dp.message.register(requests_list, F.text == "Сўровлар рўйхати")
    dp.message.register(requests_delete_start, F.text == "Сўровни ўчириш")
    dp.message.register(process_delete_request, DeleteRequest.delete_request)
    dp.message.register(close_request_start, F.text == "Сўровни ёпиш")
    dp.message.register(process_close_request, CloseRequest.select_request)
    dp.message.register(process_final_price, CloseRequest.awaiting_final_price)
    logger.info("Обработчики запросов зарегистрированы")