from flask import Flask, request, jsonify, send_file, send_from_directory, g
import sqlite3, os, json
from datetime import datetime, date
from dotenv import load_dotenv
from auth import AuthError, hash_password, issue_token, load_auth_context, require_auth, verify_password
from pdf_generator import generate_invoice_pdf, generate_pdf
from num_words import num_to_words
from sync_service import apply_push_payload, build_pull_payload, enqueue_sync, list_pending_sync, mark_sync_status

app = Flask(__name__)

load_dotenv()

APP_MODE = os.getenv('APP_MODE', 'offline').lower()  # offline | cloud
AUTH_REQUIRED = os.getenv('AUTH_REQUIRED', '0') == '1' or APP_MODE == 'cloud'
PUBLIC_PATHS = {'/api/health', '/api/auth/register', '/api/auth/login'}


def current_company_id():
    return int(getattr(g, 'company_id', 1) or 1)


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
        return jsonify({'error': 'Unauthorized', 'message': str(exc)}), 401
    return None

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
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
    DB_PATH = '/tmp/billing.db'
    BILLS_DIR = '/tmp/bills'
else:
    DB_PATH = os.path.join(os.path.dirname(__file__), 'billing.db')
    BILLS_DIR = os.path.join(os.path.dirname(__file__), 'bills')
FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'frontend', 'renderer')
)
os.makedirs(BILLS_DIR, exist_ok=True)

# ── DB Helpers ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_column(conn, table_name, column_name, definition):
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

def init_db():
    conn = get_db()
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
            invoice_no TEXT UNIQUE NOT NULL,
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

def advance_invoice_no(conn, invoice_no, company_id=1):
    try:
        num = int(str(invoice_no).split('/')[-1]) + 1
        conn.execute(
            """
            INSERT INTO company_settings (company_id, key, value, created_at, updated_at)
            VALUES (?, 'next_invoice_no', ?, datetime('now'), datetime('now'))
            ON CONFLICT(company_id, key)
            DO UPDATE SET value = excluded.value, updated_at = datetime('now')
            """,
            (company_id, str(num)),
        )
    except Exception:
        pass


def format_invoice_number(invoice_counter):
    year = datetime.utcnow().year
    return f"GT/{year}/{int(invoice_counter):05d}"

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
    return jsonify({'status': 'ok'})


@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    company_id = int(data.get('company_id') or 1)

    if not email or not password:
        return jsonify({'error': 'email and password are required'}), 400
    if len(password) < 8:
        return jsonify({'error': 'password must be at least 8 characters'}), 400

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
    conn.commit()

    token = issue_token(cur.lastrowid, company_id, email)
    conn.close()
    return jsonify({'token': token, 'user': {'id': cur.lastrowid, 'email': email, 'company_id': company_id}}), 201


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not email or not password:
        return jsonify({'error': 'email and password are required'}), 400

    conn = get_db()
    user = conn.execute(
        'SELECT id, email, password_hash, company_id FROM users WHERE email=?',
        (email,),
    ).fetchone()
    conn.close()

    if not user or not verify_password(password, user['password_hash']):
        return jsonify({'error': 'invalid credentials'}), 401

    token = issue_token(user['id'], user['company_id'], user['email'])
    return jsonify(
        {
            'token': token,
            'user': {'id': user['id'], 'email': user['email'], 'company_id': user['company_id']},
        }
    )


@app.route('/api/auth/me', methods=['GET'])
@require_auth
def auth_me():
    return jsonify({'id': g.user_id, 'email': g.user_email, 'company_id': current_company_id()})

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
    conn = get_db()
    for k, v in data.items():
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
            conn.execute("INSERT OR REPLACE INTO company VALUES (?,?)", (k, str(v)))
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
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Database error: ' + str(e)}), 500


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
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Database error: ' + str(e)}), 500


# ── Invoices ──────────────────────────────────────────────────────────────────

@app.route('/api/invoices/next-number')
def next_invoice_number():
    company_id = current_company_id()
    conn = get_db()
    co = get_company_dict(conn, company_id)
    conn.close()
    next_no = int(co.get('next_invoice_no', '1') or 1)
    return jsonify({'invoice_no': format_invoice_number(next_no)})

