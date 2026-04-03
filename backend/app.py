from flask import Flask, request, jsonify, send_file, send_from_directory, g
import sqlite3, os, json, re, threading, logging, time
from datetime import datetime, date
from urllib.parse import quote

import psycopg2
import requests
from psycopg2 import IntegrityError as PsycopgIntegrityError
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from auth import AuthError, hash_password, issue_token, load_auth_context, require_auth, verify_password
from database import _normalize_database_url
from pdf_generator import generate_invoice_pdf, generate_pdf
from num_words import num_to_words
from sync_service import enqueue_sync

app = Flask(__name__)

load_dotenv()


def _env_clean(name, default=''):
    raw_value = os.getenv(name, default)
    if raw_value is None:
        return ''
    return str(raw_value).replace('\ufeff', '').strip().strip('"').strip("'")

APP_MODE = os.getenv('APP_MODE', 'offline').lower()  # offline | cloud
CLOUD_ONLY_MODE = os.getenv('CLOUD_ONLY_MODE', '1') == '1'
LOGIN_ONLY_MODE = os.getenv('LOGIN_ONLY_MODE', '1') == '1'
ALLOW_SELF_REGISTER = (os.getenv('ALLOW_SELF_REGISTER', '0') == '1') and not LOGIN_ONLY_MODE
AUTH_REQUIRED = CLOUD_ONLY_MODE or os.getenv('AUTH_REQUIRED', '0') == '1' or APP_MODE == 'cloud'
LOG_LEVEL_NAME = _env_clean('BILLING_LOG_LEVEL', _env_clean('LOG_LEVEL', 'DEBUG')).upper()
APP_LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.DEBUG)
app.logger.setLevel(APP_LOG_LEVEL)
PUBLIC_PATHS = {'/api/health', '/api/auth/login', '/api/auth/register'}
DEFAULT_ADMIN_USERNAME = (os.getenv('DEFAULT_ADMIN_USERNAME', 'admin') or 'admin').strip().lower()
DEFAULT_ADMIN_PASSWORD_HASH = (os.getenv('DEFAULT_ADMIN_PASSWORD_HASH') or '').strip()
DEFAULT_ADMIN_PASSWORD = (os.getenv('DEFAULT_ADMIN_PASSWORD') or '').strip()
DEFAULT_CORS_ALLOWED_ORIGINS = {
    'http://localhost:3000',
    'http://127.0.0.1:3000',
    'http://localhost:5000',
    'http://127.0.0.1:5000',
    'null',
}


def _load_allowed_cors_origins():
    configured = os.getenv('CORS_ALLOWED_ORIGINS', '')
    values = {origin.strip() for origin in configured.split(',') if origin.strip()}
    return values or DEFAULT_CORS_ALLOWED_ORIGINS


CORS_ALLOWED_ORIGINS = _load_allowed_cors_origins()
ALLOWED_COMPANY_SETTING_KEYS = {
    'name',
    'address',
    'gstin',
    'state_code',
    'state_name',
    'phone',
    'email',
    'invoice_prefix',
    'next_invoice_no',
    'terms',
    'bank_name',
    'bank_account',
    'bank_ifsc',
    'bank_branch',
}
PASSWORD_MIN_LENGTH = int(os.getenv('PASSWORD_MIN_LENGTH', '10'))


def current_company_id():
    return int(getattr(g, 'company_id', 1) or 1)


@app.before_request
def begin_request_timer():
    g._request_started_at = time.perf_counter()


@app.before_request
def attach_auth_context():
    if request.method == 'OPTIONS':
        return None
    if not request.path.startswith('/api'):
        return None
    if request.path in PUBLIC_PATHS:
        return None
    try:
        load_auth_context(auth_required=AUTH_REQUIRED)
    except AuthError as exc:
        app.logger.warning(
            'auth_context_failed method=%s path=%s reason=%s',
            request.method,
            request.path,
            str(exc),
        )
        return jsonify({'error': 'Unauthorized', 'message': str(exc)}), 401
    app.logger.debug(
        'auth_context_attached method=%s path=%s company_id=%s user_id=%s',
        request.method,
        request.path,
        getattr(g, 'company_id', None),
        getattr(g, 'user_id', None),
    )
    return None

@app.after_request
def add_cors(response):
    origin = (request.headers.get('Origin') or '').strip()
    if origin and origin in CORS_ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'

    if request.path.startswith('/api'):
        started_at = getattr(g, '_request_started_at', None)
        duration_ms = ''
        if started_at is not None:
            duration_ms = f'{(time.perf_counter() - started_at) * 1000:.2f}'

        app.logger.debug(
            'api_request method=%s path=%s status=%s duration_ms=%s company_id=%s user_id=%s',
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            getattr(g, 'company_id', None),
            getattr(g, 'user_id', None),
        )

    return response

@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    return jsonify({}), 200


@app.route('/favicon.ico', methods=['GET'])
def favicon():
    icon_path = os.path.join(FRONTEND_DIR, 'favicon.ico')
    if os.path.exists(icon_path):
        return send_from_directory(
            FRONTEND_DIR,
            'favicon.ico',
            mimetype='image/vnd.microsoft.icon',
        )
    return '', 204


@app.route('/', defaults={'path': ''}, methods=['GET'])
@app.route('/<path:path>', methods=['GET'])
def serve_web(path):
    # Keep API paths on API handlers; unmatched API routes should not fall back to SPA HTML.
    if path.startswith('api/'):
        return jsonify({'error': 'Not Found'}), 404

    index_path = os.path.join(FRONTEND_DIR, 'index.html')
    if os.path.exists(index_path):
        requested_file = os.path.join(FRONTEND_DIR, path)
        if path:
            if os.path.isfile(requested_file):
                return send_from_directory(FRONTEND_DIR, path)
            # Missing file-like routes should return 404, not SPA HTML.
            # This lets the frontend onerror fallback load CDN assets in web deployments.
            if os.path.splitext(path)[1]:
                return '', 404
        return send_file(index_path)

    return jsonify({'status': 'ok', 'message': 'Billing backend is running.'}), 200

if os.getenv('VERCEL'):
    default_db_path = '/tmp/billing.db'
    default_bills_dir = '/tmp/bills'
else:
    default_db_path = os.path.join(os.path.dirname(__file__), 'billing.db')
    default_bills_dir = os.path.join(os.path.dirname(__file__), 'bills')

DB_PATH = os.getenv('BILLING_DB_PATH') or default_db_path
BILLS_DIR = os.getenv('BILLING_BILLS_DIR') or default_bills_dir
FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'frontend', 'renderer')
)
DATABASE_URL_RAW = _env_clean('DATABASE_URL')
DATABASE_URL = _normalize_database_url(DATABASE_URL_RAW) if DATABASE_URL_RAW else ''
FORCE_POSTGRES = _env_clean('FORCE_POSTGRES', '0') == '1'
USE_POSTGRES = bool(DATABASE_URL) and (FORCE_POSTGRES or bool(os.getenv('VERCEL')) or APP_MODE == 'cloud')

if USE_POSTGRES:
    from database import engine as POSTGRES_ENGINE
    from models import Base as ORMBase
else:
    POSTGRES_ENGINE = None
    ORMBase = None

if (FORCE_POSTGRES or bool(os.getenv('VERCEL')) or APP_MODE == 'cloud') and not DATABASE_URL:
    raise RuntimeError('DATABASE_URL is required when Postgres mode is enabled.')

SUPABASE_BASE_URL = _env_clean('SUPABASE_URL').rstrip('/')
SUPABASE_SERVICE_KEY = (
    _env_clean('SUPABASE_SERVICE_ROLE_KEY')
    or _env_clean('SUPABASE_SERVICE_KEY')
    or _env_clean('SUPABASE_ANON_KEY')
)
DB_SNAPSHOT_BUCKET = (
    _env_clean('SUPABASE_DB_SNAPSHOT_BUCKET')
    or _env_clean('SUPABASE_STORAGE_BUCKET')
    or _env_clean('SUPABASE_BUCKET')
    or 'invoices'
)
DB_SNAPSHOT_KEY = _env_clean('SUPABASE_DB_SNAPSHOT_KEY', 'system/billing.db') or 'system/billing.db'
DB_SNAPSHOT_ENABLED = (
    (not USE_POSTGRES)
    and
    _env_clean('ENABLE_DB_SNAPSHOT', '1') == '1'
    and bool(os.getenv('VERCEL'))
    and bool(SUPABASE_BASE_URL)
    and bool(SUPABASE_SERVICE_KEY)
)
DB_PERSISTENCE_MODE = (
    'postgres'
    if USE_POSTGRES
    else ('supabase-storage-snapshot' if DB_SNAPSHOT_ENABLED else ('ephemeral-tmp' if os.getenv('VERCEL') else 'local-file'))
)
_DB_SNAPSHOT_LOCK = threading.Lock()
DB_INTEGRITY_ERRORS = (sqlite3.IntegrityError, PsycopgIntegrityError)
db_dir = os.path.dirname(DB_PATH)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)
os.makedirs(BILLS_DIR, exist_ok=True)

# ── DB Helpers ────────────────────────────────────────────────────────────────

