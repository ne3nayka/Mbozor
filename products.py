import aiosqlite
import logging
import pytz
from aiogram import Dispatcher, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from config import DB_NAME, SELLER_ROLE, CATEGORIES, MAX_SORT_LENGTH, MAX_VOLUME_TON, MAX_PRICE, MAX_PHOTOS, CHANNEL_ID, ADMIN_IDS, ADMIN_ROLE, DB_TIMEOUT
from user_requests import notify_next_pending_item
from utils import check_role, make_keyboard, validate_number_minimal, validate_sort, check_subscription, parse_uz_datetime, format_uz_datetime, has_pending_items, get_ads_menu, get_main_menu, notify_admin
from database import generate_item_id
from regions import get_all_regions
from datetime import datetime, timedelta
from functools import wraps

logger = logging.getLogger(__name__)

MAX_PHOTOS_STRING_LENGTH = 1000
MAX_FILE_ID_LENGTH = 100

class AddProduct(StatesGroup):
    category = State()
    sort = State()
    region = State()
    volume_ton = State()
    price = State()
    photos = State()

class DeleteProduct(StatesGroup):
    delete_product = State()

class CloseProduct(StatesGroup):
    select_item = State()
    final_price = State()

class AdsMenu(StatesGroup):
    menu = State()

def seller_only(handler):
    @wraps(handler)
    async def wrapper(message: types.Message, state: FSMContext, *args, **kwargs):
        user_id = message.from_user.id
        current_state = await state.get_state()
        logger.info(f"seller_only: user_id={user_id}, text='{message.text}', state={current_state}")
        try:
            logger.debug(f"Checking pending items for user_id={user_id}")
            has_pending = await has_pending_items(user_id)
            if has_pending:
                logger.info(f"User {user_id} has pending items, notifying")
                await notify_next_pending_item(message, state)
                return

            logger.debug(f"Checking subscription for user_id={user_id}")
            success, bot_active, is_subscribed = await check_subscription(message.bot, user_id, state.storage)
            logger.debug(f"check_subscription: user_id={user_id}, success={success}, bot_active={bot_active}, is_subscribed={is_subscribed}")
            if not is_subscribed:
                logger.info(f"User {user_id} has no active subscription")
                await message.answer(
                    "Сизнинг обунангиз тугади.",
                    reply_markup=make_keyboard(["Обуна"], one_time=True)
                )
                await state.set_state("Registration:subscription")
                return

            logger.debug(f"Checking role for user_id={user_id}")
            allowed, role = await check_role(message, allow_unregistered=False)
            logger.info(f"Role check: user_id={user_id}, allowed={allowed}, role={role}")
            if not allowed or (role != SELLER_ROLE and role != ADMIN_ROLE):
                logger.info(f"User {user_id} is not a seller or admin, role={role}")
                await message.answer(
                    "Бу буйруқ фақат сотувчилар учун!",
                    reply_markup=get_main_menu(SELLER_ROLE)
                )
                await state.clear()
                return

            logger.debug(f"seller_only passed for user_id={user_id}, role={role}, has_pending={has_pending}, bot_active={bot_active}, is_subscribed={is_subscribed}")
            return await handler(message, state, *args, role=role, **kwargs)
        except aiosqlite.Error as e:
            logger.error(f"Database error in seller_only for user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Database error in seller_only for user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_ads_menu()
            )
            await state.set_state(AdsMenu.menu)
            return
        except Exception as e:
            logger.error(f"Unexpected error in seller_only for user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Unexpected error in seller_only for user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_ads_menu()
            )
            await state.set_state(AdsMenu.menu)
            return
    return wrapper

@seller_only
async def ads_menu(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.info(f"ads_menu called: user_id={user_id}, text='{message.text}', role={role}")
    try:
        await state.clear()
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu())
        await state.set_state(AdsMenu.menu)
        logger.info(f"User {user_id} entered ads menu")
    except Exception as e:
        logger.error(f"Error in ads_menu for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in ads_menu for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_main_menu(role)
        )
        await state.clear()

