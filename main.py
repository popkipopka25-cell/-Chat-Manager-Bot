
"""Точка входа: запуск Telegram-бота и HTTP-сервера для Render."""

import asyncio
import logging
import os
import sys

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN, BOT_NAME, check_config
import database as db
from handlers import router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("bot")


async def health_check(request: web.Request) -> web.Response:
    """Проверка доступности HTTP-сервера."""
    return web.json_response({
        "status": "ok",
        "service": BOT_NAME,
    })


async def home(request: web.Request) -> web.Response:
    """Главная страница сервиса."""
    return web.Response(
        text=f"{BOT_NAME}: сервис запущен.",
        content_type="text/plain",
    )


async def start_http_server() -> web.AppRunner:
    """Запускает HTTP-сервер на порту Render."""
    app = web.Application()
    app.router.add_get("/", home)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    try:
        port = int(os.environ.get("PORT", "10000"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT должен быть в диапазоне 1–65535")

        site = web.TCPSite(runner, host="0.0.0.0", port=port)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise

    logger.info("HTTP-сервер запущен на порту %s", port)
    return runner


async def maintenance_loop():
    """Периодически обновляет состояние истёкших наказаний."""
    while True:
        try:
            expired = await db.expire_punishments()
            if expired:
                logger.info(
                    "Помечено истёкших наказаний: %s", expired
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка фоновой очистки наказаний")

        await asyncio.sleep(60)


async def main():
    problems = check_config()
    for problem in problems:
        logger.error(problem)

    if problems or not BOT_TOKEN:
        logger.error(
            "Запуск остановлен. Проверьте настройки "
            "TELEGRAM_BOT_TOKEN и OWNER_TELEGRAM_ID."
        )
        raise SystemExit(1)

    await db.init_db()
    logger.info("База данных готова.")

    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    http_runner = None
    maintenance_task = None

    try:
        # Удаляем webhook, поскольку бот использует polling.
        await bot.delete_webhook(drop_pending_updates=True)

        http_runner = await start_http_server()
        maintenance_task = asyncio.create_task(
            maintenance_loop(),
            name="punishment-maintenance",
        )

        logger.info("%s запущен. Ожидаю сообщения...", BOT_NAME)
        await dp.start_polling(bot)

    finally:
        if maintenance_task is not None:
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass

        if http_runner is not None:
            await http_runner.cleanup()

        try:
            await db.close_db()
        finally:
            await bot.session.close()

        logger.info("Ресурсы бота закрыты.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