@app.route('/api/invoices', methods=['GET'])
def list_invoices():
    company_id = current_company_id()
    conn = get_db()
    q = "SELECT * FROM invoices WHERE company_id=?"
    params = [company_id]
    s = request.args.get('search','').strip()
    if s:
        q += " AND (invoice_no LIKE ? OR customer_name LIKE ?)"
        params += [f'%{s}%', f'%{s}%']
    sd = request.args.get('start_date')
    ed = request.args.get('end_date')
    if sd:
        q += " AND date >= ?"; params.append(sd)
    if ed:
        q += " AND date <= ?"; params.append(ed)
    q += " ORDER BY date DESC, id DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/invoices', methods=['POST'])
def create_invoice():
    company_id = current_company_id()
    d = request.json or {}
    conn = get_db()
    co = get_company_dict(conn, company_id)
    items = d.get('items', [])
    seller_state = co.get('state_code', '24')
    buyer_state = d.get('customer_state_code', '24')

    # Auto-generate invoice_no if not provided
    invoice_no = d.get('invoice_no')
    if not invoice_no:
        next_no = int(co.get('next_invoice_no', '1') or 1)
        invoice_no = format_invoice_number(next_no)

    # Support both 'date' and 'invoice_date' field names
    inv_date = d.get('date') or d.get('invoice_date')
    if not inv_date:
        conn.close()
        return jsonify({'error': 'date is required'}), 400

    # Auto-fill customer info from DB if only customer_id provided
    customer_name = d.get('customer_name')
    customer_address = d.get('customer_address')
    customer_gstin = d.get('customer_gstin')
    if d.get('customer_id') and not customer_name:
        cust = conn.execute(
            "SELECT * FROM customers WHERE id=? AND company_id=?",
            (d['customer_id'], company_id),
        ).fetchone()
        if cust:
            cust = dict(cust)
            customer_name = cust['name']
            customer_address = cust.get('address', '')
            customer_gstin = cust.get('gstin', '')
            buyer_state = cust.get('state_code', '24')

    # Recalculate to ensure accuracy
    total_taxable = total_cgst = total_sgst = total_igst = 0.0
    clean_items = []
    for item in items:
        qty = float(item.get('qty', 1))
        rate = float(item.get('rate', 0))
        gst_pct = float(item.get('gst_percent', 18))
        taxable = round(qty * rate, 2)
        cgst, sgst, igst = calc_gst(taxable, gst_pct, seller_state, buyer_state)
        total_taxable += taxable
        total_cgst += cgst
        total_sgst += sgst
        total_igst += igst
        clean_items.append({**item, 'taxable_amount': taxable, 'cgst': cgst, 'sgst': sgst, 'igst': igst})

    grand_total = round(total_taxable + total_cgst + total_sgst + total_igst, 2)
    place_of_supply = f"{buyer_state}-{d.get('customer_state_name', 'Gujarat')}"

    cur = conn.execute("""
                INSERT INTO invoices (company_id,invoice_no,invoice_type,date,customer_id,customer_name,
          customer_address,customer_gstin,customer_state_code,place_of_supply,
                    taxable_amount,cgst,sgst,igst,grand_total,status,notes,pdf_url,sync_status,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        """, (company_id, invoice_no, d.get('invoice_type','TAX INVOICE'), inv_date,
          d.get('customer_id'), customer_name, customer_address,
          customer_gstin, buyer_state, place_of_supply,
          round(total_taxable,2), round(total_cgst,2), round(total_sgst,2), round(total_igst,2),
                    grand_total, d.get('status','final'), d.get('notes',''), '', 'pending'))
    iid = cur.lastrowid

    for item in clean_items:
        conn.execute("""
            INSERT INTO invoice_items (invoice_id,product_id,product_name,hsn_code,
                            qty,rate,taxable_amount,gst_percent,cgst,sgst,igst,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """, (iid, item.get('product_id'), item.get('product_name'), item.get('hsn_code'),
              float(item.get('qty',1)), float(item.get('rate',0)),
              item['taxable_amount'], float(item.get('gst_percent',18)),
              item['cgst'], item['sgst'], item['igst']))

    advance_invoice_no(conn, invoice_no, company_id)

    # ── Ledger integration ──
    credit_applied = 0.0
    remaining_due = grand_total
    if d.get('customer_id'):
        cid = d['customer_id']
        # Check available credit BEFORE adding the debit
        bal_info = get_customer_balance(conn, cid, company_id)
        available_credit = max(bal_info['balance'], 0)
        # Add debit entry for the invoice
        add_ledger_entry(conn, cid, 'debit', grand_total,
                         f'Invoice #{invoice_no}', str(iid), company_id)
        # Calculate how much credit is auto-applied
        if available_credit > 0:
            credit_applied = round(min(available_credit, grand_total), 2)
            remaining_due = round(grand_total - credit_applied, 2)

    conn.commit()
    invoice = conn.execute(
        "SELECT * FROM invoices WHERE id=? AND company_id=?",
        (iid, company_id),
    ).fetchone()
    items_rows = conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (iid,)).fetchall()

    # Build invoice payload for PDF generation.
    inv_data = dict(invoice)
    inv_data['company_id'] = company_id
    inv_data['items'] = [dict(i) for i in items_rows]
    inv_data['company'] = co
    inv_data['amount_words'] = num_to_words(round(inv_data['grand_total']))
    total_gst = inv_data['cgst'] + inv_data['sgst'] + inv_data['igst']
    inv_data['gst_words'] = num_to_words(round(total_gst))

    pdf_url = ''
    sync_status = 'pending'
    try:
        if APP_MODE == 'cloud':
            pdf_url = generate_pdf(inv_data, mode='cloud')
            sync_status = 'synced'
        else:
            pdf_url = generate_pdf(inv_data, mode='local')
            sync_status = 'pending'
    except Exception:
        pdf_url = ''
        sync_status = 'pending'

    conn.execute(
        """
        UPDATE invoices
        SET pdf_url=?, sync_status=?, updated_at=datetime('now')
        WHERE id=? AND company_id=?
        """,
        (pdf_url, sync_status, iid, company_id),
    )

    conn.commit()
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
    conn.close()
    result = dict(invoice)
    result['items'] = [dict(i) for i in items_rows]
    result['credit_applied'] = credit_applied
    result['remaining_due'] = remaining_due
    return jsonify(result), 201

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
    pdf_path = os.path.join(BILLS_DIR, f"Invoice_{safe_no}.pdf")
    generate_invoice_pdf(inv_data, pdf_path)

    return jsonify({'path': pdf_path, 'pdf_url': inv_data.get('pdf_url') or pdf_path, 'filename': f"Invoice_{safe_no}.pdf"})

