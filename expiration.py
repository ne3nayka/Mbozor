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
from config import DB_NAME, DB_TIMEOUT, ADMIN_IDS, CHANNEL_ID
from utils import format_uz_datetime, parse_uz_datetime, make_keyboard, check_subscription, notify_admin, validate_number_minimal

logger = logging.getLogger(__name__)

router = Router()

class ExpiredStates(StatesGroup):
    choice = State()
    final_price = State()

async def auto_archive_pending(conn: aiosqlite.Connection, table: str, unique_id: str, archived_at: str, bot: Bot) -> bool:
    """Архивирует или удаляет элемент (эълон или сўров) по unique_id в указанной таблице."""
    try:
        async with conn.execute(
            f"SELECT channel_message_id, user_id FROM {table} WHERE unique_id = ? AND status = 'pending_response'",
            (unique_id,)
        ) as cursor:
            item = await cursor.fetchone()
        if not item:
            logger.info(f"Элемент {unique_id} в таблице {table} не найден или не в статусе pending_response")
            return False
        channel_message_id, user_id = item
        if table == "products":
            # Для объявлений: архивирование
            await conn.execute(
                f"UPDATE {table} SET status = 'archived', archived_at = ? WHERE unique_id = ?",
                (archived_at, unique_id)
            )
            action = "архивга ўтказилди"
        else:
            # Для запросов: удаление
            await conn.execute(
                f"DELETE FROM {table} WHERE unique_id = ?",
                (unique_id,)
            )
            action = "ўчирилди"
        if channel_message_id:
            try:
                await bot.delete_message(chat_id=CHANNEL_ID, message_id=channel_message_id)
                logger.debug(f"Канал хабари {channel_message_id} для {unique_id} ўчирилди")
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение {channel_message_id} для {unique_id}: {e}")
        if user_id:
            try:
                item_type = "эълонингиз" if table == "products" else "сўровингиз"
                await bot.send_message(
                    user_id,
                    f"Сизнинг {item_type} {unique_id} муддати тугагани сабабли {action}.",
                    reply_markup=make_keyboard(["Асосий меню"], columns=1)
                )
            except Exception as e:
                logger.warning(f"Не удалось уведомить пользователя {user_id} для {unique_id}: {e}")
        logger.info(f"Элемент {unique_id} в таблице {table} успешно {action}")
        return True
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при обработке {unique_id} в {table}: {e}", exc_info=True)
        await notify_admin(f"Ошибка базы данных при обработке {unique_id} в {table}: {str(e)}", bot=bot)
        return False
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при обработке {unique_id} в {table}: {e}", exc_info=True)
        await notify_admin(f"Непредвиденная ошибка при обработке {unique_id} в {table}: {str(e)}", bot=bot)
        return False

async def notify_user(bot: Bot, user_id: int, unique_id: str, is_request: bool, state: FSMContext):
    """Уведомляет пользователя о завершении срока действия объявления."""
    table = "requests" if is_request else "products"
    action_type = "Сўров" if is_request else "Эълон"
    current_state = await state.get_state()
    logger.debug(f"notify_user: user_id={user_id}, {table} {unique_id}, текущий статус={current_state}")

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                    "SELECT blocked FROM deleted_users WHERE user_id = ? AND blocked = TRUE", (user_id,)
            ) as cursor:
                if await cursor.fetchone():
                    logger.warning(
                        f"Заблокированный пользователь {user_id} не получил уведомление о {table} {unique_id}")
                    return
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при проверке блокировки для user_id={user_id}: {e}")
        await notify_admin(f"Ошибка базы данных в notify_user для user_id={user_id}: {str(e)}", bot=bot)
        return

    if user_id not in ADMIN_IDS:
        try:
            success, bot_active, is_subscribed = await check_subscription(bot, user_id, state.storage)
            if not is_subscribed:
                logger.info(
                    f"Пользователь {user_id} не получил уведомление о {table} {unique_id} из-за отсутствия подписки")
                await notify_admin(
                    f"Пользователь {user_id} пропустил уведомление о {table} {unique_id} из-за отсутствия подписки",
                    bot=bot)
                return
        except Exception as e:
            logger.error(f"Ошибка проверки подписки для user_id={user_id}: {e}")
            await notify_admin(f"Ошибка проверки подписки в notify_user для user_id={user_id}: {str(e)}", bot=bot)
            return

    if is_request:
        # Для запросов: уведомление об удалении
        try:
            await bot.send_message(
                user_id,
                f"Сизнинг сўровингиз {unique_id} муддати тугади ва яқин 48 соат ичида ўчирилади.",
                reply_markup=make_keyboard(["Асосий меню"], columns=1)
            )
            logger.info(f"Уведомление о сўрове {unique_id} отправлено пользователю {user_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю {user_id} о сўрове {unique_id}: {e}")
            await notify_admin(f"Не удалось отправить уведомление пользователю {user_id} о сўрове {unique_id}: {str(e)}",
                               bot=bot)
    else:
        # Для объявлений: запрос финальной цены
        message = f"Сизнинг эълонингиз {unique_id} муддати тугади. Якуний нарҳни киритинг ёки бекор қилинг:"
        keyboard = make_keyboard(["Якуний нарх", "Бекор қилиш"], columns=2, one_time=True)
        try:
            await bot.send_message(
                user_id,
                message,
                reply_markup=keyboard,
                reply_to_message_id=None,
                disable_notification=False
            )
            await state.update_data(unique_id=unique_id, is_request=is_request)
            await state.set_state(ExpiredStates.choice)
            logger.info(f"Уведомление о эълоне {unique_id} отправлено пользователю {user_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю {user_id} о эълоне {unique_id}: {e}")
            await notify_admin(f"Не удалось отправить уведомление пользователю {user_id} о эълоне {unique_id}: {str(e)}",
                               bot=bot)

