import aiosqlite
import logging
from datetime import datetime
from typing import Tuple, Optional
from aiogram import Bot, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup
from config import DB_NAME, ADMIN_IDS, ADMIN_ROLE, ROLE_MAPPING, CHANNEL_ID, SELLER_ROLE, BUYER_ROLE, ROLES
import pytz
import re

logger = logging.getLogger(__name__)

MONTHS_UZ = {
    "January": "январ", "February": "феврал", "March": "март", "April": "апрел",
    "May": "май", "June": "июн", "July": "июл", "August": "август",
    "September": "сентябр", "October": "октябр", "November": "ноябр", "December": "декабр"
}
MONTHS_UZ_TO_EN = {v: k for k, v in MONTHS_UZ.items()}

def parse_uz_datetime(date_str: str) -> Optional[datetime]:
    """Парсит дату в формате узбекского языка (например, '01 январ 2025 йил 12:00:00')."""
    if not date_str or "йил" not in date_str:
        logger.warning(f"parse_uz_datetime: Invalid date string: {date_str}")
        return None
    try:
        # Проверка формата: "DD MMMM YYYY йил HH:MM:SS"
        if not re.match(r"\d{2}\s+\w+\s+\d{4}\s+йил\s+\d{2}:\d{2}:\d{2}", date_str):
            logger.warning(f"parse_uz_datetime: Invalid format: {date_str}")
            return None
        for uz, en in MONTHS_UZ_TO_EN.items():
            date_str = date_str.replace(uz, en)
        dt = datetime.strptime(date_str, "%d %B %Y йил %H:%M:%S")
        return dt.replace(tzinfo=pytz.UTC)
    except Exception as e:
        logger.error(f"parse_uz_datetime: Error parsing date {date_str}: {e}", exc_info=True)
        return None

def format_uz_datetime(dt: datetime) -> str:
    """Форматирует дату в узбекский формат (например, '01 январ 2025 йил 12:00:00')."""
    try:
        eng_date = dt.astimezone(pytz.UTC).strftime("%d %B %Y йил %H:%M:%S")
        for en, uz in MONTHS_UZ.items():
            eng_date = eng_date.replace(en, uz)
        return eng_date
    except Exception as e:
        logger.error(f"format_uz_datetime: Error formatting date {dt}: {e}", exc_info=True)
        return ""

