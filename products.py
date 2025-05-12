import aiosqlite
import logging
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import DB_NAME, SELLER_ROLE, CATEGORIES, MAX_SORT_LENGTH, MAX_VOLUME_TON, MAX_PRICE, MAX_PHOTOS, CHANNEL_ID, ADMIN_IDS, ADMIN_ROLE
from user_requests import notify_next_pending_item
from utils import check_role, make_keyboard, validate_number, check_subscription, parse_uz_datetime, format_uz_datetime, has_pending_items, get_ads_menu, get_main_menu, notify_admin, normalize_text
from database import generate_item_id
from common import send_subscription_message
from regions import get_all_regions
from datetime import datetime, timedelta
from functools import wraps

logger = logging.getLogger(__name__)

MAX_PHOTOS_STRING_LENGTH = 1000
MAX_FILE_ID_LENGTH = 100

router = Router()

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
        logger.debug(f"seller_only: user_id={user_id}, text='{message.text}', state={current_state}")
        try:
            has_pending = await has_pending_items(user_id)
            if has_pending:
                logger.info(f"User {user_id} has pending items, notifying")
                await notify_next_pending_item(message, state)
                return
            channel_active, bot_active, is_subscribed = await check_subscription(message.bot, user_id)
            if not bot_active:
                logger.info(f"User {user_id} has no active subscription")
                await message.answer(
                    "Сизнинг обунангиз тугади.",
                    reply_markup=make_keyboard(["Обуна"], columns=1, one_time=True)
                )
                await state.clear()
                await state.set_state("Registration:subscription")
                return
            allowed, role = await check_role(message, SELLER_ROLE)
            if not allowed:
                logger.info(f"User {user_id} is not a seller, role={role}")
                await message.answer(
                    "Бу буйруқ фақат сотувчилар учун!",
                    reply_markup=get_main_menu(SELLER_ROLE)
                )
                await state.clear()
                return
            logger.debug(f"seller_only passed for user_id={user_id}, role={role}, pending_items={has_pending}, bot_active={bot_active}, is_subscribed={is_subscribed}")
            return await handler(message, state, *args, role=role, **kwargs)
        except aiosqlite.Error as e:
            logger.error(f"Database error in seller_only for user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Database error in seller_only for user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=get_ads_menu(SELLER_ROLE)
            )
            await state.set_state(AdsMenu.menu)
            return
        except Exception as e:
            logger.error(f"Unexpected error in seller_only for user_id={user_id}: {e}", exc_info=True)
            await notify_admin(f"Unexpected error in seller_only for user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=get_ads_menu(SELLER_ROLE)
            )
            await state.set_state(AdsMenu.menu)
            return
    return wrapper

@seller_only
async def ads_menu(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"ads_menu: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if normalized_text != "Менинг эълонларим":
        logger.warning(f"Unexpected text in ads_menu: user_id={user_id}, text='{normalized_text}'")
        return
    try:
        await state.clear()
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(SELLER_ROLE))
        await state.set_state(AdsMenu.menu)
        logger.info(f"Пользователь {user_id} вошёл в меню эълонларим")
    except Exception as e:
        logger.error(f"Error in ads_menu for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in ads_menu for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_main_menu(role)
        )
        await state.clear()

