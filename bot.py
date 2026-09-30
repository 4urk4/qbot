"""Entry point pbot.

Инициализирует:
- Config из config.toml
- БД (aiosqlite + schema)
- Bot + Dispatcher
- middleware для проброса conn/cfg/bot в хендлеры
- регистрация роутеров

Запуск: `python bot.py`.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# чтобы запускалось как `python bot.py` из корня репо
sys.path.insert(0, str(Path(__file__).parent / "src"))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import TelegramObject

from pbot.config import Config, load_config
from pbot.db import init_db
from pbot import storage
from pbot.handlers import register_all
from pbot.texts import HELP

# Глобал — текущая активная конфигурация. Изменяется через owner-DM
# callback (см. handlers/owner.py). Хендлеры читают CURRENT_CONFIG каждый раз
# → переключение модели применяется немедленно.
CURRENT_CONFIG: Config | None = None  # type: ignore[assignment]

logger = logging.getLogger("pbot")


# ---------- middleware для проброса зависимостей в хендлеры ----------

class DependencyMiddleware:
    """Прокидывает conn (aiosqlite.Connection), cfg (Config), bot в хендлеры как kwargs.

    cfg резолвится динамически из CURRENT_CONFIG глобала — чтобы owner-DM callback
    мог переключить активную модель и хендлеры сразу это видели без перезапуска.
    """

    def __init__(self, conn, bot: Bot):
        self.conn = conn
        self.bot = bot

    async def __call__(self, handler, event: TelegramObject, data: dict):
        data["conn"] = self.conn
        data["cfg"] = CURRENT_CONFIG
        data["bot"] = self.bot
        return await handler(event, data)


# ---------- /start (дубль /pidor_help) ----------

async def cmd_start(message, **_) -> None:
    """Чисто Telegram /start — отдельный хендлер, не в роутере."""
    await message.reply(HELP)


def build_bot(conn) -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=CURRENT_CONFIG.bot_token,
        default=DefaultBotProperties(parse_mode=None),  # plain text, без HTML
    )
    dp = Dispatcher()

    # middleware на ВСЕ события
    dp.message.middleware(DependencyMiddleware(conn, bot))
    dp.my_chat_member.middleware(DependencyMiddleware(conn, bot))
    dp.callback_query.middleware(DependencyMiddleware(conn, bot))

    # /start — отдельно, в main диспетчере (а не в роутерах) — обработает раньше любых команд
    @dp.message(Command("start"))
    async def _start(message, **_):
        await cmd_start(message)

    register_all(dp)

    return bot, dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    global CURRENT_CONFIG
    cfg = load_config()
    CURRENT_CONFIG = cfg
    logger.info("config loaded (db=%s), models: %d", cfg.db_path, len(cfg.llm.all_configs()))

    db = await init_db(cfg)
    logger.info("db initialized")

    try:
        bot, dp = build_bot(db)
        logger.info("starting long-polling")
        await dp.start_polling(bot)
    finally:
        await db.close()
        logger.info("db closed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass