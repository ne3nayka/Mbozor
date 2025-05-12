import aiosqlite
import logging
from aiogram import types, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardRemove
from config import DB_NAME, DB_TIMEOUT, SELLER_ROLE, MAX_COMPANY_NAME_LENGTH, ROLE_MAPPING, ADMIN_ROLE, DISPLAY_ROLE_MAPPING
from utils import check_role, make_keyboard, check_subscription, get_profile_menu, get_main_menu, get_admin_menu, format_uz_datetime, notify_admin, normalize_text
from regions import get_all_regions, get_districts_for_region
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

class EditProfile(StatesGroup):
    region = State()
    district = State()
    company_name = State()

class ProfileStates(StatesGroup):
    main = State()

async def require_subscription(message: types.Message, role: str, state: FSMContext) -> bool:
    user_id = message.from_user.id
    if role != ADMIN_ROLE:
        channel_active, bot_active, is_subscribed = await check_subscription(message.bot, user_id)
        if not is_subscribed:
            await message.answer(
                "Сизда фаол обуна мавжуд эмас. 'Обуна' тугмасини босинг:",
                reply_markup=make_keyboard(["Обуна", "Орқага"], columns=2, one_time=True)
            )
            await state.set_state("Registration:subscription")
            logger.info(f"User {user_id} denied profile access due to inactive subscription")
            return False
    return True

