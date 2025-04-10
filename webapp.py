from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from quart import Quart, jsonify, request, send_from_directory
import aiosqlite
from config import DB_NAME, BOT_TOKEN, LOG_LEVEL
import logging
import requests
from utils import format_uz_datetime, parse_uz_datetime
from quart_cors import cors
from main import get_main_menu, dp

# Настройка логирования
logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger(__name__)

app = Quart(__name__)
app = cors(app, allow_origin="*", allow_methods=["GET"])


@dp.message(F.web_app_data)
async def handle_webapp_close(message: Message, state: FSMContext):
    """Обрабатывает данные из Web App и возвращает пользователя в основное меню."""
    user_id = message.from_user.id
    web_app_data = message.web_app_data.data
    logger.info(f"Получены данные из Web App от {user_id}: '{web_app_data}'")

    # Игнорируем пустые или неожиданные данные при открытии
    if not web_app_data or web_app_data == "":
        logger.warning(f"Получены пустые данные из Web App от {user_id}, игнорируем.")
        return

    data = await state.get_data()
    role = data.get("user_role") or data.get("role_display")  # Улучшена совместимость с данными состояния

    if web_app_data == "closed":
        await message.answer(
            "Асосий менюга қайтинг:",
            reply_markup=get_main_menu(role)
        )
        await state.clear()
        logger.info(f"Пользователь {user_id} возвращён в главное меню из Web App")
    else:
        logger.warning(f"Неизвестные данные из Web App от {user_id}: '{web_app_data}'")
        await message.answer("Неверный формат данных из Web App.")


async def get_all_data(table: str, status: str | None = None) -> list[dict[str, any]]:
    """Получает данные из указанной таблицы с фильтром по статусу."""
    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            if table == "products":
                query = "SELECT p.*, u.region FROM products p JOIN users u ON p.user_id = u.id WHERE p.status != 'hidden'"
                if status:
                    query += " AND p.status = ?"
                    cursor = await conn.execute(query, (status,))
                else:
                    cursor = await conn.execute(query)
            else:  # requests
                query = "SELECT * FROM requests WHERE status != 'hidden'"
                if status:
                    query += " AND status = ?"
                    cursor = await conn.execute(query, (status,))
                else:
                    cursor = await conn.execute(query)
            items = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            result = [dict(zip(columns, item)) for item in items]

            for item in result:
                # Обработка created_at
                if "created_at" in item and item["created_at"]:
                    created_at_dt = parse_uz_datetime(item["created_at"])
                    if created_at_dt:
                        item["created_at"] = format_uz_datetime(created_at_dt)
                    else:
                        logger.warning(f"Некорректный формат created_at в {table}: {item['created_at']}")
                        item["created_at"] = "Не указано"
                # Обработка archived_at
                if "archived_at" in item and item["archived_at"]:
                    archived_at_dt = parse_uz_datetime(item["archived_at"])
                    if archived_at_dt:
                        item["archived_at"] = format_uz_datetime(archived_at_dt)
                    else:
                        logger.warning(f"Некорректный формат archived_at в {table}: {item['archived_at']}")
                        item["archived_at"] = "Не указано"

            logger.debug(f"Данные из таблицы {table} (status={status}): {len(result)} записей")
            return result
    except aiosqlite.Error as e:
        logger.error(f"Ошибка при загрузке данных из {table}: {e}")
        return []
    except Exception as e:
        logger.error(f"Неожиданная ошибка в get_all_data для таблицы {table}: {e}", exc_info=True)
        return []


@app.route('/all_products')
async def get_all_products():
    """Возвращает все активные продукты."""
    logger.info("Получен запрос на /all_products")
    products = await get_all_data("products", "active")
    if not products and products != []:  # Отличаем пустой результат от ошибки
        logger.error("Ошибка при получении продуктов: результат None или некорректен")
        return jsonify({"error": "Failed to fetch products"}), 500
    return jsonify(products), 200


@app.route('/all_requests')
async def get_all_requests():
    """Возвращает все активные запросы."""
    logger.info("Получен запрос на /all_requests")
    requests_data = await get_all_data("requests", "active")
    if not requests_data and requests_data != []:
        logger.error("Ошибка при получении запросов: результат None или некорректен")
        return jsonify({"error": "Failed to fetch requests"}), 500
    return jsonify(requests_data), 200


