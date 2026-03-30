import argparse
import os
from typing import Iterable, List

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from database import _normalize_database_url
from models import Base


load_dotenv()


DDL_STATEMENTS: List[str] = [
    # Ensure all ORM tables exist before applying alignment steps.
    # Existing tables are left untouched by create_all.
    # Column alignment: users
    "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
    "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS company_id INTEGER",
    "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE",
    # Column alignment: company settings
    "ALTER TABLE IF EXISTS company_settings ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS company_settings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE",
    # Column alignment: business entities
    "ALTER TABLE IF EXISTS customers ADD COLUMN IF NOT EXISTS company_id INTEGER",
    "ALTER TABLE IF EXISTS customers ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS customers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS products ADD COLUMN IF NOT EXISTS company_id INTEGER",
    "ALTER TABLE IF EXISTS products ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS products ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS company_id INTEGER",
    "ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS pdf_url TEXT",
    "ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS sync_status VARCHAR(32)",
    "ALTER TABLE IF EXISTS invoice_items ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS invoice_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS payments ADD COLUMN IF NOT EXISTS company_id INTEGER",
    "ALTER TABLE IF EXISTS payments ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS payments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS customer_ledger ADD COLUMN IF NOT EXISTS company_id INTEGER",
    "ALTER TABLE IF EXISTS customer_ledger ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS customer_ledger ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE",
    # Backfill and normalize users
    "UPDATE users SET company_id = COALESCE(company_id, 1)",
    "UPDATE users SET created_at = COALESCE(created_at, NOW())",
    "UPDATE users SET updated_at = COALESCE(updated_at, created_at, NOW())",
    """
    UPDATE users
    SET email = CASE
        WHEN email IS NOT NULL AND btrim(email) <> '' THEN lower(btrim(email))
        WHEN username IS NOT NULL AND btrim(username) <> '' AND position('@' in username) > 1 THEN lower(btrim(username))
        WHEN username IS NOT NULL AND btrim(username) <> '' THEN lower(regexp_replace(btrim(username), '\\s+', '', 'g')) || '+' || id::text || '@legacy.local'
        ELSE 'user+' || id::text || '@legacy.local'
    END
    WHERE email IS NULL OR btrim(email) = ''
    """,
    """
    WITH dup AS (
        SELECT lower(email) AS email_norm
        FROM users
        GROUP BY lower(email)
        HAVING COUNT(*) > 1
    ), ranked AS (
        SELECT u.id,
               row_number() OVER (PARTITION BY lower(u.email) ORDER BY u.id) AS rn
        FROM users u
        JOIN dup d ON lower(u.email) = d.email_norm
    )
    UPDATE users u
    SET email = CASE
        WHEN position('@' in lower(u.email)) > 1 THEN
            split_part(lower(u.email), '@', 1) || '+' || u.id::text || '@' || split_part(lower(u.email), '@', 2)
        ELSE
            'user+' || u.id::text || '@legacy.local'
    END
    FROM ranked r
    WHERE u.id = r.id AND r.rn > 1
    """,
    "ALTER TABLE users ALTER COLUMN company_id SET DEFAULT 1",
    "ALTER TABLE users ALTER COLUMN company_id SET NOT NULL",
    "ALTER TABLE users ALTER COLUMN email SET NOT NULL",
    "ALTER TABLE users ALTER COLUMN created_at SET DEFAULT NOW()",
    "ALTER TABLE users ALTER COLUMN created_at SET NOT NULL",
    "ALTER TABLE users ALTER COLUMN updated_at SET DEFAULT NOW()",
    "ALTER TABLE users ALTER COLUMN updated_at SET NOT NULL",
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'username'
        ) THEN
            EXECUTE 'ALTER TABLE users ALTER COLUMN username DROP NOT NULL';
        END IF;
    END $$
    """,
    # Backfill and normalize settings
    "UPDATE company_settings SET company_id = COALESCE(company_id, 1)",
    "UPDATE company_settings SET created_at = COALESCE(created_at, NOW())",
    "UPDATE company_settings SET updated_at = COALESCE(updated_at, created_at, NOW())",
    "ALTER TABLE company_settings ALTER COLUMN company_id SET DEFAULT 1",
    "ALTER TABLE company_settings ALTER COLUMN company_id SET NOT NULL",
    "ALTER TABLE company_settings ALTER COLUMN created_at SET DEFAULT NOW()",
    "ALTER TABLE company_settings ALTER COLUMN created_at SET NOT NULL",
    "ALTER TABLE company_settings ALTER COLUMN updated_at SET DEFAULT NOW()",
    "ALTER TABLE company_settings ALTER COLUMN updated_at SET NOT NULL",
    # Backfill business tables with company_id=1 legacy default
    "UPDATE customers SET company_id = COALESCE(company_id, 1)",
    "UPDATE customers SET created_at = COALESCE(created_at, NOW())",
    "UPDATE customers SET updated_at = COALESCE(updated_at, created_at, NOW())",
    "ALTER TABLE customers ALTER COLUMN company_id SET DEFAULT 1",
    "ALTER TABLE customers ALTER COLUMN company_id SET NOT NULL",
    "ALTER TABLE customers ALTER COLUMN created_at SET DEFAULT NOW()",
    "ALTER TABLE customers ALTER COLUMN created_at SET NOT NULL",
    "ALTER TABLE customers ALTER COLUMN updated_at SET DEFAULT NOW()",
    "ALTER TABLE customers ALTER COLUMN updated_at SET NOT NULL",
    "UPDATE products SET company_id = COALESCE(company_id, 1)",
    "UPDATE products SET created_at = COALESCE(created_at, NOW())",
    "UPDATE products SET updated_at = COALESCE(updated_at, created_at, NOW())",
    "ALTER TABLE products ALTER COLUMN company_id SET DEFAULT 1",
    "ALTER TABLE products ALTER COLUMN company_id SET NOT NULL",
    "ALTER TABLE products ALTER COLUMN created_at SET DEFAULT NOW()",
    "ALTER TABLE products ALTER COLUMN created_at SET NOT NULL",
    "ALTER TABLE products ALTER COLUMN updated_at SET DEFAULT NOW()",
    "ALTER TABLE products ALTER COLUMN updated_at SET NOT NULL",
    "UPDATE invoices SET company_id = COALESCE(company_id, 1)",
    "UPDATE invoices SET created_at = COALESCE(created_at, NOW())",
    "UPDATE invoices SET updated_at = COALESCE(updated_at, created_at, NOW())",
    "UPDATE invoices SET sync_status = COALESCE(NULLIF(sync_status, ''), 'pending')",
    "ALTER TABLE invoices ALTER COLUMN company_id SET DEFAULT 1",
    "ALTER TABLE invoices ALTER COLUMN company_id SET NOT NULL",
    "ALTER TABLE invoices ALTER COLUMN created_at SET DEFAULT NOW()",
    "ALTER TABLE invoices ALTER COLUMN created_at SET NOT NULL",
    "ALTER TABLE invoices ALTER COLUMN updated_at SET DEFAULT NOW()",
    "ALTER TABLE invoices ALTER COLUMN updated_at SET NOT NULL",
    "UPDATE invoice_items SET created_at = COALESCE(created_at, NOW())",
    "UPDATE invoice_items SET updated_at = COALESCE(updated_at, created_at, NOW())",
    "ALTER TABLE invoice_items ALTER COLUMN created_at SET DEFAULT NOW()",
    "ALTER TABLE invoice_items ALTER COLUMN created_at SET NOT NULL",
    "ALTER TABLE invoice_items ALTER COLUMN updated_at SET DEFAULT NOW()",
    "ALTER TABLE invoice_items ALTER COLUMN updated_at SET NOT NULL",
    "UPDATE payments SET company_id = COALESCE(company_id, 1)",
    "UPDATE payments SET created_at = COALESCE(created_at, NOW())",
    "UPDATE payments SET updated_at = COALESCE(updated_at, created_at, NOW())",
    "ALTER TABLE payments ALTER COLUMN company_id SET DEFAULT 1",
    "ALTER TABLE payments ALTER COLUMN company_id SET NOT NULL",
    "ALTER TABLE payments ALTER COLUMN created_at SET DEFAULT NOW()",
    "ALTER TABLE payments ALTER COLUMN created_at SET NOT NULL",
    "ALTER TABLE payments ALTER COLUMN updated_at SET DEFAULT NOW()",
    "ALTER TABLE payments ALTER COLUMN updated_at SET NOT NULL",
    "UPDATE customer_ledger SET company_id = COALESCE(company_id, 1)",
    "UPDATE customer_ledger SET created_at = COALESCE(created_at, NOW())",
    "UPDATE customer_ledger SET updated_at = COALESCE(updated_at, created_at, NOW())",
    "ALTER TABLE customer_ledger ALTER COLUMN company_id SET DEFAULT 1",
    "ALTER TABLE customer_ledger ALTER COLUMN company_id SET NOT NULL",
    "ALTER TABLE customer_ledger ALTER COLUMN created_at SET DEFAULT NOW()",
    "ALTER TABLE customer_ledger ALTER COLUMN created_at SET NOT NULL",
    "ALTER TABLE customer_ledger ALTER COLUMN updated_at SET DEFAULT NOW()",
    "ALTER TABLE customer_ledger ALTER COLUMN updated_at SET NOT NULL",
    # Align queue and number blocks timestamp defaults
    "UPDATE sync_queue SET created_at = COALESCE(created_at, NOW())",
    "UPDATE sync_queue SET updated_at = COALESCE(updated_at, created_at, NOW())",
    "ALTER TABLE sync_queue ALTER COLUMN status SET DEFAULT 'pending'",
    "ALTER TABLE sync_queue ALTER COLUMN created_at SET DEFAULT NOW()",
    "ALTER TABLE sync_queue ALTER COLUMN updated_at SET DEFAULT NOW()",
    "UPDATE invoice_number_blocks SET created_at = COALESCE(created_at, NOW())",
    "UPDATE invoice_number_blocks SET updated_at = COALESCE(updated_at, created_at, NOW())",
    "ALTER TABLE invoice_number_blocks ALTER COLUMN status SET DEFAULT 'active'",
    "ALTER TABLE invoice_number_blocks ALTER COLUMN created_at SET DEFAULT NOW()",
    "ALTER TABLE invoice_number_blocks ALTER COLUMN updated_at SET DEFAULT NOW()",
]


INDEX_STATEMENTS: List[str] = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users (email)",
    "CREATE INDEX IF NOT EXISTS idx_users_company_id ON users (company_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_settings_company_id ON company_settings (company_id)",
    "CREATE INDEX IF NOT EXISTS idx_customers_company_id ON customers (company_id)",
    "CREATE INDEX IF NOT EXISTS idx_products_company_id ON products (company_id)",
    "CREATE INDEX IF NOT EXISTS idx_invoices_company_id ON invoices (company_id)",
    "CREATE INDEX IF NOT EXISTS idx_invoices_customer_id ON invoices (customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_payments_company_id ON payments (company_id)",
    "CREATE INDEX IF NOT EXISTS idx_customer_ledger_company_id ON customer_ledger (company_id)",
    "CREATE INDEX IF NOT EXISTS idx_sync_queue_company_status ON sync_queue (company_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_invoice_blocks_company_year ON invoice_number_blocks (company_id, year)",
]


POST_STEPS: List[str] = [
    # Deduplicate company settings by keeping the newest row per (company_id, key), then enforce uniqueness.
    """
    WITH ranked AS (
        SELECT id,
               row_number() OVER (PARTITION BY company_id, key ORDER BY id DESC) AS rn
        FROM company_settings
    )
    DELETE FROM company_settings c
    USING ranked r
    WHERE c.id = r.id AND r.rn > 1
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_company_settings_company_key ON company_settings (company_id, key)",
    # Add invoice uniqueness only if existing data is clean.
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM (
                SELECT company_id, invoice_no, COUNT(*) c
                FROM invoices
                GROUP BY company_id, invoice_no
                HAVING COUNT(*) > 1
            ) dup
        ) THEN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'public' AND indexname = 'uq_invoices_company_invoice_no'
            ) THEN
                EXECUTE 'CREATE UNIQUE INDEX uq_invoices_company_invoice_no ON invoices (company_id, invoice_no)';
            END IF;
        END IF;
    END $$
    """,
    "CREATE INDEX IF NOT EXISTS idx_invoices_company_invoice_no ON invoices (company_id, invoice_no)",
]


def run_statements(conn, statements: Iterable[str], dry_run: bool) -> None:
    for raw_sql in statements:
        sql = " ".join(raw_sql.split())
        if dry_run:
            print(f"[dry-run] {sql}")
            continue
        conn.execute(text(raw_sql))


def main() -> None:
    parser = argparse.ArgumentParser(description="Align legacy Railway PostgreSQL schema with current billing ORM")
    parser.add_argument("--postgres-url", default=os.getenv("DATABASE_URL", ""), help="PostgreSQL URL")
    parser.add_argument("--dry-run", action="store_true", help="Print statements without executing")
    args = parser.parse_args()

    if not args.postgres_url:
        raise SystemExit("DATABASE_URL/--postgres-url is required")

    postgres_url = _normalize_database_url(args.postgres_url)
    engine = create_engine(postgres_url, future=True, pool_pre_ping=True)

    # Create any missing tables first, then align legacy columns.
    if not args.dry_run:
        Base.metadata.create_all(engine)

    with engine.begin() as conn:
        run_statements(conn, DDL_STATEMENTS, args.dry_run)
        run_statements(conn, INDEX_STATEMENTS, args.dry_run)
        run_statements(conn, POST_STEPS, args.dry_run)

    print("Schema alignment complete." if not args.dry_run else "Schema alignment dry-run complete.")


if __name__ == "__main__":
    main()
