import aiosqlite
import logging
from aiogram import types, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import DB_NAME, SELLER_ROLE, CATEGORIES, MAX_SORT_LENGTH, MAX_VOLUME_TON, MAX_PRICE, MAX_PHOTOS, CHANNEL_ID, ADMIN_IDS
from user_requests import notify_next_pending_item
from utils import check_role, make_keyboard, validate_number, check_subscription, parse_uz_datetime, format_uz_datetime, has_pending_items
from database import generate_item_id
from common import send_subscription_message
from regions import get_all_regions
from datetime import datetime, timedelta
from functools import wraps

logger = logging.getLogger(__name__)

MAX_PHOTOS_STRING_LENGTH = 1000
MAX_FILE_ID_LENGTH = 100

class AddProduct(StatesGroup):
    category = State()
    sort = State()      # Поменяли местами с region
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
        from main import get_main_menu
        user_id = message.from_user.id
        if await has_pending_items(user_id):
            await notify_next_pending_item(message, state)
            return
        _, bot_active, _ = await check_subscription(message.bot, user_id)
        if not bot_active:
            await message.answer("Сизнинг обунангиз тугади.",
                                 reply_markup=make_keyboard(["Обуна"], columns=1))
            await state.clear()
            return
        allowed, role = await check_role(message, SELLER_ROLE)
        if not allowed:
            await message.answer("Бу буйруқ фақат сотувчилар учун!",
                                 reply_markup=get_main_menu(SELLER_ROLE))
            await state.clear()
            return
        return await handler(message, state, *args, role=role, **kwargs)
    return wrapper

@seller_only
async def ads_menu(message: types.Message, state: FSMContext, role: str):
    from main import get_ads_menu
    await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(SELLER_ROLE))
    await state.set_state(AdsMenu.menu)
    logger.info(f"Пользователь {message.from_user.id} вошёл в меню эълонларим")

@seller_only
async def process_ads_menu(message: types.Message, state: FSMContext, role: str):
    from main import get_main_menu, get_ads_menu
    user_id = message.from_user.id
    options = {
        "Орқага": (get_main_menu(role), None, "вернулся в главное меню"),
        "Эълонлар рўйхати": (get_ads_menu(role), ads_list, "просмотрел список эълонларим"),
        "Эълонни ўчириш": (None, ads_delete_start, "начал удаление эълон"),
        "Эълонни ёпиш": (None, close_product_start, "начал закрытие эълон")
    }
    option = options.get(message.text)
    if option:
        if option[1]:
            await option[1](message, state)
        if option[0]:
            await message.answer("Менинг эълонларим:" if option[2].startswith("просмотрел") else "Асосий меню:",
                                 reply_markup=option[0])
        if option[2] == "вернулся в главное меню":
            await state.clear()
        elif not option[1] or option[2].startswith("просмотрел"):
            await state.set_state(AdsMenu.menu)
        logger.info(f"Пользователь {user_id} {option[2]}")
    else:
        await message.answer("Илтимос, менюдан танланг:", reply_markup=get_ads_menu(role))

@seller_only
async def add_product_start(message: types.Message, state: FSMContext, role: str):
    await message.answer("Маҳсулот турини танланг:", reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
    await state.set_state(AddProduct.category)

@seller_only
async def process_category(message: types.Message, state: FSMContext, role: str):
    from main import get_main_menu
    if message.text == "Орқага":
        await message.answer("Асосий меню:", reply_markup=get_main_menu(role))
        await state.clear()
        return
    category = message.text.strip()
    if category not in CATEGORIES:
        await message.answer("Нотўғри категория! Рўйхатдан танланг:",
                             reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
        return
    await state.update_data(category=category)
    await message.answer("Маҳсулот сортни киритинг:", reply_markup=make_keyboard(["Орқага"], columns=1))
    await state.set_state(AddProduct.sort)

@seller_only
async def process_sort(message: types.Message, state: FSMContext, role: str):
    if message.text == "Орқага":
        await message.answer("Маҳсулот турини танланг:",
                             reply_markup=make_keyboard(CATEGORIES, columns=2, with_back=True))
        await state.set_state(AddProduct.category)
        return
    sort = message.text.strip()
    if len(sort) > MAX_SORT_LENGTH:
        await message.answer(f"Сорт {MAX_SORT_LENGTH} белгидан узун бўлмаслиги керак. Қайта киритинг:")
        return
    await state.update_data(sort=sort)
    await message.answer("Вилоятни танланг:", reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True))
    await state.set_state(AddProduct.region)

@seller_only
async def process_region(message: types.Message, state: FSMContext, role: str):
    if message.text == "Орқага":
        await message.answer("Маҳсулот сортни киритинг:", reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AddProduct.sort)
        return
    region = message.text.strip()
    if region not in get_all_regions():
        await message.answer("Нотўғри вилоят номи! Рўйхатдан танланг:",
                             reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True))
        return
    await state.update_data(region=region)
    await message.answer("Маҳсулот ҳажмни киритинг (тоннада):", reply_markup=make_keyboard(["Орқага"], columns=1))
    await state.set_state(AddProduct.volume_ton)

