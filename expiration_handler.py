import aiosqlite
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import ReplyKeyboardRemove
from config import DB_NAME
from utils import format_uz_datetime, parse_uz_datetime, make_keyboard, ExpiredItem

logger = logging.getLogger(__name__)

async def notify_user(bot: Bot, user_id: int, unique_id: str, is_request: bool, state: FSMContext):
    """Уведомляет пользователя об истекшем запросе или продукте."""
    table = "requests" if is_request else "products"
    action_type = "Сўров" if is_request else "Эълон"
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
        logger.info(f"Уведомление отправлено пользователю {user_id} для {table} {unique_id}")
    except Exception as e:
        logger.error(f"Не удалось уведомить пользователя {user_id} о просроченном {table} {unique_id}: {e}")

async def check_expired_items(bot: Bot, storage: MemoryStorage):
    """Фоновая задача для проверки истекших элементов каждую минуту."""
    logger.info("Запущена фоновая задача проверки истечения срока")
    while True:
        try:
            now = datetime.now()
            logger.debug("Начало проверки истекших элементов")
            async with aiosqlite.connect(DB_NAME) as conn:
                logger.debug("Соединение с базой данных установлено")
                # Проверка истекших запросов
                async with conn.execute(
                    "SELECT r.user_id, r.unique_id, r.created_at "
                    "FROM requests r "
                    "JOIN users u ON r.user_id = u.id "  # Исправлено: u.user_id -> u.id
                    "WHERE r.status = 'active'"
                ) as cursor:
                    requests = await cursor.fetchall()
                    logger.debug(f"Найдено {len(requests)} активных запросов")
                for user_id, unique_id, created_at in requests:
                    created_at_dt = parse_uz_datetime(created_at)
                    if not created_at_dt:
                        logger.warning(f"Некорректная дата создания для запроса {unique_id}: {created_at}")
                        continue
                    expiration_time = created_at_dt + timedelta(hours=24)
                    if now >= expiration_time:
                        await conn.execute(
                            "UPDATE requests SET status = 'pending_response' WHERE unique_id = ?",
                            (unique_id,)
                        )
                        state = FSMContext(
                            storage=storage,
                            key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
                        )
                        await notify_user(bot, user_id, unique_id, is_request=True, state=state)
                        logger.debug(f"Запрос {unique_id} помечен как pending_response")

                # Проверка истекших продуктов
                async with conn.execute(
                    "SELECT p.user_id, p.unique_id, p.created_at "
                    "FROM products p "
                    "JOIN users u ON p.user_id = u.id "  # Исправлено: u.user_id -> u.id
                    "WHERE p.status = 'active'"
                ) as cursor:
                    products = await cursor.fetchall()
                    logger.debug(f"Найдено {len(products)} активных продуктов")
                for user_id, unique_id, created_at in products:
                    created_at_dt = parse_uz_datetime(created_at)
                    if not created_at_dt:
                        logger.warning(f"Некорректная дата создания для продукта {unique_id}: {created_at}")
                        continue
                    expiration_time = created_at_dt + timedelta(hours=24)
                    if now >= expiration_time:
                        await conn.execute(
                            "UPDATE products SET status = 'pending_response' WHERE unique_id = ?",
                            (unique_id,)
                        )
                        state = FSMContext(
                            storage=storage,
                            key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
                        )
                        await notify_user(bot, user_id, unique_id, is_request=False, state=state)
                        logger.debug(f"Продукт {unique_id} помечен как pending_response")

                await conn.commit()
                logger.debug("Проверка истечения срока успешно завершена")
        except aiosqlite.Error as e:
            logger.error(f"Ошибка при работе с базой в check_expired_items: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Неожиданная ошибка в check_expired_items: {e}", exc_info=True)
        logger.debug("Ожидание следующей проверки (60 секунд)")
        await asyncio.sleep(60)  # Проверка каждую минуту