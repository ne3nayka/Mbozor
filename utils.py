import asyncio
import logging
import re
import unicodedata
import json
from datetime import datetime
from typing import Optional, List, Union

import aiosqlite
import pytz
from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.utils.markdown import hcode
from aiogram.fsm.storage.base import BaseStorage

from config import ADMIN_IDS, CHANNEL_ID, DB_NAME, DB_TIMEOUT, ROLES, MAX_SORT_LENGTH, WEBAPP_URL

logger = logging.getLogger(__name__)

MONTHS_UZ = {
    "January": "Январ", "February": "Феврал", "March": "Март",
    "April": "Апрел", "May": "Май", "June": "Июн",
    "July": "Июл", "August": "Август", "September": "Сентябр",
    "October": "Октябр", "November": "Ноябр", "December": "Декабр"
}

def normalize_text(text: str) -> str:
    """Нормализует текст для ручного ввода (например, сорт), убирая эмодзи и пробелы."""
    if not isinstance(text, str):
        logger.warning(f"Invalid text type for normalize_text: {type(text)}")
        return ""
    text = re.sub(r'[\U0001F000-\U0001FFFF]', '', text)
    return unicodedata.normalize("NFKC", text.strip()).lower()

async def notify_admin(text: str, bot: Optional[Bot] = None) -> bool:
    """Отправляет уведомление администраторам."""
    if not bot:
        logger.error("Bot instance not provided for notify_admin")
        return False
    try:
        text = re.sub(r'[<>&\'"]', lambda m: f'\\{m.group(0)}', text)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=hcode(text),
                    parse_mode="HTML"
                )
                logger.info(f"Admin notification sent to {admin_id}")
            except TelegramBadRequest as e:
                logger.error(f"Failed to send notification to admin {admin_id}: {e}", exc_info=True)
                continue
        return True
    except Exception as e:
        logger.error(f"Error sending admin notification: {e}", exc_info=True)
        return False

def make_keyboard(options: list[str], columns: int = 2, with_back: bool = False, one_time: bool = False) -> ReplyKeyboardMarkup:
    """Создаёт клавиатуру из списка строк с указанным количеством столбцов."""
    keyboard = []
    for i in range(0, len(options), columns):
        row = []
        for option in options[i:i + columns]:
            if option == "Эълонлар доскаси":
                row.append(KeyboardButton(text=option, web_app=WebAppInfo(url=WEBAPP_URL)))
            else:
                row.append(KeyboardButton(text=option))
        keyboard.append(row)
    if with_back:
        keyboard.append([KeyboardButton(text="Орқага")])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=one_time,
        input_field_placeholder="Тугмани танланг:"
    )

async def check_role(event: types.Message | types.CallbackQuery, allow_unregistered: bool = False) -> tuple[bool, Optional[str]]:
    """Проверяет роль пользователя."""
    user_id = event.from_user.id
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)) as cursor:
                result = await asyncio.wait_for(cursor.fetchone(), timeout=DB_TIMEOUT)
                if result:
                    role = result[0]
                    logger.debug(f"check_role: user_id={user_id}, role={role}")
                    if role in ROLES:
                        return True, role
                    logger.warning(f"check_role: user_id={user_id} имеет некорректную роль: {role}")
                    return False, None
                logger.debug(f"check_role: user_id={user_id} не найден в базе")
                if allow_unregistered:
                    return False, None
                return False, None
    except asyncio.TimeoutError:
        logger.error(f"Timeout in check_role for user_id={user_id}")
        return False, None
    except aiosqlite.Error as e:
        logger.error(f"Database error in check_role for user_id={user_id}: {e}", exc_info=True)
        return False, None

async def check_subscription(bot: Bot, user_id: int, storage: BaseStorage) -> tuple[bool, bool, bool]:
    """Проверяет подписку пользователя, освобождая админов."""
    if user_id in ADMIN_IDS:
        return True, True, True
    cache_key = f"sub:{user_id}"
    if hasattr(storage, 'redis'):
        try:
            cached = await storage.redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Redis error in check_subscription for user_id={user_id}: {e}")

    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT bot_expires FROM payments WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                result = await cursor.fetchone()
        bot_expires = result[0] if result else None

        if bot_expires:
            expires_dt = parse_uz_datetime(bot_expires)
            if expires_dt is None:
                bot_active = False
                is_subscribed = False
            else:
                now = datetime.now(pytz.timezone('Asia/Tashkent'))
                if expires_dt > now:
                    bot_active = True
                    is_subscribed = True
                else:
                    bot_active = False
                    is_subscribed = False
        else:
            bot_active = False
            is_subscribed = False

        response = (False, bot_active, is_subscribed)
        if hasattr(storage, 'redis'):
            try:
                if not is_subscribed:
                    await storage.redis.delete(cache_key)
                else:
                    await storage.redis.setex(cache_key, 60, json.dumps(response))
            except Exception as e:
                logger.warning(f"Redis error caching subscription for user_id={user_id}: {e}")
        return response
    except asyncio.TimeoutError:
        logger.error(f"Timeout in check_subscription for user_id={user_id}")
        return False, False, False
    except aiosqlite.Error as e:
        logger.error(f"Database error in check_subscription for user_id={user_id}: {e}", exc_info=True)
        return False, False, False

async def invalidate_cache(storage, table: str) -> None:
    """Инвалидирует кэш для указанной таблицы."""
    if hasattr(storage, 'redis'):
        try:
            await storage.redis.publish(f'cache_invalidate:{table}', 'invalidate')
            logger.debug(f"Cache invalidated for table: {table}")
        except Exception as e:
            logger.warning(f"Redis error in invalidate_cache for table {table}: {e}")

