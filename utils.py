# utils.py
import aiosqlite
import logging
from datetime import datetime
from aiogram import Bot, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup
from config import DB_NAME, ADMIN_IDS, ADMIN_ROLE, ROLE_MAPPING, CHANNEL_ID  # Добавлен CHANNEL_ID

logger = logging.getLogger(__name__)

MONTHS_UZ = {
    "January": "январ", "February": "феврал", "March": "март", "April": "апрел",
    "May": "май", "June": "июн", "July": "июл", "August": "август",
    "September": "сентябр", "October": "октябр", "November": "ноябр", "December": "декабр"
}

MONTHS_UZ_TO_EN = {v: k for k, v in MONTHS_UZ.items()}


def parse_uz_datetime(date_str: str) -> datetime:
    for uz, en in MONTHS_UZ_TO_EN.items():
        date_str = date_str.replace(uz, en)
    return datetime.strptime(date_str, "%d %B %Y йил %H:%M:%S")


def format_uz_datetime(dt: datetime) -> str:
    eng_date = dt.strftime("%d %B %Y йил %H:%M:%S")
    for en, uz in MONTHS_UZ.items():
        eng_date = eng_date.replace(en, uz)
    return eng_date


async def check_subscription(bot: Bot, user_id: int) -> tuple[bool, bool, bool]:
    """
    Проверяет статус подписки пользователя на канал и бот.

    Args:
        bot: Экземпляр бота.
        user_id: ID пользователя в Telegram.

    Returns:
        Tuple[bool, bool, bool]: (channel_active, bot_active, is_subscribed)
    """
    # Проверка подписки на канал
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        channel_active = member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.error(f"Ошибка проверки подписки на канал для {user_id}: {e}")
        channel_active = False

    # Проверка подписки на бот
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT bot_expires FROM payments WHERE user_id = ?", (user_id,)) as cursor:
                result = await cursor.fetchone()
        bot_expires = result[0] if result else None
        bot_active = bot_expires and datetime.now() < parse_uz_datetime(bot_expires)
    except aiosqlite.Error as e:
        logger.error(f"Ошибка проверки подписки на бот для {user_id}: {e}")
        bot_active = False

    # Общий статус подписки
    is_subscribed = channel_active or bot_active
    logger.debug(
        f"Проверка подписки для {user_id}: channel_active={channel_active}, bot_active={bot_active}, is_subscribed={is_subscribed}")
    return channel_active, bot_active, is_subscribed


async def check_role(message: types.Message, required_role: str | None = None, allow_unregistered: bool = False) -> \
tuple[bool, str | None]:
    user_id = message.from_user.id
    bot = message.bot
    if user_id in ADMIN_IDS:
        logger.debug(f"Пользователь {user_id} в ADMIN_IDS, доступ разрешён как {ADMIN_ROLE}")
        return True, ADMIN_ROLE
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)) as cursor:
                result = await cursor.fetchone()
        if not result:
            if allow_unregistered:
                logger.debug(f"Пользователь {user_id} не зарегистрирован, но allow_unregistered=True")
                return True, None
            await message.answer("Илтимос, аввал рўйхатдан ўтинг: /start")
            logger.info(f"Пользователь {user_id} не зарегистрирован")
            return False, None
        db_role = result[0]
        display_role = {v: k for k, v in ROLE_MAPPING.items()}.get(db_role, db_role)
        logger.info(
            f"Проверка роли для {user_id}: db_role={db_role}, display_role={display_role}, required_role={required_role}")
        if display_role == ADMIN_ROLE:
            logger.debug(f"Пользователь {user_id} — администратор ({ADMIN_ROLE}), доступ разрешён")
            return True, display_role
        # Убираем проверку подписки здесь, так как она уже есть в global_subscription_check
        if required_role and display_role != required_role:
            await message.answer(f"Ушбу амал учун {required_role} роли керак.")
            logger.info(f"Доступ для {user_id} ограничен: требуется роль {required_role}, текущая роль {display_role}")
            return False, display_role
        logger.debug(f"Доступ для {user_id} разрешён: роль {display_role}")
        return True, display_role
    except aiosqlite.Error as e:
        logger.error(f"Ошибка проверки роли для пользователя {user_id}: {e}")
        await message.answer("Хатолик юз берди. Админ билан боғланинг.")
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"Ошибка базы для {user_id}: {e}")
            except Exception as send_err:
                logger.warning(f"Не удалось уведомить админа {admin_id}: {send_err}")
        return False, None


def make_keyboard(options: list[str], columns: int = 1, with_back: bool = False,
                  one_time: bool = False) -> ReplyKeyboardMarkup:
    """Создаёт клавиатуру из списка опций с заданным количеством столбцов."""
    buttons = [KeyboardButton(text=option) for option in options]
    if with_back:
        buttons.append(KeyboardButton(text="Орқага"))

    keyboard = []
    for i in range(0, len(buttons), columns):
        row = buttons[i:i + columns]
        keyboard.append(row)

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=one_time
    )


def validate_number(text: str, max_value: int) -> int | None:
    try:
        value = int(text.strip())
        if value <= 0 or value > max_value:
            return None
        return value
    except ValueError:
        return None


async def has_pending_items(user_id: int) -> bool:
    return False  # Заглушка


class ExpiredItem(StatesGroup):
    choice = State()
    final_price = State()
