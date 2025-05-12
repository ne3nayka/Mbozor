import asyncio
import logging

import aiosqlite
import pytz
import re
import unicodedata
from datetime import datetime
from typing import Optional, List, Union

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.markdown import hcode
from config import ADMIN_IDS, CHANNEL_ID, DB_NAME, DB_TIMEOUT, ROLES

logger = logging.getLogger(__name__)

MONTHS_UZ = {
    "January": "Январ", "February": "Феврал", "March": "Март",
    "April": "Апрел", "May": "Май", "June": "Июн",
    "July": "Июл", "August": "Август", "September": "Сентябр",
    "October": "Октябр", "November": "Ноябр", "December": "Декабр"
}

def normalize_text(text: str) -> str:
    """Нормализует текст, убирая неразрывные пробелы, пробелы и приводя к нижнему регистру."""
    if not isinstance(text, str):
        logger.warning(f"Invalid text type for normalize_text: {type(text)}")
        return ""
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
                logger.info(f"Admin notification sent to {admin_id}: {text}")
            except TelegramBadRequest as e:
                logger.error(f"Failed to send notification to admin {admin_id}: {e}", exc_info=True)
                continue
        return True
    except Exception as e:
        logger.error(f"Error sending admin notification: {e}", exc_info=True)
        return False

def make_keyboard(buttons: List[Union[str, KeyboardButton]], columns: int = 1, one_time: bool = False, with_back: bool = False) -> ReplyKeyboardMarkup:
    """Создаёт клавиатуру из списка кнопок."""
    keyboard = []
    button_list = []
    for button in buttons:
        if isinstance(button, KeyboardButton):
            button_list.append(button)
        elif isinstance(button, str):
            button_list.append(KeyboardButton(text=button))
        else:
            logger.warning(f"Invalid button type: {type(button)}, value: {button}")
            continue
    if with_back:
        button_list.append(KeyboardButton(text="Орқага"))
    if not button_list:
        logger.warning("No buttons provided for keyboard")
        return ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True, one_time_keyboard=one_time)
    for i in range(0, len(button_list), columns):
        row = button_list[i:i + columns]
        keyboard.append(row)
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=one_time
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
                    if role in ROLES:
                        logger.debug(f"Role check passed for user_id={user_id}, role={role}")
                        return True, role
                    logger.warning(f"Invalid role for user_id={user_id}: {role}")
                    return False, None
                if allow_unregistered:
                    logger.debug(f"User_id={user_id} is unregistered, allow_unregistered=True")
                    return False, None
                logger.warning(f"User_id={user_id} is unregistered")
                return False, None
    except asyncio.TimeoutError:
        logger.error(f"Timeout in check_role for user_id={user_id}")
        return False, None
    except aiosqlite.Error as e:
        logger.error(f"Database error in check_role for user_id={user_id}: {e}", exc_info=True)
        return False, None

async def check_subscription(bot: Bot, user_id: int) -> tuple[bool, bool, bool]:
    """Проверяет статус подписки пользователя."""
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT channel_expires, bot_expires FROM payments WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                result = await asyncio.wait_for(cursor.fetchone(), timeout=DB_TIMEOUT)
                if not result:
                    logger.debug(f"No subscription found for user_id={user_id}")
                    return False, False, False
                channel_expires, bot_expires = result
                now = datetime.now(pytz.UTC)
                channel_active = False
                bot_active = False
                if channel_expires:
                    channel_expires_dt = parse_uz_datetime(channel_expires)
                    if channel_expires_dt:
                        channel_active = channel_expires_dt > now
                    else:
                        logger.warning(f"Invalid channel_expires format for user_id={user_id}: {channel_expires}")
                if bot_expires:
                    bot_expires_dt = parse_uz_datetime(bot_expires)
                    if bot_expires_dt:
                        bot_active = bot_expires_dt > now
                    else:
                        logger.warning(f"Invalid bot_expires format for user_id={user_id}: {bot_expires}")
                is_subscribed = channel_active or bot_active
                logger.debug(f"Subscription check for user_id={user_id}: channel_active={channel_active}, bot_active={bot_active}, is_subscribed={is_subscribed}")
                return channel_active, bot_active, is_subscribed
    except asyncio.TimeoutError:
        logger.error(f"Timeout in check_subscription for user_id={user_id}")
        return False, False, False
    except aiosqlite.Error as e:
        logger.error(f"Database error in check_subscription for user_id={user_id}: {e}", exc_info=True)
        return False, False, False

def validate_number(value: str, min_value: float = 0) -> tuple[bool, Optional[float]]:
    """Проверяет, является ли строка числом, и возвращает его."""
    try:
        number = float(value.replace(',', '.').strip())
        if number < min_value:
            logger.warning(f"Number {number} is less than min_value {min_value}")
            return False, None
        return True, number
    except ValueError:
        logger.warning(f"Invalid number format: {value}")
        return False, None

async def has_pending_items(user_id: int) -> bool:
    """Проверяет наличие незавершённых элементов у пользователя."""
    try:
        async with aiosqlite.connect(DB_NAME, timeout=DB_TIMEOUT) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM pending_items WHERE user_id = ?", (user_id,)
            ) as cursor:
                result = await asyncio.wait_for(cursor.fetchone(), timeout=DB_TIMEOUT)
                count = result[0]
                logger.debug(f"Pending items check for user_id={user_id}: count={count}")
                return count > 0
    except asyncio.TimeoutError:
        logger.error(f"Timeout in has_pending_items for user_id={user_id}")
        return False
    except aiosqlite.Error as e:
        logger.error(f"Database error in has_pending_items for user_id={user_id}: {e}", exc_info=True)
        return False

def format_uz_datetime(dt: datetime) -> str:
    """Форматирует дату в узбекском формате."""
    if not isinstance(dt, datetime):
        logger.warning(f"Invalid datetime object: {dt}")
        return "Кўрсатилмаган"
    eng_date = dt.strftime("%d %B %Y йил %H:%M:%S")
    for eng, uz in MONTHS_UZ.items():
        eng_date = eng_date.replace(eng, uz)
    return eng_date

def parse_uz_datetime(date_str: str) -> Optional[datetime]:
    """Парсит дату в узбекском формате."""
    if not date_str or not isinstance(date_str, str):
        logger.warning(f"Invalid date string: {date_str}")
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.UTC)
    except ValueError:
        pass
    try:
        for eng, uz in MONTHS_UZ.items():
            date_str = date_str.replace(uz, eng)
        return datetime.strptime(date_str, '%d %B %Y йил %H:%M:%S').replace(tzinfo=pytz.UTC)
    except ValueError:
        logger.warning(f"Failed to parse date: {date_str}")
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
            "Сўров қўшиш",
            "Эълонлар доскаси"
        ]
    else:  # admin or None
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
        "Статистика"
    ]
    return make_keyboard(buttons, columns=2, one_time=False)

def get_ads_menu() -> ReplyKeyboardMarkup:
    """Создаёт меню для работы с объявлениями в двух столбцах."""
    buttons = [
        "Эълонлар рўйхати",
        "Эълонни ўчириш",
        "Эълонни ёпиш",
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

def get_requests_menu() -> ReplyKeyboardMarkup:
    """Создаёт меню для работы с запросами в двух столбцах."""
    buttons = [
        "Сўровлар рўйхати",
        "Сўровни ўчириш",
        "Сўровни ёпиш",
        "Орқага"
    ]
    return make_keyboard(buttons, columns=2, one_time=False)