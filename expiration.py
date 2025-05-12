import aiosqlite
import asyncio
import logging
from datetime import datetime, timedelta
import pytz
from aiogram import Bot, Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from config import DB_NAME, DB_TIMEOUT, ADMIN_IDS
from utils import format_uz_datetime, parse_uz_datetime, make_keyboard, check_subscription, notify_admin

logger = logging.getLogger(__name__)

router = Router()

class ExpiredItem(StatesGroup):
    choice = State()
    final_price = State()

async def notify_user(bot: Bot, user_id: int, unique_id: str, is_request: bool, state: FSMContext):
    """Фойдаланувчига эълон ёки сўров муддати тугаганлиги ҳақида хабар беради."""
    table = "requests" if is_request else "products"
    action_type = "Сўров" if is_request else "Эълон"
    current_state = await state.get_state()
    logger.debug(f"notify_user: user_id={user_id}, {table} {unique_id}, жорий ҳолат={current_state} текширилмоқда")
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT blocked FROM deleted_users WHERE user_id = ? AND blocked = TRUE", (user_id,)
            ) as cursor:
                if await cursor.fetchone():
                    logger.warning(f"Блокланган фойдаланувчи {user_id} {table} {unique_id} ҳақида хабарнома олмади")
                    return
    except aiosqlite.Error as e:
        logger.error(f"Фойдаланувчи {user_id} блокировкасини текширишда маълумотлар базаси хатолиги: {e}")
        await notify_admin(f"notify_user да маълумотлар базаси хатолиги user_id={user_id}: {str(e)}", bot=bot)
        return

    if user_id not in ADMIN_IDS:
        try:
            channel_active, bot_active, is_subscribed = await check_subscription(bot, user_id)
            if not is_subscribed:
                logger.info(f"Фойдаланувчи {user_id} обуна фаол эмаслиги сабабли {table} {unique_id} хабарномасини олмади")
                await notify_admin(f"Фойдаланувчи {user_id} обуна фаол эмаслиги сабабли {table} {unique_id} хабарномасини ўтказиб юборди", bot=bot)
                return
        except Exception as e:
            logger.error(f"Фойдаланувчи {user_id} обунасини текширишда хатолик: {e}")
            await notify_admin(f"notify_user да обуна текшириш хатолиги user_id={user_id}: {str(e)}", bot=bot)
            return

    keyboard = make_keyboard([f"Якуний нарҳни киритинг", f"{action_type}ни бекор қилинг"], columns=1)
    try:
        await bot.send_message(
            user_id,
            f"Сизнинг {action_type.lower()}ингиз {unique_id} муддати тугади. Якуний нарҳни киритинг ёки бекор қилинг:",
            reply_markup=keyboard,
            reply_to_message_id=None,
            disable_notification=False
        )
        await state.update_data(unique_id=unique_id, is_request=is_request)
        await state.set_state(ExpiredItem.choice)
        logger.info(f"{table} {unique_id} учун хабарнома фойдаланувчи {user_id} га юборилди")
    except Exception as e:
        logger.error(f"Фойдаланувчи {user_id} га {table} {unique_id} муддати тугаганлиги ҳақида хабар юбориб бўлмади: {e}")
        await notify_admin(f"Фойдаланувчи {user_id} га {table} {unique_id} муддати тугаганлиги ҳақида хабар юбориб бўлмади: {str(e)}", bot=bot)

@router.message(ExpiredItem.choice, F.text.in_(["Якуний нарҳни киритинг", "Эълонни бекор қилинг", "Сўровни бекор қилинг"]))
async def handle_expired_choice(message: Message, state: FSMContext):
    """Муддати тугаган элемент учун танловни қайта ишлайди."""
    user_id = message.from_user.id
    data = await state.get_data()
    unique_id = data.get("unique_id")
    is_request = data.get("is_request")
    table = "requests" if is_request else "products"
    action_type = "Сўров" if is_request else "Эълон"
    logger.info(f"handle_expired_choice: Фойдаланувчи {user_id} '{message.text}' ни танлади {table} {unique_id} учун")

    if message.text == "Якуний нарҳни киритинг":
        await message.answer(
            "Якуний нарҳни киритинг (сўмда):",
            reply_markup=make_keyboard(["Орқага"], columns=1)
        )
        await state.set_state(ExpiredItem.final_price)
    elif message.text in ["Эълонни бекор қилинг", "Сўровни бекор қилинг"]:
        try:
            async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                await conn.execute(
                    f"UPDATE {table} SET status = 'archived', archived_at = ? WHERE unique_id = ?",
                    (format_uz_datetime(datetime.now(pytz.UTC)), unique_id)
                )
                await conn.commit()
            await message.answer(
                f"{action_type} {unique_id} бекор қилинди.",
                reply_markup=make_keyboard(["Асосий меню"], columns=1)
            )
            await state.clear()
            logger.info(f"{action_type} {unique_id} фойдаланувчи {user_id} томонидан бекор қилинди")
        except aiosqlite.Error as e:
            logger.error(f"{table} {unique_id} архивида маълумотлар базаси хатолиги user_id={user_id}: {e}")
            await notify_admin(f"{table} {unique_id} архивида маълумотлар базаси хатолиги: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Асосий меню"], columns=1)
            )
            await state.clear()

