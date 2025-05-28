import aiosqlite
import logging
from aiogram import types, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from config import DB_NAME, DB_TIMEOUT, SELLER_ROLE, MAX_COMPANY_NAME_LENGTH, ADMIN_ROLE, DISPLAY_ROLE_MAPPING, BUYER_ROLE
from utils import check_role, make_keyboard, check_subscription, get_profile_menu, get_main_menu, get_admin_menu, format_uz_datetime, notify_admin, has_pending_items
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
    """Проверяет наличие активной подписки только для бота."""
    user_id = message.from_user.id
    logger.debug(f"require_subscription: Проверка подписки для user_id={user_id}, роль={role}")
    if role == ADMIN_ROLE:
        logger.debug(f"Админ {user_id} пропущен без проверки подписки")
        return True
    try:
        if not hasattr(state.storage, 'redis'):
            logger.error(f"Storage не поддерживает Redis для user_id={user_id}")
            raise ValueError("Storage не поддерживает Redis")
        success, bot_active, is_subscribed = await check_subscription(message.bot, user_id, state.storage)
        logger.debug(f"check_subscription для user_id={user_id}: success={success}, bot_active={bot_active}, is_subscribed={is_subscribed}")

        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                    "SELECT bot_expires FROM payments WHERE user_id = ?",
                    (user_id,)
            ) as cursor:
                result = await cursor.fetchone()
        bot_expires = result[0] if result else None
        logger.debug(f"bot_expires из базы для user_id={user_id}: {bot_expires}")

        if not is_subscribed:
            await message.answer(
                "Сизда фаол обуна мавжуд эмас. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=get_main_menu(BUYER_ROLE)
            )
            await state.set_state("Registration:subscription")
            logger.info(f"Пользователь {user_id} заблокирован из-за отсутствия подписки")
            try:
                redis_keys = await state.storage.redis.keys(f"aiogram:*:{user_id}:*")
                if redis_keys:
                    await state.storage.redis.delete(*redis_keys)
                    logger.info(f"Очищено {len(redis_keys)} ключей Redis для user_id={user_id}")
            except Exception as e:
                logger.warning(f"Ошибка очистки Redis для user_id={user_id}: {e}")
            return False
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки подписки для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка проверки подписки для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        return False

async def profile_menu(message: types.Message, state: FSMContext) -> None:
    """Отображает меню профиля."""
    user_id = message.from_user.id
    raw_text = message.text
    logger.debug(f"profile_menu: user_id={user_id}, text='{raw_text}'")
    if raw_text != "Менинг профилим":
        logger.warning(f"Непредвиденный текст в profile_menu: user_id={user_id}, text='{raw_text}'")
        await message.answer(
            "Илтимос, менюдан амал танланг:",
            reply_markup=get_main_menu(None)
        )
        return
    try:
        allowed, role = await check_role(message)
        logger.debug(f"profile_menu: user_id={user_id}, allowed={allowed}, role={role}")
        if not allowed:
            # Проверяем наличие частичной регистрации
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                async with conn.execute(
                    "SELECT role, region, phone_number FROM users WHERE id = ?", (user_id,)
                ) as cursor:
                    user = await cursor.fetchone()
                if user and not all(user):  # Частичная регистрация
                    logger.warning(f"Частичная регистрация обнаружена для user_id={user_id}: role={user[0]}, region={user[1]}, phone_number={user[2]}")
                    await conn.execute(
                        "DELETE FROM users WHERE id = ? AND (role IS NULL OR region IS NULL OR phone_number IS NULL)", (user_id,)
                    )
                    await conn.commit()
                    logger.info(f"Удалена частичная регистрация для user_id={user_id}")
            await message.answer(
                "Профильга кириш учун рўйхатдан ўтинг:",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state("Registration:start")
            logger.info(f"Пользователь {user_id} перенаправлен на регистрацию")
            return
        if not await require_subscription(message, role, state):
            logger.debug(f"Пользователь {user_id} заблокирован проверкой подписки")
            return
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "📋 Менинг профилим:",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)
        logger.info(f"Пользователь {user_id} (роль={role}) вошёл в меню профиля")
    except Exception as e:
        logger.error(f"Ошибка в profile_menu для user_id {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в profile_menu для user_id {user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_main_menu(None)
        )