@seller_only
async def process_volume_ton(message: types.Message, state: FSMContext, role: str):
    if message.text == "Орқага":
        await message.answer("Вилоятни танланг:",
                             reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True))
        await state.set_state(AddProduct.region)
        return
    volume_ton = validate_number(message.text, MAX_VOLUME_TON)
    if not volume_ton:
        await message.answer(f"Ҳажм мусбат рақам бўлиши ва {MAX_VOLUME_TON} тоннадан ошмаслиги керак:")
        return
    await state.update_data(volume_ton=volume_ton)
    await message.answer("Маҳсулот нархини киритинг (сўмда):", reply_markup=make_keyboard(["Орқага"], columns=1))
    await state.set_state(AddProduct.price)

@seller_only
async def process_price(message: types.Message, state: FSMContext, role: str):
    if message.text == "Орқага":
        await message.answer("Маҳсулот ҳажмни киритинг (тоннада):", reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AddProduct.volume_ton)
        return
    price = validate_number(message.text, MAX_PRICE)
    if not price:
        await message.answer(f"Нарх мусбат рақам бўлиши ва {MAX_PRICE} сўмдан ошмаслиги керак:")
        return
    await state.update_data(price=price)
    await message.answer(f"1 дан {MAX_PHOTOS} тагача расм юборинг. Тайёр бўлса, 'Тайёр' тугмасини босинг:",
                         reply_markup=make_keyboard(["Тайёр", "Орқага"], columns=1))
    await state.set_state(AddProduct.photos)

