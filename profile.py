import aiosqlite
import logging
from aiogram import types, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import DB_NAME, SELLER_ROLE, MAX_COMPANY_NAME_LENGTH, ROLE_MAPPING, ADMIN_ROLE
from utils import check_role, make_keyboard, check_subscription
from regions import get_all_regions, get_districts_for_region
from datetime import datetime

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
        _, bot_active, _ = await check_subscription(message.bot, user_id)
        if not bot_active:
            await message.answer(
                "Сизнинг обунангиз тугади. Обуна бўлиш учун 'Обуна' тугмасини босинг",
                reply_markup=make_keyboard(["Обуна"], columns=1)
            )
            await state.clear()
            return False
    return True

async def profile_info(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    allowed, role = await check_role(message)
    if not allowed:
        return
    if not await require_subscription(message, role, state):
        return
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role)
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
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
            await state.clear()
            return
        info = (
            f"Телефон: {user[0]}\n"
            f"Рол: {display_role}\n"
            f"Вилоят: {user[2] or 'Йўқ'}\n"
            f"Туман: {user[3] or 'Йўқ'}\n"
        )
        if role == SELLER_ROLE:
            info += f"Компания: {user[4] or 'Йўқ'}\n"
        info += f"ID: {user[5]}"
        await message.answer(f"Сизнинг профилингиз:\n{info}", reply_markup=message.bot.get_profile_menu(role=role))
        await state.set_state(ProfileStates.main)
        logger.info(f"User {user_id} viewed profile info")
    except aiosqlite.Error as e:
        logger.error(f"Error loading profile for user {user_id}: {e}")
        await message.answer("Профильни юклашда хатолик.", reply_markup=message.bot.get_profile_menu(role=role))
        await state.set_state(ProfileStates.main)

async def edit_profile_start(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    allowed, role = await check_role(message)
    if not allowed:
        return
    if not await require_subscription(message, role, state):
        return
    try:
        regions = get_all_regions()
        if not regions:
            await message.answer("Вилоятлар рўйхати бўш. Админ билан боғланинг.",
                                 reply_markup=message.bot.get_profile_menu(role=role))
            await state.set_state(ProfileStates.main)
            return
        await message.answer(
            "Янги вилоятни танланг:",
            reply_markup=make_keyboard(regions, columns=2, with_back=True)
        )
        await state.set_state(EditProfile.region)
        await state.update_data(role=role)
        logger.debug(f"User {user_id} started editing profile, moved to EditProfile.region")
    except Exception as e:
        logger.error(f"Error loading regions for user {user_id}: {e}")
        await message.answer("Хатолик юз берди.", reply_markup=message.bot.get_profile_menu(role=role))
        await state.set_state(ProfileStates.main)

async def save_edited_profile(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    data = await state.get_data()
    role = data.get("role")
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role)
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            if role == SELLER_ROLE:
                await conn.execute(
                    "UPDATE users SET region = ?, district = ?, company_name = ? WHERE id = ?",
                    (data["region"], data["district"], data["company_name"], user_id)
                )
            else:
                await conn.execute(
                    "UPDATE users SET region = ?, district = ? WHERE id = ?",
                    (data["region"], data["district"], user_id)
                )
            await conn.commit()
        await message.answer(
            "Профиль янгиланди! Асосий менюга қайтдик.",
            reply_markup=message.bot.get_main_menu(display_role)
        )
        await state.clear()
        logger.info(f"User {user_id} saved edited profile")
    except aiosqlite.Error as e:
        logger.error(f"Error updating profile for user {user_id}: {e}")
        await message.answer(
            "Профильни янгилашда хатолик.",
            reply_markup=message.bot.get_main_menu(display_role)
        )
        await state.clear()

async def profile_menu(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"User {user_id} not allowed to access profile")
        return
    if not await require_subscription(message, role, state):
        return
    await message.answer("Менинг профилим:", reply_markup=message.bot.get_profile_menu(role=role))
    await state.set_state(ProfileStates.main)
    logger.info(f"User {user_id} (role={role}) entered profile menu")

async def process_profile_action(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    current_state = await state.get_state()
    logger.debug(f"Processing ProfileStates.main: user_id={user_id}, text={message.text}, state={current_state}")
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"User {user_id} not allowed")
        return
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role)
    if current_state != ProfileStates.main.state:
        logger.warning(f"User {user_id} not in ProfileStates.main, current state: {current_state}")
        await message.answer("Профиль менюсидан фойдаланинг:", reply_markup=message.bot.get_profile_menu(role=role))
        await state.set_state(ProfileStates.main)
        return
    if message.text == "Орқага":
        await message.answer("Асосий меню:", reply_markup=message.bot.get_main_menu(display_role))
        await state.clear()
        logger.info(f"User {user_id} returned to main menu from profile")
    elif message.text == "Профиль ҳақида маълумот":
        await profile_info(message, state)
    elif message.text == "Профильни таҳрирлаш":
        await edit_profile_start(message, state)
    elif message.text == "Профильни ўчириш":
        await profile_delete(message, state)
    else:
        await message.answer("Илтимос, менюдан танланг:", reply_markup=message.bot.get_profile_menu(role=role))