def validate_number(value: str, min_value: float = 0) -> tuple[bool, Optional[float]]:
    """Проверяет, является ли строка числом, и возвращает его."""
    try:
        number = float(value.replace(',', '.').strip())
        if number < min_value:
            return False, None
        return True, number
    except ValueError:
        return False, None

async def validate_number_minimal(value: str) -> tuple[bool, float]:
    """Минимальная проверка числа для объёма и цены."""
    try:
        number = float(value)
        return True, number
    except ValueError:
        return False, 0

async def validate_sort(sort: str) -> bool:
    """Проверяет длину строки сорта."""
    return len(sort) <= MAX_SORT_LENGTH

async def has_pending_items(user_id: int) -> bool:
    """Проверяет наличие незавершённых элементов у пользователя."""
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                    "SELECT COUNT(*) FROM pending_items WHERE user_id = ?", (user_id,)
            ) as cursor:
                result = await asyncio.wait_for(cursor.fetchone(), timeout=DB_TIMEOUT)
                count = result[0]
                return count > 0
    except asyncio.TimeoutError:
        logger.error(f"Timeout in has_pending_items for user_id={user_id}")
        return False
    except aiosqlite.Error as e:
        logger.error(f"Database error in has_pending_items for user_id={user_id}: {e}", exc_info=True)
        return False

def format_uz_datetime(dt: datetime) -> str:
    """Форматирует дату в формате DD.MM.YYYY HH:MM:SS в часовом поясе Asia/Tashkent."""
    if not isinstance(dt, datetime):
        return "Кўрсатилмаган"
    tz = pytz.timezone('Asia/Tashkent')
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    dt_local = dt.astimezone(tz)
    return dt_local.strftime("%d.%m.%Y %H:%M:%S")

def parse_uz_datetime(date_str: str) -> Optional[datetime]:
    """Парсит дату в узбекском формате или других форматах, возвращая время в Asia/Tashkent."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        dt = datetime.strptime(date_str, '%d.%m.%Y %H:%M:%S')
        return pytz.timezone('Asia/Tashkent').localize(dt)
    except ValueError:
        pass
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        return pytz.timezone('Asia/Tashkent').localize(dt)
    except ValueError:
        pass
    try:
        for eng, uz in MONTHS_UZ.items():
            date_str = date_str.replace(uz, eng)
        dt = datetime.strptime(date_str, '%d %B %Y йил %H:%M:%S')
        return pytz.timezone('Asia/Tashkent').localize(dt)
    except ValueError:
        return None

def get_main_menu(role: Optional[str]) -> ReplyKeyboardMarkup:
    """Создаёт главное меню для пользователя в двух столбцах."""
    if role == ROLES[0]:  # seller
        buttons = [
            "Менинг профилим",
            "Менинг эълонларим",
            "Эълон қўшиш",
            "Эълонлар доскаси"
        ]
    elif role == ROLES[1]:  # buyer
        buttons = [
            "Менинг профилим",
            "Менинг сўровларим",
            "Сўров юбориш",
            "Эълонлар доскаси"
        ]
    elif role == ROLES[2]:  # admin
        return get_admin_menu()
    else:  # unregistered
        buttons = ["Рўйхатдан ўтиш"]
    return make_keyboard(buttons, columns=2, one_time=False)

def get_admin_menu() -> ReplyKeyboardMarkup:
    """Создаёт меню для админа в двух столбцах."""
    buttons = [
        "Фойдаланувчиларни бошқариш",
        "Эълонларни бошқариш",
        "Сўровларни бошқариш",
        "Обунани бошқариш",
        "Архивни бошқариш",
        "Статистика",
        "Эълонлар доскаси"
    ]
    return make_keyboard(buttons, columns=2, one_time=False)

def get_ads_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Создаёт меню для работы с объявлениями в двух столбцах."""
    buttons = [
        "Барча эълонлар рўйхати" if is_admin else "Эълонлар рўйхати",
        "Эълонларни ўчириш" if is_admin else "Эълонни ўчириш",
        "Эълонларни архивга ўтказиш" if is_admin else "Эълонни ёпиш",
        "Орқага"
    ]
    return make_keyboard(buttons, columns=2, one_time=False)

def get_profile_menu() -> ReplyKeyboardMarkup:
    """Создаёт меню для работы с профилем в двух столбцах."""
    buttons = [
        "Профиль ҳақида маълумот",
        "Профильни таҳрирлаш",
        "Профильни ўчириш",
        "Орқага"
    ]
    return make_keyboard(buttons, columns=2, one_time=False)

def get_requests_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Создаёт меню для работы с запросами в двух столбцах."""
    buttons = [
        "Барча сўровлар рўйхати" if is_admin else "Сўровлар рўйхати",
        "Сўровларни ўчириш" if is_admin else "Сўровни ўчириш",
        "Сўровларни архивга ўтказиш" if is_admin else "Сўровни ёпиш",
        "Орқага"
    ]
    return make_keyboard(buttons, columns=2, one_time=False)

def validate_phone(phone: str) -> bool:
    """Проверяет, соответствует ли номер телефона форматам Узбекистана (+998), Кыргызстана/Таджикистана (+99X), или России/Казахстана (+7)."""
    logger.debug(f"Validating phone number: {phone}")
    return bool(re.match(r'^\+((998|99[0-9])[0-9]{9}|7[0-9]{10})$', phone))

async def save_registration_state(storage, user_id: int, data: dict):
    """Сохраняет промежуточное состояние регистрации в Redis."""
    if hasattr(storage, 'redis'):
        try:
            await storage.redis.setex(f"reg:{user_id}", 3600, json.dumps(data))
            logger.debug(f"Registration state saved for user_id={user_id}")
        except Exception as e:
            logger.warning(f"Redis error saving registration state for user_id={user_id}: {e}")