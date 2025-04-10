import os
import logging
from dotenv import load_dotenv
from urllib.parse import urlparse

# Настройка логирования для этого модуля
logger = logging.getLogger(__name__)

# Загружаем переменные из .env
load_dotenv()

# Токен бота из .env (обязательная переменная)
BOT_TOKEN = os.getenv("API_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN не установлен в .env")
    raise ValueError("BOT_TOKEN не установлен в .env")

# Уровень логирования (по умолчанию "INFO", а не "DEBUG")
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
LOG_FILE = "bot.log"

# Определение ролей пользователей
SELLER_ROLE = "Сотувчи"  # Роль продавца
BUYER_ROLE = "Харидор"   # Роль покупателя
ADMIN_ROLE = "Админ"     # Роль администратора
ROLES = [SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE]  # Список всех ролей

# Маппинг ролей между узбекским (интерфейс) и английским (база данных)
ROLE_MAPPING = {
    "Сотувчи": "seller",
    "Харидор": "buyer",
    "Админ": "admin"
}

# Базовые ID для генерации идентификаторов пользователей
SELLER_BASE_ID ="C-0000"  # Увеличена емкость до 6 цифр
BUYER_BASE_ID = "X-0000"   # Увеличена емкость до 6 цифр
ADMIN_BASE_ID = "A-0000"   # Увеличена емкость до 6 цифр

# Название файла базы данных (по умолчанию "market_bot.db")
DB_NAME = os.getenv("DB_NAME", "market_bot.db")

# Список категорий товаров
CATEGORIES = ["Помидор", "Бодринг", "Қалампир", "Бақлажон", "Қулупнай", "Лимон"]

# Константы ограничений
MAX_COMPANY_NAME_LENGTH = 50    # Максимальная длина названия компании
MAX_SORT_LENGTH = 50            # Максимальная длина сорта
MAX_VOLUME_TON = 1000           # Максимальный объём в тоннах
MAX_PRICE = 100000000           # Максимальная цена
MAX_PHOTOS = 4                  # Максимальное количество фотографий

# ID канала (по умолчанию "-1002021157080")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1002391886062")
if not CHANNEL_ID.startswith("-100"):
    logger.error("CHANNEL_ID должен начинаться с '-100' для Telegram каналов")
    raise ValueError("CHANNEL_ID должен начинаться с '-100' для Telegram каналов")

# URL мини-приложения (по умолчанию из .env с валидацией)
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://146.190.105.84:5001")
try:
    parsed_url = urlparse(WEBAPP_URL)
    if not all([parsed_url.scheme, parsed_url.netloc]):
        raise ValueError("WEBAPP_URL должен быть валидным URL (например, https://example.com)")
except ValueError as e:
    logger.error(f"Некорректный формат WEBAPP_URL: {WEBAPP_URL}. Ошибка: {str(e)}")
    raise ValueError(f"Некорректный WEBAPP_URL: {WEBAPP_URL}. Ошибка: {str(e)}")

# Список ID администраторов с улучшенной обработкой ошибок
try:
    admin_ids_str = os.getenv("ADMIN_IDS")  # Например, "1105173889,987654321"
    if admin_ids_str:
        ADMIN_IDS = []
        for id_str in admin_ids_str.split(","):
            try:
                admin_id = int(id_str.strip())
                ADMIN_IDS.append(admin_id)
            except ValueError:
                logger.warning(f"Некорректное значение в ADMIN_IDS: '{id_str}' пропущено")
        if not ADMIN_IDS:
            logger.warning("Ни один ID в ADMIN_IDS не был корректно распознан, список пуст")
    else:
        logger.warning("ADMIN_IDS не указан в .env, используется пустой список")
        ADMIN_IDS = []
except AttributeError as e:
    logger.error(f"Ошибка в парсинге ADMIN_IDS: {str(e)}. Используется пустой список.")
    ADMIN_IDS = []

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

# Словарь для обратного преобразования (узбекский -> английский)
MONTHS_UZ_TO_EN = {uz: en for en, uz in MONTHS_UZ.items()}

def generate_request_number(request_id: int) -> str:
    """Генерирует номер запроса в формате R-xxx для синхронизации с database.py.

    Args:
        request_id (int): ID запроса.

    Returns:
        str: Отформатированный номер, например, 'R-001'.
    """
    return f"R-{request_id:03d}"  # Формат 'R-xxx' для совместимости с базой данных

# Проверка конфигурации при запуске модуля напрямую
if __name__ == "__main__":
    logger.info("Конфигурация успешно загружена")
