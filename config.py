import os
import logging
from dotenv import load_dotenv
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Load environment variables from .env file
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    logger.info(f"Loaded environment variables from {dotenv_path}")
else:
    logger.warning("No .env file found. Using system environment variables.")

# --- Mandatory variables ---

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical(f"BOT_TOKEN not set in environment variables! Got: {'***' + BOT_TOKEN[-4:] if BOT_TOKEN else 'None'}")
    raise ValueError("BOT_TOKEN is required")

DB_NAME = os.getenv("DB_NAME")
if not DB_NAME:
    logger.critical("DB_NAME not set in environment variables!")
    raise ValueError("DB_NAME is required")
if not DB_NAME.endswith(".db") or os.path.isdir(DB_NAME):
    logger.critical(f"DB_NAME ({DB_NAME}) must be a valid SQLite database file path")
    raise ValueError("DB_NAME must be a valid SQLite database file path")
if len(DB_NAME) > 255:
    logger.critical(f"DB_NAME path too long: {DB_NAME}")
    raise ValueError("DB_NAME path must be less than 255 characters")
db_dir = os.path.dirname(DB_NAME)
if db_dir and not os.path.exists(db_dir):
    try:
        os.makedirs(db_dir)
        logger.info(f"Created directory for database: {db_dir}")
    except OSError as e:
        logger.error(f"Failed to create directory for database {db_dir}: {e}", exc_info=True)
        raise

admin_ids_str = os.getenv("ADMIN_IDS")
ADMIN_IDS = []
if admin_ids_str:
    try:
        ADMIN_IDS = list({int(id_str.strip()) for id_str in admin_ids_str.split(",") if id_str.strip()})
        if len(ADMIN_IDS) != len(set(ADMIN_IDS)):
            logger.warning("Duplicate IDs found in ADMIN_IDS")
        logger.info(f"Loaded ADMIN_IDS: {ADMIN_IDS}")
    except ValueError as e:
        logger.error(f"Invalid ADMIN_IDS format: {admin_ids_str}: {e}", exc_info=True)
        raise ValueError("Invalid ADMIN_IDS format")
if not ADMIN_IDS:
    logger.critical("ADMIN_IDS is empty after parsing!")
    raise ValueError("At least one valid ADMIN_IDS is required")

CHANNEL_ID = os.getenv("CHANNEL_ID")
if CHANNEL_ID:
    try:
        channel_id_int = int(CHANNEL_ID)
        if channel_id_int >= 0:
            logger.warning(f"CHANNEL_ID ({CHANNEL_ID}) must be a negative number for channels/supergroups")
            raise ValueError("CHANNEL_ID must be negative")
        logger.info(f"Loaded CHANNEL_ID: {CHANNEL_ID}")
    except ValueError as e:
        logger.error(f"Invalid CHANNEL_ID format: {CHANNEL_ID}: {e}", exc_info=True)
        raise ValueError("Invalid CHANNEL_ID format")
else:
    logger.critical("CHANNEL_ID not set in environment variables!")
    raise ValueError("CHANNEL_ID is required")

# --- Webhook and SSL variables ---

WEBAPP_URL = os.getenv("WEBAPP_URL")
if WEBAPP_URL:
    try:
        parsed_url = urlparse(WEBAPP_URL)
        if not all([parsed_url.scheme == "https", parsed_url.netloc]):
            raise ValueError("WEBAPP_URL must be a valid HTTPS URL")
        logger.info(f"Loaded WEBAPP_URL: {WEBAPP_URL}")
    except ValueError as e:
        logger.error(f"Invalid WEBAPP_URL format: {WEBAPP_URL}: {e}", exc_info=True)
        raise ValueError("Invalid WEBAPP_URL format")
else:
    logger.warning("WEBAPP_URL not set. WebApp functionality will be disabled.")

WEBHOOK_PATH = os.getenv("WEBHOOK_PATH")
if not WEBHOOK_PATH:
    logger.critical("WEBHOOK_PATH not set in environment variables!")
    raise ValueError("WEBHOOK_PATH is required")
if not WEBHOOK_PATH.startswith("/"):
    logger.critical(f"WEBHOOK_PATH ({WEBHOOK_PATH}) must start with '/'")
    raise ValueError("WEBHOOK_PATH must start with '/'")
logger.info(f"Loaded WEBHOOK_PATH: {WEBHOOK_PATH}")

CERT_PATH = os.getenv("CERT_PATH")
if not CERT_PATH:
    logger.critical("CERT_PATH not set in environment variables!")
    raise ValueError("CERT_PATH is required")
if not os.path.exists(CERT_PATH):
    logger.critical(f"Certificate file not found: {CERT_PATH}")
    raise FileNotFoundError(f"Certificate file not found: {CERT_PATH}")
logger.info(f"Loaded CERT_PATH: {CERT_PATH}")

KEY_PATH = os.getenv("KEY_PATH")
if not KEY_PATH:
    logger.critical("KEY_PATH not set in environment variables!")
    raise ValueError("KEY_PATH is required")
if not os.path.exists(KEY_PATH):
    logger.critical(f"Private key file not found: {KEY_PATH}")
    raise FileNotFoundError(f"Private key file not found: {KEY_PATH}")
logger.info(f"Loaded KEY_PATH: {KEY_PATH}")

# --- Optional variables ---

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
if LOG_LEVEL not in VALID_LOG_LEVELS:
    logger.warning(f"Invalid LOG_LEVEL: {LOG_LEVEL}. Defaulting to 'DEBUG'.")
    LOG_LEVEL = "DEBUG"
logger.info(f"Set LOG_LEVEL: {LOG_LEVEL}")

