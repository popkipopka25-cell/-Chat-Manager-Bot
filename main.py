"""Точка входа: запуск Telegram-бота."""
import asyncio
import logging
import sys

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


async def _maintenance_loop():
    """Периодически синхронизирует состояние временных наказаний в базе."""
    while True:
        try:
            expired = await db.expire_punishments()
            if expired:
                logger.info("Помечено истёкших наказаний: %s", expired)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка фоновой очистки наказаний")
        await asyncio.sleep(60)


async def main():
    problems = check_config()
    for problem in problems:
        logger.error(problem)
    # Не запускаем бота с неполной/небезопасной конфигурацией.
    if problems:
        logger.error("Запуск остановлен: исправьте конфигурацию в .env или Secrets хостинга.")
        sys.exit(1)
    if not BOT_TOKEN:  # дополнительная защита на случай изменения check_config()
        logger.error("Запуск невозможен без TELEGRAM_BOT_TOKEN. См. README.md.")
        sys.exit(1)

    await db.init_db()
    logger.info("База данных готова.")

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    maintenance_task = asyncio.create_task(_maintenance_loop(), name="punishment-maintenance")
    logger.info("%s запущен. Ожидаю сообщения...", BOT_NAME)
    try:
        await dp.start_polling(bot)
    finally:
        maintenance_task.cancel()
        try:
            await maintenance_task
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await db.close_db()
            finally:
                await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