# ── Payments ──────────────────────────────────────────────────────────────────

@app.route('/api/payments', methods=['POST'])
def add_payment():
    company_id = current_company_id()
    d = request.json or {}
    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO payments (company_id,invoice_id,amount,payment_date,mode,reference,updated_at)
        VALUES (?,?,?,?,?,?,datetime('now'))
        """,
        (
            company_id,
            d.get('invoice_id'),
            float(d.get('amount', 0)),
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
    amount = float(d.get('amount', 0))
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

    monthly = conn.execute("""
        SELECT strftime('%Y-%m', date) month,
               COALESCE(SUM(grand_total),0) revenue,
               COALESCE(SUM(taxable_amount),0) taxable,
               COALESCE(SUM(cgst+sgst+igst),0) gst,
               COUNT(*) count
        FROM invoices WHERE company_id=? AND date >= date('now','-6 months')
        GROUP BY month ORDER BY month""", (company_id,)).fetchall()

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

def get_date_filter_ext(req):
    period_type = req.args.get('period_type')
    if period_type == 'yearly':
        year = req.args.get('year', str(date.today().year))
        return "strftime('%Y', i.date)=?", [year], year
    elif period_type == 'date':
        start_date = req.args.get('start_date', '')
        end_date = req.args.get('end_date', '')
        safe_str = f"{start_date}_to_{end_date}"
        return "i.date >= ? AND i.date <= ?", [start_date, end_date], safe_str
    elif period_type == 'all':
        return "1=1", [], "AllTime"
    else:
        month = req.args.get('month', date.today().strftime('%Y-%m'))
        flt = "strftime('%Y', i.date)=?" if len(month) == 4 else "strftime('%Y-%m', i.date)=?"
        return flt, [month], month

@app.route('/api/reports/monthly')
def monthly_report():
    company_id = current_company_id()
    customer_id = request.args.get('customer_id')
    conn = get_db()
    
    date_filter, date_params, period_str = get_date_filter_ext(request)
    base_params = [company_id] + list(date_params)
    
    query = f"""
        SELECT i.*, 
               (SELECT COALESCE(SUM(qty), 0) FROM invoice_items WHERE invoice_id = i.id) AS total_qty,
               (SELECT GROUP_CONCAT(product_name, ', ') FROM invoice_items WHERE invoice_id = i.id) AS product_names
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
    
    return jsonify({'month': period_str, 'invoices': [dict(r) for r in invoices], 'summary': summary_dict})