@router.message(ExpiredStates.choice, F.text.in_(["Якуний нарх", "Бекор қилиш"]))
async def handle_expired_choice(message: Message, state: FSMContext):
    """Обрабатывает выбор пользователя для истёкшего объявления."""
    user_id = message.from_user.id
    data = await state.get_data()
    unique_id = data.get("unique_id")
    is_request = data.get("is_request")
    table = "requests" if is_request else "products"
    action_type = "Сўров" if is_request else "Эълон"
    choice = message.text
    logger.info(f"handle_expired_choice: Пользователь {user_id} выбрал '{choice}' для {table} {unique_id}")

    if is_request:
        # Запросы не обрабатываются в этом состоянии, так как они удаляются автоматически
        logger.warning(f"Некорректный выбор для сўрова {unique_id}, пользователь {user_id}")
        return

    if choice == "Якуний нарх":
        await message.answer(
            "Якуний нархни киритинг (сўм):",
            reply_markup=make_keyboard([], with_back=True)
        )
        await state.set_state(ExpiredStates.final_price)
    elif choice == "Бекор қилиш":
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
            logger.info(f"{action_type} {unique_id} бекор қилинди пользователем {user_id}")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка базы данных при архивации {table} {unique_id} для user_id={user_id}: {e}")
            await notify_admin(f"Ошибка базы данных при архивации {table} {unique_id}: {str(e)}", bot=message.bot)
            await message.answer(
                "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                reply_markup=make_keyboard(["Асосий меню"], columns=1)
            )
            await state.clear()

@router.message(ExpiredStates.choice)
async def handle_invalid_expired_choice(message: Message, state: FSMContext):
    """Обрабатывает некорректный выбор для истёкшего объявления."""
    user_id = message.from_user.id
    data = await state.get_data()
    unique_id = data.get("unique_id")
    is_request = data.get("is_request")
    action_type = "Сўров" if is_request else "Эълон"
    if is_request:
        # Запросы не обрабатываются в этом состоянии
        logger.warning(f"Некорректный выбор для сўрова {unique_id}, пользователь {user_id}")
        return
    await message.answer(
        "Илтимос, тугмани танланг:",
        reply_markup=make_keyboard(["Якуний нарх", "Бекор қилиш"], columns=2, one_time=True)
    )
    logger.warning(
        f"Пользователь {user_id} отправил некорректный выбор для {action_type.lower()} {unique_id}: {message.text}")