async def profile_info(message: types.Message, state: FSMContext) -> None:
    """Отображает информацию о профиле пользователя."""
    user_id = message.from_user.id
    raw_text = message.text
    logger.debug(f"profile_info: user_id={user_id}, text='{raw_text}'")
    if raw_text != "Профиль ҳақида маълумот":
        logger.warning(f"Непредвиденный текст в profile_info: user_id={user_id}, text='{raw_text}'")
        await message.answer(
            "Илтимос, профиль менюсидан амал танланг:",
            reply_markup=get_profile_menu()
        )
        return
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"Пользователь {user_id} не имеет доступа к профилю")
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
            logger.warning(f"Профиль не найден для user_id {user_id}")
            return
        info = (
            f"📞 Телефон: {user[0]}\n"
            f"🎭 Рол: {display_role}\n"
            f"🌍 Вилоят: {user[2] or 'Йўқ'}\n"
            f"🏞 Туман: {user[3] or 'Йўқ'}\n"
        )
        if role == SELLER_ROLE:
            info += f"🏢 Ташкилот: {user[4] or 'Йўқ'}\n"
        info += f"🆔 ID: {user[5]}"
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            f"Сизнинг профилингиз:\n\n{info}",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)
        logger.info(f"Пользователь {user_id} просмотрел информацию о профиле")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка загрузки профиля для user_id {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка загрузки профиля для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Профильни юклашда хатолик. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)