@router.message(ExpiredItem.choice)
async def handle_invalid_expired_choice(message: Message, state: FSMContext):
    """Муддати тугаган элемент учун нотўғри танловни қайта ишлайди."""
    user_id = message.from_user.id
    data = await state.get_data()
    unique_id = data.get("unique_id")
    is_request = data.get("is_request")
    action_type = "Сўров" if is_request else "Эълон"
    await message.answer(
        f"Илтимос, '{action_type}ни бекор қилинг' ёки 'Якуний нарҳни киритинг' ни танланг:",
        reply_markup=make_keyboard([f"Якуний нарҳни киритинг", f"{action_type}ни бекор қилинг"], columns=1)
    )
    logger.warning(f"Фойдаланувчи {user_id} {action_type.lower()} {unique_id} учун нотўғри танлов юборди: {message.text}")

@router.message(ExpiredItem.final_price, F.text == "Орқага")
async def handle_final_price_back(message: Message, state: FSMContext):
    """Якуний нарҳдан орқага қайтишни қайта ишлайди."""
    user_id = message.from_user.id
    data = await state.get_data()
    unique_id = data.get("unique_id")
    is_request = data.get("is_request")
    action_type = "Сўров" if is_request else "Эълон"
    logger.info(f"handle_final_price_back: Фойдаланувчи {user_id} {action_type.lower()} {unique_id} учун орқага қайтди")
    await message.answer(
        f"Сизнинг {action_type.lower()}ингиз {unique_id} муддати тугади. Якуний нарҳни киритинг ёки бекор қилинг:",
        reply_markup=make_keyboard([f"Якуний нарҳни киритинг", f"{action_type}ни бекор қилинг"], columns=1)
    )
    await state.set_state(ExpiredItem.choice)

@router.message(ExpiredItem.final_price, F.text.is_digit())
async def handle_final_price(message: Message, state: FSMContext):
    """Якуний нарҳни қайта ишлайди."""
    user_id = message.from_user.id
    data = await state.get_data()
    unique_id = data.get("unique_id")
    is_request = data.get("is_request")
    table = "requests" if is_request else "products"
    action_type = "Сўров" if is_request else "Эълон"
    final_price = int(message.text)
    logger.info(f"handle_final_price: Фойдаланувчи {user_id} {table} {unique_id} учун якуний нарҳ {final_price} киритди")

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute(
                f"UPDATE {table} SET final_price = ?, status = 'completed', completed_at = ? WHERE unique_id = ?",
                (final_price, format_uz_datetime(datetime.now(pytz.UTC)), unique_id)
            )
            await conn.commit()
        await message.answer(
            f"{action_type} {unique_id} якуний нарҳи {final_price} сўм билан якунланди.",
            reply_markup=make_keyboard(["Асосий меню"], columns=1)
        )
        await state.clear()
        logger.info(f"{action_type} {unique_id} якуний нарҳи {final_price} билан якунланди user_id={user_id}")
    except aiosqlite.Error as e:
        logger.error(f"{table} {unique_id} якуний нарҳини янгилалишда маълумотлар базаси хатолиги: {e}")
        await notify_admin(f"{table} {unique_id} якуний нарҳини янгилалишда маълумотлар базаси хатолиги: {str(e)}", bot=message.bot)
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Асосий меню"], columns=1)
        )
        await state.clear()

@router.message(ExpiredItem.final_price)
async def handle_invalid_final_price(message: Message, state: FSMContext):
    """Нотўғри якуний нарҳни қайта ишлайди."""
    user_id = message.from_user.id
    data = await state.get_data()
    unique_id = data.get("unique_id")
    is_request = data.get("is_request")
    action_type = "Сўров" if is_request else "Эълон"
    await message.answer(
        "Илтимос, якуний нарҳни рақамларда киритинг (сўмда):",
        reply_markup=make_keyboard(["Орқага"], columns=1)
    )
    logger.warning(f"Фойдаланувчи {user_id} {action_type.lower()} {unique_id} учун нотўғри якуний нарҳ юборди: {message.text}")

