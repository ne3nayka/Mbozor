import os
import logging
from dotenv import load_dotenv
from urllib.parse import urlparse

# Настройка логирования для этого модуля
logger = logging.getLogger(__name__)

# Загружаем переменные из .env
load_dotenv()

# Токен бота из .env (обязательная переменная)
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Исправлено с API_TOKEN на BOT_TOKEN
if not BOT_TOKEN:
    logger.error("BOT_TOKEN не установлен в .env")
    raise ValueError("BOT_TOKEN не установлен в .env")

# Уровень логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")  # По умолчанию INFO для продакшена
LOG_FILE = "bot.log"

# Определение ролей пользователей (английские версии для базы данных)
SELLER_ROLE = "seller"
BUYER_ROLE = "buyer"
ADMIN_ROLE = "admin"
ROLES = [SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE]

# Маппинг ролей между узбекским (интерфейс) и английским (база данных)
ROLE_MAPPING = {
    "Сотувчи": SELLER_ROLE,
    "Харидор": BUYER_ROLE,
    "Админ": ADMIN_ROLE
}

# Обратный маппинг для отображения
ROLE_DISPLAY_NAMES = {
    SELLER_ROLE: "Сотувчи",
    BUYER_ROLE: "Харидор",
    ADMIN_ROLE: "Админ"
}

# Название файла базы данных
DB_NAME = os.getenv("DB_NAME", "market_bot.db")

# Список категорий товаров
CATEGORIES = ["Помидор", "Бодринг", "Қалампир", "Бақлажон", "Қулупнай", "Лимон"]

# Константы ограничений
MAX_COMPANY_NAME_LENGTH = 50
MAX_SORT_LENGTH = 50
MAX_VOLUME_TON = 1000
MAX_PRICE = 100000000
MAX_PHOTOS = 4

# ID канала
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1002391886062")
if not CHANNEL_ID.startswith("-100"):
    logger.error("CHANNEL_ID должен начинаться с '-100' для Telegram каналов")
    raise ValueError("CHANNEL_ID должен начинаться с '-100' для Telegram каналов")

# URL мини-приложения
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://mbozor.msma.uz")
try:
    parsed_url = urlparse(WEBAPP_URL)
    if not all([parsed_url.scheme, parsed_url.netloc]):
        raise ValueError("WEBAPP_URL должен быть валидным URL")
except ValueError as e:
    logger.error(f"Некорректный формат WEBAPP_URL: {WEBAPP_URL}. Ошибка: {str(e)}")
    raise

# Список ID администраторов
ADMIN_IDS = []
admin_ids_str = os.getenv("ADMIN_IDS", "")
if admin_ids_str:
    for id_str in admin_ids_str.split(","):
        try:
            ADMIN_IDS.append(int(id_str.strip()))
        except ValueError:
            logger.warning(f"Некорректный ID администратора: '{id_str}'")
if not ADMIN_IDS:
    logger.warning("Список ADMIN_IDS пуст! Бот не будет иметь администраторов")

# Словарь месяцев на узбекском языке
MONTHS_UZ = {
    "January": "Yanvar",
    "February": "Fevral",
    "March": "Mart",
    "April": "Aprel",
    "May": "May",
    "June": "Iyun",
    "July": "Iyul",
    "August": "Avgust",
    "September": "Sentyabr",
    "October": "Oktyabr",
    "November": "Noyabr",
    "December": "Dekabr"
}

# Проверка конфигурации
if __name__ == "__main__":
    logger.info("Проверка конфигурации:")
    logger.info(f"BOT_TOKEN: {'установлен' if BOT_TOKEN else 'НЕ УСТАНОВЛЕН'}")
    logger.info(f"ADMIN_IDS: {ADMIN_IDS}")
    logger.info(f"ROLE_MAPPING: {ROLE_MAPPING}")