def _rewrite_sql_for_postgres(query):
    if query is None:
        return None

    text = str(query)
    stripped = text.strip()
    if not stripped:
        return text

    upper = stripped.upper()
    if upper.startswith('PRAGMA '):
        return None
    if upper.startswith('BEGIN IMMEDIATE'):
        return 'BEGIN'

    if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+company\s+VALUES", text, flags=re.IGNORECASE):
        text = (
            'INSERT INTO company (key, value) VALUES (?, ?) '
            'ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value'
        )

    if re.search(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", text, flags=re.IGNORECASE):
        text = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", 'INSERT INTO', text, flags=re.IGNORECASE)
        text = text.rstrip().rstrip(';')
        if 'ON CONFLICT' not in text.upper():
            text = f"{text} ON CONFLICT DO NOTHING"

    text = text.replace("strftime('%Y-%m', i.date)", "substring(i.date from 1 for 7)")
    text = text.replace("strftime('%Y', i.date)", "substring(i.date from 1 for 4)")
    text = text.replace("strftime('%Y-%m', date)", "substring(\"date\" from 1 for 7)")
    text = text.replace("strftime('%Y', date)", "substring(\"date\" from 1 for 4)")
    text = text.replace("date('now','-6 months')", "TO_CHAR(CURRENT_DATE - INTERVAL '6 months', 'YYYY-MM-DD')")

    text = text.replace("datetime('now')", 'CURRENT_TIMESTAMP')
    text = text.replace('datetime("now")', 'CURRENT_TIMESTAMP')
    text = text.replace('AUTOINCREMENT', '')
    text = text.replace('?', '%s')
    return text


class PostgresCursorAdapter:
    def __init__(self, raw_cursor):
        self._raw_cursor = raw_cursor
        self.lastrowid = None
        self.rowcount = -1

    def execute(self, query, params=None):
        rewritten = _rewrite_sql_for_postgres(query)
        if rewritten is None:
            self.lastrowid = None
            self.rowcount = 0
            return self

        if params is None:
            self._raw_cursor.execute(rewritten)
        else:
            self._raw_cursor.execute(rewritten, params)
        self.rowcount = self._raw_cursor.rowcount
        self.lastrowid = None

        if rewritten.lstrip().upper().startswith('INSERT'):
            try:
                self._raw_cursor.execute('SELECT LASTVAL() AS lastrowid')
                row = self._raw_cursor.fetchone()
                if row:
                    self.lastrowid = row.get('lastrowid') if isinstance(row, dict) else row[0]
            except Exception:
                self.lastrowid = None
        return self

    def fetchone(self):
        return self._raw_cursor.fetchone()

    def fetchall(self):
        return self._raw_cursor.fetchall()

    def close(self):
        self._raw_cursor.close()


class PostgresConnectionAdapter:
    def __init__(self, raw_connection):
        self._raw_connection = raw_connection
        self.row_factory = None

    def cursor(self):
        return PostgresCursorAdapter(self._raw_connection.cursor(cursor_factory=RealDictCursor))

    def execute(self, query, params=None):
        return self.cursor().execute(query, params)

    def commit(self):
        self._raw_connection.commit()

    def rollback(self):
        self._raw_connection.rollback()

    def close(self):
        self._raw_connection.close()


def _connect_postgres():
    if not USE_POSTGRES:
        raise RuntimeError('Postgres mode is not enabled.')
    raw_connection = POSTGRES_ENGINE.raw_connection()
    return PostgresConnectionAdapter(raw_connection)

def _db_snapshot_object_url():
    bucket_path = quote(str(DB_SNAPSHOT_BUCKET), safe='')
    object_path = quote(str(DB_SNAPSHOT_KEY), safe='/')
    return f"{SUPABASE_BASE_URL}/storage/v1/object/{bucket_path}/{object_path}"


def _db_snapshot_auth_headers():
    return {
        'apikey': SUPABASE_SERVICE_KEY,
        'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
    }


def _restore_db_snapshot():
    if not DB_SNAPSHOT_ENABLED:
        return False

    temp_path = f"{DB_PATH}.snapshot.download"
    with _DB_SNAPSHOT_LOCK:
        try:
            response = requests.get(
                _db_snapshot_object_url(),
                headers=_db_snapshot_auth_headers(),
                timeout=20,
            )
            body_text = (response.text or '').lower()
            if response.status_code == 404:
                return False
            if response.status_code == 400 and (
                'not_found' in body_text or 'object not found' in body_text
            ):
                return False
            if response.status_code != 200:
                app.logger.warning(
                    'DB snapshot restore failed with status %s: %s',
                    response.status_code,
                    response.text,
                )
                return False
            if not response.content:
                return False

            with open(temp_path, 'wb') as handle:
                handle.write(response.content)
            os.replace(temp_path, DB_PATH)
            return True
        except Exception as exc:
            app.logger.warning('DB snapshot restore failed: %s', exc)
            return False
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


def _persist_db_snapshot():
    if not DB_SNAPSHOT_ENABLED or not os.path.exists(DB_PATH):
        return False

    with _DB_SNAPSHOT_LOCK:
        try:
            with open(DB_PATH, 'rb') as handle:
                payload = handle.read()
            if not payload:
                return False

            headers = {
                **_db_snapshot_auth_headers(),
                'Content-Type': 'application/octet-stream',
                'x-upsert': 'true',
            }
            response = requests.post(
                _db_snapshot_object_url(),
                headers=headers,
                data=payload,
                timeout=20,
            )
            if response.status_code not in (200, 201):
                app.logger.warning(
                    'DB snapshot persist failed with status %s: %s',
                    response.status_code,
                    response.text,
                )
                return False
            return True
        except Exception as exc:
            app.logger.warning('DB snapshot persist failed: %s', exc)
            return False


class SnapshotConnection(sqlite3.Connection):
    def commit(self):
        super().commit()
        _persist_db_snapshot()


def get_db():
    if USE_POSTGRES:
        return _connect_postgres()

    if DB_SNAPSHOT_ENABLED:
        _restore_db_snapshot()

    factory = SnapshotConnection if DB_SNAPSHOT_ENABLED else sqlite3.Connection
    conn = sqlite3.connect(DB_PATH, factory=factory)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _postgres_definition(definition, column_name):
    updated = definition.replace("DEFAULT (datetime('now'))", 'DEFAULT CURRENT_TIMESTAMP')
    updated = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', updated)
    if column_name in ('created_at', 'updated_at', 'last_attempt_at'):
        updated = re.sub(r'^\s*TEXT\b', 'TIMESTAMP WITHOUT TIME ZONE', updated)
    return updated


def ensure_column(conn, table_name, column_name, definition):
    if USE_POSTGRES:
        cols = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            """,
            (table_name,),
        ).fetchall()
        existing = {c['column_name'] for c in cols}
        if column_name not in existing:
            pg_definition = _postgres_definition(definition, column_name)
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} {pg_definition}")
            if column_name in ('created_at', 'updated_at'):
                conn.execute(
                    f"UPDATE {table_name} SET {column_name}=CURRENT_TIMESTAMP WHERE {column_name} IS NULL"
                )
        return

    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {c['name'] for c in cols}
    if column_name not in existing:
        try:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
        except sqlite3.OperationalError as exc:
            # SQLite ALTER TABLE rejects expression defaults such as datetime('now').
            if 'non-constant default' not in str(exc).lower():
                raise
            fallback = definition.replace("DEFAULT (datetime('now'))", '').strip()
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {fallback}")
            if column_name in ('created_at', 'updated_at'):
                conn.execute(
                    f"UPDATE {table_name} SET {column_name}=datetime('now') WHERE {column_name} IS NULL"
                )


def _migrate_company_to_company_settings(conn):
    rows = conn.execute("SELECT key, value FROM company").fetchall()
    for row in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO company_settings (company_id, key, value, created_at, updated_at)
            VALUES (1, ?, ?, datetime('now'), datetime('now'))
            """,
            (row['key'], row['value']),
        )


def ensure_default_admin_user(conn):
    username = DEFAULT_ADMIN_USERNAME
    password_hash = DEFAULT_ADMIN_PASSWORD_HASH
    if not password_hash and DEFAULT_ADMIN_PASSWORD:
        password_hash = hash_password(DEFAULT_ADMIN_PASSWORD)
    if not username or not password_hash:
        return

    existing = conn.execute(
        "SELECT id, password_hash FROM users WHERE lower(email)=?",
        (username,),
    ).fetchone()

    if existing:
        if existing['password_hash'] != password_hash:
            conn.execute(
                "UPDATE users SET password_hash=?, updated_at=datetime('now') WHERE id=?",
                (password_hash, existing['id']),
            )
        return

    conn.execute(
        """
        INSERT INTO users (email, password_hash, company_id, created_at, updated_at)
        VALUES (?, ?, 1, datetime('now'), datetime('now'))
        """,
        (username, password_hash),
    )


