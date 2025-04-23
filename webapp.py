import logging
import os
from typing import Tuple
from quart import Quart, send_from_directory, Response
from quart_cors import cors
import hypercorn.asyncio
import hypercorn.config
import asyncio
import signal
import pytz
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import LOG_LEVEL, WEBAPP_URL
from main import UzbekDateFormatter  # Импортируем из main.py

logger = logging.getLogger(__name__)

# Настройка логирования
formatter = UzbekDateFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = RotatingFileHandler(
    os.path.join(os.path.dirname(__file__), "webapp.log"),
    maxBytes=10*1024*1024,
    backupCount=5,
    encoding='utf-8'
)
handler.setFormatter(formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    handlers=[handler, console_handler]
)

app = Quart(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB
app = cors(app, allow_origin=[WEBAPP_URL, "https://mbozor.msma.uz:5001"], allow_methods=["GET"])
logger.info(f"CORS настроен для: {WEBAPP_URL}, https://mbozor.msma.uz:5001")

@app.route('/webapp.html')
async def serve_webapp() -> Response:
    """Возвращает файл webapp.html для Telegram Web App."""
    logger.info("Запрос на /webapp.html")
    try:
        response = await send_from_directory('.', 'webapp.html')
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        response.headers['Cache-Control'] = 'public, max-age=3600'  # Кэш на 1 час
        logger.debug("Файл webapp.html успешно отправлен")
        return response
    except FileNotFoundError:
        logger.error("Файл webapp.html не найден")
        return {"error": "Webapp file not found"}, 404
    except Exception as e:
        logger.error(f"Ошибка при отправке webapp.html: {e}", exc_info=True)
        return {"error": "Server error", "details": str(e)}, 500

@app.route('/static/<path:filename>')
async def serve_static(filename: str) -> Response:
    """Возвращает статические файлы (например, CSS, JS)."""
    logger.info(f"Запрос на статический файл: {filename}")
    try:
        response = await send_from_directory('static', filename)
        logger.debug(f"Статический файл {filename} успешно отправлен")
        return response
    except FileNotFoundError:
        logger.error(f"Статический файл {filename} не найден")
        return {"error": "File not found"}, 404
    except Exception as e:
        logger.error(f"Ошибка при отправке статического файла {filename}: {e}", exc_info=True)
        return {"error": "Server error", "details": str(e)}, 500

async def shutdown():
    """Graceful shutdown сервера."""
    logger.info("Завершение работы сервера webapp.py...")
    tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.sleep(0.1)
    logger.info("Сервер webapp.py остановлен")

def handle_shutdown(loop):
    """Обрабатывает сигналы SIGINT/SIGTERM."""
    logger.info("Получен сигнал завершения")
    loop.create_task(shutdown())

if __name__ == "__main__":
    async def main():
        config = hypercorn.config.Config()
        config.bind = ["0.0.0.0:5001"]
        config.certfile = "/home/developer/Mbozor/certs/fullchain.pem"
        config.keyfile = "/home/developer/Mbozor/certs/privkey.pem"
        logger.info("Запуск веб-сервера на 0.0.0.0:5001 с HTTPS")
        try:
            loop = asyncio.get_event_loop()
            loop.add_signal_handler(signal.SIGINT, handle_shutdown, loop)
            loop.add_signal_handler(signal.SIGTERM, handle_shutdown, loop)
            await hypercorn.asyncio.serve(app, config)
        except Exception as e:
            logger.error(f"Ошибка запуска сервера: {e}", exc_info=True)
            raise

    asyncio.run(main())
