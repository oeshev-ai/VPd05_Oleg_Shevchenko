"""Инициализация SQLite и операции с пользователями, поездками и расходами."""

from typing import TypedDict

import aiosqlite

from config import get_settings


class Trip(TypedDict):
    """Строка таблицы `trips`."""

    id: int
    user_id: int
    name: str
    home_currency: str
    target_currency: str
    exchange_rate: float
    is_manual_rate: bool
    home_balance: float
    target_balance: float
    is_active: bool


class Expense(TypedDict):
    """Строка таблицы `expenses`."""

    id: int
    trip_id: int
    amount_target: float
    amount_home: float
    timestamp: str
    description: str | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    home_currency TEXT NOT NULL DEFAULT 'RUB'
);

CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    home_currency TEXT NOT NULL,
    target_currency TEXT NOT NULL,
    exchange_rate REAL NOT NULL,
    is_manual_rate INTEGER NOT NULL DEFAULT 0,
    home_balance REAL NOT NULL DEFAULT 0,
    target_balance REAL NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id INTEGER NOT NULL,
    amount_target REAL NOT NULL,
    amount_home REAL NOT NULL,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT,
    FOREIGN KEY (trip_id) REFERENCES trips (id)
);

CREATE INDEX IF NOT EXISTS idx_trips_user_active ON trips (user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_expenses_trip_time ON expenses (trip_id, timestamp);

-- Не больше одной активной поездки на пользователя.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_trip
    ON trips (user_id) WHERE is_active = 1;
"""


def _db_path(db_path: str | None) -> str:
    """Путь к файлу базы: явный аргумент или значение из настроек."""
    return db_path if db_path is not None else get_settings().db_path


def _connect(db_path: str | None) -> aiosqlite.Connection:
    """Открывает соединение в режиме autocommit.

    Несколько связанных записей оборачиваются в явные BEGIN/COMMIT.
    """
    return aiosqlite.connect(_db_path(db_path), isolation_level=None)


def _trip_from_row(row: aiosqlite.Row) -> Trip:
    """Преобразует строку SQLite в словарь поездки."""
    return Trip(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        home_currency=row["home_currency"],
        target_currency=row["target_currency"],
        exchange_rate=float(row["exchange_rate"]),
        is_manual_rate=bool(row["is_manual_rate"]),
        home_balance=float(row["home_balance"]),
        target_balance=float(row["target_balance"]),
        is_active=bool(row["is_active"]),
    )


def _expense_from_row(row: aiosqlite.Row) -> Expense:
    """Преобразует строку SQLite в словарь расхода."""
    return Expense(
        id=row["id"],
        trip_id=row["trip_id"],
        amount_target=float(row["amount_target"]),
        amount_home=float(row["amount_home"]),
        timestamp=str(row["timestamp"]),
        description=row["description"],
    )


async def init_db(db_path: str | None = None) -> None:
    """Создаёт таблицы и индексы, если их ещё нет."""
    async with _connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(_SCHEMA)


async def create_user(
    user_id: int,
    home_currency: str = "RUB",
    db_path: str | None = None,
) -> None:
    """Создаёт пользователя или обновляет его домашнюю валюту."""
    currency = home_currency.strip().upper()
    if not currency:
        raise ValueError("Домашняя валюта не может быть пустой.")

    async with _connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO users (user_id, home_currency)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET home_currency = excluded.home_currency
            """,
            (user_id, currency),
        )