async def profile_edit_region(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    logger.debug(f"Entering profile_edit_region: user_id={user_id}, text={message.text}")
    allowed, role = await check_role(message)
    if not allowed:
        return
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role)
    if message.text == "Орқага":
        await message.answer("Менинг профилим:", reply_markup=message.bot.get_profile_menu(role=display_role))
        await state.set_state(ProfileStates.main)
        return
    if not await require_subscription(message, role, state):
        return
    region = message.text.strip()
    regions = get_all_regions()
    if region not in regions:
        await message.answer(
            "Бундай вилоят йўқ. Рўйхатдан танланг:",
            reply_markup=make_keyboard(regions, columns=2, with_back=True)
        )
        return
    await state.update_data(region=region)
    if region == "Тошкент шаҳри":
        if role == SELLER_ROLE:
            await message.answer(
                "Янги компания номини киритинг (макс. 50 белги):",
                reply_markup=make_keyboard(["Орқага"], columns=1)
            )
            await state.set_state(EditProfile.company_name)
        else:
            await state.update_data(district=None)
            await save_edited_profile(message, state)
    else:
        districts = get_districts_for_region(region)
        if not districts:
            await state.update_data(district=None)
            if role == SELLER_ROLE:
                await message.answer(
                    "Янги компания номини киритинг (макс. 50 белги):",
                    reply_markup=make_keyboard(["Орқага"], columns=1)
                )
                await state.set_state(EditProfile.company_name)
            else:
                await save_edited_profile(message, state)
        else:
            await message.answer(
                "Янги туманни танланг:",
                reply_markup=make_keyboard(districts, columns=2, with_back=True)
            )
            await state.set_state(EditProfile.district)

async def profile_edit_district(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    logger.debug(f"Entering profile_edit_district: user_id={user_id}, text={message.text}")
    allowed, role = await check_role(message)
    if not allowed:
        return
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role)
    if message.text == "Орқага":
        regions = get_all_regions()
        await message.answer(
            "Янги вилоятни танланг:",
            reply_markup=make_keyboard(regions, columns=2, with_back=True)
        )
        await state.set_state(EditProfile.region)
        return
    if not await require_subscription(message, role, state):
        return
    district = message.text.strip()
    data = await state.get_data()
    districts = get_districts_for_region(data["region"])
    if district not in districts:
        await message.answer(
            "Бундай туман йўқ. Рўйхатдан танланг:",
            reply_markup=make_keyboard(districts, columns=2, with_back=True)
        )
        return
    await state.update_data(district=district)
    if role == SELLER_ROLE:
        await message.answer(
            "Янги компания номини киритинг (макс. 50 белги):",
            reply_markup=make_keyboard(["Орқага"], columns=1)
        )
        await state.set_state(EditProfile.company_name)
    else:
        await save_edited_profile(message, state)

async def profile_edit_company_name(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    logger.debug(f"Entering profile_edit_company_name: user_id={user_id}, text={message.text}")
    allowed, role = await check_role(message)
    if not allowed:
        return
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role)
    if message.text == "Орқага":
        data = await state.get_data()
        districts = get_districts_for_region(data["region"]) if data["region"] != "Тошкент шаҳри" else []
        if districts:
            await message.answer(
                "Янги туманни танланг:",
                reply_markup=make_keyboard(districts, columns=2, with_back=True)
            )
            await state.set_state(EditProfile.district)
        else:
            await message.answer(
                "Янги вилоятни танланг:",
                reply_markup=make_keyboard(get_all_regions(), columns=2, with_back=True)
            )
            await state.set_state(EditProfile.region)
        return
    if not await require_subscription(message, role, state):
        return
    company_name = message.text.strip()
    if not company_name or len(company_name) > MAX_COMPANY_NAME_LENGTH:
        await message.answer(
            f"Компания номи бўш ёки {MAX_COMPANY_NAME_LENGTH} белгидан узун бўлмаслиги керак:",
            reply_markup=make_keyboard(["Орқага"], columns=1)
        )
        return
    await state.update_data(company_name=company_name)
    await save_edited_profile(message, state)

async def profile_delete(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"User {user_id} not allowed to delete profile")
        return
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(role, role)
    if not await require_subscription(message, role, state):
        return

    from database import db_lock
    async with db_lock:
        conn = None
        try:
            conn = await aiosqlite.connect(DB_NAME)
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute("BEGIN TRANSACTION")

            async with conn.execute(
                    "SELECT id, phone_number, role, region, district, company_name, unique_id FROM users WHERE id = ?",
                    (user_id,)
            ) as cursor:
                user = await cursor.fetchone()
            if not user:
                await message.answer("❌ Профиль не найден", reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True))
                await conn.execute("ROLLBACK")
                await state.clear()
                return

            deleted_at = datetime.now().strftime("%d %B %Y йил %H:%M:%S")
            await conn.execute(
                """INSERT INTO deleted_users 
                (user_id, phone_number, role, region, district, company_name, unique_id, deleted_at, blocked) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user[0], user[1], user[2], user[3], user[4], user[5], user[6], deleted_at, False)
            )
            logger.debug(f"User {user_id} saved to deleted_users with deleted_at={deleted_at}")

            await conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            logger.debug(f"User {user_id} deleted from users")

            await conn.commit()

            await message.answer(
                "✅ Профиль успешно удалён. Для повторной регистрации нажмите кнопку:",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.clear()
            logger.info(f"User {user_id} successfully deleted profile, payments preserved")

        except aiosqlite.Error as e:
            if conn:
                await conn.execute("ROLLBACK")
            logger.error(f"Database error during profile deletion for {user_id}: {str(e)}")
            await message.answer(
                "❌ Ошибка при удалении профиля. Пожалуйста, попробуйте позже или обратитесь к администратору.",
                reply_markup=message.bot.get_profile_menu(role=display_role)
            )
            await state.set_state(ProfileStates.main)
        finally:
            if conn:
                await conn.close()

def register_handlers(dp: Dispatcher):
    dp.message.register(profile_menu, F.text == "Менинг профилим")
    dp.message.register(process_profile_action, ProfileStates.main)
    dp.message.register(profile_edit_region, EditProfile.region)
    dp.message.register(profile_edit_district, EditProfile.district)
    dp.message.register(profile_edit_company_name, EditProfile.company_name)
    logger.debug("Profile handlers registered successfully")