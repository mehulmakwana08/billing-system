import argparse
import os
import sqlite3
from typing import Dict, List
from dotenv import load_dotenv

from sqlalchemy import create_engine, text

from database import _normalize_database_url
from models import Base


load_dotenv()


TABLE_COPY_ORDER = [
    "company_settings",
    "users",
    "customers",
    "products",
    "invoices",
    "invoice_items",
    "payments",
    "customer_ledger",
    "sync_queue",
    "invoice_number_blocks",
]


def get_columns_sqlite(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def get_columns_pg(engine, table: str) -> List[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table
                ORDER BY ordinal_position
                """
            ),
            {"table": table},
        ).fetchall()
    return [r[0] for r in rows]


def ensure_company_settings_from_legacy(sqlite_conn: sqlite3.Connection) -> None:
    tables = {
        r[0] for r in sqlite_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "company_settings" in tables:
        return

    sqlite_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL DEFAULT 1,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(company_id, key)
        )
        """
    )

    if "company" in tables:
        rows = sqlite_conn.execute("SELECT key, value FROM company").fetchall()
        for key, value in rows:
            sqlite_conn.execute(
                """
                INSERT OR IGNORE INTO company_settings (company_id, key, value, created_at, updated_at)
                VALUES (1, ?, ?, datetime('now'), datetime('now'))
                """,
                (key, value),
            )
    sqlite_conn.commit()


def copy_table(sqlite_conn: sqlite3.Connection, pg_engine, table: str) -> int:
    sqlite_columns = get_columns_sqlite(sqlite_conn, table)
    if not sqlite_columns:
        return 0

    pg_columns = set(get_columns_pg(pg_engine, table))
    common_columns = [c for c in sqlite_columns if c in pg_columns]
    if not common_columns:
        return 0

    rows = sqlite_conn.execute(f"SELECT {', '.join(common_columns)} FROM {table}").fetchall()
    if not rows:
        return 0

    columns_clause = ", ".join(common_columns)
    values_clause = ", ".join(f":{c}" for c in common_columns)
    insert_sql = text(f"INSERT INTO {table} ({columns_clause}) VALUES ({values_clause})")

    with pg_engine.begin() as conn:
        for row in rows:
            payload: Dict[str, object] = {col: row[idx] for idx, col in enumerate(common_columns)}
            conn.execute(insert_sql, payload)

    return len(rows)


def reset_sequence(pg_engine, table: str) -> None:
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    true
                )
                """
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate billing SQLite data to PostgreSQL")
    parser.add_argument("--sqlite-path", default=os.path.join(os.path.dirname(__file__), "billing.db"))
    parser.add_argument("--postgres-url", default=os.getenv("DATABASE_URL", ""))
    args = parser.parse_args()

    if not args.postgres_url:
        raise SystemExit("DATABASE_URL/--postgres-url is required")

    postgres_url = _normalize_database_url(args.postgres_url)
    sqlite_path = os.path.abspath(args.sqlite_path)

    if not os.path.exists(sqlite_path):
        raise SystemExit(f"SQLite database not found: {sqlite_path}")

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    ensure_company_settings_from_legacy(sqlite_conn)

    pg_engine = create_engine(postgres_url, future=True)
    Base.metadata.create_all(pg_engine)

    copied = {}
    for table in TABLE_COPY_ORDER:
        copied[table] = copy_table(sqlite_conn, pg_engine, table)

    for table in TABLE_COPY_ORDER:
        if copied.get(table, 0) > 0:
            try:
                reset_sequence(pg_engine, table)
            except Exception:
                # Tables without serial sequences can be ignored.
                pass

    sqlite_conn.close()

    print("Migration complete.")
    for table in TABLE_COPY_ORDER:
        print(f"{table}: {copied.get(table, 0)} rows")


if __name__ == "__main__":
    main()