async def create_trip(
    user_id: int,
    name: str,
    home_currency: str,
    target_currency: str,
    exchange_rate: float,
    is_manual_rate: bool,
    home_balance: float,
    target_balance: float,
    db_path: str | None = None,
) -> int:
    """Создаёт поездку и делает её единственной активной у пользователя.

    Курс хранится как «1 единица валюты назначения = X домашней валюты».
    Возвращает id новой поездки.
    """
    title = name.strip()
    home = home_currency.strip().upper()
    target = target_currency.strip().upper()
    if not title:
        raise ValueError("Название путешествия не может быть пустым.")
    if not home or not target:
        raise ValueError("Коды валют не могут быть пустыми.")
    if exchange_rate <= 0:
        raise ValueError("Курс должен быть положительным числом.")
    if home_balance < 0 or target_balance < 0:
        raise ValueError("Стартовый баланс не может быть отрицательным.")

    async with _connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("BEGIN")
        await db.execute(
            """
            INSERT INTO users (user_id, home_currency)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET home_currency = excluded.home_currency
            """,
            (user_id, home),
        )
        # Снимаем активность с предыдущей поездки до вставки новой.
        await db.execute(
            "UPDATE trips SET is_active = 0 WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        cursor = await db.execute(
            """
            INSERT INTO trips (
                user_id, name, home_currency, target_currency,
                exchange_rate, is_manual_rate, home_balance, target_balance, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                user_id,
                title,
                home,
                target,
                float(exchange_rate),
                int(is_manual_rate),
                float(home_balance),
                float(target_balance),
            ),
        )
        await db.execute("COMMIT")
        return int(cursor.lastrowid)


async def get_active_trip(
    user_id: int,
    db_path: str | None = None,
) -> Trip | None:
    """Возвращает активную поездку пользователя или None."""
    async with _connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, user_id, name, home_currency, target_currency,
                   exchange_rate, is_manual_rate, home_balance, target_balance, is_active
            FROM trips
            WHERE user_id = ? AND is_active = 1
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _trip_from_row(row)


async def list_trips(
    user_id: int,
    db_path: str | None = None,
) -> list[Trip]:
    """Список поездок пользователя: активная первая, затем более новые."""
    async with _connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, user_id, name, home_currency, target_currency,
                   exchange_rate, is_manual_rate, home_balance, target_balance, is_active
            FROM trips
            WHERE user_id = ?
            ORDER BY is_active DESC, id DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [_trip_from_row(row) for row in rows]


async def switch_trip(
    user_id: int,
    trip_id: int,
    db_path: str | None = None,
) -> Trip:
    """Делает указанную поездку активной и возвращает её.

    Поездка должна принадлежать этому пользователю.
    """
    async with _connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN")
        cursor = await db.execute(
            """
            SELECT id, user_id, name, home_currency, target_currency,
                   exchange_rate, is_manual_rate, home_balance, target_balance, is_active
            FROM trips
            WHERE id = ? AND user_id = ?
            """,
            (trip_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.rollback()
            raise ValueError("Путешествие не найдено.")

        await db.execute(
            "UPDATE trips SET is_active = 0 WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        await db.execute(
            "UPDATE trips SET is_active = 1 WHERE id = ? AND user_id = ?",
            (trip_id, user_id),
        )
        await db.execute("COMMIT")

        cursor = await db.execute(
            """
            SELECT id, user_id, name, home_currency, target_currency,
                   exchange_rate, is_manual_rate, home_balance, target_balance, is_active
            FROM trips
            WHERE id = ? AND user_id = ?
            """,
            (trip_id, user_id),
        )
        updated = await cursor.fetchone()
        if updated is None:
            raise ValueError("Путешествие не найдено.")
        return _trip_from_row(updated)


async def update_balance(
    trip_id: int,
    home_balance: float,
    target_balance: float,
    db_path: str | None = None,
) -> None:
    """Записывает оба остатка поездки как есть, без пересчёта по курсу."""
    async with _connect(db_path) as db:
        cursor = await db.execute(
            """
            UPDATE trips
            SET home_balance = ?, target_balance = ?
            WHERE id = ?
            """,
            (float(home_balance), float(target_balance), trip_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Путешествие не найдено.")


async def update_exchange_rate(
    trip_id: int,
    user_id: int,
    exchange_rate: float,
    is_manual_rate: bool,
    db_path: str | None = None,
) -> Trip:
    """Обновляет курс активной или любой своей поездки. Балансы не пересчитывает."""
    if exchange_rate <= 0:
        raise ValueError("Курс должен быть положительным числом.")

    async with _connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            UPDATE trips
            SET exchange_rate = ?, is_manual_rate = ?
            WHERE id = ? AND user_id = ?
            """,
            (float(exchange_rate), int(is_manual_rate), trip_id, user_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Путешествие не найдено.")
        cursor = await db.execute(
            """
            SELECT id, user_id, name, home_currency, target_currency,
                   exchange_rate, is_manual_rate, home_balance, target_balance, is_active
            FROM trips
            WHERE id = ? AND user_id = ?
            """,
            (trip_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("Путешествие не найдено.")
        return _trip_from_row(row)


async def add_expense(
    trip_id: int,
    amount_target: float,
    amount_home: float,
    description: str | None = None,
    db_path: str | None = None,
) -> Expense:
    """Списывает расход с обоих балансов и сохраняет запись в `expenses`.

    Списание и вставка выполняются в одной транзакции.
    Отрицательный остаток здесь допустим: предупреждение показывает хендлер.
    """
    if amount_target <= 0 or amount_home <= 0:
        raise ValueError("Сумма расхода должна быть положительным числом.")

    note = description.strip() if description else None
    if note == "":
        note = None

    async with _connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN")
        cursor = await db.execute(
            """
            SELECT home_balance, target_balance
            FROM trips
            WHERE id = ?
            """,
            (trip_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.rollback()
            raise ValueError("Путешествие не найдено.")

        new_home = float(row["home_balance"]) - float(amount_home)
        new_target = float(row["target_balance"]) - float(amount_target)
        await db.execute(
            """
            UPDATE trips
            SET home_balance = ?, target_balance = ?
            WHERE id = ?
            """,
            (new_home, new_target, trip_id),
        )
        cursor = await db.execute(
            """
            INSERT INTO expenses (trip_id, amount_target, amount_home, description)
            VALUES (?, ?, ?, ?)
            """,
            (trip_id, float(amount_target), float(amount_home), note),
        )
        expense_id = int(cursor.lastrowid)
        await db.execute("COMMIT")
        cursor = await db.execute(
            """
            SELECT id, trip_id, amount_target, amount_home, timestamp, description
            FROM expenses
            WHERE id = ?
            """,
            (expense_id,),
        )
        saved = await cursor.fetchone()
        if saved is None:
            raise ValueError("Не удалось сохранить расход.")
        return _expense_from_row(saved)


async def get_expenses(
    trip_id: int,
    limit: int = 10,
    db_path: str | None = None,
) -> list[Expense]:
    """Последние расходы поездки, сначала самые новые."""
    if limit < 1:
        raise ValueError("Количество записей истории должно быть больше нуля.")

    async with _connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, trip_id, amount_target, amount_home, timestamp, description
            FROM expenses
            WHERE trip_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (trip_id, limit),
        )
        rows = await cursor.fetchall()
        return [_expense_from_row(row) for row in rows]