@router.message(ExpiredStates.final_price)
async def handle_final_price(message: Message, state: FSMContext):
    """Обрабатывает ввод финальной цены для объявления."""
    user_id = message.from_user.id
    data = await state.get_data()
    unique_id = data.get("unique_id")
    is_request = data.get("is_request")
    table = "requests" if is_request else "products"
    action_type = "Сўров" if is_request else "Эълон"
    price = message.text
    logger.info(f"handle_final_price: Пользователь {user_id} ввёл цену '{price}' для {table} {unique_id}")

    if is_request:
        # Запросы не обрабатываются в этом состоянии
        logger.warning(f"Некорректный ввод цены для сўрова {unique_id}, пользователь {user_id}")
        return

    if price == "Орқага":
        await message.answer(
            "Танланг:",
            reply_markup=make_keyboard(["Якуний нарх", "Бекор қилиш"], columns=2, one_time=True)
        )
        await state.set_state(ExpiredStates.choice)
        logger.info(f"Пользователь {user_id} вернулся назад для {action_type.lower()} {unique_id}")
        return

    valid, final_price = await validate_number_minimal(price)
    if not valid or final_price <= 0:
        await message.answer(
            "Нарх мусбат рақам бўлиши керак:",
            reply_markup=make_keyboard([], with_back=True)
        )
        logger.warning(f"Некорректная цена для {action_type.lower()} {unique_id}: {price}")
        return

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            await conn.execute(
                f"UPDATE {table} SET final_price = ?, status = 'completed', completed_at = ? WHERE unique_id = ?",
                (final_price, format_uz_datetime(datetime.now(pytz.UTC)), unique_id)
            )
            await conn.commit()
        await message.answer(
            f"{action_type} {unique_id} якуний нарҳи {final_price:,.0f} сўм билан якунланди.",
            reply_markup=make_keyboard(["Асосий меню"], columns=1)
        )
        await state.clear()
        logger.info(f"{action_type} {unique_id} завершён с финальной ценой {final_price} пользователем {user_id}")
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при обновлении финальной цены для {table} {unique_id}: {e}")
        await notify_admin(f"Ошибка базы данных при обновлении финальной цены {table} {unique_id}: {str(e)}",
                           bot=message.bot)
        await message.answer(
            "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
            reply_markup=make_keyboard(["Асосий меню"], columns=1)
        )
        await state.clear()

