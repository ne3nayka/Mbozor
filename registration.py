import aiosqlite
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import types, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import DB_NAME, ADMIN_IDS, ADMIN_ROLE, MAX_COMPANY_NAME_LENGTH
from database import generate_user_id, db_lock
from utils import check_role, make_keyboard, format_uz_datetime, parse_uz_datetime
from regions import get_all_regions, get_districts_for_region

logger = logging.getLogger(__name__)

MIN_PHONE_LENGTH = 10
MAX_PHONE_LENGTH = 13


class Registration(StatesGroup):
    phone = State()
    role = State()
    region = State()
    district = State()
    company_name = State()
    subscription = State()


ROLE_MAPPING = {"Сотувчи": "seller", "Харидор": "buyer", "Админ": "admin"}


def register_handlers(dp: Dispatcher):
    @dp.message(F.text == "Рўйхатдан ўтиш")
    async def register_phone(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        logger.debug(f"register_phone: Начало обработки для user_id={user_id}")
        allowed, role = await check_role(message, allow_unregistered=True)
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT role, phone_number FROM users WHERE id = ?", (user_id,)) as cursor:
                existing_user = await cursor.fetchone()
            async with conn.execute("SELECT bot_expires, trial_used FROM payments WHERE user_id = ?",
                                    (user_id,)) as cursor:
                payment = await cursor.fetchone()
        if existing_user:
            db_role, phone = existing_user
            display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(db_role, db_role)
            bot_expires = payment[0] if payment else None
            subscription_active = bot_expires and datetime.now() < parse_uz_datetime(bot_expires)
            expires_str = format_uz_datetime(parse_uz_datetime(bot_expires)) if bot_expires else "Фаол эмас"
            await message.answer(
                f"Сиз рўйхатдан ўтгансиз!\nРол: {display_role}\nТелефон: {phone}\nОбуна: {'Актив' if subscription_active else 'Фаол эмас'} ({expires_str})",
                reply_markup=message.bot.get_main_menu(display_role)
            )
            await state.clear()
            logger.info(f"register_phone: Пользователь {user_id} уже зарегистрирован с ролью {display_role}")
            return
        # Если пользователь удалил профиль, но подписка осталась
        if payment:
            bot_expires, trial_used = payment
            subscription_active = bot_expires and datetime.now() < parse_uz_datetime(bot_expires)
            expires_str = format_uz_datetime(parse_uz_datetime(bot_expires)) if bot_expires else "Фаол эмас"
            await state.update_data(bot_expires=bot_expires, trial_used=trial_used)  # Сохраняем данные о подписке
            if subscription_active:
                await message.answer(
                    f"Сизда фаол обуна мавжуд ({expires_str}). Янгиланган профильни рўйхатдан ўтказинг:",
                    reply_markup=types.ReplyKeyboardMarkup(
                        keyboard=[[types.KeyboardButton(text="Телефон рақамни юбориш", request_contact=True)]],
                        resize_keyboard=True, one_time_keyboard=True
                    )
                )
            else:
                await message.answer(
                    f"Сизнинг олдинги обунангиз тугаган ({expires_str}). Янги профиль учун телефон рақамингизни юборинг:",
                    reply_markup=types.ReplyKeyboardMarkup(
                        keyboard=[[types.KeyboardButton(text="Телефон рақамни юбориш", request_contact=True)]],
                        resize_keyboard=True, one_time_keyboard=True
                    )
                )
        else:
            await message.answer(
                "Телефон рақамингизни юборинг:",
                reply_markup=types.ReplyKeyboardMarkup(
                    keyboard=[[types.KeyboardButton(text="Телефон рақамни юбориш", request_contact=True)]],
                    resize_keyboard=True, one_time_keyboard=True
                )
            )
        await state.set_state(Registration.phone)
        logger.debug(f"register_phone: Установлено состояние Registration.phone для user_id={user_id}")

    @dp.message(Registration.phone, F.content_type.in_([ContentType.CONTACT, ContentType.TEXT]))
    async def process_phone(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        if message.text == "Орқага":
            await message.answer("Рўйхатдан ўтиш бекор қилинди.",
                                 reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True))
            await state.clear()
            return
        if message.content_type != ContentType.CONTACT:
            await message.answer(
                "Илтимос, контакт орқали телефон юборинг:",
                reply_markup=types.ReplyKeyboardMarkup(
                    keyboard=[[types.KeyboardButton(text="Телефон рақамни юбориш", request_contact=True)]],
                    resize_keyboard=True, one_time_keyboard=True
                )
            )
            return
        phone = message.contact.phone_number
        if not phone.startswith("+"):
            phone = f"+{phone}"
        if not (MIN_PHONE_LENGTH <= len(phone) <= MAX_PHONE_LENGTH and phone[1:].isdigit()):
            await message.answer(
                f"Телефон формати нотўғри ({MIN_PHONE_LENGTH}-{MAX_PHONE_LENGTH} белги):",
                reply_markup=types.ReplyKeyboardMarkup(
                    keyboard=[[types.KeyboardButton(text="Телефон рақамни юбориш", request_contact=True)]],
                    resize_keyboard=True, one_time_keyboard=True
                )
            )
            return
        await state.update_data(phone=phone)
        if user_id in ADMIN_IDS:
            await state.update_data(role=ROLE_MAPPING["Админ"])
            async with db_lock:
                async with aiosqlite.connect(DB_NAME) as conn:
                    unique_id = await generate_user_id("Админ")
                    await conn.execute(
                        "INSERT INTO users (id, phone_number, role, region, unique_id) VALUES (?, ?, ?, ?, ?)",
                        (user_id, phone, ADMIN_ROLE, "Тошкент шаҳри", unique_id)
                    )
                    await conn.execute(
                        "INSERT OR REPLACE INTO payments (user_id, bot_expires, trial_used) VALUES (?, ?, ?)",
                        (user_id, None, 0)
                    )
                    await conn.commit()
            await complete_registration(message, state)
        else:
            await message.answer("Ролни танланг:", reply_markup=make_keyboard(["Сотувчи", "Харидор"], columns=2))
            await state.set_state(Registration.role)

    @dp.message(Registration.role)
    async def process_role(message: types.Message, state: FSMContext):
        if message.text == "Орқага":
            await message.answer(
                "Телефон рақамингизни юборинг:",
                reply_markup=types.ReplyKeyboardMarkup(
                    keyboard=[[types.KeyboardButton(text="Телефон рақамни юбориш", request_contact=True)]],
                    resize_keyboard=True, one_time_keyboard=True
                )
            )
            await state.set_state(Registration.phone)
            return
        role = message.text
        if role not in ["Сотувчи", "Харидор"]:
            await message.answer("Ролни танланг:", reply_markup=make_keyboard(["Сотувчи", "Харидор"], columns=2))
            return
        await state.update_data(role=role)
        regions = get_all_regions()
        await message.answer("Вилоятни танланг:", reply_markup=make_keyboard(regions, columns=2, with_back=True))
        await state.set_state(Registration.region)

    @dp.message(Registration.region)
    async def process_region(message: types.Message, state: FSMContext):
        if message.text == "Орқага":
            await message.answer("Ролни танланг:", reply_markup=make_keyboard(["Сотувчи", "Харидор"], columns=2))
            await state.set_state(Registration.role)
            return
        region = message.text.strip()
        regions = get_all_regions()
        if region not in regions:
            await message.answer("Вилоятни танланг:", reply_markup=make_keyboard(regions, columns=2, with_back=True))
            return
        await state.update_data(region=region)
        if region == "Тошкент шаҳри":
            await check_company_name(message, state)
        else:
            districts = get_districts_for_region(region)
            if not districts:
                await state.update_data(district=None)
                await check_company_name(message, state)
            else:
                await message.answer("Туманни танланг:",
                                     reply_markup=make_keyboard(districts, columns=2, with_back=True))
                await state.set_state(Registration.district)

    @dp.message(Registration.district)
    async def process_district(message: types.Message, state: FSMContext):
        data = await state.get_data()
        if message.text == "Орқага":
            regions = get_all_regions()
            await message.answer("Вилоятни танланг:", reply_markup=make_keyboard(regions, columns=2, with_back=True))
            await state.set_state(Registration.region)
            return
        district = message.text.strip()
        districts = get_districts_for_region(data["region"])
        if district not in districts:
            await message.answer("Туманни танланг:", reply_markup=make_keyboard(districts, columns=2, with_back=True))
            return
        await state.update_data(district=district)
        await check_company_name(message, state)

    @dp.message(Registration.company_name)
    async def process_company_name(message: types.Message, state: FSMContext):
        data = await state.get_data()
        if message.text == "Орқага":
            if data["region"] == "Тошкент шаҳри":
                regions = get_all_regions()
                await message.answer("Вилоятни танланг:",
                                     reply_markup=make_keyboard(regions, columns=2, with_back=True))
                await state.set_state(Registration.region)
            else:
                districts = get_districts_for_region(data["region"])
                await message.answer("Туманни танланг:",
                                     reply_markup=make_keyboard(districts, columns=2, with_back=True))
                await state.set_state(Registration.district)
            return
        company_name = message.text.strip()
        if not company_name or len(company_name) > MAX_COMPANY_NAME_LENGTH:
            await message.answer(
                f"Компания номи бўш ёки {MAX_COMPANY_NAME_LENGTH} белгидан узун бўлмаслиги керак:",
                reply_markup=make_keyboard(["Орқага"], columns=1)
            )
            return
        await state.update_data(company_name=company_name)
        await complete_registration(message, state)

    @dp.message(Registration.subscription)
    async def process_subscription(message: types.Message, state: FSMContext):
        data = await state.get_data()
        user_id = message.from_user.id
        role = data.get("role")
        display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(ROLE_MAPPING[role], role)
        if message.text == "Орқага":
            await message.answer("Рўйхатдан ўтиш якунланди.", reply_markup=message.bot.get_main_menu(display_role))
            await state.clear()
            return
        if message.text != "Обуна":
            await message.answer("Обуна тугмасини босинг:", reply_markup=make_keyboard(["Обуна"], columns=1))
            return
        trial_end_str = data.get("trial_end_str", "Не указано")
        subscription_message = (
            "Ботга обуна: Фаол\nТўлиқ обуна (30 кун):\n1. Каналга обуна: 10,000 сўм\n2. Бот + Канал: 50,000 сўм/ой\n"
            "Тўловдан сўнг админга ёзинг (@MSMA_UZ): /myid\nТўлов: Click/Payme\n"
            f"Тест даври тугаш вақти: {trial_end_str}"
        )
        await message.answer(subscription_message, reply_markup=message.bot.get_main_menu(display_role))
        await state.clear()


async def check_company_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if data["role"] == "Сотувчи":
        await message.answer("Компания номини киритинг:", reply_markup=make_keyboard(["Орқага"], columns=1))
        await state.set_state(Registration.company_name)
    else:
        await complete_registration(message, state)


async def complete_registration(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    if "phone" not in data or "role" not in data:
        await message.answer("Хатолик: Маълумот тўлиқ эмас.",
                             reply_markup=make_keyboard(["Рўйхатдан ўтиш"], one_time=True))
        await state.clear()
        return
    db_role = ROLE_MAPPING[data["role"]]
    display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(db_role, db_role)
    unique_id = await generate_user_id(data["role"])
    region = data.get("region", "Тошкент шаҳри")

    # Проверяем существующую подписку из FSM
    bot_expires = data.get("bot_expires")
    trial_used = data.get("trial_used")

    # Если подписки нет (новый пользователь), создаём тестовую
    if not bot_expires and db_role != ADMIN_ROLE:
        trial_end = datetime.now() + timedelta(days=3)
        trial_end_str = format_uz_datetime(trial_end)
        trial_used = 1
    else:
        # Используем существующую подписку или None для админов
        trial_end_str = format_uz_datetime(parse_uz_datetime(bot_expires)) if bot_expires else None
        trial_used = trial_used if trial_used is not None else 0

    async with db_lock:
        async with aiosqlite.connect(DB_NAME) as conn:
            await conn.execute(
                "INSERT INTO users (id, phone_number, role, region, district, company_name, unique_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, data["phone"], db_role, region, data.get("district"), data.get("company_name"), unique_id)
            )
            # Обновляем payments только если это новый пользователь без подписки
            if not bot_expires and db_role != ADMIN_ROLE:
                await conn.execute(
                    "INSERT OR REPLACE INTO payments (user_id, bot_expires, trial_used) VALUES (?, ?, ?)",
                    (user_id, trial_end_str, trial_used)
                )
            await conn.commit()

    # Формируем сообщение о профиле
    profile_message = (
        f"Рўйхатдан ўтиш якунланди!\n"
        f"Телефон: {data['phone']}\n"
        f"Рол: {display_role}\n"
        f"Вилоят: {region}\n"
        f"Туман: {data.get('district', 'Йўқ')}\n"
        f"Компания: {data.get('company_name', 'Йўқ')}\n"
        f"ID: {unique_id}\n"
    )

    # Добавляем информацию о подписке
    if bot_expires and datetime.now() < parse_uz_datetime(bot_expires):
        profile_message += f"Обуна фаол. {trial_end_str} гача"
    elif trial_end_str:
        profile_message += f"Сизга 3 кунлик тест даври берилди. Тугайди: {trial_end_str}"

    await message.answer(profile_message, reply_markup=message.bot.get_main_menu(display_role))
    await state.clear()
