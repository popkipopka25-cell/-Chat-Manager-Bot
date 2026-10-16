
"""Миграции схемы SQLite: создание недостающих таблиц."""

import aiosqlite


async def run_migrations(conn: aiosqlite.Connection) -> None:
    """Создаёт таблицы, необходимые существующим функциям базы данных."""

    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS premium_memberships (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            expires_at REAL NOT NULL,
            granted_by INTEGER,
            created_at REAL NOT NULL,
            status_text TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (chat_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS task_progress (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            task_date TEXT NOT NULL,
            task_key TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            claimed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id, task_date, task_key)
        );

        CREATE INDEX IF NOT EXISTS idx_punishments_active
            ON punishments (chat_id, user_id, active);

        CREATE INDEX IF NOT EXISTS idx_premium_expiry
            ON premium_memberships (chat_id, user_id, expires_at);
        """
    )

    await conn.commit()