async def check_expired_items(bot: Bot, storage):
    """Фоновая задача для проверки истёкших элементов и отправки уведомлений о финальной цене."""
    logger.info("Фоновая вазифа ишга туширилди: истекший элементларни текшириш")
    try:
        while True:
            try:
                now = datetime.now(pytz.timezone('Asia/Tashkent'))
                cutoff_time = now - timedelta(hours=49)
                cutoff_time_str = format_uz_datetime(cutoff_time)
                logger.debug(f"Истекший элементларни текшириш, cutoff_time={cutoff_time_str}")

                async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
                    async with conn.execute(
                        "SELECT unique_id, created_at FROM requests WHERE status = 'pending_response'"
                    ) as cursor:
                        pending = await cursor.fetchall()
                    for unique_id, created_at in pending:
                        created_at_dt = parse_uz_datetime(created_at)
                        if created_at_dt:
                            created_at_dt = created_at_dt.replace(tzinfo=pytz.timezone('Asia/Tashkent'))
                        if created_at_dt and now > created_at_dt + timedelta(hours=48):
                            await auto_archive_pending(conn, "requests", unique_id, format_uz_datetime(now), bot)
                            logger.info(f"Сўров {unique_id} автомат равишда ўчирилди (pending_response)")

                    async with conn.execute(
                        "SELECT unique_id, created_at FROM products WHERE status = 'pending_response'"
                    ) as cursor:
                        pending = await cursor.fetchall()
                    for unique_id, created_at in pending:
                        created_at_dt = parse_uz_datetime(created_at)
                        if created_at_dt:
                            created_at_dt = created_at_dt.replace(tzinfo=pytz.timezone('Asia/Tashkent'))
                        if created_at_dt and now > created_at_dt + timedelta(hours=48):
                            await auto_archive_pending(conn, "products", unique_id, format_uz_datetime(now), bot)
                            logger.info(f"Эълон {unique_id} автомат равишда архивга ўтказилди (pending_response)")

                    batch_size = 100
                    offset = 0
                    expired_count = 0
                    while True:
                        async with conn.execute(
                            """
                            SELECT r.user_id, r.unique_id, r.created_at 
                            FROM requests r JOIN users u ON r.user_id = u.id 
                            WHERE r.status = 'active' AND r.created_at >= ? 
                            LIMIT ? OFFSET ?
                            """,
                            (cutoff_time_str, batch_size, offset)
                        ) as cursor:
                            requests = await cursor.fetchall()
                        offset += batch_size
                        logger.debug(f"Фаол сўровлар сони батчда: {len(requests)}")
                        for user_id, unique_id, created_at in requests:
                            try:
                                created_at_dt = parse_uz_datetime(created_at)
                                if created_at_dt:
                                    created_at_dt = created_at_dt.replace(tzinfo=pytz.timezone('Asia/Tashkent'))
                                if not created_at_dt:
                                    logger.warning(f"Сўров {unique_id} учун яроқсиз яратилган сана: {created_at}")
                                    continue
                                expiration_time = created_at_dt + timedelta(hours=48)
                                if now >= expiration_time:
                                    await conn.execute(
                                        "UPDATE requests SET status = 'pending_response' WHERE unique_id = ?",
                                        (unique_id,)
                                    )
                                    state = FSMContext(
                                        storage=storage,
                                        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
                                    )
                                    logger.debug(f"Сўров {unique_id} муддати тугади, срок: {format_uz_datetime(expiration_time)}")
                                    await notify_user(bot, user_id, unique_id, is_request=True, state=state)
                                    expired_count += 1
                                    logger.debug(f"Сўров {unique_id} pending_response сифатида белгиланди")
                            except Exception as e:
                                logger.error(f"FSMContext хатоси user_id={user_id}, сўров {unique_id}: {e}")
                                await notify_admin(
                                    f"check_expired_items да FSMContext хатоси user_id={user_id}: {str(e)}", bot=bot)
                                await bot.send_message(
                                    user_id,
                                    "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                                    reply_markup=make_keyboard(["Асосий меню"], columns=1)
                                )
                        if len(requests) < batch_size:
                            break

                    offset = 0
                    while True:
                        async with conn.execute(
                            """
                            SELECT p.user_id, p.unique_id, p.created_at, p.final_price 
                            FROM products p JOIN users u ON p.user_id = u.id 
                            WHERE p.status = 'active' AND p.created_at >= ? 
                            LIMIT ? OFFSET ?
                            """,
                            (cutoff_time_str, batch_size, offset)
                        ) as cursor:
                            products = await cursor.fetchall()
                        offset += batch_size
                        logger.debug(f"Фаол эълонлар сони батчда: {len(products)}")
                        for user_id, unique_id, created_at, final_price in products:
                            try:
                                created_at_dt = parse_uz_datetime(created_at)
                                if created_at_dt:
                                    created_at_dt = created_at_dt.replace(tzinfo=pytz.timezone('Asia/Tashkent'))
                                if not created_at_dt:
                                    logger.warning(f"Эълон {unique_id} учун яроқсиз яратилган сана: {created_at}")
                                    continue
                                expiration_time = created_at_dt + timedelta(hours=48)
                                if now >= expiration_time and final_price is None:
                                    await conn.execute(
                                        "UPDATE products SET status = 'pending_response' WHERE unique_id = ?",
                                        (unique_id,)
                                    )
                                    state = FSMContext(
                                        storage=storage,
                                        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
                                    )
                                    logger.debug(f"Эълон {unique_id} муддати тугади, срок: {format_uz_datetime(expiration_time)}, final_price отсутствует")
                                    await notify_user(bot, user_id, unique_id, is_request=False, state=state)
                                    expired_count += 1
                                    logger.debug(f"Эълон {unique_id} pending_response сифатида белгиланди")
                            except Exception as e:
                                logger.error(f"FSMContext хатоси user_id={user_id}, эълон {unique_id}: {e}")
                                await notify_admin(
                                    f"check_expired_items да FSMContext хатоси user_id={user_id}: {str(e)}", bot=bot)
                                await bot.send_message(
                                    user_id,
                                    "Хатолик юз берди. Админ билан боғланинг (@ad_mbozor).",
                                    reply_markup=make_keyboard(["Асосий меню"], columns=1)
                                )
                        if len(products) < batch_size:
                            break

                    await conn.commit()
                    logger.info(f"Текширув якунланди: {expired_count} истекший элементлар ишлов берилди")
            except aiosqlite.Error as e:
                logger.error(f"check_expired_items да маълумотлар базаси хатоси: {e}", exc_info=True)
                await notify_admin(f"check_expired_items да маълумотлар базаси хатоси: {str(e)}", bot=bot)
            except Exception as e:
                logger.error(f"check_expired_items да кутилмаган хато: {e}", exc_info=True)
                await notify_admin(f"check_expired_items да кутилмаган хато: {str(e)}", bot=bot)
            logger.debug("Кейинги текширув кутилмоқда (300 сония)")
            await asyncio.sleep(300)  # Интервал 5 минут
    except asyncio.CancelledError:
        logger.info("Фоновая вазифа check_expired_items бекор қилинди")
        raise