try:
    DB_TIMEOUT = int(os.getenv("DB_TIMEOUT", "15"))
    if DB_TIMEOUT <= 0:
        raise ValueError("DB_TIMEOUT must be positive")
    logger.info(f"Set DB_TIMEOUT: {DB_TIMEOUT} seconds")
except ValueError as e:
    logger.warning(f"Invalid DB_TIMEOUT: {os.getenv('DB_TIMEOUT')}. Defaulting to 15 seconds: {e}", exc_info=True)
    DB_TIMEOUT = 15

try:
    FSM_TIMEOUT = int(os.getenv("FSM_TIMEOUT", "600"))
    if FSM_TIMEOUT <= 0:
        raise ValueError("FSM_TIMEOUT must be positive")
    logger.info(f"Set FSM_TIMEOUT: {FSM_TIMEOUT} seconds")
except ValueError as e:
    logger.warning(f"Invalid FSM_TIMEOUT: {os.getenv('FSM_TIMEOUT')}. Defaulting to 600 seconds: {e}", exc_info=True)
    FSM_TIMEOUT = 600

try:
    PORT = int(os.getenv("PORT", "8443"))
    if not (0 < PORT < 65536):
        raise ValueError("PORT must be in range 1-65535")
    logger.info(f"Set PORT: {PORT}")
except ValueError as e:
    logger.warning(f"Invalid PORT: {os.getenv('PORT')}. Defaulting to 8443: {e}", exc_info=True)
    PORT = 8443

try:
    MAX_COMPANY_NAME_LENGTH = int(os.getenv("MAX_COMPANY_NAME_LENGTH", "50"))
    if MAX_COMPANY_NAME_LENGTH <= 0:
        raise ValueError("MAX_COMPANY_NAME_LENGTH must be positive")
    logger.info(f"Set MAX_COMPANY_NAME_LENGTH: {MAX_COMPANY_NAME_LENGTH} characters")
except ValueError as e:
    logger.warning(f"Invalid MAX_COMPANY_NAME_LENGTH: {os.getenv('MAX_COMPANY_NAME_LENGTH')}. Defaulting to 50: {e}", exc_info=True)
    MAX_COMPANY_NAME_LENGTH = 50

# --- Application constants ---

SELLER_ROLE = "seller"  # Role for sellers
BUYER_ROLE = "buyer"    # Role for buyers
ADMIN_ROLE = "admin"    # Role for administrators
ROLES = [SELLER_ROLE, BUYER_ROLE, ADMIN_ROLE]

ROLE_MAPPING = {
    "Сотувчи": SELLER_ROLE,
    "Харидор": BUYER_ROLE,
    "Админ": ADMIN_ROLE
}
ROLE_DISPLAY_NAMES = {v: k for k, v in ROLE_MAPPING.items()}

SELLER_BASE_ID = "C-000000"  # Format: C-000123
BUYER_BASE_ID = "X-000000"   # Format: X-000456
ADMIN_BASE_ID = "A-000000"   # Format: A-000001

CATEGORIES = ["Помидор", "Бодринг", "Қалампир", "Бақлажон", "Қулупнай", "Лимон"]

MAX_SORT_LENGTH = 50                 # Maximum length for product sort
MAX_VOLUME_TON = 1000                # Maximum volume in tons
MAX_PRICE = 100_000_000              # Maximum price in UZS
MAX_PHOTOS = 4                       # Maximum number of photos per product

# Subscription prices
SUBSCRIPTION_PRICES = {
    "period_days": 30,           # Subscription period in days
    "channel": 50000,            # Price for channel subscription in UZS
    "bot_and_channel": 100000    # Price for bot + channel subscription in UZS
}

# --- Configuration check ---

if __name__ == "__main__":
    print("--- Configuration Check ---")
    print(f"BOT_TOKEN: {'***' + BOT_TOKEN[-6:] if BOT_TOKEN else 'NOT SET!'}")
    print(f"LOG_LEVEL: {LOG_LEVEL}")
    print(f"ADMIN_IDS: {ADMIN_IDS}")
    print(f"DB_NAME: {DB_NAME}")
    print(f"DB_TIMEOUT: {DB_TIMEOUT} sec")
    print(f"CHANNEL_ID: {CHANNEL_ID if CHANNEL_ID else 'Not set'}")
    print(f"WEBAPP_URL: {WEBAPP_URL if WEBAPP_URL else 'Not set'}")
    print(f"WEBHOOK_PATH: {WEBHOOK_PATH if WEBHOOK_PATH else 'Not set'}")
    print(f"CERT_PATH: {CERT_PATH if CERT_PATH else 'Not set'}")
    print(f"KEY_PATH: {KEY_PATH if KEY_PATH else 'Not set'}")
    print(f"PORT: {PORT}")
    print(f"FSM_TIMEOUT: {FSM_TIMEOUT} sec")
    print(f"MAX_COMPANY_NAME_LENGTH: {MAX_COMPANY_NAME_LENGTH}")
    print(f"SUBSCRIPTION_PRICES: {SUBSCRIPTION_PRICES}")
    print("-" * 20)
    print(f"ROLE_MAPPING: {ROLE_MAPPING}")
    print(f"ROLE_DISPLAY_NAMES: {ROLE_DISPLAY_NAMES}")
    print(f"SELLER_BASE_ID: {SELLER_BASE_ID}")
    print(f"BUYER_BASE_ID: {BUYER_BASE_ID}")
    print(f"ADMIN_BASE_ID: {ADMIN_BASE_ID}")
    print(f"CATEGORIES: {CATEGORIES}")
    print(f"Limits: Company={MAX_COMPANY_NAME_LENGTH}, Sort={MAX_SORT_LENGTH}, "
          f"Tons={MAX_VOLUME_TON}, Price={MAX_PRICE:,}, Photos={MAX_PHOTOS}")
    print("--- Check Completed ---")