@seller_only
async def handle_ads_back_button(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"handle_ads_back_button: user_id={user_id}, text='{message.text}', state={await state.get_state()}")
    if message.text != "Орқага":
        logger.debug(f"handle_ads_back_button skipped: text='{message.text}'")
        return
    try:
        success, bot_active, is_subscribed = await check_subscription(message.bot, user_id, state.storage)
        logger.debug(f"Subscription check in handle_ads_back_button: user_id={user_id}, success={success}, bot_active={bot_active}, is_subscribed={is_subscribed}")
        if not is_subscribed and role != ADMIN_ROLE:
            await message.answer(
                "Сизда фаол обуна мавжуд эмас. 'Обуна' тугмасини босинг:",
                reply_markup=make_keyboard(["Обуна"], one_time=True)
            )
            await state.set_state("Registration:subscription")
            logger.info(f"User {user_id} redirected to subscription from handle_ads_back_button")
            return
        await message.answer("Асосий меню:", reply_markup=get_main_menu(role))
        await state.clear()
        logger.info(f"User {user_id} returned to main menu from ads menu")
    except Exception as e:
        logger.error(f"Error in handle_ads_back_button for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in handle_ads_back_button for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_ads_menu(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    text = message.text.strip()
    current_state = await state.get_state()
    logger.debug(f"process_ads_menu: user_id={user_id}, text='{text}', raw_text='{message.text}', state={current_state}")
    if text.startswith('/'):
        logger.debug(f"Ignoring command in process_ads_menu: user_id={user_id}, text='{text}'")
        return
    if current_state != AdsMenu.menu.state:
        logger.debug(f"Skipping process_ads_menu: user_id={user_id}, text='{text}', state={current_state} is not AdsMenu:menu")
        return
    options = {
        "Эълон қўшиш": (None, add_product_start, "started adding an ad"),
        "Эълонлар рўйхати": (None, ads_list, "viewed ads list"),
        "Эълонни ўчириш": (None, ads_delete_start, "started ad deletion"),
        "Эълонни ёпиш": (None, close_product_start, "started ad closure")
    }
    option = options.get(text)
    if option:
        logger.debug(f"process_ads_menu: user_id={user_id}, selected option='{text}'")
        try:
            if option[1]:
                logger.info(f"Calling {option[1].__name__} for user_id={user_id}")
                await option[1](message, state, role=role)
            logger.info(f"User {user_id} {option[2]}")
        except aiosqlite.Error as e:
            logger.error(f"Database error in process_ads_menu for user_id={user_id}, option={text}: {e}", exc_info=True)
            await notify_admin(f"Database error in process_ads_menu for user_id={user_id}, option={text}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_ads_menu()
            )
            await state.set_state(AdsMenu.menu)
        except Exception as e:
            logger.error(f"Unexpected error in process_ads_menu for user_id={user_id}, option={text}: {e}", exc_info=True)
            await notify_admin(f"Unexpected error in process_ads_menu for user_id={user_id}, option={text}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_ads_menu()
            )
            await state.set_state(AdsMenu.menu)
    else:
        logger.warning(f"Invalid option in process_ads_menu: user_id={user_id}, text='{text}', raw_text='{message.text}'")
        await message.answer(f"Илтимос, менюдан танланг (получен: '{text}'):", reply_markup=get_ads_menu())
        await state.set_state(AdsMenu.menu)

@seller_only
async def ads_list(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"ads_list: user_id={user_id}, text='{message.text}', state={await state.get_state()}")
    try:
        logger.info(f"Attempting to fetch active products for user_id={user_id}")
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
            ) as cursor:
                table_exists = await cursor.fetchone()
                if not table_exists:
                    logger.error(f"Table 'products' does not exist for user_id={user_id}")
                    raise aiosqlite.Error("Table 'products' does not exist")
            async with conn.execute(
                "SELECT unique_id, category, region, sort, volume_ton, price, photos, created_at "
                "FROM products WHERE user_id = ? AND status = 'active'", (user_id,)
            ) as cursor:
                products = await cursor.fetchall()
        logger.debug(f"Fetched products: {products}")
        if not products:
            await message.answer("Сизда эълонлар йўқ.", reply_markup=get_ads_menu())
            await state.set_state(AdsMenu.menu)
            logger.info(f"User {user_id} has no active ads")
            return
        now = datetime.now(pytz.timezone('Asia/Tashkent'))
        for unique_id, category, region, sort, volume_ton, price, photos, created_at in products:
            try:
                created_at_dt = parse_uz_datetime(created_at)
                if not created_at_dt:
                    logger.warning(f"Invalid creation date for ad {unique_id}: {created_at}")
                    continue
                expiration = created_at_dt + timedelta(hours=24)
                status = "Фаол" if now < expiration else "Муддати тугаган"
                info = (
                    f"Эълон {unique_id}\n"
                    f"Категория: {category}\n"
                    f"Вилоят: {region}\n"
                    f"Сорт: {sort}\n"
                    f"Ҳажм: {volume_ton} тонна\n"
                    f"Нарх: {price:,.0f} сўм\n"
                    f"Ҳолат: {status} ({format_uz_datetime(expiration)} гача)"
                )
                photos_list = photos.split(",") if photos else []
                if photos_list:
                    media = [types.InputMediaPhoto(media=photo, caption=info if i == 0 else None) for i, photo in enumerate(photos_list)]
                    await message.answer_media_group(media=media)
                else:
                    await message.answer(info)
            except Exception as e:
                logger.error(f"Error processing ad {unique_id} for user_id={user_id}: {e}", exc_info=True)
                continue
        await state.set_state(AdsMenu.menu)
        logger.info(f"User {user_id} viewed ads list")
    except aiosqlite.Error as e:
        logger.error(f"Database error in ads_list for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Database error in ads_list for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонлар рўйхатини юклашда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in ads_list for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in ads_list for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def add_product_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"add_product_start: user_id={user_id}, text='{message.text}', state={await state.get_state()}")
    try:
        await state.clear()
        await message.answer("Маҳсулот турини танланг:", reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
        await state.set_state(AddProduct.category)
        logger.info(f"User {user_id} started adding an ad")
    except Exception as e:
        logger.error(f"Error in add_product_start for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in add_product_start for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_category(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    category = message.text
    logger.debug(f"process_category: user_id={user_id}, category='{category}', state={await state.get_state()}")
    try:
        if category == "Орқага":
            await message.answer("Асосий меню:", reply_markup=get_main_menu(role))
            await state.clear()
            logger.info(f"User {user_id} returned to main menu from category selection")
            return
        if category not in CATEGORIES:
            await message.answer(
                "Илтимос, категориядан танланг:",
                reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True)
            )
            logger.warning(f"Invalid category from user_id={user_id}: {category}")
            return
        await state.update_data(category=category)
        await message.answer("Маҳсулот сортни киритинг:", reply_markup=make_keyboard([], with_back=True))
        await state.set_state(AddProduct.sort)
        logger.debug(f"User {user_id} selected category: {category}")
    except Exception as e:
        logger.error(f"Error in process_category for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in process_category for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_sort(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    sort = message.text
    logger.debug(f"process_sort: user_id={user_id}, sort='{sort}', state={await state.get_state()}")
    try:
        if sort == "Орқага":
            await message.answer(
                "Маҳсулот турини танланг:",
                reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True)
            )
            await state.set_state(AddProduct.category)
            logger.info(f"User {user_id} returned to category selection from sort")
            return
        if not await validate_sort(sort):
            await message.answer(f"Сорт {MAX_SORT_LENGTH} белгидан узун бўлмаслиги керак:")
            logger.warning(f"Sort too long from user_id={user_id}: {len(sort)} characters")
            return
        await state.update_data(sort=sort)
        await message.answer(
            "Вилоятни танланг:",
            reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True)
        )
        await state.set_state(AddProduct.region)
        logger.debug(f"User {user_id} entered sort: {sort}")
    except Exception as e:
        logger.error(f"Error in process_sort for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in process_sort for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_region(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    region = message.text
    logger.debug(f"process_region: user_id={user_id}, region='{region}', state={await state.get_state()}")
    try:
        if region == "Орқага":
            await message.answer(
                "Маҳсулот сортни киритинг:",
                reply_markup=make_keyboard([], with_back=True)
            )
            await state.set_state(AddProduct.sort)
            logger.info(f"User {user_id} returned to sort input from region")
            return
        if region not in get_all_regions():
            await message.answer(
                "Илтимос, рўйхатдан вилоят танланг:",
                reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True)
            )
            logger.warning(f"Invalid region from user_id={user_id}: {region}")
            return
        await state.update_data(region=region)
        await message.answer(
            "Маҳсулот ҳажмни киритинг (тоннада):",
            reply_markup=make_keyboard([], with_back=True)
        )
        await state.set_state(AddProduct.volume_ton)
        logger.debug(f"User {user_id} selected region: {region}")
    except Exception as e:
        logger.error(f"Error in process_region for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in process_region for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_volume_ton(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    volume = message.text
    logger.debug(f"process_volume_ton: user_id={user_id}, volume='{volume}', state={await state.get_state()}")
    try:
        if volume == "Орқага":
            await message.answer(
                "Вилоятни танланг:",
                reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True)
            )
            await state.set_state(AddProduct.region)
            logger.info(f"User {user_id} returned to region selection from volume")
            return
        valid, volume_ton = await validate_number_minimal(volume)
        if not valid or volume_ton <= 0 or volume_ton > MAX_VOLUME_TON:
            await message.answer(f"Ҳажм мусбат рақам бўлиши ва {MAX_VOLUME_TON} тоннадан ошмаслиги керак:")
            logger.warning(f"Invalid volume from user_id={user_id}: {volume}")
            return
        await state.update_data(volume_ton=volume_ton)
        await message.answer(
            "Маҳсулот нархини киритинг (сўмда):",
            reply_markup=make_keyboard([], with_back=True)
        )
        await state.set_state(AddProduct.price)
        logger.debug(f"User {user_id} entered volume: {volume_ton} tons")
    except Exception as e:
        logger.error(f"Error in process_volume_ton for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in process_volume_ton for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_price(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    price = message.text
    logger.debug(f"process_price: user_id={user_id}, price='{price}', state={await state.get_state()}")
    try:
        if price == "Орқага":
            await message.answer(
                "Маҳсулот ҳажмни киритинг (тоннада):",
                reply_markup=make_keyboard([], with_back=True)
            )
            await state.set_state(AddProduct.volume_ton)
            logger.info(f"User {user_id} returned to volume input from price")
            return
        valid, price_value = await validate_number_minimal(price)
        if not valid or price_value <= 0 or price_value > MAX_PRICE:
            await message.answer(f"Нарх мусбат рақам бўлиши ва {MAX_PRICE} сўмдан ошмаслиги керак:")
            logger.warning(f"Invalid price from user_id={user_id}: {price}")
            return
        await state.update_data(price=price_value)
        await message.answer(
            f"1 дан {MAX_PHOTOS} тагача расм юборинг. Тайёр бўлса, 'Тайёр' тугмасини босинг:",
            reply_markup=make_keyboard(["Тайёр", "Орқага"], one_time=True)
        )
        await state.set_state(AddProduct.photos)
        logger.debug(f"User {user_id} entered price: {price_value} sʻom")
    except Exception as e:
        logger.error(f"Error in process_price for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in process_price for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_photos(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"process_photos: user_id={user_id}, text='{message.text}', has_photo={message.photo is not None}, state={await state.get_state()}")
    data = await state.get_data()
    current_photos = data.get("photos", [])
    try:
        if message.text == "Орқага":
            await message.answer(
                "Маҳсулот нархини киритинг (сўмда):",
                reply_markup=make_keyboard([], with_back=True)
            )
            await state.set_state(AddProduct.price)
            logger.info(f"User {user_id} returned to price input from photos")
            return
        if message.text == "Тайёр":
            if not current_photos:
                await message.answer(
                    "Камида 1 та расм юборинг:",
                    reply_markup=make_keyboard(["Орқага"], one_time=True)
                )
                logger.warning(f"No photos provided by user_id={user_id}")
                return
        elif message.photo:
            file_info = await message.bot.get_file(message.photo[-1].file_id)
            if file_info.file_size > 5 * 1024 * 1024:
                await message.answer(
                    "Файл жуда катта (макс. 5 МБ)!",
                    reply_markup=make_keyboard(["Тайёр", "Орқага"], one_time=True)
                )
                logger.warning(f"File too large from user_id={user_id}: {file_info.file_size} bytes")
                return
            new_photo = message.photo[-1].file_id
            if len(new_photo) > MAX_FILE_ID_LENGTH:
                await message.answer(
                    f"Расм file_id жуда узун!",
                    reply_markup=make_keyboard(["Тайёр", "Орқага"], one_time=True)
                )
                logger.warning(f"Photo file_id too long from user_id={user_id}")
                return
            if len(current_photos) >= MAX_PHOTOS:
                await message.answer(
                    f"Максимал {MAX_PHOTOS} та расм. 'Тайёр' тугмасини босинг:",
                    reply_markup=make_keyboard(["Тайёр", "Орқага"], one_time=True)
                )
                logger.warning(f"Maximum photo limit reached for user_id={user_id}: {MAX_PHOTOS}")
                return
            current_photos.append(new_photo)
            await state.update_data(photos=current_photos)
            await message.answer(
                f"Расм қабул қилинди. {len(current_photos)}/{MAX_PHOTOS}. Яна расм ёки 'Тайёр':",
                reply_markup=make_keyboard(["Тайёр", "Орқага"], one_time=True)
            )
            logger.debug(f"Photo added by user {user_id}, total photos: {len(current_photos)}")
            return
        else:
            await message.answer(
                "Расм юборинг ёки 'Тайёр' тугмасини босинг:",
                reply_markup=make_keyboard(["Тайёр", "Орқага"], one_time=True)
            )
            logger.warning(f"Invalid input in process_photos from user_id={user_id}: {message.text}")
            return

        photos = current_photos[:MAX_PHOTOS]
        photos_str = ",".join(photos)
        if len(photos_str) > MAX_PHOTOS_STRING_LENGTH:
            await message.answer("Жуда кўп расм юборилди!", reply_markup=get_ads_menu())
            await state.set_state(AdsMenu.menu)
            logger.error(f"Photos string too long for user_id={user_id}: {len(photos_str)}")
            return

        item_id = await generate_item_id("products", "E")
        created_at = format_uz_datetime(datetime.now(pytz.timezone('Asia/Tashkent')))
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute(
                "INSERT INTO products (unique_id, user_id, category, region, sort, volume_ton, price, photos, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (item_id, user_id, data["category"], data["region"], data["sort"], data["volume_ton"], data["price"],
                 photos_str, created_at)
            )
            await conn.commit()

        info = (
            f"Эълон: {item_id}\n"
            f"Категория: {data['category']}\n"
            f"Вилоят: {data['region']}\n"
            f"Сорт: {data['sort']}\n"
            f"Ҳажм: {data['volume_ton']} тонна\n"
            f"Нарх: {data['price']:,.0f} сўм"
        )
        media = [types.InputMediaPhoto(media=photo, caption=info if i == 0 else None) for i, photo in enumerate(photos)]

        channel_msg = None
        try:
            channel_msg = await message.bot.send_media_group(chat_id=CHANNEL_ID, media=media)
            message_ids = ",".join(str(msg.message_id) for msg in channel_msg)
            logger.debug(f"Ad {item_id} sent to channel, message_ids={message_ids}")
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                await conn.execute(
                    "UPDATE products SET channel_message_ids = ? WHERE unique_id = ?",
                    (message_ids, item_id)
                )
                await conn.commit()
        except Exception as e:
            logger.error(f"Error sending ad {item_id} to channel for user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Error sending ad {item_id} to channel for user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer("Эълон каналга юборилмади. Админ билан боғланинг (@ad_mbozor).", reply_markup=get_ads_menu())
            await state.set_state(AdsMenu.menu)
            return

        await message.answer(
            f"Сизнинг эълонингиз юборилди. Эълон рақами {item_id}. Танишиш учун Эълонлар доскаси ёки <a href=\"https://t.me/+6WXzyGqqotgzODM6\">Каналга</a> ўтинг.",
            reply_markup=get_ads_menu(),
            parse_mode="HTML"
        )
        await state.set_state(AdsMenu.menu)
        logger.info(f"Ad {item_id} successfully added by user {user_id}")
    except aiosqlite.Error as e:
        logger.error(f"Database error in process_photos for user_id={user_id}, item_id={item_id or 'unknown'}: {e}", exc_info=True)
        await notify_admin(f"Database error in process_photos for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонни қўшишда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in process_photos for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in process_photos for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def ads_delete_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"ads_delete_start: user_id={user_id}, text='{message.text}', current_state={await state.get_state()}")
    try:
        logger.info(f"Attempting to fetch active products for user_id={user_id}")
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
            ) as cursor:
                table_exists = await cursor.fetchone()
                if not table_exists:
                    logger.error(f"Table 'products' does not exist for user_id={user_id}")
                    raise aiosqlite.Error("Table 'products' does not exist")
            async with conn.execute(
                "SELECT unique_id FROM products WHERE user_id = ? AND status = 'active'", (user_id,)
            ) as cursor:
                products = await cursor.fetchall()
        logger.debug(f"Fetched products: {products}")
        if not products:
            await message.answer("Ўчириш учун эълонлар йўқ.", reply_markup=get_ads_menu())
            await state.set_state(AdsMenu.menu)
            logger.info(f"User {user_id} has no ads to delete")
            return
        product_ids = [p[0] for p in products]
        logger.debug(f"Product IDs available for deletion: {product_ids}")
        await message.answer(
            "Ўчириш учун эълон танланг:",
            reply_markup=make_keyboard(product_ids, columns=2, with_back=True)
        )
        await state.update_data(product_ids=product_ids)
        await state.set_state(DeleteProduct.delete_product)
        current_state = await state.get_state()
        logger.info(f"User {user_id} started ad deletion process, state set to {current_state}")
    except aiosqlite.Error as e:
        logger.error(f"Database error in ads_delete_start for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Database error in ads_delete_start for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонлар рўйхатини юклашда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)
        current_state = await state.get_state()
        logger.error(f"State after database error: {current_state}")
    except Exception as e:
        logger.error(f"Unexpected error in ads_delete_start for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in ads_delete_start for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)
        current_state = await state.get_state()
        logger.error(f"State after unexpected error: {current_state}")

@seller_only
async def process_delete_product(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    item_id = message.text.strip()
    logger.debug(f"process_delete_product: user_id={user_id}, text='{item_id}', state={await state.get_state()}")
    try:
        data = await state.get_data()
        product_ids = data.get("product_ids", [])
        logger.debug(f"Available product IDs for user_id={user_id}: {product_ids}")
        if item_id == "Орқага":
            await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu())
            await state.set_state(AdsMenu.menu)
            logger.info(f"User {user_id} returned to ads menu from deletion")
            return
        if item_id not in product_ids:
            await message.answer(
                f"Илтимос, рўйхатдан эълон танланг (получен: '{item_id}'):",
                reply_markup=make_keyboard(product_ids, columns=2, with_back=True)
            )
            logger.warning(f"Invalid ad selection for deletion by user_id={user_id}: {item_id}")
            return
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT channel_message_ids, channel_message_id FROM products WHERE unique_id = ? AND user_id = ? AND status = 'active'",
                (item_id, user_id)
            ) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(
                    f"Эълон {item_id} топилмади ёки у сизга тегишли эмас!",
                    reply_markup=get_ads_menu()
                )
                await state.set_state(AdsMenu.menu)
                logger.warning(f"User {user_id} tried to delete non-existent/unauthorized ad {item_id}")
                return
            if product[0]:  # channel_message_ids
                try:
                    message_ids = product[0].split(",")
                    for msg_id in message_ids:
                        try:
                            await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=int(msg_id))
                            logger.debug(f"Message {msg_id} deleted from channel {CHANNEL_ID}")
                        except TelegramBadRequest as e:
                            if "message can't be deleted" in str(e) or "message to delete not found" in str(e):
                                logger.warning(f"Message {msg_id} for ad {item_id} already deleted or not found: {e}")
                            else:
                                logger.error(f"Failed to delete message {msg_id} from channel for ad {item_id}: {e}", exc_info=True)
                                await notify_admin(f"Failed to delete message {msg_id} from channel for ad {item_id}: {str(e)}", bot=message.bot)
                except Exception as e:
                    logger.warning(f"Failed to delete messages {product[0]} from channel: {e}", exc_info=True)
                    await notify_admin(f"Failed to delete messages {product[0]} from channel for ad {item_id}: {str(e)}", bot=message.bot)
            elif product[1]:  # channel_message_id (для старых объявлений)
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=product[1])
                    logger.debug(f"Legacy message {product[1]} deleted from channel {CHANNEL_ID}")
                except TelegramBadRequest as e:
                    if "message can't be deleted" in str(e) or "message to delete not found" in str(e):
                        logger.warning(f"Legacy message {product[1]} for ad {item_id} already deleted or not found: {e}")
                        await conn.execute(
                            "UPDATE products SET channel_message_id = NULL WHERE unique_id = ? AND user_id = ?",
                            (item_id, user_id)
                        )
                        await conn.commit()
                        logger.info(f"Reset channel_message_id for ad {item_id}")
                    else:
                        logger.error(f"Failed to delete legacy message {product[1]} from channel for ad {item_id}: {e}", exc_info=True)
                        await notify_admin(f"Failed to delete legacy message {product[1]} from channel for ad {item_id}: {str(e)}", bot=message.bot)
            await conn.execute(
                "UPDATE products SET status = 'deleted' WHERE unique_id = ? AND user_id = ?",
                (item_id, user_id)
            )
            await conn.commit()
        await message.answer(f"Эълон {item_id} ўчирилди!", reply_markup=get_ads_menu())
        await state.set_state(AdsMenu.menu)
        logger.info(f"User {user_id} successfully deleted ad {item_id}")
    except aiosqlite.Error as e:
        logger.error(f"Database error in process_delete_product for user_id={user_id}, item_id={item_id or 'unknown'}: {e}", exc_info=True)
        await notify_admin(f"Database error in process_delete_product for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонни ўчиришда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in process_delete_product for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in process_delete_product for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def close_product_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"close_product_start: user_id={user_id}, text='{message.text}', current_state={await state.get_state()}")
    try:
        logger.info(f"Attempting to fetch active products for user_id={user_id}")
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
            ) as cursor:
                table_exists = await cursor.fetchone()
                if not table_exists:
                    logger.error(f"Table 'products' does not exist for user_id={user_id}")
                    raise aiosqlite.Error("Table 'products' does not exist")
            async with conn.execute(
                "SELECT unique_id, category, sort, volume_ton, price FROM products WHERE user_id = ? AND status = 'active'",
                (user_id,)
            ) as cursor:
                products = await cursor.fetchall()
        logger.debug(f"Fetched products: {products}")
        if not products:
            await message.answer("Ёпиш учун эълонлар йўқ.", reply_markup=get_ads_menu())
            await state.set_state(AdsMenu.menu)
            logger.info(f"User {user_id} has no ads to close")
            return
        product_ids = [p[0] for p in products]
        logger.debug(f"Product IDs available for closure: {product_ids}")
        product_list = "Ёпиш учун эълонни танланг:\n"
        for unique_id, category, sort, volume_ton, price in products:
            product_list += f"{unique_id} - {category} ({sort}), {volume_ton} тонна, {price:,.0f} сўм\n"
        await message.answer(product_list, reply_markup=make_keyboard(product_ids, columns=2, with_back=True))
        await state.update_data(products=products)
        await state.set_state(CloseProduct.select_item)
        current_state = await state.get_state()
        logger.info(f"User {user_id} started ad closure process, state set to {current_state}")
    except aiosqlite.Error as e:
        logger.error(f"Database error in close_product_start for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Database error in close_product_start for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонлар рўйхатини юклашда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)
        current_state = await state.get_state()
        logger.error(f"State after database error: {current_state}")
    except Exception as e:
        logger.error(f"Unexpected error in close_product_start for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in close_product_start for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)
        current_state = await state.get_state()
        logger.error(f"State after unexpected error: {current_state}")

@seller_only
async def process_close_product_select(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    item_id = message.text.strip()
    logger.debug(f"process_close_product_select: user_id={user_id}, text='{item_id}', state={await state.get_state()}")
    try:
        if item_id == "Орқага":
            await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu())
            await state.set_state(AdsMenu.menu)
            logger.info(f"User {user_id} returned to ads menu from closure selection")
            return
        data = await state.get_data()
        products = data.get("products", [])
        product_ids = [p[0] for p in products]
        logger.debug(f"Available product IDs for user_id={user_id}: {product_ids}")
        if item_id not in product_ids:
            await message.answer(
                f"Эълон {item_id} топилмади! Илтимос, рўйхатдан танланг:",
                reply_markup=make_keyboard(product_ids, columns=2, with_back=True)
            )
            await state.set_state(CloseProduct.select_item)
            logger.warning(f"Ad {item_id} not found for user_id={user_id}")
            return
        await state.update_data(unique_id=item_id)
        await message.answer(
            "Якуний нархни киритинг (сўмда):",
            reply_markup=make_keyboard([], with_back=True)
        )
        await state.set_state(CloseProduct.final_price)
        logger.debug(f"User {user_id} selected ad {item_id} for closure")
    except aiosqlite.Error as e:
        logger.error(f"Database error in process_close_product_select for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Database error in process_close_product_select for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in process_close_product_select for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in process_close_product_select for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

async def check_expired_products_without_final_price(bot: Bot, user_id: int) -> bool:
    """
    Проверяет, есть ли у пользователя истёкшие объявления без `final_price`.
    Если есть, отправляет уведомление и возвращает True.
    """
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            now = datetime.now(pytz.timezone('Asia/Tashkent'))
            expires_before = format_uz_datetime(now)
            async with conn.execute(
                """
                SELECT unique_id, created_at
                FROM products
                WHERE user_id = ? 
                AND status = 'active'
                AND final_price IS NULL
                AND datetime(created_at, '+48 hours') <= ?
                """,
                (user_id, expires_before)
            ) as cursor:
                expired_products = await cursor.fetchall()

        if expired_products:
            for product in expired_products:
                unique_id = product[0]
                try:
                    await bot.send_message(
                        user_id,
                        f"Эълон {unique_id} муддати (48 соат) тугади! Илтимос, якуний нархни киритинг. "
                        f"Бунгача ботдан фойдаланишингиз чекланади.",
                        reply_markup=make_keyboard(["Эълонни ёпинг"], one_time=True)
                    )
                    logger.info(f"Отправлено уведомление о вводе final_price для user_id={user_id}, unique_id={unique_id}")
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления user_id={user_id}, unique_id={unique_id}: {e}")
                    await notify_admin(f"Ошибка отправки уведомления user_id={user_id}, unique_id={unique_id}: {str(e)}", bot=bot)
            return True
        return False
    except aiosqlite.Error as e:
        logger.error(f"Ошибка проверки истёкших объявлений для user_id={user_id}: {e}")
        await notify_admin(f"Ошибка проверки истёкших объявлений для user_id={user_id}: {str(e)}", bot=bot)
        return False

@seller_only
async def process_close_product_final_price(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    logger.debug(f"process_close_product_final_price: user_id={user_id}, text='{message.text}', state={await state.get_state()}")
    try:
        if message.text == "Орқага":
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute(
                    "SELECT unique_id, category, sort, volume_ton, price FROM products WHERE user_id = ? AND status = 'active'",
                    (user_id,)
                ) as cursor:
                    products = await cursor.fetchall()
            if not products:
                await message.answer("Ёпиш учун эълонлар йўқ.", reply_markup=get_ads_menu())
                await state.set_state(AdsMenu.menu)
                logger.info(f"User {user_id} has no ads to close")
                return
            product_ids = [p[0] for p in products]
            product_list = "Ёпиш учун эълонни танланг:\n"
            for unique_id, category, sort, volume_ton, price in products:
                product_list += f"{unique_id} - {category} ({sort}), {volume_ton} тонна, {price:,.0f} сўм\n"
            await message.answer(product_list, reply_markup=make_keyboard(product_ids, columns=2, with_back=True))
            await state.update_data(products=products)
            await state.set_state(CloseProduct.select_item)
            logger.info(f"User {user_id} returned to ad selection for closure")
            return
        valid, final_price = await validate_number_minimal(message.text)
        if not valid or final_price <= 0 or final_price > MAX_PRICE:
            await message.answer(
                f"Нарх мусбат рақам бўлиши керак (макс. {MAX_PRICE} сўм):",
                reply_markup=make_keyboard([], with_back=True)
            )
            logger.warning(f"Invalid final price from user_id={user_id}: {message.text}")
            return
        data = await state.get_data()
        item_id = data.get("unique_id")
        if not item_id:
            await message.answer("Ошибка: эълон не выбран. Начните заново.", reply_markup=get_ads_menu())
            await state.set_state(AdsMenu.menu)
            logger.warning(f"Missing item_id in state for user_id={user_id}")
            return
        archived_at = format_uz_datetime(datetime.now(pytz.timezone('Asia/Tashkent')))
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                """
                SELECT channel_message_ids, photos, channel_message_id, created_at 
                FROM products 
                WHERE unique_id = ? AND user_id = ? AND status = 'active'
                """,
                (item_id, user_id)
            ) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(f"Эълон {item_id} топилмади!", reply_markup=get_ads_menu())
                await state.set_state(AdsMenu.menu)
                logger.warning(f"Ad {item_id} not found for user_id={user_id}")
                return
            logger.debug(f"Processing closure for ad {item_id}: channel_message_ids={product[0]}, photos={product[1]}, channel_message_id={product[2]}, created_at={product[3]}")
            created_at_dt = parse_uz_datetime(product[3])
            is_expired = created_at_dt and datetime.now(pytz.timezone('Asia/Tashkent')) >= created_at_dt + timedelta(hours=48)
            if product[0]:  # channel_message_ids
                try:
                    message_ids = product[0].split(",")
                    for msg_id in message_ids:
                        try:
                            await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=int(msg_id))
                            logger.debug(f"Message {msg_id} deleted from channel")
                        except TelegramBadRequest as e:
                            if "message can't be deleted" in str(e) or "message to delete not found" in str(e):
                                logger.warning(f"Message {msg_id} for ad {item_id} already deleted or not found: {e}")
                            else:
                                logger.error(f"Failed to delete message {msg_id} from channel for ad {item_id}: {e}", exc_info=True)
                                await notify_admin(f"Failed to delete message {msg_id} from channel for ad {item_id}: {str(e)}", bot=message.bot)
                except Exception as e:
                    logger.warning(f"Failed to delete messages {product[0]} from channel: {e}", exc_info=True)
                    await notify_admin(f"Failed to delete messages {product[0]} from channel for ad {item_id}: {str(e)}", bot=message.bot)
            elif product[2]:  # channel_message_id
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=product[2])
                    logger.debug(f"Legacy message {product[2]} deleted from channel {CHANNEL_ID}")
                except TelegramBadRequest as e:
                    if "message can't be deleted" in str(e) or "message to delete not found" in str(e):
                        logger.warning(f"Legacy message {product[2]} for ad {item_id} already deleted or not found: {e}")
                        await conn.execute(
                            "UPDATE products SET channel_message_id = NULL WHERE unique_id = ? AND user_id = ?",
                            (item_id, user_id)
                        )
                        await conn.commit()
                        logger.info(f"Reset channel_message_id for ad {item_id}")
                    else:
                        logger.error(f"Failed to delete legacy message {product[2]} from channel for ad {item_id}: {e}", exc_info=True)
                        await notify_admin(f"Failed to delete legacy message {product[2]} from channel for ad {item_id}: {str(e)}", bot=message.bot)
            await conn.execute(
                """
                UPDATE products 
                SET status = 'archived', final_price = ?, archived_at = ?, archived_photos = ?, completed_at = ? 
                WHERE unique_id = ? AND user_id = ?
                """,
                (final_price, archived_at, product[1], archived_at, item_id, user_id)
            )
            await conn.commit()
            logger.info(f"Ad {item_id} archived successfully for user_id={user_id}, is_expired={is_expired}")
        await message.answer(
            f"Эълон {item_id} архивига ўтказилди. Якуний нарх: {final_price:,.0f} сўм.",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)
        logger.info(f"User {user_id} closed ad {item_id} with final price {final_price}")
    except aiosqlite.Error as e:
        logger.error(f"Database error in process_close_product_final_price for user_id={user_id}, item_id={item_id or 'unknown'}: {e}", exc_info=True)
        await notify_admin(f"Database error in process_close_product_final_price for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонни ёпишда хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in process_close_product_final_price for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in process_close_product_final_price for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_ads_menu()
        )
        await state.set_state(AdsMenu.menu)

def register_handlers(dp: Dispatcher):
    logger.info("Executing register_handlers in products.py for 'Менинг эълонларим'")
    dp.message.register(ads_menu, F.text == "Менинг эълонларим")
    dp.message.register(add_product_start, F.text == "Эълон қўшиш")
    dp.message.register(ads_list, F.text == "Эълонлар рўйхати")
    dp.message.register(ads_delete_start, F.text == "Эълонни ўчириш")
    dp.message.register(close_product_start, F.text == "Эълонни ёпиш")
    dp.message.register(handle_ads_back_button, AdsMenu.menu, F.text == "Орқага")
    dp.message.register(process_delete_product, DeleteProduct.delete_product)
    dp.message.register(process_close_product_select, CloseProduct.select_item)
    dp.message.register(process_close_product_final_price, CloseProduct.final_price)
    dp.message.register(process_category, AddProduct.category)
    dp.message.register(process_sort, AddProduct.sort)
    dp.message.register(process_region, AddProduct.region)
    dp.message.register(process_volume_ton, AddProduct.volume_ton)
    dp.message.register(process_price, AddProduct.price)
    dp.message.register(process_photos, AddProduct.photos)
    dp.message.register(process_ads_menu, AdsMenu.menu)  # В конец для меньшего приоритета
    logger.info("Product handlers registered: ads_menu for 'Менинг эълонларим'")