async def check_expired_items(bot: Bot, storage):
    """Муддати тугаган элементларни текшириш учун фон вазифаси."""
    logger.info("Муддати тугаган элементларни текшириш учун фон вазифаси бошланди")
    try:
        while True:
            try:
                now = datetime.now(pytz.UTC)
                cutoff_time = now - timedelta(hours=25)
                cutoff_time_str = cutoff_time.strftime('%Y-%m-%d %H:%M:%S')
                logger.debug(f"Муддати тугаган элементлар текширилмоқда, cutoff_time={cutoff_time_str}")
                async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                    expired_count = 0
                    async with conn.execute(
                        "SELECT r.user_id, r.unique_id, r.created_at "
                        "FROM requests r "
                        "JOIN users u ON r.user_id = u.id "
                        "WHERE r.status = 'active' AND r.created_at >= ?",
                        (cutoff_time_str,)
                    ) as cursor:
                        requests = await cursor.fetchall()
                        logger.debug(f"{len(requests)} та фаол сўров топилди")
                    for user_id, unique_id, created_at in requests:
                        try:
                            created_at_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.UTC)
                        except ValueError:
                            logger.warning(f"Сўров {unique_id} учун нотўғри яратилган сана: {created_at}")
                            continue
                        expiration_time = created_at_dt + timedelta(hours=24)
                        if now >= expiration_time:
                            await conn.execute(
                                "UPDATE requests SET status = 'pending_response' WHERE unique_id = ?",
                                (unique_id,)
                            )
                            try:
                                state = FSMContext(
                                    storage=storage,
                                    key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
                                )
                                logger.debug(f"Сўров {unique_id} муддати тугади, муддат: {format_uz_datetime(expiration_time)}")
                                await notify_user(bot, user_id, unique_id, is_request=True, state=state)
                                expired_count += 1
                                logger.debug(f"Сўров {unique_id} pending_response деб белгиланди")
                            except Exception as e:
                                logger.error(f"FSMContext хатолиги user_id={user_id}, сўров {unique_id}: {e}")
                                await notify_admin(f"check_expired_items да FSMContext хатолиги user_id={user_id}: {str(e)}", bot=bot)
                                await bot.send_message(
                                    user_id,
                                    "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                                    reply_markup=make_keyboard(["Асосий меню"], columns=1)
                                )

                    async with conn.execute(
                        "SELECT p.user_id, p.unique_id, p.created_at "
                        "FROM products p "
                        "JOIN users u ON p.user_id = u.id "
                        "WHERE p.status = 'active' AND p.created_at >= ?",
                        (cutoff_time_str,)
                    ) as cursor:
                        products = await cursor.fetchall()
                        logger.debug(f"{len(products)} та фаол эълон топилди")
                    for user_id, unique_id, created_at in products:
                        try:
                            created_at_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.UTC)
                        except ValueError:
                            logger.warning(f"Эълон {unique_id} учун нотўғри яратилган сана: {created_at}")
                            continue
                        expiration_time = created_at_dt + timedelta(hours=24)
                        if now >= expiration_time:
                            await conn.execute(
                                "UPDATE products SET status = 'pending_response' WHERE unique_id = ?",
                                (unique_id,)
                            )
                            try:
                                state = FSMContext(
                                    storage=storage,
                                    key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
                                )
                                logger.debug(f"Эълон {unique_id} муддати тугади, муддат: {format_uz_datetime(expiration_time)}")
                                await notify_user(bot, user_id, unique_id, is_request=False, state=state)
                                expired_count += 1
                                logger.debug(f"Эълон {unique_id} pending_response деб белгиланди")
                            except Exception as e:
                                logger.error(f"FSMContext хатолиги user_id={user_id}, эълон {unique_id}: {e}")
                                await notify_admin(f"check_expired_items да FSMContext хатолиги user_id={user_id}: {str(e)}", bot=bot)
                                await bot.send_message(
                                    user_id,
                                    "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                                    reply_markup=make_keyboard(["Асосий меню"], columns=1)
                                )

                    await conn.commit()
                    logger.info(f"Текширув якунланди: {expired_count} та муддати тугаган элемент қайта ишланди")
            except aiosqlite.Error as e:
                logger.error(f"check_expired_items да маълумотлар базаси хатолиги: {e}", exc_info=True)
                await notify_admin(f"check_expired_items да маълумотлар базаси хатолиги: {str(e)}", bot=bot)
            except Exception as e:
                logger.error(f"check_expired_items да кутмаган хатолик: {e}", exc_info=True)
                await notify_admin(f"check_expired_items да кутмаган хатолик: {str(e)}", bot=bot)
            logger.debug("Кейинги текширувни кутиш (300 секунд)")
            await asyncio.sleep(300)  # Увеличен интервал до 5 минут
    except asyncio.CancelledError:
        logger.info("check_expired_items фон вазифаси бекор қилинди")
        raise