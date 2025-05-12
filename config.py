import os
import logging
import re
from dotenv import load_dotenv
from urllib.parse import urlparse

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# Загрузка .env файла
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    logger.info(f"Загружен .env файл: {dotenv_path}")
else:
    logger.warning(".env файл не найден. Используются системные переменные окружения.")

# --- Обязательные переменные ---

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("BOT_TOKEN не задан!")
    raise ValueError("BOT_TOKEN обязателен")
if not re.match(r'^\d+:[A-Za-z0-9_-]+$', BOT_TOKEN):
    logger.critical("Неверный формат BOT_TOKEN!")
    raise ValueError("BOT_TOKEN должен соответствовать формату '<bot_id>:<random_string>'")

DB_NAME = os.getenv("DB_NAME")
if not DB_NAME:
    logger.critical("DB_NAME не задан!")
    raise ValueError("DB_NAME обязателен")
if not DB_NAME.endswith(".db"):
    logger.critical(f"DB_NAME ({DB_NAME}) должен быть файлом SQLite с расширением .db")
    raise ValueError("DB_NAME должен быть файлом SQLite")
db_dir = os.path.dirname(DB_NAME)
if db_dir and not os.path.exists(db_dir):
    try:
        os.makedirs(db_dir)
        logger.info(f"Создана директория для базы данных: {db_dir}")
    except OSError as e:
        logger.critical(f"Не удалось создать директорию {db_dir}: {e}")
        raise
if db_dir and not os.access(db_dir, os.W_OK):
    logger.critical(f"Нет прав записи в директорию: {db_dir}")
    raise PermissionError(f"Нет прав записи в {db_dir}")

ADMIN_IDS = []
admin_ids_str = os.getenv("ADMIN_IDS")
if admin_ids_str:
    try:
        ADMIN_IDS = [int(id.strip()) for id in admin_ids_str.split(",") if id.strip()]
        logger.info(f"Загружены ADMIN_IDS: {ADMIN_IDS}")
    except ValueError as e:
        logger.critical(f"Неверный формат ADMIN_IDS: {admin_ids_str}: {e}")
        raise ValueError("Неверный формат ADMIN_IDS")
if not ADMIN_IDS:
    logger.critical("ADMIN_IDS пустой!")
    raise ValueError("Требуется хотя бы один ADMIN_IDS")

CHANNEL_ID = os.getenv("CHANNEL_ID")
if not CHANNEL_ID:
    logger.critical("CHANNEL_ID не задан!")
    raise ValueError("CHANNEL_ID обязателен")
try:
    channel_id_int = int(CHANNEL_ID)
    if channel_id_int >= 0:
        logger.critical(f"CHANNEL_ID ({CHANNEL_ID}) должен быть отрицательным для каналов/супергрупп")
        raise ValueError("CHANNEL_ID должен быть отрицательным")
    logger.info(f"Загружен CHANNEL_ID: {CHANNEL_ID}")
except ValueError as e:
    logger.critical(f"Неверный формат CHANNEL_ID: {CHANNEL_ID}: {e}")
    raise ValueError("Неверный формат CHANNEL_ID")

WEBHOOK_PATH = os.getenv("WEBHOOK_PATH")
if not WEBHOOK_PATH:
    logger.critical("WEBHOOK_PATH не задан!")
    raise ValueError("WEBHOOK_PATH обязателен")
if not WEBHOOK_PATH.startswith("/"):
    logger.critical(f"WEBHOOK_PATH ({WEBHOOK_PATH}) должен начинаться с '/'")
    raise ValueError("WEBHOOK_PATH должен начинаться с '/'")
logger.info(f"Загружен WEBHOOK_PATH: {WEBHOOK_PATH}")

# --- Необязательные переменные ---

WEBAPP_URL = os.getenv("WEBAPP_URL")
if WEBAPP_URL:
    try:
        parsed_url = urlparse(WEBAPP_URL)
        if not (parsed_url.scheme == "https" and parsed_url.netloc):
            raise ValueError("WEBAPP_URL должен быть валидным HTTPS URL")
        logger.info(f"Загружен WEBAPP_URL: {WEBAPP_URL}")
    except ValueError as e:
        logger.critical(f"Неверный формат WEBAPP_URL: {WEBAPP_URL}: {e}")
        raise ValueError("Неверный формат WEBAPP_URL")
else:
    logger.warning("WEBAPP_URL не задан. WebApp функциональность будет отключена.")

# SSL не используется в приложении (обрабатывается NGINX)
CERT_PATH = None
KEY_PATH = None

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
if LOG_LEVEL not in VALID_LOG_LEVELS:
    logger.warning(f"Неверный LOG_LEVEL: {LOG_LEVEL}. Установлен по умолчанию 'DEBUG'.")
    LOG_LEVEL = "DEBUG"
logger.info(f"Установлен LOG_LEVEL: {LOG_LEVEL}")