@app.route('/archive')
async def get_archive():
    """Возвращает все архивные записи (продукты и запросы)."""
    logger.info("Получен запрос на /archive")
    archived_products = await get_all_data("products", "archived")
    archived_requests = await get_all_data("requests", "archived")
    if (not archived_products and archived_products != []) or (not archived_requests and archived_requests != []):
        logger.error("Ошибка при получении архивных данных")
        return jsonify({"error": "Failed to fetch archive"}), 500
    archived = archived_products + archived_requests
    return jsonify(archived), 200


@app.route('/get_user_phone')
async def get_user_phone():
    """Возвращает номер телефона и регион пользователя по user_id."""
    user_id = request.args.get('user_id')
    logger.info(f"Запрос номера телефона для user_id: {user_id}")
    if not user_id:
        return jsonify({"error": "User ID is required"}), 400

    try:
        user_id_int = int(user_id)
        async with aiosqlite.connect(DB_NAME) as conn:
            async with conn.execute(
                    "SELECT phone_number, region FROM users WHERE id = ?",
                    (user_id_int,)
            ) as cursor:
                result = await cursor.fetchone()
        if result:
            response = {"phone_number": result[0], "region": result[1] or "Не указан"}
            logger.debug(f"Ответ для get_user_phone: {response}")
            return jsonify(response), 200
        logger.warning(f"Пользователь с ID {user_id_int} не найден")
        return jsonify({"error": "User not found"}), 404
    except ValueError:
        logger.error(f"Некорректный user_id: {user_id}")
        return jsonify({"error": "Invalid user_id format"}), 400
    except aiosqlite.Error as e:
        logger.error(f"Ошибка базы данных при получении номера телефона для {user_id}: {e}")
        return jsonify({"error": "Database error"}), 500


@app.route('/photo/<file_id>')
async def get_photo(file_id: str):
    """Возвращает фото по file_id из Telegram."""
    logger.info(f"Запрос фото с file_id: {file_id}")
    get_file_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
    try:
        get_file_response = requests.get(get_file_url, timeout=10)
        get_file_response.raise_for_status()
        file_data = get_file_response.json()
        if not file_data.get("ok"):
            logger.error(f"Неверный ответ от Telegram getFile: {file_data}")
            return jsonify({"error": "Invalid file data", "details": file_data}), 500

        file_path = file_data["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        response = requests.get(file_url, timeout=10)
        response.raise_for_status()
        logger.debug(f"Успешно загружено фото {file_id}")
        return response.content, 200, {'Content-Type': 'image/jpeg'}
    except requests.RequestException as e:
        logger.error(f"Ошибка при загрузке фото {file_id}: {e}")
        return jsonify({"error": "Photo fetch error", "details": str(e)}), 500
    except Exception as e:
        logger.error(f"Неожиданная ошибка при загрузке фото {file_id}: {e}", exc_info=True)
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500


@app.route('/webapp.html')
async def serve_webapp():
    """Сервирует файл webapp.html."""
    logger.info("Запрос на /webapp.html")
    try:
        response = await send_from_directory('.', 'webapp.html')
        logger.debug("Файл webapp.html успешно отправлен")
        return response
    except FileNotFoundError:
        logger.error("Файл webapp.html не найден в текущей директории")
        return jsonify({"error": "Webapp file not found"}), 404
    except Exception as e:
        logger.error(f"Ошибка при отправке webapp.html: {e}", exc_info=True)
        return jsonify({"error": "Server error", "details": str(e)}), 500


if __name__ == '__main__':
    import hypercorn.asyncio
    import hypercorn.config
    import asyncio

    async def main():
        config = hypercorn.config.Config()
        config.bind = ["0.0.0.0:5001"]
        logger.info("Запуск веб-сервера на 0.0.0.0:5001")
        try:
            await hypercorn.asyncio.serve(app, config)
        except Exception as e:
            logger.error(f"Ошибка запуска сервера: {e}", exc_info=True)

    asyncio.run(main())