def _init_postgres_schema(conn):
    ORMBase.metadata.create_all(bind=POSTGRES_ENGINE)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invoice_counters (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL DEFAULT 1,
            year INTEGER NOT NULL,
            next_no INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(company_id, year)
        )
        """
    )

def init_db():
    conn = get_db()
    if USE_POSTGRES:
        _init_postgres_schema(conn)
        c = conn
    else:
        c = conn.cursor()
        c.executescript('''
        CREATE TABLE IF NOT EXISTS company (key TEXT PRIMARY KEY, value TEXT);

        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT,
            gstin TEXT,
            state_code TEXT DEFAULT '24',
            phone TEXT,
            email TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            hsn_code TEXT,
            default_rate REAL DEFAULT 0,
            gst_percent REAL DEFAULT 18,
            unit TEXT DEFAULT 'PCS',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT NOT NULL,
            invoice_type TEXT DEFAULT 'TAX INVOICE',
            date TEXT NOT NULL,
            customer_id INTEGER,
            customer_name TEXT,
            customer_address TEXT,
            customer_gstin TEXT,
            customer_state_code TEXT DEFAULT '24',
            place_of_supply TEXT DEFAULT '24-Gujarat',
            taxable_amount REAL DEFAULT 0,
            cgst REAL DEFAULT 0,
            sgst REAL DEFAULT 0,
            igst REAL DEFAULT 0,
            grand_total REAL DEFAULT 0,
            status TEXT DEFAULT 'final',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            product_id INTEGER,
            product_name TEXT,
            hsn_code TEXT,
            qty REAL DEFAULT 1,
            rate REAL DEFAULT 0,
            taxable_amount REAL DEFAULT 0,
            gst_percent REAL DEFAULT 18,
            cgst REAL DEFAULT 0,
            sgst REAL DEFAULT 0,
            igst REAL DEFAULT 0,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_date TEXT,
            mode TEXT DEFAULT 'Cash',
            reference TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (invoice_id) REFERENCES invoices(id)
        );

        CREATE TABLE IF NOT EXISTS customer_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('credit','debit')),
            amount REAL NOT NULL,
            description TEXT,
            reference_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        CREATE TABLE IF NOT EXISTS company_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL DEFAULT 1,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(company_id, key)
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            company_id INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL DEFAULT 1,
            entity TEXT NOT NULL,
            action TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            last_attempt_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS invoice_number_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL DEFAULT 1,
            year INTEGER NOT NULL,
            start_no INTEGER NOT NULL,
            end_no INTEGER NOT NULL,
            next_no INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS invoice_counters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL DEFAULT 1,
            year INTEGER NOT NULL,
            next_no INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(company_id, year)
        );
    ''')

    # Backward-compatible schema upgrades for hybrid/cloud mode.
    for table in ('customers', 'products', 'invoices', 'payments', 'customer_ledger'):
        ensure_column(conn, table, 'company_id', "INTEGER NOT NULL DEFAULT 1")
        ensure_column(conn, table, 'updated_at', "TEXT DEFAULT (datetime('now'))")

    ensure_column(conn, 'invoices', 'pdf_url', 'TEXT')
    ensure_column(conn, 'invoices', 'sync_status', "TEXT DEFAULT 'pending'")
    ensure_column(conn, 'invoice_items', 'created_at', "TEXT DEFAULT (datetime('now'))")
    ensure_column(conn, 'invoice_items', 'updated_at', "TEXT DEFAULT (datetime('now'))")

    _migrate_company_to_company_settings(conn)

    # Default company settings
    defaults = {
        'name': 'ARVIND PLASTIC INDUSTRIES',
        'address': 'R S NO.152/P4, COMMERCIAL PLOT NO-1, SHOP NO.8, PALIYAD ROAD, SAYLA, SURENDRANAGAR',
        'gstin': '24AAIFC6554D1ZN',
        'state_code': '24',
        'state_name': 'Gujarat',
        'phone': '',
        'email': '',
        'invoice_prefix': 'GT/',
        'next_invoice_no': '31',
        'terms': '1. Goods once sold will not be taken back.\n2. Interest @18% p.a. will be charged if payment is not made within due date.\n3. Our risk and responsibility ceases as soon as the goods leave our premises.\n4. Subject to SAYLA Jurisdiction only. E.&.O.E',
        'bank_name': '',
        'bank_account': '',
        'bank_ifsc': '',
        'bank_branch': '',
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO company VALUES (?,?)", (k, v))
        c.execute(
            """
            INSERT OR IGNORE INTO company_settings (company_id, key, value, created_at, updated_at)
            VALUES (1, ?, ?, datetime('now'), datetime('now'))
            """,
            (k, v),
        )

    # Seed sample data
    c.execute("""
        INSERT OR IGNORE INTO products (
            id, company_id, name, hsn_code, default_rate, gst_percent, unit, updated_at
        ) VALUES (1, 1, 'Bangles Acrylic Paip', '3906', 80.0, 18.0, 'PCS', datetime('now'))
    """)
    c.execute("""
        INSERT OR IGNORE INTO customers (
            id, company_id, name, address, gstin, state_code, updated_at
        ) VALUES (
            1, 1, 'Chamunda Bangles',
            'SAYLA, NEAR BUS STOP, SAYLA, SAYLA, SAYLA, Surendranagar - 363430',
            '24BGFPM0677R1ZT', '24', datetime('now')
        )
    """)

    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_company_no ON invoices(company_id, invoice_no)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_queue_company_status ON sync_queue(company_id, status)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_company_email ON users(company_id, email)"
    )
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_counters_company_year ON invoice_counters(company_id, year)"
    )

    try:
        seed_next = int(defaults.get('next_invoice_no', '1') or 1)
    except Exception:
        seed_next = 1
    c.execute(
        """
        INSERT OR IGNORE INTO invoice_counters (company_id, year, next_no, created_at, updated_at)
        VALUES (1, ?, ?, datetime('now'), datetime('now'))
        """,
        (datetime.utcnow().year, seed_next),
    )

    ensure_default_admin_user(conn)

    conn.commit()
    conn.close()

# ── Utility ───────────────────────────────────────────────────────────────────

def get_company_dict(conn, company_id=1):
    rows = conn.execute(
        "SELECT key, value FROM company_settings WHERE company_id = ?",
        (company_id,),
    ).fetchall()
    if rows:
        return {r['key']: r['value'] for r in rows}
    legacy_rows = conn.execute("SELECT key, value FROM company").fetchall()
    return {r['key']: r['value'] for r in legacy_rows}

def calc_gst(taxable, gst_pct, seller_state, buyer_state):
    """Returns (cgst, sgst, igst)"""
    tax = round(taxable * gst_pct / 100, 2)
    if seller_state == buyer_state:
        half = round(tax / 2, 2)
        return half, half, 0.0
    else:
        return 0.0, 0.0, tax


def normalize_invoice_prefix(prefix):
    value = (prefix or 'GT/').strip()
    if not value:
        value = 'GT/'
    return value if value.endswith('/') else f"{value}/"


def extract_invoice_counter(invoice_no):
    try:
        return int(str(invoice_no).split('/')[-1])
    except Exception:
        return None


def format_invoice_number(invoice_counter, prefix='GT/', year=None):
    invoice_year = int(year or datetime.utcnow().year)
    return f"{normalize_invoice_prefix(prefix)}{invoice_year}/{int(invoice_counter):05d}"


def get_invoice_number_preview(conn, company_id=1):
    co = get_company_dict(conn, company_id)
    year = datetime.utcnow().year
    prefix = normalize_invoice_prefix(co.get('invoice_prefix', 'GT/'))
    row = conn.execute(
        "SELECT next_no FROM invoice_counters WHERE company_id=? AND year=?",
        (company_id, year),
    ).fetchone()
    if row and row['next_no'] is not None:
        next_no = int(row['next_no'])
    else:
        try:
            next_no = int(co.get('next_invoice_no', '1') or 1)
        except Exception:
            next_no = 1
    return format_invoice_number(next_no, prefix, year), next_no, prefix


def reserve_invoice_number(conn, company_id=1, company_settings=None):
    co = company_settings or get_company_dict(conn, company_id)
    year = datetime.utcnow().year
    prefix = normalize_invoice_prefix(co.get('invoice_prefix', 'GT/'))
    try:
        fallback_next = int(co.get('next_invoice_no', '1') or 1)
    except Exception:
        fallback_next = 1

    conn.execute(
        """
        INSERT INTO invoice_counters (company_id, year, next_no, created_at, updated_at)
        VALUES (?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(company_id, year) DO NOTHING
        """,
        (company_id, year, fallback_next),
    )
    row = conn.execute(
        "SELECT next_no FROM invoice_counters WHERE company_id=? AND year=?",
        (company_id, year),
    ).fetchone()
    next_no = int(row['next_no']) if row and row['next_no'] is not None else fallback_next
    conn.execute(
        """
        UPDATE invoice_counters
        SET next_no=?, updated_at=datetime('now')
        WHERE company_id=? AND year=?
        """,
        (next_no + 1, company_id, year),
    )
    conn.execute(
        """
        INSERT INTO company_settings (company_id, key, value, created_at, updated_at)
        VALUES (?, 'next_invoice_no', ?, datetime('now'), datetime('now'))
        ON CONFLICT(company_id, key)
        DO UPDATE SET value = excluded.value, updated_at = datetime('now')
        """,
        (company_id, str(next_no + 1)),
    )
    return format_invoice_number(next_no, prefix, year), next_no

def advance_invoice_no(conn, invoice_no, company_id=1):
    counter = extract_invoice_counter(invoice_no)
    if counter is None:
        return

    next_no = counter + 1
    year = datetime.utcnow().year
    conn.execute(
        """
        INSERT INTO invoice_counters (company_id, year, next_no, created_at, updated_at)
        VALUES (?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(company_id, year)
        DO UPDATE SET
            next_no = CASE
                WHEN invoice_counters.next_no < excluded.next_no THEN excluded.next_no
                ELSE invoice_counters.next_no
            END,
            updated_at = datetime('now')
        """,
        (company_id, year, next_no),
    )
    conn.execute(
        """
        INSERT INTO company_settings (company_id, key, value, created_at, updated_at)
        VALUES (?, 'next_invoice_no', ?, datetime('now'), datetime('now'))
        ON CONFLICT(company_id, key)
        DO UPDATE SET
            value = CASE
                WHEN CAST(company_settings.value AS INTEGER) < CAST(excluded.value AS INTEGER)
                    THEN excluded.value
                ELSE company_settings.value
            END,
            updated_at = datetime('now')
        """,
        (company_id, str(next_no)),
    )


def to_float(value, field_name):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} must be a valid number')


def validate_password_strength(password):
    if len(password) < PASSWORD_MIN_LENGTH:
        return f'password must be at least {PASSWORD_MIN_LENGTH} characters'
    if not re.search(r'[A-Z]', password):
        return 'password must include at least one uppercase letter'
    if not re.search(r'[a-z]', password):
        return 'password must include at least one lowercase letter'
    if not re.search(r'\d', password):
        return 'password must include at least one digit'
    if not re.search(r'[^A-Za-z0-9]', password):
        return 'password must include at least one special character'
    return None


def parse_iso_date_param(value, field_name):
    text = (value or '').strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError as exc:
        raise ValueError(f'{field_name} must be in YYYY-MM-DD format') from exc


def parse_period_month_param(value):
    text = (value or '').strip()
    if re.fullmatch(r'\d{4}', text):
        return text
    if re.fullmatch(r'\d{4}-\d{2}', text):
        try:
            datetime.strptime(text, '%Y-%m')
            return text
        except ValueError as exc:
            raise ValueError('month must be in YYYY-MM format') from exc
    raise ValueError('month must be in YYYY-MM format or YYYY')

# ── Ledger Helpers ────────────────────────────────────────────────────────────

def add_ledger_entry(conn, customer_id, entry_type, amount, description='', reference_id='', company_id=1):
    """Insert a credit or debit entry into the customer ledger."""
    cur = conn.execute(
        """
        INSERT INTO customer_ledger
        (company_id, customer_id, type, amount, description, reference_id, updated_at)
        VALUES (?,?,?,?,?,?,datetime('now'))
        """,
        (company_id, customer_id, entry_type, round(amount, 2), description, str(reference_id))
    )
    return cur.lastrowid

def get_customer_balance(conn, customer_id, company_id=1):
    """Calculate total credit, total debit, balance and status for a customer."""
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN type='credit' THEN amount ELSE 0 END),0) tc, "
        "COALESCE(SUM(CASE WHEN type='debit' THEN amount ELSE 0 END),0) td "
        "FROM customer_ledger WHERE customer_id=? AND company_id=?", (customer_id, company_id)
    ).fetchone()
    tc = round(row['tc'], 2)
    td = round(row['td'], 2)
    balance = round(tc - td, 2)
    if balance > 0:
        status = 'Overpaid'
    elif balance == 0:
        status = 'Settled'
    else:
        status = 'Due'
    return {'total_credit': tc, 'total_debit': td, 'balance': balance, 'status': status}

# ── Health ────────────────────────────────────────────────────────────────────

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'persistence_mode': DB_PERSISTENCE_MODE})


@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    if not ALLOW_SELF_REGISTER:
        return jsonify({'error': 'self registration is disabled'}), 403

    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    try:
        company_id = int(data.get('company_id') or 1)
    except (TypeError, ValueError):
        return jsonify({'error': 'company_id must be a valid integer'}), 400

    if not email or not password:
        return jsonify({'error': 'email and password are required'}), 400
    password_error = validate_password_strength(password)
    if password_error:
        return jsonify({'error': password_error}), 400

    conn = get_db()
    exists = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
    if exists:
        conn.close()
        return jsonify({'error': 'email already registered'}), 409

    password_hash = hash_password(password)
    cur = conn.execute(
        """
        INSERT INTO users (email, password_hash, company_id, created_at, updated_at)
        VALUES (?, ?, ?, datetime('now'), datetime('now'))
        """,
        (email, password_hash, company_id),
    )
    user_id = cur.lastrowid
    if user_id is None:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'failed to create user'}), 500

    conn.commit()

    token = issue_token(int(user_id), company_id, email)
    conn.close()
    return jsonify({'token': token, 'user': {'id': int(user_id), 'email': email, 'company_id': company_id}}), 201


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.json or {}
    username = (data.get('username') or data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not username or not password:
        return jsonify({'error': 'username and password are required'}), 400

    conn = get_db()
    user = conn.execute(
        'SELECT id, email, password_hash, company_id FROM users WHERE lower(email)=?',
        (username,),
    ).fetchone()

    if not user or not verify_password(password, user['password_hash']):
        conn.close()
        return jsonify({'error': 'invalid credentials'}), 401

    token = issue_token(user['id'], user['company_id'], user['email'])
    conn.close()
    return jsonify(
        {
            'token': token,
            'user': {
                'id': user['id'],
                'email': user['email'],
                'username': user['email'],
                'company_id': user['company_id'],
            },
        }
    )


@app.route('/api/auth/me', methods=['GET'])
@require_auth
def auth_me():
    return jsonify(
        {
            'id': g.user_id,
            'email': g.user_email,
            'username': g.user_email,
            'company_id': current_company_id(),
        }
    )

# ── Company ───────────────────────────────────────────────────────────────────

@app.route('/api/company', methods=['GET'])
def get_company():
    company_id = current_company_id()
    conn = get_db()
    data = get_company_dict(conn, company_id)
    conn.close()
    return jsonify(data)

@app.route('/api/company', methods=['POST'])
def update_company():
    company_id = current_company_id()
    data = request.json or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'company settings payload must be an object'}), 400

    unknown_keys = sorted([k for k in data.keys() if k not in ALLOWED_COMPANY_SETTING_KEYS])
    if unknown_keys:
        return jsonify({'error': f'unsupported company setting keys: {", ".join(unknown_keys)}'}), 400

    normalized = {}
    for key, value in data.items():
        text = str(value if value is not None else '').strip()
        if key == 'state_code' and text and not re.fullmatch(r'\d{2}', text):
            return jsonify({'error': 'state_code must be a 2-digit code'}), 400
        if key == 'next_invoice_no':
            try:
                next_no = int(text)
            except (TypeError, ValueError):
                return jsonify({'error': 'next_invoice_no must be a positive integer'}), 400
            if next_no < 1:
                return jsonify({'error': 'next_invoice_no must be a positive integer'}), 400
            text = str(next_no)
        if key == 'invoice_prefix':
            text = normalize_invoice_prefix(text)
        normalized[key] = text

    conn = get_db()
    for k, v in normalized.items():
        conn.execute(
            """
            INSERT INTO company_settings (company_id, key, value, created_at, updated_at)
            VALUES (?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(company_id, key)
            DO UPDATE SET value = excluded.value, updated_at = datetime('now')
            """,
            (company_id, k, str(v)),
        )
        if company_id == 1:
            # Keep legacy table for backward compatibility with old exports.
            conn.execute(
                """
                INSERT INTO company (key, value)
                VALUES (?, ?)
                ON CONFLICT (key)
                DO UPDATE SET value = excluded.value
                """,
                (k, str(v)),
            )
        if k == 'next_invoice_no':
            year = datetime.utcnow().year
            conn.execute(
                """
                INSERT INTO invoice_counters (company_id, year, next_no, created_at, updated_at)
                VALUES (?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(company_id, year)
                DO UPDATE SET next_no = excluded.next_no, updated_at = datetime('now')
                """,
                (company_id, year, int(v)),
            )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ── Customers ─────────────────────────────────────────────────────────────────

@app.route('/api/customers', methods=['GET'])
def list_customers():
    company_id = current_company_id()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM customers WHERE company_id=? ORDER BY name",
        (company_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        bal = get_customer_balance(conn, d['id'], company_id)
        d['balance'] = bal['balance']
        d['balance_status'] = bal['status']
        result.append(d)
    conn.close()
    return jsonify(result)

@app.route('/api/customers', methods=['POST'])
def create_customer():
    company_id = current_company_id()
    d = request.json or {}
    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO customers (company_id,name,address,gstin,state_code,phone,email,updated_at)
        VALUES (?,?,?,?,?,?,?,datetime('now'))
        """,
        (company_id, d.get('name'), d.get('address'), d.get('gstin'),
         d.get('state_code','24'), d.get('phone',''), d.get('email',''),)
    )
    row = conn.execute(
        "SELECT * FROM customers WHERE id=? AND company_id=?",
        (cur.lastrowid, company_id),
    ).fetchone()
    if APP_MODE != 'cloud':
        enqueue_sync(conn, company_id, 'customer', 'create', dict(row))
    conn.commit()
    conn.close()
    return jsonify(dict(row)), 201

@app.route('/api/customers/<int:cid>', methods=['GET'])
def get_customer(cid):
    company_id = current_company_id()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM customers WHERE id=? AND company_id=?",
        (cid, company_id),
    ).fetchone()
    conn.close()
    return jsonify(dict(row)) if row else (jsonify({'error':'Not found'}), 404)

@app.route('/api/customers/<int:cid>', methods=['PUT'])
def update_customer(cid):
    company_id = current_company_id()
    d = request.json or {}
    conn = get_db()
    conn.execute(
        """
        UPDATE customers
        SET name=?,address=?,gstin=?,state_code=?,phone=?,email=?,updated_at=datetime('now')
        WHERE id=? AND company_id=?
        """,
        (d.get('name'), d.get('address'), d.get('gstin'),
         d.get('state_code','24'), d.get('phone',''), d.get('email',''), cid, company_id)
    )
    row = conn.execute(
        "SELECT * FROM customers WHERE id=? AND company_id=?",
        (cid, company_id),
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    if APP_MODE != 'cloud':
        enqueue_sync(conn, company_id, 'customer', 'update', dict(row))
    conn.commit()
    conn.close()
    return jsonify(dict(row))

@app.route('/api/customers/<int:cid>', methods=['DELETE'])
def delete_customer(cid):
    conn = None
    try:
        company_id = current_company_id()
        conn = get_db()
        # Instead of blocking, set customer_id to NULL on existing invoices
        # so we don't lose historical invoice records!
        conn.execute(
            "UPDATE invoices SET customer_id = NULL, updated_at=datetime('now') WHERE customer_id = ? AND company_id=?",
            (cid, company_id),
        )
        conn.execute("DELETE FROM customers WHERE id=? AND company_id=?", (cid, company_id))
        if APP_MODE != 'cloud':
            enqueue_sync(conn, company_id, 'customer', 'delete', {'id': cid})
        conn.commit()
        return jsonify({'success': True})
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        app.logger.exception('Failed to delete customer %s', cid)
        return jsonify({'error': 'Database error: ' + str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


# ── Products ──────────────────────────────────────────────────────────────────

@app.route('/api/products', methods=['GET'])
def list_products():
    company_id = current_company_id()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM products WHERE company_id=? ORDER BY name",
        (company_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/products', methods=['POST'])
def create_product():
    company_id = current_company_id()
    d = request.json or {}
    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO products (company_id,name,hsn_code,default_rate,gst_percent,unit,updated_at)
        VALUES (?,?,?,?,?,?,datetime('now'))
        """,
        (company_id, d.get('name'), d.get('hsn_code',''),
         float(d.get('default_rate',0)), float(d.get('gst_percent',18)), d.get('unit','PCS'))
    )
    row = conn.execute(
        "SELECT * FROM products WHERE id=? AND company_id=?",
        (cur.lastrowid, company_id),
    ).fetchone()
    if APP_MODE != 'cloud':
        enqueue_sync(conn, company_id, 'product', 'create', dict(row))
    conn.commit()
    conn.close()
    return jsonify(dict(row)), 201

@app.route('/api/products/<int:pid>', methods=['GET'])
def get_product(pid):
    company_id = current_company_id()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM products WHERE id=? AND company_id=?",
        (pid, company_id),
    ).fetchone()
    conn.close()
    return jsonify(dict(row)) if row else (jsonify({'error':'Not found'}), 404)

@app.route('/api/products/<int:pid>', methods=['PUT'])
def update_product(pid):
    company_id = current_company_id()
    d = request.json or {}
    conn = get_db()
    conn.execute(
        """
        UPDATE products
        SET name=?,hsn_code=?,default_rate=?,gst_percent=?,unit=?,updated_at=datetime('now')
        WHERE id=? AND company_id=?
        """,
        (d.get('name'), d.get('hsn_code',''),
         float(d.get('default_rate',0)), float(d.get('gst_percent',18)), d.get('unit','PCS'), pid, company_id)
    )
    row = conn.execute(
        "SELECT * FROM products WHERE id=? AND company_id=?",
        (pid, company_id),
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    if APP_MODE != 'cloud':
        enqueue_sync(conn, company_id, 'product', 'update', dict(row))
    conn.commit()
    conn.close()
    return jsonify(dict(row))

@app.route('/api/products/<int:pid>', methods=['DELETE'])
def delete_product(pid):
    conn = None
    try:
        company_id = current_company_id()
        conn = get_db()
        # Instead of blocking, set product_id to NULL on existing invoice items
        # so we don't lose historical invoice item records!
        conn.execute(
            """
            UPDATE invoice_items
            SET product_id = NULL, updated_at=datetime('now')
            WHERE product_id = ?
              AND invoice_id IN (SELECT id FROM invoices WHERE company_id = ?)
            """,
            (pid, company_id),
        )
        conn.execute("DELETE FROM products WHERE id=? AND company_id=?", (pid, company_id))
        if APP_MODE != 'cloud':
            enqueue_sync(conn, company_id, 'product', 'delete', {'id': pid})
        conn.commit()
        return jsonify({'success': True})
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        app.logger.exception('Failed to delete product %s', pid)
        return jsonify({'error': 'Database error: ' + str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


# ── Invoices ──────────────────────────────────────────────────────────────────

@app.route('/api/invoices/next-number')
def next_invoice_number():
    company_id = current_company_id()
    conn = get_db()
    invoice_no, _, _ = get_invoice_number_preview(conn, company_id)
    conn.close()
    return jsonify({'invoice_no': invoice_no})

@app.route('/api/invoices', methods=['GET'])
def list_invoices():
    company_id = current_company_id()
    try:
        sd = parse_iso_date_param(request.args.get('start_date'), 'start_date')
        ed = parse_iso_date_param(request.args.get('end_date'), 'end_date')
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    if sd and ed and sd > ed:
        return jsonify({'error': 'start_date must be less than or equal to end_date'}), 400

    conn = get_db()
    q = "SELECT * FROM invoices WHERE company_id=?"
    params = [company_id]  # type: list[object]
    s = request.args.get('search','').strip()
    if s:
        q += " AND (invoice_no LIKE ? OR customer_name LIKE ?)"
        params += [f'%{s}%', f'%{s}%']
    if sd:
        q += " AND date >= ?"; params.append(sd.isoformat())
    if ed:
        q += " AND date <= ?"; params.append(ed.isoformat())
    q += " ORDER BY date DESC, id DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/invoices', methods=['POST'])
def create_invoice():
    company_id = current_company_id()
    d = request.json or {}
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        co = get_company_dict(conn, company_id)
        items = d.get('items', [])
        if not isinstance(items, list) or len(items) == 0:
            raise ValueError('at least one invoice item is required')

        seller_state = str(co.get('state_code', '24') or '24')
        buyer_state = str(d.get('customer_state_code', '24') or '24')

        invoice_no = (d.get('invoice_no') or '').strip()
        auto_generated_number = not invoice_no
        if auto_generated_number:
            invoice_no, _ = reserve_invoice_number(conn, company_id, co)

        inv_date = (d.get('date') or d.get('invoice_date') or '').strip()
        if not inv_date:
            raise ValueError('date is required')

        customer_id = d.get('customer_id')
        if customer_id in ('', None):
            customer_id = None
        else:
            try:
                customer_id = int(customer_id)
            except (TypeError, ValueError):
                raise ValueError('customer_id must be a valid integer')

        customer_name = d.get('customer_name')
        customer_address = d.get('customer_address')
        customer_gstin = d.get('customer_gstin')
        if customer_id:
            cust = conn.execute(
                "SELECT * FROM customers WHERE id=? AND company_id=?",
                (customer_id, company_id),
            ).fetchone()
            if not cust:
                raise ValueError('customer_id does not exist for this company')
            cust = dict(cust)
            if not customer_name:
                customer_name = cust['name']
            if not customer_address:
                customer_address = cust.get('address', '')
            if not customer_gstin:
                customer_gstin = cust.get('gstin', '')
            buyer_state = str(cust.get('state_code', buyer_state) or buyer_state)

        if not customer_name:
            raise ValueError('customer_name is required')

        total_taxable = total_cgst = total_sgst = total_igst = 0.0
        clean_items = []
        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f'items[{idx}] must be an object')

            qty = to_float(item.get('qty', 1), f'items[{idx}].qty')
            rate = to_float(item.get('rate', 0), f'items[{idx}].rate')
            gst_pct = to_float(item.get('gst_percent', 18), f'items[{idx}].gst_percent')

            if qty <= 0:
                raise ValueError(f'items[{idx}].qty must be greater than 0')
            if rate < 0:
                raise ValueError(f'items[{idx}].rate must be non-negative')
            if gst_pct < 0 or gst_pct > 100:
                raise ValueError(f'items[{idx}].gst_percent must be between 0 and 100')

            taxable = round(qty * rate, 2)
            cgst, sgst, igst = calc_gst(taxable, gst_pct, seller_state, buyer_state)
            total_taxable += taxable
            total_cgst += cgst
            total_sgst += sgst
            total_igst += igst
            clean_items.append({**item, 'taxable_amount': taxable, 'cgst': cgst, 'sgst': sgst, 'igst': igst})

        grand_total = round(total_taxable + total_cgst + total_sgst + total_igst, 2)
        customer_state_name = d.get('customer_state_name') or co.get('state_name') or 'Gujarat'
        place_of_supply = f"{buyer_state}-{customer_state_name}"

        cur = conn.execute(
            """
            INSERT INTO invoices (company_id,invoice_no,invoice_type,date,customer_id,customer_name,
            customer_address,customer_gstin,customer_state_code,place_of_supply,
            taxable_amount,cgst,sgst,igst,grand_total,status,notes,pdf_url,sync_status,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            """,
            (
                company_id,
                invoice_no,
                d.get('invoice_type', 'TAX INVOICE'),
                inv_date,
                customer_id,
                customer_name,
                customer_address,
                customer_gstin,
                buyer_state,
                place_of_supply,
                round(total_taxable, 2),
                round(total_cgst, 2),
                round(total_sgst, 2),
                round(total_igst, 2),
                grand_total,
                d.get('status', 'final'),
                d.get('notes', ''),
                '',
                'pending',
            ),
        )
        iid = cur.lastrowid

        for item in clean_items:
            conn.execute(
                """
                INSERT INTO invoice_items (invoice_id,product_id,product_name,hsn_code,
                qty,rate,taxable_amount,gst_percent,cgst,sgst,igst,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
                """,
                (
                    iid,
                    item.get('product_id'),
                    item.get('product_name'),
                    item.get('hsn_code'),
                    to_float(item.get('qty', 1), 'item.qty'),
                    to_float(item.get('rate', 0), 'item.rate'),
                    item['taxable_amount'],
                    to_float(item.get('gst_percent', 18), 'item.gst_percent'),
                    item['cgst'],
                    item['sgst'],
                    item['igst'],
                ),
            )

        if not auto_generated_number:
            advance_invoice_no(conn, invoice_no, company_id)

        credit_applied = 0.0
        remaining_due = grand_total
        if customer_id:
            bal_info = get_customer_balance(conn, customer_id, company_id)
            available_credit = max(bal_info['balance'], 0)
            add_ledger_entry(
                conn,
                customer_id,
                'debit',
                grand_total,
                f'Invoice #{invoice_no}',
                str(iid),
                company_id,
            )
            if available_credit > 0:
                credit_applied = round(min(available_credit, grand_total), 2)
                remaining_due = round(grand_total - credit_applied, 2)

        invoice = conn.execute(
            "SELECT * FROM invoices WHERE id=? AND company_id=?",
            (iid, company_id),
        ).fetchone()
        items_rows = conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (iid,)).fetchall()

        inv_data = dict(invoice)
        inv_data['company_id'] = company_id
        inv_data['items'] = [dict(i) for i in items_rows]
        inv_data['company'] = co
        inv_data['amount_words'] = num_to_words(round(inv_data['grand_total']))
        total_gst = inv_data['cgst'] + inv_data['sgst'] + inv_data['igst']
        inv_data['gst_words'] = num_to_words(round(total_gst))

        pdf_warning = None
        pdf_url = ''
        sync_status = 'pending'
        if APP_MODE == 'cloud':
            try:
                pdf_url = generate_pdf(inv_data, mode='cloud')
                sync_status = 'synced'
            except Exception as exc:
                pdf_warning = str(exc)
                app.logger.warning('Cloud PDF generation skipped for invoice %s: %s', invoice_no, exc)
        else:
            try:
                pdf_url = generate_pdf(inv_data, mode='local')
            except Exception as exc:
                pdf_warning = str(exc)
                app.logger.warning('Local PDF generation skipped for invoice %s: %s', invoice_no, exc)

        conn.execute(
            """
            UPDATE invoices
            SET pdf_url=?, sync_status=?, updated_at=datetime('now')
            WHERE id=? AND company_id=?
            """,
            (pdf_url, sync_status, iid, company_id),
        )

        invoice = conn.execute(
            "SELECT * FROM invoices WHERE id=? AND company_id=?",
            (iid, company_id),
        ).fetchone()
        items_rows = conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (iid,)).fetchall()

        if APP_MODE != 'cloud':
            invoice_payload = dict(invoice)
            invoice_payload['items'] = [dict(i) for i in items_rows]
            enqueue_sync(conn, company_id, 'invoice', 'create', invoice_payload)

        conn.commit()

        result = dict(invoice)
        result['items'] = [dict(i) for i in items_rows]
        result['credit_applied'] = credit_applied
        result['remaining_due'] = remaining_due
        if pdf_warning:
            result['pdf_warning'] = pdf_warning
        return jsonify(result), 201
    except ValueError as exc:
        conn.rollback()
        return jsonify({'error': str(exc)}), 400
    except DB_INTEGRITY_ERRORS as exc:
        conn.rollback()
        msg = str(exc).lower()
        if 'unique' in msg and 'invoice' in msg:
            return jsonify({'error': 'invoice number already exists'}), 409
        return jsonify({'error': f'database integrity error: {exc}'}), 409
    except RuntimeError as exc:
        conn.rollback()
        return jsonify({'error': str(exc)}), 502
    except Exception as exc:
        conn.rollback()
        return jsonify({'error': f'failed to create invoice: {exc}'}), 500
    finally:
        conn.close()

@app.route('/api/invoices/<int:iid>', methods=['GET'])
def get_invoice(iid):
    company_id = current_company_id()
    conn = get_db()
    inv = conn.execute(
        "SELECT * FROM invoices WHERE id=? AND company_id=?",
        (iid, company_id),
    ).fetchone()
    if not inv:
        conn.close()
        return jsonify({'error':'Not found'}), 404
    items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (iid,)).fetchall()
    payments = conn.execute(
        "SELECT * FROM payments WHERE invoice_id=? AND company_id=?",
        (iid, company_id),
    ).fetchall()
    conn.close()
    res = dict(inv)
    res['items'] = [dict(i) for i in items]
    res['payments'] = [dict(p) for p in payments]
    return jsonify(res)

@app.route('/api/invoices/<int:iid>', methods=['DELETE'])
def delete_invoice(iid):
    company_id = current_company_id()
    conn = get_db()
    invoice = conn.execute(
        "SELECT id, invoice_no FROM invoices WHERE id=? AND company_id=?",
        (iid, company_id),
    ).fetchone()
    if not invoice:
        conn.close()
        return jsonify({'error': 'Invoice not found'}), 404

    payment_count = conn.execute(
        "SELECT COUNT(*) AS c FROM payments WHERE invoice_id=? AND company_id=?",
        (iid, company_id),
    ).fetchone()['c']
    if payment_count > 0:
        conn.close()
        return jsonify({'error': 'Cannot delete invoice with linked payments'}), 409

    conn.execute("DELETE FROM invoices WHERE id=? AND company_id=?", (iid, company_id))
    if APP_MODE != 'cloud':
        enqueue_sync(conn, company_id, 'invoice', 'delete', {'id': iid})
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/invoices/<int:iid>/pdf')
def invoice_pdf(iid):
    company_id = current_company_id()
    conn = get_db()
    inv = conn.execute(
        "SELECT * FROM invoices WHERE id=? AND company_id=?",
        (iid, company_id),
    ).fetchone()
    if not inv:
        conn.close()
        return jsonify({'error':'Not found'}), 404
    items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (iid,)).fetchall()
    
    phone = ''
    if inv['customer_id']:
        c = conn.execute(
            "SELECT phone FROM customers WHERE id=? AND company_id=?",
            (inv['customer_id'], company_id),
        ).fetchone()
        if c and c['phone']:
            phone = c['phone']
            
    co = get_company_dict(conn, company_id)
    conn.close()

    inv_data = dict(inv)
    inv_data['customer_phone'] = phone
    inv_data['items'] = [dict(i) for i in items]
    inv_data['company'] = co
    inv_data['amount_words'] = num_to_words(round(inv_data['grand_total']))
    total_gst = inv_data['cgst'] + inv_data['sgst'] + inv_data['igst']
    inv_data['gst_words'] = num_to_words(round(total_gst))

    safe_no = inv_data['invoice_no'].replace('/', '_')
    pdf_path = os.path.join(BILLS_DIR, f"Invoice_{safe_no}.pdf")
    generate_invoice_pdf(inv_data, pdf_path)

    return send_file(pdf_path, mimetype='application/pdf', as_attachment=False,
                     download_name=f"Invoice_{safe_no}.pdf")

@app.route('/api/invoices/<int:iid>/pdf-path')
def invoice_pdf_path(iid):
    """Generate PDF and return the file path (for Electron shell.openPath)"""
    company_id = current_company_id()
    conn = get_db()
    inv = conn.execute(
        "SELECT * FROM invoices WHERE id=? AND company_id=?",
        (iid, company_id),
    ).fetchone()
    if not inv:
        conn.close()
        return jsonify({'error':'Not found'}), 404

    if inv['pdf_url'] and str(inv['pdf_url']).startswith('http'):
        conn.close()
        safe_no = str(inv['invoice_no']).replace('/', '_')
        return jsonify({'path': inv['pdf_url'], 'pdf_url': inv['pdf_url'], 'filename': f"Invoice_{safe_no}.pdf"})

    items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (iid,)).fetchall()
    
    phone = ''
    if inv['customer_id']:
        c = conn.execute(
            "SELECT phone FROM customers WHERE id=? AND company_id=?",
            (inv['customer_id'], company_id),
        ).fetchone()
        if c and c['phone']:
            phone = c['phone']
            
    co = get_company_dict(conn, company_id)
    conn.close()

    inv_data = dict(inv)
    inv_data['customer_phone'] = phone
    inv_data['items'] = [dict(i) for i in items]
    inv_data['company'] = co
    inv_data['amount_words'] = num_to_words(round(inv_data['grand_total']))
    total_gst = inv_data['cgst'] + inv_data['sgst'] + inv_data['igst']
    inv_data['gst_words'] = num_to_words(round(total_gst))

    safe_no = inv_data['invoice_no'].replace('/', '_')

    if APP_MODE == 'cloud':
        try:
            pdf_url = generate_pdf(inv_data, mode='cloud')
            upd_conn = get_db()
            upd_conn.execute(
                """
                UPDATE invoices
                SET pdf_url=?, sync_status=?, updated_at=datetime('now')
                WHERE id=? AND company_id=?
                """,
                (pdf_url, 'synced', iid, company_id),
            )
            upd_conn.commit()
            upd_conn.close()
            return jsonify({'path': pdf_url, 'pdf_url': pdf_url, 'filename': f"Invoice_{safe_no}.pdf"})
        except Exception as exc:
            app.logger.warning('Cloud PDF URL unavailable for invoice %s: %s', inv_data.get('invoice_no'), exc)
            fallback_url = f"{request.host_url.rstrip('/')}/api/invoices/{iid}/pdf"
            return jsonify({
                'path': fallback_url,
                'pdf_url': fallback_url,
                'filename': f"Invoice_{safe_no}.pdf",
                'warning': f'cloud pdf unavailable: {exc}',
            })

    pdf_path = os.path.join(BILLS_DIR, f"Invoice_{safe_no}.pdf")
    generate_invoice_pdf(inv_data, pdf_path)

    return jsonify({'path': pdf_path, 'pdf_url': inv_data.get('pdf_url') or pdf_path, 'filename': f"Invoice_{safe_no}.pdf"})

# ── Payments ──────────────────────────────────────────────────────────────────

@app.route('/api/payments', methods=['POST'])
def add_payment():
    company_id = current_company_id()
    d = request.json or {}

    invoice_id = d.get('invoice_id')
    if invoice_id in (None, ''):
        return jsonify({'error': 'invoice_id is required'}), 400
    try:
        invoice_id = int(invoice_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'invoice_id must be a valid integer'}), 400

    try:
        amount = to_float(d.get('amount', 0), 'amount')
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if amount <= 0:
        return jsonify({'error': 'amount must be greater than 0'}), 400

    conn = get_db()
    invoice = conn.execute(
        "SELECT id FROM invoices WHERE id=? AND company_id=?",
        (invoice_id, company_id),
    ).fetchone()
    if not invoice:
        conn.close()
        return jsonify({'error': 'invoice not found for this company'}), 404

    cur = conn.execute(
        """
        INSERT INTO payments (company_id,invoice_id,amount,payment_date,mode,reference,updated_at)
        VALUES (?,?,?,?,?,?,datetime('now'))
        """,
        (
            company_id,
            invoice_id,
            amount,
            d.get('payment_date', date.today().isoformat()),
            d.get('mode', 'Cash'),
            d.get('reference', ''),
        ),
    )
    row = conn.execute(
        "SELECT * FROM payments WHERE id=? AND company_id=?",
        (cur.lastrowid, company_id),
    ).fetchone()
    if APP_MODE != 'cloud':
        enqueue_sync(conn, company_id, 'payment', 'create', dict(row))
    conn.commit()
    conn.close()
    return jsonify(dict(row)), 201

# ── Ledger ────────────────────────────────────────────────────────────────────

@app.route('/api/ledger/payment', methods=['POST'])
def ledger_record_payment():
    """Record an advance or partial payment as a credit entry."""
    company_id = current_company_id()
    d = request.json or {}
    cid = d.get('customer_id')
    try:
        amount = to_float(d.get('amount', 0), 'amount')
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if not cid or amount <= 0:
        return jsonify({'error': 'customer_id and positive amount required'}), 400
    desc = d.get('description', 'Payment received')
    mode = d.get('mode', 'Cash')
    ref = d.get('reference', '')
    full_desc = f'{desc} ({mode})' if mode else desc
    conn = get_db()
    ledger_id = add_ledger_entry(conn, cid, 'credit', amount, full_desc, ref, company_id)
    ledger_row = conn.execute(
        "SELECT * FROM customer_ledger WHERE id=? AND company_id=?",
        (ledger_id, company_id),
    ).fetchone()
    if APP_MODE != 'cloud':
        enqueue_sync(conn, company_id, 'ledger', 'create', dict(ledger_row))
    conn.commit()
    bal = get_customer_balance(conn, cid, company_id)
    conn.close()
    return jsonify({'success': True, **bal}), 201

@app.route('/api/ledger/<int:cid>')
def ledger_history(cid):
    """Full transaction history for a customer with running balance."""
    company_id = current_company_id()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM customer_ledger WHERE customer_id=? AND company_id=? ORDER BY created_at, id",
        (cid, company_id)
    ).fetchall()
    bal = get_customer_balance(conn, cid, company_id)
    conn.close()
    entries = []
    running = 0.0
    for r in rows:
        d = dict(r)
        if d['type'] == 'credit':
            running += d['amount']
        else:
            running -= d['amount']
        d['running_balance'] = round(running, 2)
        entries.append(d)
    return jsonify({'entries': entries, **bal})

@app.route('/api/customers/<int:cid>/balance')
def customer_balance(cid):
    """Quick balance check for a single customer."""
    company_id = current_company_id()
    conn = get_db()
    bal = get_customer_balance(conn, cid, company_id)
    conn.close()
    return jsonify(bal)

# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/api/dashboard')
def dashboard():
    company_id = current_company_id()
    conn = get_db()
    today = date.today()
    month_start = today.replace(day=1).isoformat()

    r = conn.execute("""
        SELECT COUNT(*) c, COALESCE(SUM(grand_total),0) rev, COALESCE(SUM(cgst+sgst+igst),0) gst
        FROM invoices WHERE company_id=? AND date >= ?""", (company_id, month_start)).fetchone()
    r2 = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(grand_total),0) rev FROM invoices WHERE company_id=?",
        (company_id,),
    ).fetchone()

    if USE_POSTGRES:
        monthly = conn.execute(
            """
            SELECT to_char(NULLIF("date", '')::date, 'YYYY-MM') AS month,
                   COALESCE(SUM(grand_total),0) revenue,
                   COALESCE(SUM(taxable_amount),0) taxable,
                   COALESCE(SUM(cgst+sgst+igst),0) gst,
                   COUNT(*) count
            FROM invoices
            WHERE company_id=?
              AND NULLIF("date", '')::date >= (CURRENT_DATE - INTERVAL '6 months')
            GROUP BY 1
            ORDER BY 1
            """,
            (company_id,),
        ).fetchall()
    else:
        monthly = conn.execute(
            """
            SELECT strftime('%Y-%m', date) month,
                   COALESCE(SUM(grand_total),0) revenue,
                   COALESCE(SUM(taxable_amount),0) taxable,
                   COALESCE(SUM(cgst+sgst+igst),0) gst,
                   COUNT(*) count
            FROM invoices WHERE company_id=? AND date >= date('now','-6 months')
            GROUP BY month ORDER BY month
            """,
            (company_id,),
        ).fetchall()

    recent = conn.execute("""
        SELECT id, invoice_no, date, customer_name, grand_total, status
        FROM invoices WHERE company_id=? ORDER BY id DESC LIMIT 8""", (company_id,)).fetchall()

    customers_count = conn.execute(
        "SELECT COUNT(*) c FROM customers WHERE company_id=?",
        (company_id,),
    ).fetchone()['c']
    products_count = conn.execute(
        "SELECT COUNT(*) c FROM products WHERE company_id=?",
        (company_id,),
    ).fetchone()['c']
    conn.close()

    return jsonify({
        'month_invoices': r['c'],
        'month_revenue': round(r['rev'], 2),
        'month_gst': round(r['gst'], 2),
        'total_invoices': r2['c'],
        'total_revenue': round(r2['rev'], 2),
        'total_customers': customers_count,
        'total_products': products_count,
        'monthly_chart': [dict(x) for x in monthly],
        'recent_invoices': [dict(x) for x in recent],
    })

# ── Reports ───────────────────────────────────────────────────────────────────

def get_date_filter_ext(req, period_type_override=None):
    period_type = period_type_override or req.args.get('period_type')
    if period_type == 'yearly':
        year = req.args.get('year', str(date.today().year))
        if not re.fullmatch(r'\d{4}', str(year).strip()):
            raise ValueError('year must be a 4-digit value')
        return "strftime('%Y', i.date)=?", [year], year
    elif period_type == 'date':
        start_date = req.args.get('start_date', '')
        end_date = req.args.get('end_date', '')
        start = parse_iso_date_param(start_date, 'start_date')
        end = parse_iso_date_param(end_date, 'end_date')
        if not start or not end:
            raise ValueError('start_date and end_date are required for date period')
        if start > end:
            raise ValueError('start_date must be less than or equal to end_date')
        safe_str = f"{start.isoformat()}_to_{end.isoformat()}"
        return "i.date >= ? AND i.date <= ?", [start.isoformat(), end.isoformat()], safe_str
    elif period_type == 'all':
        return "1=1", [], "AllTime"
    else:
        month = parse_period_month_param(req.args.get('month', date.today().strftime('%Y-%m')))
        flt = "strftime('%Y', i.date)=?" if len(month) == 4 else "strftime('%Y-%m', i.date)=?"
        return flt, [month], month

@app.route('/api/reports/monthly')
def monthly_report():
    try:
        return jsonify(_build_sales_report_payload())
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


def _build_sales_report_payload(period_type_override=None):
    company_id = current_company_id()
    customer_id = request.args.get('customer_id')
    conn = get_db()
    
    date_filter, date_params, period_str = get_date_filter_ext(request, period_type_override)
    base_params = [company_id] + list(date_params)

    # SQLite uses GROUP_CONCAT while Postgres uses STRING_AGG.
    product_names_expr = (
        "(SELECT STRING_AGG(product_name::text, ', ') FROM invoice_items WHERE invoice_id = i.id)"
        if USE_POSTGRES
        else "(SELECT GROUP_CONCAT(product_name, ', ') FROM invoice_items WHERE invoice_id = i.id)"
    )
    
    query = f"""
        SELECT i.*, 
               (SELECT COALESCE(SUM(qty), 0) FROM invoice_items WHERE invoice_id = i.id) AS total_qty,
               {product_names_expr} AS product_names
        FROM invoices i
        WHERE i.company_id=? AND {date_filter}
    """
    query_params = list(base_params)
    if customer_id:
        query += " AND i.customer_id=?"
        query_params.append(customer_id)
    query += " ORDER BY i.date"
    
    invoices = conn.execute(query, query_params).fetchall()
    
    sum_query = f"""
        SELECT COUNT(*) count,
               COALESCE(SUM(taxable_amount),0) taxable,
               COALESCE(SUM(cgst),0) cgst,
               COALESCE(SUM(sgst),0) sgst,
               COALESCE(SUM(igst),0) igst,
               COALESCE(SUM(grand_total),0) grand_total
        FROM invoices i WHERE i.company_id=? AND {date_filter}
    """
    sum_params = list(base_params)
    if customer_id:
        sum_query += " AND customer_id=?"
        sum_params.append(customer_id)
        
    summary = conn.execute(sum_query, sum_params).fetchone()
    conn.close()
    
    summary_dict = dict(summary)
    summary_dict['total_qty'] = sum(r['total_qty'] for r in invoices)
    
    return {'month': period_str, 'invoices': [dict(r) for r in invoices], 'summary': summary_dict}


@app.route('/api/reports/yearly')
def yearly_report_compat():
    # Legacy route kept for compatibility; yearly filtering now shares monthly report logic.
    try:
        return jsonify(_build_sales_report_payload(period_type_override='yearly'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

@app.route('/api/reports/sales-pdf')
def sales_report_pdf():
    company_id = current_company_id()
    customer_id = request.args.get('customer_id')
    try:
        date_filter, date_params, period_str = get_date_filter_ext(request)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    conn = get_db()

    params = [company_id] + list(date_params)
    
    query = f"SELECT i.* FROM invoices i WHERE i.company_id=? AND {date_filter}"
    if customer_id:
        query += " AND i.customer_id=?"
        params.append(customer_id)
        
    invoices = conn.execute(query, params).fetchall()
    
    if not invoices:
        conn.close()
        return jsonify({'error':'No data for this period'}), 404
        
    invoice_ids = [str(r['id']) for r in invoices]
    placeholders = ",".join("?" for _ in invoice_ids)
    items_query = f"""
        SELECT it.*, i.date, i.customer_name 
        FROM invoice_items it 
        JOIN invoices i ON it.invoice_id = i.id 
        WHERE i.id IN ({placeholders})
    """
    items = conn.execute(items_query, invoice_ids).fetchall()
    
    co = get_company_dict(conn, company_id)
    
    cust_name = ""
    cust_address = ""
    cust_gstin = ""
    cust_phone = ""
    place_of_supply = ""
    show_customer = False
    show_date = False
    
    if customer_id:
        show_date = True
        c = conn.execute(
            "SELECT * FROM customers WHERE id=? AND company_id=?",
            (customer_id, company_id),
        ).fetchone()
        if c:
            cd = dict(c)
            cust_name = cd['name']
            cust_address = cd['address']
            cust_gstin = cd['gstin']
            cust_phone = cd.get('phone', '')
            place_of_supply = cd.get('state_code', '24') + "-Gujarat"
    else:
        unique_customers = list(set([r['customer_name'] for r in invoices]))
        if len(unique_customers) == 1:
            show_date = True
            cust_name = unique_customers[0]
            cust_address = invoices[0]['customer_address']
            cust_gstin = invoices[0]['customer_gstin']
            place_of_supply = invoices[0]['place_of_supply']
        else:
            show_customer = True
            cust_name = "Multiple Customers"
            cust_address = "Consolidated Report"
            cust_gstin = ""
            place_of_supply = ""
            
    conn.close()
    
    total_taxable = sum(r['taxable_amount'] for r in invoices)
    total_cgst = sum(r['cgst'] for r in invoices)
    total_sgst = sum(r['sgst'] for r in invoices)
    total_igst = sum(r['igst'] for r in invoices)
    grand_total = sum(r['grand_total'] for r in invoices)
    
    syn_inv = {
        'company': co,
        'company_id': company_id,
        'invoice_no': f"REP-{period_str}",
        'date': date.today().strftime('%d/%m/%Y'),
        'invoice_type': "CONSOLIDATED BILL",
        'customer_name': cust_name,
        'customer_address': cust_address,
        'customer_gstin': cust_gstin,
        'customer_phone': cust_phone,
        'place_of_supply': place_of_supply,
        'taxable_amount': total_taxable,
        'cgst': total_cgst,
        'sgst': total_sgst,
        'igst': total_igst,
        'grand_total': grand_total,
        'amount_words': num_to_words(round(grand_total)),
        'gst_words': num_to_words(round(total_cgst + total_sgst + total_igst)),
        'show_customer': show_customer,
        'show_date': show_date,
        'items': [dict(i) for i in items]
    }
    
    safe_no = syn_inv['invoice_no'].replace('/', '_')
    if APP_MODE == 'cloud':
        pdf_url = generate_pdf(syn_inv, mode='cloud')
        return jsonify({'path': pdf_url, 'pdf_url': pdf_url, 'filename': f"Report_{safe_no}.pdf"})

    pdf_path = os.path.join(BILLS_DIR, f"Report_{safe_no}.pdf")
    generate_invoice_pdf(syn_inv, pdf_path)
    return jsonify({'path': pdf_path, 'pdf_url': pdf_path, 'filename': f"Report_{safe_no}.pdf"})

@app.route('/api/reports/gstr1')
def gstr1_report():
    company_id = current_company_id()
    customer_id = request.args.get('customer_id')
    try:
        date_filter, date_params, period_str = get_date_filter_ext(request)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    conn = get_db()

    params = [company_id] + list(date_params)
    
    query = f"""
        SELECT i.invoice_no, i.date, i.customer_name, i.customer_gstin,
               i.place_of_supply, ii.product_name, ii.hsn_code, ii.qty,
               ii.rate, ii.taxable_amount, ii.gst_percent, ii.cgst, ii.sgst, ii.igst
        FROM invoices i
        JOIN invoice_items ii ON ii.invoice_id = i.id
        WHERE i.company_id=? AND {date_filter}
    """
    if customer_id:
        query += " AND i.customer_id = ?"
        params.append(customer_id)
    query += " ORDER BY i.date, i.invoice_no"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/reports/hsn-summary')
def hsn_summary():
    company_id = current_company_id()
    customer_id = request.args.get('customer_id')
    try:
        date_filter, date_params, period_str = get_date_filter_ext(request)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    conn = get_db()

    params = [company_id] + list(date_params)
    
    query = f"""
        SELECT ii.hsn_code, ii.product_name,
               SUM(ii.qty) total_qty, SUM(ii.taxable_amount) taxable,
               SUM(ii.cgst) cgst, SUM(ii.sgst) sgst, SUM(ii.igst) igst,
               ii.gst_percent
        FROM invoice_items ii
        JOIN invoices i ON i.id = ii.invoice_id
        WHERE i.company_id=? AND {date_filter}
    """
    if customer_id:
        query += " AND i.customer_id = ?"
        params.append(customer_id)
    query += " GROUP BY ii.hsn_code, ii.product_name"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/reports/hsn')
def hsn_summary_compat():
    # Legacy alias for older clients.
    return hsn_summary()

@app.route('/api/reports/customer-ledger')
def customer_ledger():
    company_id = current_company_id()
    cid = request.args.get('customer_id', type=int)
    conn = get_db()
    q = """
        SELECT i.*, COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id AND company_id=?),0) paid
        FROM invoices i WHERE i.company_id=?"""
    params = [company_id, company_id]
    if cid is not None:
        q += " AND i.customer_id=?"; params.append(cid)
    q += " ORDER BY i.date DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/ledger/summary')
def ledger_summary_compat():
    # Legacy endpoint: when customer_id is provided, reuse canonical ledger history response.
    # If omitted, return aggregate ledger totals for the whole company.
    cid = request.args.get('customer_id', type=int)
    if cid:
        return ledger_history(cid)

    company_id = current_company_id()
    conn = get_db()
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type='credit' THEN amount ELSE 0 END), 0) AS total_credit,
            COALESCE(SUM(CASE WHEN type='debit' THEN amount ELSE 0 END), 0) AS total_debit
        FROM customer_ledger
        WHERE company_id=?
        """,
        (company_id,),
    ).fetchone()
    conn.close()

    total_credit = float(row['total_credit'] or 0)
    total_debit = float(row['total_debit'] or 0)
    balance = round(total_credit - total_debit, 2)
    status = 'Settled' if abs(balance) < 1e-9 else ('Overpaid' if balance > 0 else 'Due')

    return jsonify(
        {
            'entries': [],
            'total_credit': round(total_credit, 2),
            'total_debit': round(total_debit, 2),
            'balance': balance,
            'status': status,
        }
    )


# ── Sync & Offline Number Blocks ─────────────────────────────────────────────

@app.route('/api/invoices/reserve-number-block', methods=['POST'])
def reserve_invoice_number_block():
    company_id = current_company_id()
    payload = request.json or {}
    block_size = int(payload.get('size', 50) or 50)
    if block_size < 1 or block_size > 5000:
        return jsonify({'error': 'size must be between 1 and 5000'}), 400

    year = datetime.utcnow().year
    conn = get_db()
    co = get_company_dict(conn, company_id)
    try:
        next_no = int(co.get('next_invoice_no', '1') or 1)
    except Exception:
        next_no = 1
    prefix = normalize_invoice_prefix(co.get('invoice_prefix', 'GT/'))

    max_existing = conn.execute(
        """
        SELECT COALESCE(MAX(end_no), 0) mx
        FROM invoice_number_blocks
        WHERE company_id = ? AND year = ?
        """,
        (company_id, year),
    ).fetchone()['mx']

    start_no = max(next_no, int(max_existing) + 1)
    end_no = start_no + block_size - 1

    conn.execute(
        """
        INSERT INTO invoice_number_blocks
        (company_id, year, start_no, end_no, next_no, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'active', datetime('now'), datetime('now'))
        """,
        (company_id, year, start_no, end_no, start_no),
    )
    conn.execute(
        """
        INSERT INTO company_settings (company_id, key, value, created_at, updated_at)
        VALUES (?, 'next_invoice_no', ?, datetime('now'), datetime('now'))
        ON CONFLICT(company_id, key)
        DO UPDATE SET value = excluded.value, updated_at = datetime('now')
        """,
        (company_id, str(end_no + 1)),
    )
    conn.execute(
        """
        INSERT INTO invoice_counters (company_id, year, next_no, created_at, updated_at)
        VALUES (?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(company_id, year)
        DO UPDATE SET
            next_no = CASE
                WHEN invoice_counters.next_no < excluded.next_no THEN excluded.next_no
                ELSE invoice_counters.next_no
            END,
            updated_at = datetime('now')
        """,
        (company_id, year, end_no + 1),
    )
    conn.commit()
    conn.close()

    return jsonify(
        {
            'year': year,
            'start_no': start_no,
            'end_no': end_no,
            'format_preview': format_invoice_number(start_no, prefix, year),
        }
    )


@app.route('/api/sync/push', methods=['POST'])
def sync_push():
    return jsonify({'error': 'legacy sync endpoints are retired in cloud-only mode'}), 410


@app.route('/api/sync/pull', methods=['GET'])
def sync_pull():
    return jsonify({'error': 'legacy sync endpoints are retired in cloud-only mode'}), 410


@app.route('/api/sync/queue/<int:queue_id>', methods=['POST'])
def sync_queue_mark(queue_id):
    return jsonify({'error': 'legacy sync endpoints are retired in cloud-only mode'}), 410

if __name__ == '__main__':
    init_db()
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', os.getenv('BILLING_BACKEND_PORT', '5000')))
    debug = os.getenv('FLASK_DEBUG', '0') == '1'
    app.logger.info(
        'Starting Arvind Billing System Backend on %s:%s (debug=%s, app_mode=%s, persistence_mode=%s, cloud_only=%s)',
        host,
        port,
        debug,
        APP_MODE,
        DB_PERSISTENCE_MODE,
        CLOUD_ONLY_MODE,
    )
    app.run(host=host, port=port, debug=debug, threaded=True)