@seller_only
async def handle_ads_back_button(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"handle_ads_back_button: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if current_state != AdsMenu.menu.state:
        logger.warning(f"Unexpected state for handle_ads_back_button: user_id={user_id}, state={current_state}")
        await state.set_state(AdsMenu.menu)
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(SELLER_ROLE))
        return
    if normalized_text != "Орқага":
        logger.debug(f"handle_ads_back_button skipped: text='{normalized_text}', state={current_state}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_ads_menu(SELLER_ROLE))
        await state.set_state(AdsMenu.menu)
        return
    try:
        channel_active, bot_active, is_subscribed = await check_subscription(message.bot, user_id)
        logger.debug(f"Subscription check in handle_ads_back_button: user_id={user_id}, is_subscribed={is_subscribed}, bot_active={bot_active}")
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
        logger.info(f"Пользователь {user_id} вернулся в главное меню из меню эълонларим")
    except Exception as e:
        logger.error(f"Error in handle_ads_back_button for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in handle_ads_back_button for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(SELLER_ROLE)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_ads_menu(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_ads_menu: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != AdsMenu.menu.state:
        logger.warning(f"Unexpected state for process_ads_menu: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        return
    options = {
        "Эълон қўшиш": (None, add_product_start, "начал добавление эълона"),
        "Эълонлар рўйхати": (get_ads_menu(role), ads_list, "просмотрел список эълонларим"),
        "Эълонни ўчириш": (None, ads_delete_start, "начал удаление эълона"),
        "Эълонни ёпиш": (None, close_product_start, "начал закрытие эълона")
    }
    option = options.get(normalized_text)
    if option:
        logger.debug(f"process_ads_menu: user_id={user_id}, selected option='{normalized_text}'")
        try:
            if option[1]:
                await option[1](message, state, role=role)
            if option[0]:
                await message.answer("Менинг эълонларим:", reply_markup=option[0])
            await state.set_state(AdsMenu.menu)
            logger.info(f"Пользователь {user_id} {option[2]}")
        except Exception as e:
            logger.error(f"Error in process_ads_menu for user_id={user_id}, option={normalized_text}: {e}", exc_info=True)
            await notify_admin(f"Error in process_ads_menu for user_id={user_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=get_ads_menu(role)
            )
            await state.set_state(AdsMenu.menu)
    else:
        logger.warning(f"Invalid option in process_ads_menu: user_id={user_id}, text='{normalized_text}', state={current_state}")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)

@seller_only
async def ads_list(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"ads_list: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != AdsMenu.menu.state:
        logger.warning(f"Unexpected state for ads_list: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        return
    if normalized_text != "Эълонлар рўйхати":
        logger.warning(f"Unexpected text in ads_list: user_id={user_id}, text='{normalized_text}'")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        return
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                    "SELECT unique_id, category, region, sort, volume_ton, price, photos, created_at "
                    "FROM products WHERE user_id = ? AND status = 'active'", (user_id,)
            ) as cursor:
                products = await cursor.fetchall()
        if not products:
            await message.answer("Сизда эълонлар йўқ.", reply_markup=get_ads_menu(role))
            await state.set_state(AdsMenu.menu)
            logger.info(f"User {user_id} has no active products")
            return
        now = datetime.now(pytz.UTC)
        for unique_id, category, region, sort, volume_ton, price, photos, created_at in products:
            try:
                created_at_dt = parse_uz_datetime(created_at)
                if not created_at_dt:
                    logger.warning(f"Invalid created_at for product {unique_id}: {created_at}")
                    continue
                expiration = created_at_dt + timedelta(hours=24)
                status = "Фаол" if now < expiration else "Муддати тугаган"
                info = (
                    f"Эълон {unique_id}\n"
                    f"Категория: {category}\n"
                    f"Вилоят: {region}\n"
                    f"Сорт: {sort}\n"
                    f"Ҳажм: {volume_ton} тонна\n"
                    f"Нарх: {price} сўм\n"
                    f"Ҳолат: {status} ({format_uz_datetime(expiration)} гача)"
                )
                photos_list = photos.split(",") if photos else []
                if photos_list:
                    media = [types.InputMediaPhoto(media=photo, caption=info if i == 0 else None) for i, photo in enumerate(photos_list)]
                    await message.answer_media_group(media=media)
                else:
                    await message.answer(info)
            except Exception as e:
                logger.error(f"Error processing product {unique_id} for user_id={user_id}: {e}", exc_info=True)
                continue
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        logger.info(f"User {user_id} viewed product list")
    except aiosqlite.Error as e:
        logger.error(f"Database error in ads_list for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Database error in ads_list for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонлар рўйхатини юклашда хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in ads_list for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in ads_list for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def add_product_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"add_product_start: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != AdsMenu.menu.state:
        logger.warning(f"Unexpected state for add_product_start: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        return
    if normalized_text != "Эълон қўшиш":
        logger.warning(f"Unexpected text in add_product_start: user_id={user_id}, text='{normalized_text}'")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        return
    try:
        await message.answer("Маҳсулот турини танланг:", reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
        await state.set_state(AddProduct.category)
        logger.info(f"Пользователь {user_id} начал добавление эълона")
    except Exception as e:
        logger.error(f"Error in add_product_start for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in add_product_start for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_category(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_category: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != AddProduct.category.state:
        logger.warning(f"Unexpected state for process_category: user_id={user_id}, state={current_state}")
        await state.set_state(AdsMenu.menu)
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        return
    try:
        if normalized_text == "Орқага":
            await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
            await state.set_state(AdsMenu.menu)
            logger.info(f"Пользователь {user_id} вернулся в меню эълонларим из выбора категории")
            return
        category = normalized_text
        if category not in CATEGORIES:
            await message.answer(
                "Нотўғри категория! Рўйхатдан танланг:",
                reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True)
            )
            logger.warning(f"Invalid category from user_id={user_id}: {category}")
            return
        await state.update_data(category=category)
        await message.answer("Маҳсулот сортни киритинг:", reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True))
        await state.set_state(AddProduct.sort)
        logger.debug(f"Пользователь {user_id} выбрал категорию: {category}")
    except Exception as e:
        logger.error(f"Error in process_category for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in process_category for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_sort(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_sort: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != AddProduct.sort.state:
        logger.warning(f"Unexpected state for process_sort: user_id={user_id}, state={current_state}")
        await state.set_state(AdsMenu.menu)
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        return
    try:
        if normalized_text == "Орқага":
            await message.answer(
                "Маҳсулот турини танланг:",
                reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True)
            )
            await state.set_state(AddProduct.category)
            logger.info(f"Пользователь {user_id} вернулся к выбору категории из сорта")
            return
        sort = normalized_text
        if len(sort) > MAX_SORT_LENGTH:
            await message.answer(f"Сорт {MAX_SORT_LENGTH} белгидан узун бўлмаслиги керак. Қайта киритинг:")
            logger.warning(f"Sort too long from user_id={user_id}: {len(sort)} characters")
            return
        await state.update_data(sort=sort)
        await message.answer(
            "Вилоятни танланг:",
            reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True)
        )
        await state.set_state(AddProduct.region)
        logger.debug(f"Пользователь {user_id} ввёл сорт: {sort}")
    except Exception as e:
        logger.error(f"Error in process_sort for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in process_sort for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_region(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_region: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != AddProduct.region.state:
        logger.warning(f"Unexpected state for process_region: user_id={user_id}, state={current_state}")
        await state.set_state(AdsMenu.menu)
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        return
    try:
        if normalized_text == "Орқага":
            await message.answer(
                "Маҳсулот сортни киритинг:",
                reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True)
            )
            await state.set_state(AddProduct.sort)
            logger.info(f"Пользователь {user_id} вернулся к вводу сорта из региона")
            return
        region = normalized_text
        if region not in get_all_regions():
            await message.answer(
                "Нотўғри вилоят номи! Рўйхатдан танланг:",
                reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True)
            )
            logger.warning(f"Invalid region from user_id={user_id}: {region}")
            return
        await state.update_data(region=region)
        await message.answer(
            "Маҳсулот ҳажмни киритинг (тоннада):",
            reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True)
        )
        await state.set_state(AddProduct.volume_ton)
        logger.debug(f"Пользователь {user_id} выбрал регион: {region}")
    except Exception as e:
        logger.error(f"Error in process_region for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in process_region for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_volume_ton(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_volume_ton: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != AddProduct.volume_ton.state:
        logger.warning(f"Unexpected state for process_volume_ton: user_id={user_id}, state={current_state}")
        await state.set_state(AdsMenu.menu)
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        return
    try:
        if normalized_text == "Орқага":
            await message.answer(
                "Вилоятни танланг:",
                reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True)
            )
            await state.set_state(AddProduct.region)
            logger.info(f"Пользователь {user_id} вернулся к выбору региона из объёма")
            return
        valid, volume_ton = validate_number(normalized_text, min_value=0)
        if not valid or volume_ton > MAX_VOLUME_TON:
            await message.answer(f"Ҳажм мусбат рақам бўлиши ва {MAX_VOLUME_TON} тоннадан ошмаслиги керак:")
            logger.warning(f"Invalid volume from user_id={user_id}: {normalized_text}")
            return
        await state.update_data(volume_ton=volume_ton)
        await message.answer(
            "Маҳсулот нархини киритинг (сўмда):",
            reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True)
        )
        await state.set_state(AddProduct.price)
        logger.debug(f"Пользователь {user_id} ввёл объём: {volume_ton} тонн")
    except Exception as e:
        logger.error(f"Error in process_volume_ton for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in process_volume_ton for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_price(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_price: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != AddProduct.price.state:
        logger.warning(f"Unexpected state for process_price: user_id={user_id}, state={current_state}")
        await state.set_state(AdsMenu.menu)
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        return
    try:
        if normalized_text == "Орқага":
            await message.answer(
                "Маҳсулот ҳажмни киритинг (тоннада):",
                reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True)
            )
            await state.set_state(AddProduct.volume_ton)
            logger.info(f"Пользователь {user_id} вернулся к вводу объёма из цены")
            return
        valid, price = validate_number(normalized_text, min_value=0)
        if not valid or price > MAX_PRICE:
            await message.answer(f"Нарх мусбат рақам бўлиши ва {MAX_PRICE} сўмдан ошмаслиги керак:")
            logger.warning(f"Invalid price from user_id={user_id}: {normalized_text}")
            return
        await state.update_data(price=price)
        await message.answer(
            f"1 дан {MAX_PHOTOS} тагача расм юборинг. Тайёр бўлса, 'Тайёр' тугмасини босинг:",
            reply_markup=make_keyboard(["Тайёр", "Орқага"], columns=1, one_time=True)
        )
        await state.set_state(AddProduct.photos)
        logger.debug(f"Пользователь {user_id} ввёл цену: {price} сўм")
    except Exception as e:
        logger.error(f"Error in process_price for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Error in process_price for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_photos(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    current_state = await state.get_state()
    normalized_text = normalize_text(message.text)
    logger.debug(f"process_photos: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}, has_photo={message.photo is not None}")
    if current_state != AddProduct.photos.state:
        logger.warning(f"Unexpected state for process_photos: user_id={user_id}, state={current_state}")
        await state.set_state(AdsMenu.menu)
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        return
    data = await state.get_data()
    current_photos = data.get("photos", [])
    try:
        if normalized_text == "Орқага":
            await message.answer(
                "Маҳсулот нархини киритинг (сўмда):",
                reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True)
            )
            await state.set_state(AddProduct.price)
            logger.info(f"Пользователь {user_id} вернулся к вводу цены из фото")
            return
        if normalized_text == "Тайёр":
            if not current_photos:
                await message.answer(
                    "Камида 1 та расм юборинг:",
                    reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True)
                )
                logger.warning(f"No photos provided by user_id={user_id}")
                return
        elif message.photo:
            new_photo = message.photo[-1].file_id
            if len(new_photo) > MAX_FILE_ID_LENGTH:
                await message.answer(
                    f"Рaсм file_id жуда узун!",
                    reply_markup=make_keyboard(["Тайёр", "Орқага"], columns=1, one_time=True)
                )
                logger.warning(f"Photo file_id too long from user_id={user_id}")
                return
            if len(current_photos) >= MAX_PHOTOS:
                await message.answer(
                    f"Максимал {MAX_PHOTOS} та расм. 'Тайёр' тугмасини босинг:",
                    reply_markup=make_keyboard(["Тайёр", "Орқага"], columns=1, one_time=True)
                )
                logger.warning(f"Max photos reached by user_id={user_id}: {MAX_PHOTOS}")
                return
            current_photos.append(new_photo)
            await state.update_data(photos=current_photos)
            await message.answer(
                f"Расм қабул қилинди. {len(current_photos)}/{MAX_PHOTOS}. Яна расм ёки 'Тайёр':",
                reply_markup=make_keyboard(["Тайёр", "Орқага"], columns=1, one_time=True)
            )
            logger.debug(f"Photo added by user_id={user_id}, total photos: {len(current_photos)}")
            return
        else:
            await message.answer(
                "Расм юборинг ёки 'Тайёр' тугмасини босинг:",
                reply_markup=make_keyboard(["Тайёр", "Орқага"], columns=1, one_time=True)
            )
            logger.warning(f"Invalid input in process_photos from user_id={user_id}: {normalized_text}")
            return

        photos = current_photos[:MAX_PHOTOS]
        photos_str = ",".join(photos)
        if len(photos_str) > MAX_PHOTOS_STRING_LENGTH:
            await message.answer("Жуда кўп расм юборилди!", reply_markup=get_ads_menu(role))
            await state.set_state(AdsMenu.menu)
            logger.error(f"Photos string too long for user_id={user_id}: {len(photos_str)}")
            return

        item_id = await generate_item_id("products", "E")
        created_at = format_uz_datetime(datetime.now())
        async with aiosqlite.connect(DB_NAME) as conn:
            await conn.execute(
                "INSERT INTO products (unique_id, user_id, category, region, sort, volume_ton, price, photos, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (item_id, user_id, data["category"], data["region"], data["sort"], data["volume_ton"], data["price"],
                 photos_str, created_at)
            )
            await conn.commit()

        info = (
            f"Эълон {item_id}\n"
            f"Категория: {data['category']}\n"
            f"Вилоят: {data['region']}\n"
            f"Сорт: {data['sort']}\n"
            f"Ҳажм: {data['volume_ton']} тонна\n"
            f"Нарх: {data['price']} сўм"
        )
        media = [types.InputMediaPhoto(media=photo, caption=info if i == 0 else None) for i, photo in enumerate(photos)]

        channel_msg = None
        try:
            channel_msg = await message.bot.send_media_group(chat_id=CHANNEL_ID, media=media)
            logger.debug(f"Эълон {item_id} успешно отправлен в канал, message_id={channel_msg[0].message_id}")
        except types.TelegramError as e:
            logger.error(f"Ошибка отправки эълона {item_id} в канал: {e}", exc_info=True)
            for admin_id in ADMIN_IDS:
                await message.bot.send_message(admin_id, f"Эълон {item_id} канлага юборилмади: {e}")
            await message.answer("Эълон каналга юборилмади. Админ билан боғланинг.", reply_markup=get_ads_menu(role))
            await state.set_state(AdsMenu.menu)
            return

        try:
            async with aiosqlite.connect(DB_NAME) as conn:
                await conn.execute(
                    "UPDATE products SET channel_message_id = ? WHERE unique_id = ?",
                    (channel_msg[0].message_id, item_id)
                )
                await conn.commit()
        except aiosqlite.Error as e:
            logger.error(f"Ошибка обновления channel_message_id для {item_id}: {e}", exc_info=True)
            for admin_id in ADMIN_IDS:
                await message.bot.send_message(admin_id, f"Эълон {item_id} каналга юборилди, лекин базада хатолик: {e}")

        await message.answer(
            f"Сизнинг эълонингиз юборилди. Эълон рақами {item_id}. Танишиш учун Эълонлар доскаси ёки <a href=\"https://t.me/+6WXzyGqqotgzODM6\">Каналга</a> ўтинг.",
            reply_markup=get_ads_menu(role),
            parse_mode="HTML"
        )
        await state.set_state(AdsMenu.menu)
        logger.info(f"Эълон {item_id} успешно добавлен пользователем {user_id}")
    except aiosqlite.Error as e:
        logger.error(f"Database error in process_photos for user_id={user_id}, item_id={item_id or 'unknown'}: {e}", exc_info=True)
        await notify_admin(f"Database error in process_photos for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонни қўшишда хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in process_photos for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in process_photos for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def ads_delete_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"ads_delete_start: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != AdsMenu.menu.state:
        logger.warning(f"Unexpected state for ads_delete_start: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        return
    if normalized_text != "Эълонни ўчириш":
        logger.warning(f"Unexpected text in ads_delete_start: user_id={user_id}, text='{normalized_text}'")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        return
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT unique_id FROM products WHERE user_id = ? AND status = 'active'", (user_id,)
            ) as cursor:
                products = await cursor.fetchall()
        if not products:
            await message.answer("Ўчириш учун эълонлар йўқ.", reply_markup=get_ads_menu(SELLER_ROLE))
            await state.set_state(AdsMenu.menu)
            logger.info(f"Пользователь {user_id} не имеет объявлений для удаления")
            return
        product_ids = [p[0] for p in products]
        await message.answer(
            "Ўчириш учун эълон танланг:",
            reply_markup=make_keyboard(product_ids, columns=2, with_back=True)
        )
        await state.set_state(DeleteProduct.delete_product)
        logger.info(f"Пользователь {user_id} начал процесс удаления объявления")
    except aiosqlite.Error as e:
        logger.error(f"Database error in ads_delete_start for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Database error in ads_delete_start for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонлар рўйхатини юклашда хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in ads_delete_start for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in ads_delete_start for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_delete_product(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_delete_product: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != DeleteProduct.delete_product.state:
        logger.warning(f"Unexpected state for process_delete_product: user_id={user_id}, state={current_state}")
        await state.set_state(AdsMenu.menu)
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        return
    try:
        if normalized_text == "Орқага":
            await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
            await state.set_state(AdsMenu.menu)
            logger.info(f"Пользователь {user_id} вернулся в меню эълонларим из удаления")
            return
        item_id = normalized_text
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT channel_message_id FROM products WHERE unique_id = ? AND user_id = ? AND status = 'active'",
                (item_id, user_id)
            ) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(
                    f"Эълон {item_id} топилмади ёки у сизга тегишли эмас!",
                    reply_markup=get_ads_menu(role)
                )
                await state.set_state(AdsMenu.menu)
                logger.warning(f"Пользователь {user_id} пытался удалить несуществующий/чужой эълон {item_id}")
                return
            if product[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=product[0])
                    logger.debug(f"Сообщение {product[0]} удалено из канала {CHANNEL_ID}")
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {product[0]} из канала: {e}", exc_info=True)
            await conn.execute(
                "UPDATE products SET status = 'deleted' WHERE unique_id = ? AND user_id = ?",
                (item_id, user_id)
            )
            await conn.commit()
        await message.answer(f"Эълон {item_id} ўчирилди!", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        logger.info(f"Пользователь {user_id} успешно удалил эълон {item_id}")
    except aiosqlite.Error as e:
        logger.error(f"Database error in process_delete_product for user_id={user_id}, item_id={item_id or 'unknown'}: {e}", exc_info=True)
        await notify_admin(f"Database error in process_delete_product for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонни ўчиришда хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in process_delete_product for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in process_delete_product for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def close_product_start(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"close_product_start: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != AdsMenu.menu.state:
        logger.warning(f"Unexpected state for close_product_start: user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        return
    if normalized_text != "Эълонни ёпиш":
        logger.warning(f"Unexpected text in close_product_start: user_id={user_id}, text='{normalized_text}'")
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        return
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT unique_id, category, sort, volume_ton, price FROM products WHERE user_id = ? AND status = 'active'",
                (user_id,)
            ) as cursor:
                products = await cursor.fetchall()
        if not products:
            await message.answer("Ёпиш учун эълонлар йўқ.", reply_markup=get_ads_menu(SELLER_ROLE))
            await state.set_state(AdsMenu.menu)
            logger.info(f"Пользователь {user_id} не имеет объявлений для закрытия")
            return
        product_list = "Ёпиш учун Эълонни танланг:\n"
        product_ids = []
        for unique_id, category, sort, volume_ton, price in products:
            product_list += f"{unique_id} - {category} ({sort}), {volume_ton} тонна, {price} сўм\n"
            product_ids.append(unique_id)
        await message.answer(product_list, reply_markup=make_keyboard(product_ids, columns=2, with_back=True))
        await state.set_state(CloseProduct.select_item)
        logger.info(f"Пользователь {user_id} начал процесс закрытия объявления")
    except aiosqlite.Error as e:
        logger.error(f"Database error in close_product_start for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Database error in close_product_start for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонлар рўйхатини юклашда хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in close_product_start for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in close_product_start for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_close_product_select(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_close_product_select: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != CloseProduct.select_item.state:
        logger.warning(f"Unexpected state for process_close_product_select: user_id={user_id}, state={current_state}")
        await state.set_state(AdsMenu.menu)
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        return
    try:
        if normalized_text == "Орқага":
            await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
            await state.set_state(AdsMenu.menu)
            logger.info(f"Пользователь {user_id} вернулся в меню эълонларим из выбора для закрытия")
            return
        item_id = normalized_text
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT unique_id FROM products WHERE unique_id = ? AND user_id = ? AND status = 'active'",
                (item_id, user_id)
            ) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(f"Эълон {item_id} топилмади!", reply_markup=get_ads_menu(role))
                await state.set_state(AdsMenu.menu)
                logger.warning(f"Эълон {item_id} не найден для user_id={user_id}")
                return
        await state.update_data(unique_id=item_id)
        await message.answer(
            "Якуний нархни киритинг (сўмда):",
            reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True)
        )
        await state.set_state(CloseProduct.final_price)
        logger.debug(f"Пользователь {user_id} выбрал эълон {item_id} для закрытия")
    except aiosqlite.Error as e:
        logger.error(f"Database error in process_close_product_select for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Database error in process_close_product_select for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in process_close_product_select for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in process_close_product_select for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

@seller_only
async def process_close_product_final_price(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"process_close_product_final_price: user_id={user_id}, normalized_text='{normalized_text}', state={current_state}")
    if current_state != CloseProduct.final_price.state:
        logger.warning(f"Unexpected state for process_close_product_final_price: user_id={user_id}, state={current_state}")
        await state.set_state(AdsMenu.menu)
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        return
    try:
        if normalized_text == "Орқага":
            async with aiosqlite.connect(DB_NAME) as conn:
                async with conn.execute(
                    "SELECT unique_id FROM products WHERE user_id = ? AND status = 'active'", (user_id,)
                ) as cursor:
                    products = await cursor.fetchall()
            product_ids = [p[0] for p in products]
            await message.answer(
                "Ёпиш учун эълон танланг:",
                reply_markup=make_keyboard(product_ids, columns=2, with_back=True)
            )
            await state.set_state(CloseProduct.select_item)
            logger.info(f"Пользователь {user_id} вернулся к выбору эълона для закрытия")
            return
        valid, final_price = validate_number(normalized_text, min_value=0)
        if not valid or final_price > MAX_PRICE:
            await message.answer(
                f"Нарх мусбат рақам бўлиши керак (макс. {MAX_PRICE} сўм):",
                reply_markup=make_keyboard(["Орқага"], columns=1, one_time=True)
            )
            logger.warning(f"Invalid final price from user_id={user_id}: {normalized_text}")
            return
        data = await state.get_data()
        item_id = data.get("unique_id")
        if not item_id:
            await message.answer("Ошибка: эълон не выбран. Начните заново.", reply_markup=get_ads_menu(role))
            await state.set_state(AdsMenu.menu)
            logger.warning(f"No item_id in state for user_id={user_id}")
            return
        archived_at = format_uz_datetime(datetime.now())
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT channel_message_id FROM products WHERE unique_id = ? AND user_id = ? AND status = 'active'",
                (item_id, user_id)
            ) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(f"Эълон {item_id} топилмади!", reply_markup=get_ads_menu(role))
                await state.set_state(AdsMenu.menu)
                logger.warning(f"Эълон {item_id} не найден для user_id={user_id}")
                return
            await conn.execute(
                "UPDATE products SET status = 'archived', final_price = ?, archived_at = ? WHERE unique_id = ? AND user_id = ?",
                (final_price, archived_at, item_id, user_id)
            )
            await conn.commit()
            if product[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=product[0])
                    logger.debug(f"Сообщение {product[0]} удалено из канала")
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {product[0]} из канала: {e}", exc_info=True)
        await message.answer(
            f"Эълон {item_id} архивига ўтказилди. Якуний нарх: {final_price} сўм.",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)
        logger.info(f"Пользователь {user_id} закрыл эълон {item_id} с финальной ценой {final_price}")
    except aiosqlite.Error as e:
        logger.error(f"Database error in process_close_product_final_price for user_id={user_id}, item_id={item_id or 'unknown'}: {e}", exc_info=True)
        await notify_admin(f"Database error in process_close_product_final_price for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Эълонни ёпишда хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Unexpected error in process_close_product_final_price for user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Unexpected error in process_close_product_final_price for user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди! Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=get_ads_menu(role)
        )
        await state.set_state(AdsMenu.menu)

def register_handlers():
    logger.debug("Registering product handlers")
    router.message.register(ads_menu, F.text == "Менинг эълонларим")
    router.message.register(handle_ads_back_button, F.text == "Орқага", AdsMenu.menu)
    router.message.register(process_ads_menu, AdsMenu.menu)
    router.message.register(add_product_start, F.text == "Эълон қўшиш")
    router.message.register(process_category, AddProduct.category)
    router.message.register(process_sort, AddProduct.sort)
    router.message.register(process_region, AddProduct.region)
    router.message.register(process_volume_ton, AddProduct.volume_ton)
    router.message.register(process_price, AddProduct.price)
    router.message.register(process_photos, AddProduct.photos)
    router.message.register(ads_list, F.text == "Эълонлар рўйхати")
    router.message.register(ads_delete_start, F.text == "Эълонни ўчириш")
    router.message.register(process_delete_product, DeleteProduct.delete_product)
    router.message.register(close_product_start, F.text == "Эълонни ёпиш")
    router.message.register(process_close_product_select, CloseProduct.select_item)
    router.message.register(process_close_product_final_price, CloseProduct.final_price)
    router.message.register(send_subscription_message, Command("subscribe"))
    logger.debug("Product handlers registered successfully")