@seller_only
async def process_photos(message: types.Message, state: FSMContext, role: str):
    from main import get_ads_menu
    user_id = message.from_user.id
    data = await state.get_data()
    current_photos = data.get("photos", [])
    if message.text == "Орқага":
        await message.answer("Маҳсулот нархини киритинг (сўмда):", reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(AddProduct.price)
        return
    if message.text == "Тайёр":
        if not current_photos:
            await message.answer("Камида 1 та расм юборинг:", reply_markup=make_keyboard(["Орқага"], columns=1))
            return
    elif message.photo:
        new_photo = message.photo[-1].file_id
        if len(new_photo) > MAX_FILE_ID_LENGTH:
            await message.answer(f"Рaсм file_id жуда узун!", reply_markup=make_keyboard(["Тайёр", "Орқага"], columns=1))
            return
        if len(current_photos) >= MAX_PHOTOS:
            await message.answer(f"Максимум {MAX_PHOTOS} та расм. 'Тайёр' тугмасини босинг:",
                                 reply_markup=make_keyboard(["Тайёр", "Орқага"], columns=1))
            return
        current_photos.append(new_photo)
        await state.update_data(photos=current_photos)
        await message.answer(f"Расм қабул қилинди. {len(current_photos)}/{MAX_PHOTOS}. Яна расм ёки 'Тайёр':",
                             reply_markup=make_keyboard(["Тайёр", "Орқага"], columns=1))
        return
    else:
        await message.answer("Расм юборинг ёки 'Тайёр' тугмасини босинг:",
                             reply_markup=make_keyboard(["Тайёр", "Орқага"], columns=1))
        return

    photos = current_photos[:MAX_PHOTOS]
    photos_str = ",".join(photos)
    if len(photos_str) > MAX_PHOTOS_STRING_LENGTH:
        await message.answer("Жуда кўп расм юборилди!", reply_markup=get_ads_menu(role))
        await state.clear()
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
        logger.error(f"Ошибка отправки эълона {item_id} в канал: {e}")
        for admin_id in ADMIN_IDS:
            await message.bot.send_message(admin_id, f"Эълон {item_id} канлага юборилмади: {e}")
        await message.answer("Эълон каналга юборилмади. Админ билан боғланинг.", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        return

    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            await conn.execute("UPDATE products SET channel_message_id = ? WHERE unique_id = ?",
                               (channel_msg[0].message_id, item_id))
            await conn.commit()
    except aiosqlite.Error as e:
        logger.error(f"Ошибка обновления channel_message_id для {item_id}: {e}")
        for admin_id in ADMIN_IDS:
            await message.bot.send_message(admin_id, f"Эълон {item_id} каналга юборилди, лекин базада хатолик: {e}")

    await message.answer(
        f"Сизнинг эълонингиз юборилди. Эълон рақами {item_id}. Танишиш учун Эълонлар доскаси ёки <a href=\"https://t.me/+6WXzyGqqotgzODM6\">Каналга</a> ўтинг.",
        reply_markup=get_ads_menu(role),
        parse_mode="HTML"
    )
    await state.set_state(AdsMenu.menu)

@seller_only
async def ads_list(message: types.Message, state: FSMContext, role: str):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute(
                "SELECT unique_id, category, region, sort, volume_ton, price, photos, created_at "
                "FROM products WHERE user_id = ? AND status = 'active'", (user_id,)
        ) as cursor:
            products = await cursor.fetchall()
    if not products:
        await message.answer("Сизда эълонлар йўқ.")
        return
    now = datetime.now()
    for unique_id, category, region, sort, volume_ton, price, photos, created_at in products:
        created_at_dt = parse_uz_datetime(created_at)
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
        photos = photos.split(",") if photos else []
        if photos:
            media = [types.InputMediaPhoto(media=photo, caption=info if i == 0 else None) for i, photo in enumerate(photos)]
            await message.answer_media_group(media=media)
        else:
            await message.answer(info)

@seller_only
async def ads_delete_start(message: types.Message, state: FSMContext, role: str):
    from main import get_ads_menu
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute("SELECT unique_id FROM products WHERE user_id = ? AND status = 'active'", (user_id,)) as cursor:
            products = await cursor.fetchall()
    if not products:
        await message.answer("Ўчириш учун эълонлар йўқ.", reply_markup=get_ads_menu(SELLER_ROLE))
        await state.set_state(AdsMenu.menu)
        return
    product_ids = [p[0] for p in products]
    await message.answer("Ўчириш учун эълон танланг:",
                         reply_markup=make_keyboard(product_ids, columns=2, with_back=True))
    await state.set_state(DeleteProduct.delete_product)
    logger.info(f"Пользователь {user_id} начал процесс удаления объявления")

@seller_only
async def process_delete_product(message: types.Message, state: FSMContext, role: str):
    from main import get_ads_menu
    user_id = message.from_user.id
    if message.text == "Орқага":
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        logger.info(f"Пользователь {user_id} вернулся в меню эълонларим из удаления")
        return
    item_id = message.text.strip()
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT channel_message_id FROM products WHERE unique_id = ? AND user_id = ? AND status = 'active'",
                (item_id, user_id)
            ) as cursor:
                product = await cursor.fetchone()
            if not product:
                await message.answer(f"Эълон {item_id} топилмади ёки у сизга тегишли эмас!", reply_markup=get_ads_menu(role))
                await state.set_state(AdsMenu.menu)
                logger.warning(f"Пользователь {user_id} пытался удалить несуществующий/чужой эълон {item_id}")
                return
            if product[0]:
                try:
                    await message.bot.delete_message(chat_id=CHANNEL_ID, message_id=product[0])
                    logger.debug(f"Сообщение {product[0]} удалено из канала {CHANNEL_ID}")
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {product[0]} из канала: {e}")
            await conn.execute(
                "UPDATE products SET status = 'deleted' WHERE unique_id = ? AND user_id = ?",
                (item_id, user_id)
            )
            await conn.commit()
        await message.answer(f"Эълон {item_id} ўчирилди!", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        logger.info(f"Пользователь {user_id} успешно удалил эълон {item_id}")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при удалении эълона {item_id} для {user_id}: {e}")
        await message.answer("Эълонни ўчиришда хатолик юз берди!", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
    except Exception as e:
        logger.error(f"Неожиданная ошибка при удалении эълона {item_id} для {user_id}: {e}", exc_info=True)
        await message.answer("Хатолик юз берди!", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)

@seller_only
async def close_product_start(message: types.Message, state: FSMContext, role: str):
    from main import get_ads_menu
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute(
            "SELECT unique_id, category, sort, volume_ton, price FROM products WHERE user_id = ? AND status = 'active'",
            (user_id,)
        ) as cursor:
            products = await cursor.fetchall()
    if not products:
        await message.answer("Ёпиш учун эълонлар йўқ.", reply_markup=get_ads_menu(SELLER_ROLE))
        await state.set_state(AdsMenu.menu)
        return

    product_list = "Ёпиш учун Эълонни танланг:\n"
    product_ids = []
    for unique_id, category, sort, volume_ton, price in products:
        product_list += f"{unique_id} - {category} ({sort}), {volume_ton} тонна, {price} сўм\n"
        product_ids.append(unique_id)

    await message.answer(product_list, reply_markup=make_keyboard(product_ids, columns=2, with_back=True))
    await state.set_state(CloseProduct.select_item)
    logger.info(f"Пользователь {user_id} начал процесс закрытия объявления")

@seller_only
async def process_close_product_select(message: types.Message, state: FSMContext, role: str):
    from main import get_ads_menu
    user_id = message.from_user.id
    if message.text == "Орқага":
        await message.answer("Менинг эълонларим:", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        logger.info(f"Пользователь {user_id} вернулся в меню эълонларим из выбора для закрытия")
        return
    item_id = message.text.strip()
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute(
            "SELECT unique_id FROM products WHERE unique_id = ? AND user_id = ? AND status = 'active'",
            (item_id, user_id)
        ) as cursor:
            product = await cursor.fetchone()
        if not product:
            await message.answer(f"Эълон {item_id} топилмади!", reply_markup=get_ads_menu(role))
            await state.set_state(AdsMenu.menu)
            return
    await state.update_data(unique_id=item_id)
    await message.answer("Якуний нархни киритинг (сўмда):", reply_markup=make_keyboard(["Орқага"], columns=1))
    await state.set_state(CloseProduct.final_price)

@seller_only
async def process_close_product_final_price(message: types.Message, state: FSMContext, role: str):
    from main import get_ads_menu
    user_id = message.from_user.id
    if message.text == "Орқага":
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT unique_id FROM products WHERE user_id = ? AND status = 'active'", (user_id,)) as cursor:
                products = await cursor.fetchall()
        product_ids = [p[0] for p in products]
        await message.answer("Ёпиш учун эълон танланг:",
                             reply_markup=make_keyboard(product_ids, columns=2, with_back=True))
        await state.set_state(CloseProduct.select_item)
        logger.info(f"Пользователь {user_id} вернулся к выбору эълона для закрытия")
        return
    final_price = validate_number(message.text, MAX_PRICE)
    if not final_price:
        await message.answer(f"Нарх мусбат рақам бўлиши керак (макс. {MAX_PRICE} сўм):", reply_markup=make_keyboard(["Орқага"], columns=1))
        return
    data = await state.get_data()
    item_id = data["unique_id"]
    archived_at = format_uz_datetime(datetime.now())
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            await conn.execute(
                "UPDATE products SET status = 'archived', final_price = ?, archived_at = ? WHERE unique_id = ? AND user_id = ?",
                (final_price, archived_at, item_id, user_id)
            )
            await conn.commit()
        await message.answer(f"Эълон {item_id} архивига ўтказилди. Якуний нарх: {final_price} сўм.", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)
        logger.info(f"Пользователь {user_id} закрыл эълон {item_id} с финальной ценой {final_price}")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при закрытии эълона {item_id} для {user_id}: {e}")
        await message.answer("Эълонни ёпишда хатолик юз берди!", reply_markup=get_ads_menu(role))
        await state.set_state(AdsMenu.menu)

def register_handlers(dp: Dispatcher):
    dp.message.register(ads_menu, F.text == "Менинг эълонларим")
    dp.message.register(process_ads_menu, AdsMenu.menu)
    dp.message.register(add_product_start, F.text == "Эълон қўшиш")
    dp.message.register(process_category, AddProduct.category)
    dp.message.register(process_sort, AddProduct.sort)
    dp.message.register(process_region, AddProduct.region)
    dp.message.register(process_volume_ton, AddProduct.volume_ton)
    dp.message.register(process_price, AddProduct.price)
    dp.message.register(process_photos, AddProduct.photos)
    dp.message.register(ads_list, F.text == "Эълонлар рўйхати")
    dp.message.register(ads_delete_start, F.text == "Эълонни ўчириш")
    dp.message.register(process_delete_product, DeleteProduct.delete_product)
    dp.message.register(close_product_start, F.text == "Эълонни ёпиш")
    dp.message.register(process_close_product_select, CloseProduct.select_item)
    dp.message.register(process_close_product_final_price, CloseProduct.final_price)
    dp.message.register(send_subscription_message, Command("subscribe"))
    logger.debug("Product handlers registered successfully")