async def edit_profile_start(message: types.Message, state: FSMContext) -> None:
    """Запускает процесс редактирования профиля."""
    user_id = message.from_user.id
    raw_text = message.text
    logger.debug(f"edit_profile_start: user_id={user_id}, text='{raw_text}'")
    if raw_text != "Профильни таҳрирлаш":
        logger.warning(f"Непредвиденный текст в edit_profile_start: user_id={user_id}, text='{raw_text}'")
        await message.answer(
            "Илтимос, профиль менюсидан амал танланг:",
            reply_markup=get_profile_menu()
        )
        return
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"Пользователь {user_id} не имеет доступа к редактированию профиля")
        await message.answer(
            "Профильни таҳрирлаш учун рўйхатдан ўтиш керак.",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state("Registration:start")
        return
    if not await require_subscription(message, role, state):
        logger.debug(f"Пользователь {user_id} заблокирован проверкой подписки")
        return
    try:
        regions = get_all_regions()
        if not regions:
            reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
            await message.answer(
                "Вилоятлар рўйхати бўш. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=reply_markup
            )
            await state.set_state(ProfileStates.main)
            logger.warning(f"Пустой список регионов для user_id {user_id}")
            return
        await message.answer(
            "Янги вилоятни танланг:",
            reply_markup=make_keyboard(regions, columns=2, with_back=True)
        )
        await state.set_state(EditProfile.region)
        await state.update_data(role=role)
        logger.info(f"Пользователь {user_id} начал редактирование профиля, роль={role}, перешёл в EditProfile.region")
    except Exception as e:
        logger.error(f"Ошибка в edit_profile_start для user_id {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в edit_profile_start для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)

async def process_region(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает выбор нового региона."""
    user_id = message.from_user.id
    region = message.text
    data = await state.get_data()
    role = data.get("role")  # Исправлено: "roleempl" -> "role"
    regions = get_all_regions()
    current_state = await state.get_state()
    logger.debug(f"process_region: user_id={user_id}, region='{region}', state={current_state}")
    if current_state != EditProfile.region.state:
        logger.warning(f"Непредвиденное состояние для process_region: user_id={user_id}, state={current_state}")
        await state.set_state(ProfileStates.main)
        # Проверка на None для role, чтобы избежать ошибки
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer("Профиль менюси:", reply_markup=reply_markup)
        return
    try:
        if region == "Орқага":
            reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
            await message.answer("Профиль менюси:", reply_markup=reply_markup)
            await state.set_state(ProfileStates.main)
            logger.info(f"Пользователь {user_id} вернулся в меню профиля из выбора региона")
            return
        if region not in regions:
            await message.answer(
                "Илтимос, рўйхатдан вилоят танланг:",
                reply_markup=make_keyboard(regions, columns=2, with_back=True)
            )
            logger.warning(f"Пользователь {user_id} выбрал некорректный регион: {region}")
            return
        await state.update_data(region=region)
        if role == SELLER_ROLE:
            districts = get_districts_for_region(region)
            if not districts:
                async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                    await conn.execute(
                        "UPDATE users SET region = ?, district = ? WHERE id = ?",
                        (region, "Йўқ", user_id)
                    )
                    await conn.commit()
                await message.answer(
                    f"Янги ташкилот номини киритинг (макс. {MAX_COMPANY_NAME_LENGTH} белги):",
                    reply_markup=make_keyboard(["Орқага"], one_time=True)
                )
                await state.set_state(EditProfile.company_name)
                logger.info(f"Пользователь {user_id} выбрал регион {region} без района, перешёл в EditProfile.company_name")
            else:
                await message.answer(
                    "Янги туманни танланг:",
                    reply_markup=make_keyboard(districts, columns=2, with_back=True)
                )
                await state.set_state(EditProfile.district)
                logger.info(f"Пользователь {user_id} выбрал регион {region}, перешёл в EditProfile.district")
        else:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                await conn.execute(
                    "UPDATE users SET region = ?, district = ? WHERE id = ?",
                    (region, "Йўқ", user_id)
                )
                await conn.commit()
            await message.answer(
                "✅ Профиль янгиланди! Асосий менюга қайтдик.",
                reply_markup=get_main_menu(role)
            )
            await state.clear()
            logger.info(f"Пользователь {user_id} обновил регион {region} для роли Харидор")
    except Exception as e:
        logger.error(f"Ошибка обработки региона для user_id {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка обработки региона для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)

async def process_district(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает выбор нового района для продавцов."""
    user_id = message.from_user.id
    district = message.text
    data = await state.get_data()
    role = data.get("role")
    region = data.get("region")
    districts = get_districts_for_region(region)
    current_state = await state.get_state()
    logger.debug(f"process_district: user_id={user_id}, district='{district}', state={current_state}")
    if current_state != EditProfile.district.state:
        logger.warning(f"Непредвиденное состояние для process_district: user_id={user_id}, state={current_state}")
        await state.set_state(ProfileStates.main)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer("Профиль менюси:", reply_markup=reply_markup)
        return
    try:
        if district == "Орқага":
            regions = get_all_regions()
            await message.answer(
                "Янги вилоятни танланг:",
                reply_markup=make_keyboard(regions, columns=2, with_back=True)
            )
            await state.set_state(EditProfile.region)
            logger.info(f"Пользователь {user_id} вернулся к выбору региона из района")
            return
        if district not in districts:
            await message.answer(
                "Илтимос, рўйхатдан туман танланг:",
                reply_markup=make_keyboard(districts, columns=2, with_back=True)
            )
            logger.warning(f"Пользователь {user_id} выбрал некорректный район: {district}")
            return
        await state.update_data(district=district)
        await message.answer(
            f"Янги ташкилот номини киритинг (макс. {MAX_COMPANY_NAME_LENGTH} белги):",
            reply_markup=make_keyboard(["Орқага"], one_time=True)
        )
        await state.set_state(EditProfile.company_name)
        logger.info(f"Пользователь {user_id} выбрал район {district}, перешёл к EditProfile.company_name")
    except Exception as e:
        logger.error(f"Ошибка обработки района для user_id {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка обработки района для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)

async def process_company_name(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает ввод нового названия компании для продавцов."""
    user_id = message.from_user.id
    company_name = message.text
    data = await state.get_data()
    role = data.get("role")
    current_state = await state.get_state()
    logger.debug(f"process_company_name: user_id={user_id}, company_name='{company_name}', state={current_state}")
    if current_state != EditProfile.company_name.state:
        logger.warning(f"Непредвиденное состояние для process_company_name: user_id={user_id}, state={current_state}")
        await state.set_state(ProfileStates.main)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer("Профиль менюси:", reply_markup=reply_markup)
        return
    try:
        if company_name == "Орқага":
            districts = get_districts_for_region(data.get("region"))
            if not districts:
                regions = get_all_regions()
                await message.answer(
                    "Янги вилоятни танланг:",
                    reply_markup=make_keyboard(regions, columns=2, with_back=True)
                )
                await state.set_state(EditProfile.region)
                logger.info(f"Пользователь {user_id} вернулся к выбору региона из ввода компании (пустые туманы)")
            else:
                await message.answer(
                    "Янги туманни танланг:",
                    reply_markup=make_keyboard(districts, columns=2, with_back=True)
                )
                await state.set_state(EditProfile.district)
                logger.info(f"Пользователь {user_id} вернулся к выбору района из ввода компании")
            return
        if len(company_name) > MAX_COMPANY_NAME_LENGTH:
            await message.answer(
                f"Ташкилот номи {MAX_COMPANY_NAME_LENGTH} белгидан ошмаслиги керак. Илтимос, қайта киритинг:",
                reply_markup=make_keyboard(["Орқага"], one_time=True)
            )
            logger.warning(f"Пользователь {user_id} ввёл слишком длинное название компании: {len(company_name)} символов")
            return
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute(
                "UPDATE users SET region = ?, district = ?, company_name = ? WHERE id = ?",
                (data["region"], data.get("district", "Йўқ"), company_name, user_id)
            )
            await conn.commit()
        await message.answer(
            "✅ Профиль янгиланди! Асосий менюга қайтдик.",
            reply_markup=get_main_menu(role)
        )
        await state.clear()
        logger.info(f"Пользователь {user_id} обновил профиль: region={data['region']}, district={data.get('district', 'Йўқ')}, company_name={company_name}")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка обновления профиля для user_id {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка обновления профиля для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Профильни янгилашда хатолик. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в process_company_name для user_id={user_id}: {e}", exc_info=True)
        await notify_admin(f"Непредвиденная ошибка в process_company_name для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Орқага"], one_time=True)
        )
        await state.set_state(EditProfile.company_name)

async def profile_delete(message: types.Message, state: FSMContext) -> None:
    """Удаляет профиль пользователя после проверки незавершённых элементов."""
    user_id = message.from_user.id
    raw_text = message.text
    logger.debug(f"profile_delete: user_id={user_id}, text='{raw_text}'")
    if raw_text != "Профильни ўчириш":
        logger.warning(f"Непредвиденный текст в profile_delete: user_id={user_id}, text='{raw_text}'")
        await message.answer(
            "Илтимос, профиль менюсидан амал танланг:",
            reply_markup=get_profile_menu()
        )
        return
    allowed, role = await check_role(message)
    if not allowed:
        logger.info(f"Пользователь {user_id} не имеет доступа к удалению профиля")
        await message.answer(
            "Профильни ўчириш учун рўйхатдан ўтиш керак.",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state("Registration:start")
        return
    try:
        if await has_pending_items(user_id):
            await message.answer(
                "Сизда фаол эълонлар ёки сўровлар мавжуд. Аввал уларни якунланг.",
                reply_markup=get_profile_menu()
            )
            await state.set_state(ProfileStates.main)
            logger.info(f"Удаление профиля для user_id {user_id} заблокировано из-за активных элементов")
            return
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
                logger.warning(f"Профиль не найден для удаления для user_id {user_id}")
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
                    logger.info(f"Очищено {len(redis_keys)} ключей Redis для user_id {user_id}")
        except Exception as e:
            logger.warning(f"Не удалось очистить состояние Redis для user_id {user_id}: {e}", exc_info=True)
        await message.answer(
            "✅ Профиль муваффақиятли ўчирилди. Қайта рўйхатдан ўтиш учун тугмани босинг:",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш", "Орқага"], columns=2, one_time=True)
        )
        await state.set_state("Registration:start")
        logger.info(f"Пользователь {user_id} удалил свой профиль и переведён в Registration:start")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка удаления профиля для user_id {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка удаления профиля для user_id {user_id}: {str(e)}", bot=message.bot)
        reply_markup = get_admin_menu() if role == ADMIN_ROLE else get_profile_menu()
        await message.answer(
            "Профильни ўчиришда хатолик. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=reply_markup
        )
        await state.set_state(ProfileStates.main)

async def handle_start_registration_after_delete(message: types.Message, state: FSMContext, dp: Dispatcher) -> None:
    """Обрабатывает команду 'Рўйхатдан ўтиш' после удаления профиля."""
    user_id = message.from_user.id
    raw_text = message.text
    logger.debug(f"handle_start_registration_after_delete: user_id={user_id}, text='{raw_text}'")
    if raw_text != "Рўйхатдан ўтиш":
        logger.warning(f"Непредвиденный текст в handle_start_registration_after_delete: user_id={user_id}, text='{raw_text}'")
        await message.answer(
            "Илтимос, 'Рўйхатдан ўтиш' тугмасини босинг:",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        return
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            # Проверка блокировки
            async with conn.execute(
                "SELECT blocked FROM deleted_users WHERE user_id = ? AND blocked = TRUE", (user_id,)
            ) as cursor:
                blocked = await cursor.fetchone()
            if blocked:
                await message.answer(
                    "Сизнинг Telegram ID блокланган. Админга мурожаат қилинг (@ad_mbozor).",
                    reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
                )
                await state.clear()
                logger.warning(f"Заблокированный пользователь {user_id} пытался начать регистрацию")
                return

            # Проверка полной регистрации
            async with conn.execute(
                "SELECT role, region, phone_number FROM users WHERE id = ?", (user_id,)
            ) as cursor:
                user = await cursor.fetchone()
                if user and all(user):
                    await message.answer(
                        "Сиз аллақачон рўйхатдан ўтгансиз. Асосий менюга қайтинг:",
                        reply_markup=get_main_menu(user[0])
                    )
                    await state.clear()
                    logger.warning(f"Пользователь {user_id} уже полностью зарегистрирован")
                    return

            # Удаление частичной регистрации
            await conn.execute(
                "DELETE FROM users WHERE id = ? AND (role IS NULL OR region IS NULL OR phone_number IS NULL)", (user_id,)
            )
            await conn.commit()

        # Переход к запросу телефона
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Телефон рақамини улашиш", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            "Илтимос, телефон рақамингизни улашинг:",
            reply_markup=keyboard
        )
        await state.set_state("Registration:phone")
        logger.info(f"Пользователь {user_id} начал регистрацию после удаления профиля, переведён в Registration:phone")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных в handle_start_registration_after_delete для user_id={user_id}: {e}")
        await notify_admin(f"Ошибка базы данных в handle_start_registration_after_delete для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state("Registration:start")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_start_registration_after_delete для user_id={user_id}: {e}")
        await notify_admin(f"Непредвиденная ошибка в handle_start_registration_after_delete для user_id={user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
        )
        await state.set_state("Registration:start")

async def handle_back_button(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает нажатие кнопки 'Орқага' в меню профиля."""
    user_id = message.from_user.id
    raw_text = message.text
    current_state = await state.get_state()
    logger.debug(f"handle_back_button: user_id={user_id}, text='{raw_text}', state={current_state}")
    if raw_text != "Орқага" or current_state != ProfileStates.main.state:
        logger.debug(f"handle_back_button пропущен: user_id={user_id}, text='{raw_text}', state={current_state}")
        return
    try:
        allowed, role = await check_role(message)
        if not allowed:
            logger.info(f"Пользователь {user_id} не имеет доступа")
            await message.answer(
                "Профиль менюсига кириш учун рўйхатдан ўтиш керак.",
                reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True)
            )
            await state.set_state("Registration:start")
            return
        await message.answer(
            "Асосий меню:",
            reply_markup=get_main_menu(role)
        )
        await state.clear()
        logger.info(f"Пользователь {user_id} вернулся в основное меню через кнопку назад")
    except Exception as e:
        logger.error(f"Ошибка в handle_back_button для user_id {user_id}: {e}", exc_info=True)
        await notify_admin(f"Ошибка в handle_back_button для user_id {user_id}: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=get_main_menu(None)
        )

def register_handlers(dp: Dispatcher):
    """Регистрирует обработчики для профиля."""
    logger.info("Регистрация обработчиков профиля")
    dp.message.register(profile_menu, F.text == "Менинг профилим")
    dp.message.register(profile_info, F.text == "Профиль ҳақида маълумот")
    dp.message.register(edit_profile_start, F.text == "Профильни таҳрирлаш")
    dp.message.register(profile_delete, F.text == "Профильни ўчириш")
    dp.message.register(process_region, EditProfile.region)
    dp.message.register(process_district, EditProfile.district)
    dp.message.register(process_company_name, EditProfile.company_name)
    dp.message.register(handle_back_button, ProfileStates.main, F.text == "Орқага")
    dp.message.register(handle_start_registration_after_delete, F.text == "Рўйхатдан ўтиш")
    logger.info("Обработчики профиля зарегистрированы")