async def profile_info(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"User {user_id} not allowed to access profile")
        await message.answer(
            "Профильга кириш учун рўйхатдан ўтинг:",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state("Registration:start")
        return
    if not await require_subscription(message, role, state):
        return
    display_role = DISPLAY_ROLE_MAPPING.get(role, role)
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT phone_number, role, region, district, company_name, unique_id FROM users WHERE id = ?",
                (user_id,)
            ) as cursor:
                user = await cursor.fetchone()
        if not user:
            await message.answer(
                "Сизнинг профилингиз топилмади. Илтимос, рўйхатдан ўтинг.",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state("Registration:start")
            logger.warning(f"Profile not found for user {user_id}")
            return
        info = (
            f"📞 Телефон: {user[0]}\n"
            f"🎭 Рол: {display_role}\n"
            f"🌍 Вилоят: {user[2] or 'Йўқ'}\n"
            f"🏞 Туман: {user[3] or 'Йўқ'}\n"
        )
        if role == SELLER_ROLE:
            info += f"🏢 Компания: {user[4] or 'Йўқ'}\n"
        info += f"🆔 ID: {user[5]}"
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            f"Сизнинг профилингиз:\n\n{info}",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)
        logger.info(f"User {user_id} viewed profile info")
    except aiosqlite.Error as e:
        logger.error(f"Error loading profile for user {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка загрузки профиля для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Профильни юклашда хатолик. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)

async def edit_profile_start(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    logger.debug(f"Starting edit_profile_start for user_id={user_id}")
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"User {user_id} not allowed to edit profile")
        await message.answer(
            "Профильни таҳрирлаш учун рўйхатдан ўтиш керак.",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state("Registration:start")
        return
    if not await require_subscription(message, role, state):
        logger.debug(f"User {user_id} blocked by subscription check")
        return
    try:
        regions = get_all_regions()
        logger.debug(f"Regions loaded for user_id={user_id}: {regions}")
        if not regions:
            reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
            await message.answer(
                "Вилоятлар рўйхати бўш. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=reply_markup
            )
            await state.set_state(ProfileStates.main)
            logger.warning(f"Empty regions list for user {user_id}")
            return
        await message.answer(
            "Янги вилоятни танланг:",
            reply_markup=make_keyboard(regions, columns=2, with_back=True)
        )
        await state.set_state(EditProfile.region)
        await state.update_data(role=role)
        logger.info(f"User {user_id} started editing profile, moved to EditProfile.region")
    except Exception as e:
        logger.error(f"Error in edit_profile_start for user {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в edit_profile_start для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)

async def process_region(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    data = await state.get_data()
    role = data.get("role")
    regions = get_all_regions()
    if normalized_text == "Орқага":
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Профиль менюси:",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)
        logger.info(f"User {user_id} returned to profile menu from region selection")
        return
    if normalized_text not in regions:
        await message.answer(
            "Илтимос, рўйхатдан вилоят танланг:",
            reply_markup=make_keyboard(regions, columns=2, with_back=True)
        )
        logger.warning(f"User {user_id} selected invalid region: {message.text}")
        return
    try:
        districts = get_districts_for_region(normalized_text)
        if not districts:
            reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
            await message.answer(
                "Туманлар рўйхати бўш. Админ билан боғланинг (@MSMA_UZ).",
                reply_markup=reply_markup
            )
            await state.set_state(ProfileStates.main)
            logger.warning(f"Empty districts list for region {normalized_text} for user {user_id}")
            return
        await state.update_data(region=normalized_text)
        await message.answer(
            "Янги туманни танланг:",
            reply_markup=make_keyboard(districts, columns=2, with_back=True)
        )
        await state.set_state(EditProfile.district)
        logger.info(f"User {user_id} selected region {normalized_text}, moved to EditProfile.district")
    except Exception as e:
        logger.error(f"Error processing region for user {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка обработки региона для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)

async def process_district(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    data = await state.get_data()
    role = data.get("role")
    region = data.get("region")
    districts = get_districts_for_region(region)
    if normalized_text == "Орқага":
        regions = get_all_regions()
        await message.answer(
            "Янги вилоятни танланг:",
            reply_markup=make_keyboard(regions, columns=2, with_back=True)
        )
        await state.set_state(EditProfile.region)
        logger.info(f"User {user_id} returned to region selection from district")
        return
    if normalized_text not in districts:
        await message.answer(
            "Илтимос, рўйхатдан туман танланг:",
            reply_markup=make_keyboard(districts, columns=2, with_back=True)
        )
        logger.warning(f"User {user_id} selected invalid district: {message.text}")
        return
    await state.update_data(district=normalized_text)
    if role == SELLER_ROLE:
        await message.answer(
            f"Компания номини киритинг (макс. {MAX_COMPANY_NAME_LENGTH} белги):",
            reply_markup=make_keyboard(["Орқага"], one_time=True)
        )
        await state.set_state(EditProfile.company_name)
        logger.info(f"User {user_id} selected district {normalized_text}, moved to EditProfile.company_name")
    else:
        await save_edited_profile(message, state)

async def process_company_name(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    data = await state.get_data()
    role = data.get("role")
    if normalized_text == "Орқага":
        districts = get_districts_for_region(data.get("region"))
        await message.answer(
            "Янги туманни танланг:",
            reply_markup=make_keyboard(districts, columns=2, with_back=True)
        )
        await state.set_state(EditProfile.district)
        logger.info(f"User {user_id} returned to district selection from company_name")
        return
    company_name = normalized_text.strip()
    if len(company_name) > MAX_COMPANY_NAME_LENGTH:
        await message.answer(
            f"Компания номи {MAX_COMPANY_NAME_LENGTH} белгидан ошмаслиги керак. Илтимос, қайта киритинг:",
            reply_markup=make_keyboard(["Орқага"], one_time=True)
        )
        logger.warning(f"User {user_id} entered too long company name: {len(company_name)} characters")
        return
    await state.update_data(company_name=company_name)
    await save_edited_profile(message, state)

async def save_edited_profile(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    data = await state.get_data()
    role = data.get("role")
    try:
        company_name = data.get("company_name") if role == SELLER_ROLE else None
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            if role == SELLER_ROLE:
                await conn.execute(
                    "UPDATE users SET region = ?, district = ?, company_name = ? WHERE id = ?",
                    (data["region"], data["district"], company_name, user_id)
                )
            else:
                await conn.execute(
                    "UPDATE users SET region = ?, district = ? WHERE id = ?",
                    (data["region"], data["district"], user_id)
                )
            await conn.commit()
        await message.answer(
            "✅ Профиль янгиланди! Асосий менюга қайтдик.",
            reply_markup=get_main_menu(role)
        )
        await state.clear()
        logger.info(f"User {user_id} saved edited profile: region={data['region']}, district={data['district']}")
    except aiosqlite.Error as e:
        logger.error(f"Error updating profile for user {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка обновления профиля для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Профильни янгилашда хатолик. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)

async def profile_delete(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"User {user_id} not allowed to delete profile")
        await message.answer(
            "Профильни ўчириш учун рўйхатдан ўтиш керак.",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state("Registration:start")
        return
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT phone_number, role, region, district, company_name, unique_id FROM users WHERE id = ?",
                (user_id,)
            ) as cursor:
                user = await cursor.fetchone()
            if not user:
                await message.answer(
                    "Сизнинг профилингиз топилмади.",
                    reply_markup=get_main_menu(None)
                )
                await state.clear()
                logger.warning(f"Profile not found for deletion for user {user_id}")
                return
            now_utc = format_uz_datetime(datetime.now(pytz.UTC))
            await conn.execute(
                """
                INSERT INTO deleted_users (user_id, phone_number, role, region, district, company_name, unique_id, deleted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, user[0], user[1], user[2], user[3], user[4], user[5], now_utc)
            )
            await conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            await conn.commit()
        await state.clear()
        try:
            storage = state.storage
            if hasattr(storage, 'redis'):
                redis_keys = await storage.redis.keys(f"aiogram:*:{user_id}:*")
                if redis_keys:
                    await storage.redis.delete(*redis_keys)
                    logger.info(f"Cleared {len(redis_keys)} Redis keys for user {user_id}")
        except Exception as e:
            logger.warning(f"Failed to clear Redis state for user {user_id}: {e}", exc_info=True)
        await message.answer(
            "✅ Профиль муваффақиятли ўчирилди. Қайта рўйхатдан ўтиш учун тугмани босинг:",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш", "Орқага"], columns=2, one_time=True)
        )
        logger.info(f"User {user_id} deleted their profile")
    except aiosqlite.Error as e:
        logger.error(f"Error deleting profile for user {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка удаления профиля для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Профильни ўчиришда хатолик. Админ билан боғланинг (@MSMA_UZ).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)

async def profile_menu(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    if normalized_text != "Менинг профилим":
        logger.warning(f"Unexpected text in profile_menu: user_id={user_id}, text='{normalized_text}'")
        return
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"User {user_id} not allowed to access profile")
        await message.answer(
            "Профильга кириш учун рўйхатдан ўтинг:",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state("Registration:start")
        return
    if not await require_subscription(message, role, state):
        return
    reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
    await message.answer(
        "📋 Менинг профилим:",
        reply_markup=reply_markup
    )
    await state.set_state(ProfileStates.main)
    logger.info(f"User {user_id} (role={role}) entered profile menu")

async def process_profile_action(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"Processing ProfileStates.main: user_id={user_id}, text={normalized_text}, state={current_state}")
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"User {user_id} not allowed")
        await message.answer(
            "Профиль менюсига кириш учун рўйхатдан ўтиш керак.",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state("Registration:start")
        return
    if current_state != ProfileStates.main.state:
        logger.warning(f"User {user_id} not in ProfileStates.main, current state: {current_state}")
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Профиль менюсидан фойдаланинг:",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)
        return
    reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
    if normalized_text == "Орқага":
        await message.answer(
            "🏠 Асосий меню:",
            reply_markup=get_main_menu(role)
        )
        await state.clear()
        logger.info(f"User {user_id} returned to main menu from profile")
    elif normalized_text == "Профиль ҳақида маълумот":
        await profile_info(message, state)
    elif normalized_text == "Профильни таҳрирлаш":
        logger.debug(f"User {user_id} selected 'Профильни таҳрирлаш'")
        await edit_profile_start(message, state)
    elif normalized_text == "Профильни ўчириш":
        await profile_delete(message, state)
    else:
        await message.answer(
            "Илтимос, менюдан танланг:",
            reply_markup=reply_markup
        )
        logger.warning(f"User {user_id} sent invalid profile action: {normalized_text}")

async def handle_back_button(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    normalized_text = normalize_text(message.text)
    current_state = await state.get_state()
    logger.debug(f"handle_back_button: user_id={user_id}, text='{normalized_text}', state={current_state}")
    if normalized_text != "орқага" or current_state != ProfileStates.main.state:
        logger.debug(f"handle_back_button skipped: user_id={user_id}, text='{normalized_text}', state={current_state}")
        return
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"User {user_id} not allowed")
        await message.answer(
            "Профиль менюсига кириш учун рўйхатдан ўтиш керак.",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state("Registration:start")
        return
    reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
    await message.answer(
        "📋 Менинг профилим:",
        reply_markup=reply_markup
    )
    await state.set_state(ProfileStates.main)
    logger.info(f"User {user_id} returned to profile menu via back button")

def register_handlers(dp: Dispatcher):
    logger.info("Registering profile handlers")
    dp.message.register(profile_menu, F.text == "Менинг профилим")
    dp.message.register(process_profile_action, ProfileStates.main)
    dp.message.register(process_region, EditProfile.region)
    dp.message.register(process_district, EditProfile.district)
    dp.message.register(process_company_name, EditProfile.company_name)
    dp.message.register(handle_back_button, ProfileStates.main, F.text == "Орқага")
    logger.info("Profile handlers registered")