@app.route('/api/reports/sales-pdf')
def sales_report_pdf():
    company_id = current_company_id()
    customer_id = request.args.get('customer_id')
    conn = get_db()
    
    date_filter, date_params, period_str = get_date_filter_ext(request)
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
    conn = get_db()
    
    date_filter, date_params, period_str = get_date_filter_ext(request)
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
    conn = get_db()
    
    date_filter, date_params, period_str = get_date_filter_ext(request)
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

@app.route('/api/reports/customer-ledger')
def customer_ledger():
    company_id = current_company_id()
    cid = request.args.get('customer_id')
    conn = get_db()
    q = """
        SELECT i.*, COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id AND company_id=?),0) paid
        FROM invoices i WHERE i.company_id=?"""
    params = [company_id, company_id]
    if cid:
        q += " AND i.customer_id=?"; params.append(cid)
    q += " ORDER BY i.date DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


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
    next_no = int(co.get('next_invoice_no', '1') or 1)

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
    conn.commit()
    conn.close()

    return jsonify(
        {
            'year': year,
            'start_no': start_no,
            'end_no': end_no,
            'format_preview': format_invoice_number(start_no),
        }
    )


@app.route('/api/sync/push', methods=['POST'])
def sync_push():
    company_id = current_company_id()
    payload = request.json or {}
    changes = payload.get('changes') or []

    if not isinstance(changes, list):
        return jsonify({'error': 'changes must be a list'}), 400

    conn = get_db()
    try:
        results = apply_push_payload(conn, company_id, changes)
        conn.commit()
        return jsonify({'success': True, 'results': results, 'server_time': datetime.utcnow().isoformat() + 'Z'})
    except Exception as exc:
        conn.rollback()
        return jsonify({'error': f'sync push failed: {exc}'}), 500
    finally:
        conn.close()


@app.route('/api/sync/pull', methods=['GET'])
def sync_pull():
    company_id = current_company_id()
    since = request.args.get('since', '')
    conn = get_db()
    try:
        payload = build_pull_payload(conn, company_id, since)
        payload['pending_queue'] = list_pending_sync(conn, company_id)
        return jsonify(payload)
    finally:
        conn.close()


@app.route('/api/sync/queue/<int:queue_id>', methods=['POST'])
def sync_queue_mark(queue_id):
    company_id = current_company_id()
    payload = request.json or {}
    status = payload.get('status', 'synced')
    error = payload.get('error', '')

    conn = get_db()
    row = conn.execute(
        'SELECT id FROM sync_queue WHERE id=? AND company_id=?',
        (queue_id, company_id),
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'queue item not found'}), 404

    mark_sync_status(conn, queue_id, status, error)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

if __name__ == '__main__':
    init_db()
    print("Starting Arvind Billing System Backend on port 5000...")
    app.run(port=5000, debug=False, threaded=True)
