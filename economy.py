
"""Экономика Чапи: баланс, ежедневная награда и переводы."""

import time
import logging

from aiogram import Router, F
from aiogram.types import Message

import database as db
import config

router = Router(name="economy")
logger = logging.getLogger(__name__)

DAILY_REWARD = 100
MIN_TRANSFER = 1
MAX_TRANSFER = 1_000_000_000


def _display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


async def _ensure_member(chat_id: int, user_id: int) -> None:
    conn = await db.get_db()
    await conn.execute(
        """
        INSERT OR IGNORE INTO members (chat_id, user_id, balance)
        VALUES (?, ?, ?)
        """,
        (chat_id, user_id, getattr(config, "START_BALANCE", 0)),
    )
    await conn.commit()


async def _get_balance(chat_id: int, user_id: int) -> int:
    await _ensure_member(chat_id, user_id)
    conn = await db.get_db()
    cur = await conn.execute(
        "SELECT balance FROM members WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = await cur.fetchone()
    return int(row["balance"]) if row else 0


@router.message(F.text.regexp(r"(?i)^(?:баланс|бал|кошел[её]к)(?:\s|$)"))
async def balance_command(message: Message):
    if not message.from_user:
        return

    chat_id = message.chat.id
    user = message.from_user
    target = user

    parts = (message.text or "").split()
    if len(parts) >= 2:
        query = parts[1].strip()
        if query.startswith("@"):
            target_id = await db.find_user_by_username(query[1:])
            if target_id is None:
                await message.answer(
                    "Не нашёл этого пользователя. "
                    "Пусть он напишет любое сообщение в чат."
                )
                return
            target_row = await db.get_user(target_id)
            if target_row is None:
                await message.answer("Пользователь ещё не зарегистрирован.")
                return
            target_id = int(target_row["user_id"])
        elif query.isdigit():
            target_id = int(query)
        else:
            await message.answer(
                "Использование: баланс или баланс ID/@username"
            )
            return

        await _ensure_member(chat_id, target_id)
        target = None
        target_name = query
        amount = await _get_balance(chat_id, target_id)
    else:
        target_name = _display_name(user)
        amount = await _get_balance(chat_id, user.id)

    await message.answer(
        f"💰 Баланс {target_name}: <b>{amount:,}</b> монет".replace(",", " ")
    )


@router.message(F.text.regexp(r"(?i)^ежедневн(?:ая|юю|о)?(?:\s|$)|^дейли(?:\s|$)|^daily(?:\s|$)"))
async def daily_command(message: Message):
    if not message.from_user:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    now = time.time()

    await _ensure_member(chat_id, user_id)

    try:
        async with db.transaction() as conn:
            cur = await conn.execute(
                "SELECT last_daily FROM daily WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            last_daily = float(row["last_daily"]) if row else 0.0
            remaining = 86400 - (now - last_daily)

            if remaining > 0:
                hours = int(remaining // 3600)
                minutes = int((remaining % 3600) // 60)
                await message.answer(
                    f"⏳ Ежедневную награду можно получить через "
                    f"{hours} ч. {minutes} мин."
                )
                return

            await conn.execute(
                """
                INSERT INTO daily (chat_id, user_id, last_daily)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET last_daily=excluded.last_daily
                """,
                (chat_id, user_id, now),
            )
            await conn.execute(
                "UPDATE members SET balance=balance+? "
                "WHERE chat_id=? AND user_id=?",
                (DAILY_REWARD, chat_id, user_id),
            )

        try:
            await db.bump_task_progress(chat_id, user_id, "daily")
        except Exception:
            logger.debug("Не удалось обновить прогресс заданий", exc_info=True)

        balance = await _get_balance(chat_id, user_id)
        await message.answer(
            f"🎁 Ежедневная награда получена!\n"
            f"Начислено: <b>{DAILY_REWARD}</b> монет.\n"
            f"Твой баланс: <b>{balance}</b> монет."
        )

    except Exception:
        logger.exception("Ошибка ежедневной награды")
        await message.answer(
            "Не удалось выдать награду. Попробуй ещё раз позже."
        )


@router.message(F.text.regexp(r"(?i)^(?:перевод|перевести|передать)(?:\s|$)"))
async def transfer_command(message: Message):
    if not message.from_user:
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(
            "💸 Формат перевода:\n"
            "перевод ID сумма\n"
            "или перевод @username сумма\n\n"
            "Пример: перевод 123456789 50"
        )
        return

    target_text = parts[1].strip()
    try:
        amount = int(parts[2])
    except ValueError:
        await message.answer("Сумма должна быть целым числом.")
        return

    if not MIN_TRANSFER <= amount <= MAX_TRANSFER:
        await message.answer(
            f"Сумма должна быть от {MIN_TRANSFER} до {MAX_TRANSFER} монет."
        )
        return

    sender_id = message.from_user.id
    chat_id = message.chat.id

    if target_text.startswith("@"):
        target_id = await db.find_user_by_username(target_text[1:])
        if target_id is None:
            await message.answer(
                "Не нашёл пользователя. Пусть он напишет сообщение в чат."
            )
            return
    elif target_text.isdigit():
        target_id = int(target_text)
    else:
        await message.answer("Укажи Telegram ID или @username получателя.")
        return

    if target_id == sender_id:
        await message.answer("Нельзя переводить монеты самому себе.")
        return

    if target_id <= 0:
        await message.answer("Некорректный ID получателя.")
        return

    try:
        async with db.transaction() as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO members (chat_id, user_id, balance)
                VALUES (?, ?, ?)
                """,
                (chat_id, sender_id, getattr(config, "START_BALANCE", 0)),
            )
            await conn.execute(
                """
                INSERT OR IGNORE INTO members (chat_id, user_id, balance)
                VALUES (?, ?, ?)
                """,
                (chat_id, target_id, getattr(config, "START_BALANCE", 0)),
            )

            cur = await conn.execute(
                "SELECT balance FROM members WHERE chat_id=? AND user_id=?",
                (chat_id, sender_id),
            )
            row = await cur.fetchone()
            sender_balance = int(row["balance"]) if row else 0

            if sender_balance < amount:
                await message.answer(
                    f"Недостаточно монет. Твой баланс: {sender_balance}."
                )
                return

            await conn.execute(
                "UPDATE members SET balance=balance-? "
                "WHERE chat_id=? AND user_id=?",
                (amount, chat_id, sender_id),
            )
            await conn.execute(
                "UPDATE members SET balance=balance+? "
                "WHERE chat_id=? AND user_id=?",
                (amount, chat_id, target_id),
            )
            await conn.execute(
                """
                INSERT INTO transactions
                    (chat_id, from_id, to_id, amount, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, sender_id, target_id, amount, time.time()),
            )

        try:
            await db.bump_task_progress(chat_id, sender_id, "transfer")
        except Exception:
            logger.debug("Не удалось обновить прогресс заданий", exc_info=True)

        await message.answer(
            f"✅ Перевод выполнен!\n"
            f"Получатель: {target_text}\n"
            f"Сумма: <b>{amount}</b> монет."
        )

    except Exception:
        logger.exception("Ошибка перевода")
        await message.answer(
            "Не удалось выполнить перевод. Попробуй позже."
        )


@router.message(F.text.regexp(r"(?i)^(?:экономика|экономика помощь|помощь экономика)$"))
async def economy_help(message: Message):
    await message.answer(
        "<b>💰 Экономика Чапи</b>\n\n"
        "• <code>баланс</code> — свой баланс\n"
        "• <code>баланс @username</code> — баланс участника\n"
        "• <code>ежедневно</code> — получить награду\n"
        "• <code>перевод ID сумма</code> — перевести монеты\n\n"
        f"Ежедневная награда: {DAILY_REWARD} монет."
    )