try:
    DB_TIMEOUT = int(os.getenv("DB_TIMEOUT", "15"))
    if DB_TIMEOUT <= 0:
        raise ValueError("DB_TIMEOUT должен быть положительным")
    logger.info(f"Установлен DB_TIMEOUT: {DB_TIMEOUT} секунд")
except ValueError as e:
    logger.warning(f"Неверный DB_TIMEOUT: {os.getenv('DB_TIMEOUT')}. Установлен по умолчанию 15 секунд: {e}")
    DB_TIMEOUT = 15

try:
    FSM_TIMEOUT = int(os.getenv("FSM_TIMEOUT", "600"))
    if FSM_TIMEOUT <= 0:
        raise ValueError("FSM_TIMEOUT должен быть положительным")
    logger.info(f"Установлен FSM_TIMEOUT: {FSM_TIMEOUT} секунд")
except ValueError as e:
    logger.warning(f"Неверный FSM_TIMEOUT: {os.getenv('FSM_TIMEOUT')}. Установлен по умолчанию 600 секунд: {e}")
    FSM_TIMEOUT = 600

try:
    PORT = int(os.getenv("PORT", "8443"))
    if not (0 < PORT < 65536):
        raise ValueError("PORT должен быть в диапазоне 1-65535")
    logger.info(f"Установлен PORT: {PORT}")
except ValueError as e:
    logger.warning(f"Неверный PORT: {os.getenv('PORT')}. Установлен по умолчанию 8443: {e}")
    PORT = 8443

try:
    MAX_COMPANY_NAME_LENGTH = int(os.getenv("MAX_COMPANY_NAME_LENGTH", "50"))
    if MAX_COMPANY_NAME_LENGTH <= 0:
        raise ValueError("MAX_COMPANY_NAME_LENGTH должен быть положительным")
    logger.info(f"Установлен MAX_COMPANY_NAME_LENGTH: {MAX_COMPANY_NAME_LENGTH} символов")
except ValueError as e:
    logger.warning(f"Неверный MAX_COMPANY_NAME_LENGTH: {os.getenv('MAX_COMPANY_NAME_LENGTH')}. Установлен по умолчанию 50: {e}")
    MAX_COMPANY_NAME_LENGTH = 50

# --- Константы приложения ---

SELLER_ROLE = "seller"
BUYER_ROLE = "buyer"
ADMIN_ROLE = "admin"
ROLES = [SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE]

ROLE_MAPPING = {
    "Сотувчи": SELLER_ROLE,
    "Харидор": BUYER_ROLE,
    "Админ": ADMIN_ROLE
}
ROLE_DISPLAY_NAMES = {v: k for k, v in ROLE_MAPPING.items()}
DISPLAY_ROLE_MAPPING = ROLE_DISPLAY_NAMES

SELLER_BASE_ID = "C-000000"
BUYER_BASE_ID = "X-000000"
ADMIN_BASE_ID = "A-000000"

CATEGORIES_STR = os.getenv("CATEGORIES", "Помидор,Бодринг,Қалампир,Бақлажон,Қулупнай,Лимон")
CATEGORIES = [cat.strip() for cat in CATEGORIES_STR.split(",") if cat.strip()]
if not CATEGORIES:
    logger.critical("CATEGORIES пустой!")
    raise ValueError("Требуется хотя бы одна категория")
logger.info(f"Загружены CATEGORIES: {CATEGORIES}")

try:
    MAX_SORT_LENGTH = int(os.getenv("MAX_SORT_LENGTH", "50"))
    if MAX_SORT_LENGTH <= 0:
        raise ValueError("MAX_SORT_LENGTH должен быть положительным")
    logger.info(f"Установлен MAX_SORT_LENGTH: {MAX_SORT_LENGTH} символов")
except ValueError as e:
    logger.warning(f"Неверный MAX_SORT_LENGTH: {os.getenv('MAX_SORT_LENGTH')}. Установлен по умолчанию 50: {e}")
    MAX_SORT_LENGTH = 50

try:
    MAX_VOLUME_TON = int(os.getenv("MAX_VOLUME_TON", "1000"))
    if MAX_VOLUME_TON <= 0:
        raise ValueError("MAX_VOLUME_TON должен быть положительным")
    logger.info(f"Установлен MAX_VOLUME_TON: {MAX_VOLUME_TON} тонн")
except ValueError as e:
    logger.warning(f"Неверный MAX_VOLUME_TON: {os.getenv('MAX_VOLUME_TON')}. Установлен по умолчанию 1000: {e}")
    MAX_VOLUME_TON = 1000

try:
    MAX_PRICE = int(os.getenv("MAX_PRICE", "100000000"))
    if MAX_PRICE <= 0:
        raise ValueError("MAX_PRICE должен быть положительным")
    logger.info(f"Установлен MAX_PRICE: {MAX_PRICE} UZS")
except ValueError as e:
    logger.warning(f"Неверный MAX_PRICE: {os.getenv('MAX_PRICE')}. Установлен по умолчанию 100000000: {e}")
    MAX_PRICE = 100_000_000

try:
    MAX_PHOTOS = int(os.getenv("MAX_PHOTOS", "4"))
    if MAX_PHOTOS <= 0:
        raise ValueError("MAX_PHOTOS должен быть положительным")
    logger.info(f"Установлен MAX_PHOTOS: {MAX_PHOTOS}")
except ValueError as e:
    logger.warning(f"Неверный MAX_PHOTOS: {os.getenv('MAX_PHOTOS')}. Установлен по умолчанию 4: {e}")
    MAX_PHOTOS = 4

try:
    MAX_PHOTOS_STRING_LENGTH = int(os.getenv("MAX_PHOTOS_STRING_LENGTH", "1000"))
    if MAX_PHOTOS_STRING_LENGTH <= 0:
        raise ValueError("MAX_PHOTOS_STRING_LENGTH должен быть положительным")
    logger.info(f"Установлен MAX_PHOTOS_STRING_LENGTH: {MAX_PHOTOS_STRING_LENGTH} символов")
except ValueError as e:
    logger.warning(f"Неверный MAX_PHOTOS_STRING_LENGTH: {os.getenv('MAX_PHOTOS_STRING_LENGTH')}. Установлен по умолчанию 1000: {e}")
    MAX_PHOTOS_STRING_LENGTH = 1000

try:
    MAX_FILE_ID_LENGTH = int(os.getenv("MAX_FILE_ID_LENGTH", "100"))
    if MAX_FILE_ID_LENGTH <= 0:
        raise ValueError("MAX_FILE_ID_LENGTH должен быть положительным")
    logger.info(f"Установлен MAX_FILE_ID_LENGTH: {MAX_FILE_ID_LENGTH} символов")
except ValueError as e:
    logger.warning(f"Неверный MAX_FILE_ID_LENGTH: {os.getenv('MAX_FILE_ID_LENGTH')}. Установлен по умолчанию 100: {e}")
    MAX_FILE_ID_LENGTH = 100

SUBSCRIPTION_PRICES = {
    "period_days": int(os.getenv("SUBSCRIPTION_PERIOD_DAYS", "30")),
    "bot": int(os.getenv("SUBSCRIPTION_BOT_PRICE", "100000"))
}
try:
    if SUBSCRIPTION_PRICES["period_days"] <= 0:
        raise ValueError("SUBSCRIPTION_PERIOD_DAYS должен быть положительным")
    if SUBSCRIPTION_PRICES["bot"] <= 0:
        raise ValueError("SUBSCRIPTION_BOT_PRICE должен быть положительным")
    logger.info(f"Загружены SUBSCRIPTION_PRICES: {SUBSCRIPTION_PRICES}")
except ValueError as e:
    logger.critical(f"Неверные SUBSCRIPTION_PRICES: {e}")
    raise

# --- Проверка конфигурации ---

if __name__ == "__main__":
    print("--- Проверка конфигурации ---")
    print(f"BOT_TOKEN: {'***' + BOT_TOKEN[-6:] if BOT_TOKEN else 'НЕ ЗАДАН!'}")
    print(f"LOG_LEVEL: {LOG_LEVEL}")
    print(f"ADMIN_IDS: {ADMIN_IDS}")
    print(f"DB_NAME: {DB_NAME}")
    print(f"DB_TIMEOUT: {DB_TIMEOUT} сек")
    print(f"CHANNEL_ID: {CHANNEL_ID}")
    print(f"WEBAPP_URL: {WEBAPP_URL if WEBAPP_URL else 'Не задан'}")
    print(f"WEBHOOK_PATH: {WEBHOOK_PATH}")
    print(f"PORT: {PORT}")
    print(f"FSM_TIMEOUT: {FSM_TIMEOUT} сек")
    print(f"MAX_COMPANY_NAME_LENGTH: {MAX_COMPANY_NAME_LENGTH}")
    print(f"SUBSCRIPTION_PRICES: {SUBSCRIPTION_PRICES}")
    print("-" * 20)
    print(f"ROLE_MAPPING: {ROLE_MAPPING}")
    print(f"ROLE_DISPLAY_NAMES: {ROLE_DISPLAY_NAMES}")
    print(f"DISPLAY_ROLE_MAPPING: {DISPLAY_ROLE_MAPPING}")
    print(f"SELLER_BASE_ID: {SELLER_BASE_ID}")
    print(f"BUYER_BASE_ID: {BUYER_BASE_ID}")
    print(f"ADMIN_BASE_ID: {ADMIN_BASE_ID}")
    print(f"CATEGORIES: {CATEGORIES}")
    print(f"Лимиты: Company={MAX_COMPANY_NAME_LENGTH}, Sort={MAX_SORT_LENGTH}, "
          f"Tons={MAX_VOLUME_TON}, Price={MAX_PRICE:,}, Photos={MAX_PHOTOS}, "
          f"PhotoString={MAX_PHOTOS_STRING_LENGTH}, FileID={MAX_FILE_ID_LENGTH}")
    print("--- Проверка завершена ---")