async def check_channel_subscription(bot: Bot, user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал."""
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id, request_timeout=5.0)
        is_subscribed = member.status in ["member", "administrator", "creator"]
        logger.debug(f"check_channel_subscription: user_id={user_id}, channel={CHANNEL_ID}, is_subscribed={is_subscribed}")
        return is_subscribed
    except Exception as e:
        logger.error(f"check_channel_subscription: Error for user_id={user_id}, channel={CHANNEL_ID}: {e}", exc_info=True)
        return False

async def check_subscription(bot: Bot, user_id: int) -> Tuple[bool, bool, bool]:
    """
    Проверяет статус подписки пользователя на канал и бот.

    Args:
        bot: Экземпляр бота.
        user_id: ID пользователя в Telegram.

    Returns:
        Tuple[bool, bool, bool]: (channel_active, bot_active, is_subscribed)
    """
    try:
        channel_active = await check_channel_subscription(bot, user_id)
    except Exception as e:
        logger.error(f"Error checking channel subscription for user_id={user_id}, channel={CHANNEL_ID}: {e}", exc_info=True)
        channel_active = False

    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT bot_expires FROM payments WHERE user_id = ?", (user_id,)) as cursor:
                result = await cursor.fetchone()
        bot_expires = result[0] if result else None
        bot_active = False
        if bot_expires:
            expires_dt = parse_uz_datetime(bot_expires)
            bot_active = bool(expires_dt and datetime.now(pytz.UTC) < expires_dt)
    except aiosqlite.Error as e:
        logger.error(f"Error checking bot subscription for user_id={user_id}: {e}", exc_info=True)
        bot_active = False

    is_subscribed = channel_active or bot_active
    logger.debug(
        f"Subscription check for user_id={user_id}: channel_active={channel_active}, bot_active={bot_active}, is_subscribed={is_subscribed}"
    )
    return channel_active, bot_active, is_subscribed

async def check_role(event: types.Message | types.CallbackQuery, bot: Optional[Bot] = None, allow_unregistered: bool = False) -> Tuple[bool, Optional[str]]:
    """
    Проверяет роль пользователя в базе данных.

    Args:
        event: Событие (Message или CallbackQuery).
        bot: Экземпляр бота (не используется, оставлен для совместимости).
        allow_unregistered: Разрешить незарегистрированных пользователей.

    Returns:
        Tuple[bool, Optional[str]]: (allowed, role)
    """
    logger.debug(f"check_role: Event {type(event).__name__}")
    if isinstance(event, (types.Message, types.CallbackQuery)):
        user_id = event.from_user.id
    else:
        logger.warning(f"check_role: Unsupported event type {type(event)}")
        return False, None

    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)) as cursor:
                result = await cursor.fetchone()
                if result and result[0] in ROLES:
                    logger.debug(f"check_role: User {user_id} role: {result[0]}")
                    return True, result[0]
                logger.debug(f"check_role: User {user_id} not in DB or invalid role")
                return allow_unregistered, None
    except aiosqlite.Error as e:
        logger.error(f"check_role: DB error for user_id={user_id}: {e}", exc_info=True)
        return False, None

def make_keyboard(options: list[str], columns: int = 1, add_back_button: bool = False, one_time: bool = False) -> ReplyKeyboardMarkup:
    """
    Создаёт клавиатуру из списка опций.

    Args:
        options: Список текстов кнопок.
        columns: Количество столбцов.
        add_back_button: Добавить кнопку "Орқага".
        one_time: Скрыть клавиатуру после выбора.

    Returns:
        ReplyKeyboardMarkup: Клавиатура.
    """
    if not options:
        logger.debug("make_keyboard: Empty options list, returning empty keyboard")
        return ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True, one_time_keyboard=one_time)

    buttons = [KeyboardButton(text=option) for option in options]
    if add_back_button:
        buttons.append(KeyboardButton(text="Орқага"))

    keyboard = [buttons[i:i + columns] for i in range(0, len(buttons), columns)]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=one_time
    )

def validate_number(text: str, max_value: int, allow_float: bool = False) -> Optional[int | float]:
    """
    Валидирует текст как число в заданном диапазоне.

    Args:
        text: Входной текст.
        max_value: Максимальное значение.
        allow_float: Разрешить дробные числа.

    Returns:
        Optional[int | float]: Число или None, если невалидно.
    """
    try:
        value = float(text.strip()) if allow_float else int(text.strip())
        if value <= 0 or value > max_value:
            logger.warning(f"validate_number: Value {value} out of range (max={max_value})")
            return None
        return value
    except ValueError as e:
        logger.warning(f"validate_number: Invalid number format: {text}: {e}")
        return None

async def has_pending_items(user_id: int) -> bool:
    """
    Проверяет наличие незавершённых элементов в таблице pending_items.

    Args:
        user_id: ID пользователя.

    Returns:
        bool: True, если есть незавершённые элементы.
    """
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM pending_items WHERE user_id = ?", (user_id,)
            ) as cursor:
                count = (await cursor.fetchone())[0]
        has_pending = count > 0
        logger.debug(f"has_pending_items: user_id={user_id}, count={count}, has_pending={has_pending}")
        return has_pending
    except aiosqlite.Error as e:
        logger.error(f"has_pending_items: DB error for user_id={user_id}: {e}", exc_info=True)
        return False

class ExpiredItem(StatesGroup):
    choice = State()        # Выбор действия с истёкшим элементом
    final_price = State()   # Ввод финальной цены

# --- Menu generation functions ---
def get_main_menu(role: Optional[str]) -> ReplyKeyboardMarkup:
    """Создаёт главное меню в зависимости от роли пользователя."""
    if role == SELLER_ROLE:
        return make_keyboard(["Менинг профилим", "Менинг эълонларим", "Эълон қўшиш", "Эълонлар доскаси"], columns=2)
    elif role == BUYER_ROLE:
        return make_keyboard(["Менинг профилим", "Менинг сўровларим", "Сўров юбориш", "Эълонлар доскаси"], columns=2)
    elif role == ADMIN_ROLE:
        return make_keyboard(["Админ панель"], columns=1)
    return make_keyboard(["Рўйхатдан ўтиш"], columns=1)

def get_admin_menu() -> ReplyKeyboardMarkup:
    """Создаёт меню администратора."""
    return make_keyboard([
        "Фойдаланувчиларни бошқариш", "Эълонларни бошқариш", "Сўровларни бошқариш",
        "Обунани бошқариш", "Архивни бошқариш", "Статистика", "Орқага"
    ], columns=2, add_back_button=False)

def get_profile_menu(role: str) -> ReplyKeyboardMarkup:
    """Создаёт меню профиля."""
    return make_keyboard(
        ["Профиль ҳақида маълумот", "Профильни таҳрирлаш", "Профильни ўчириш", "Орқага"],
        columns=2,
        add_back_button=False
    )

def get_requests_menu(role: Optional[str] = None) -> ReplyKeyboardMarkup:
    """Создаёт меню запросов."""
    return make_keyboard(
        ["Сўровлар рўйхати", "Сўров қўшиш", "Сўровни ўчириш", "Сўровни ёпиш", "Орқага"],
        columns=2,
        add_back_button=False
    )

def get_ads_menu(role: Optional[str] = None) -> ReplyKeyboardMarkup:
    """Создаёт меню объявлений."""
    items = ["Эълонлар рўйхати", "Эълонни ўчириш", "Эълонни ёпиш"] if role == SELLER_ROLE else []
    return make_keyboard(items, columns=2)
