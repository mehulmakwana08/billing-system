import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), 'billing.db')
print("Exists:", os.path.exists(db_path))

if not os.path.exists(db_path):
    print("Database file not found. Start backend once to create billing.db.")
    raise SystemExit(1)

conn = None
try:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    cur = conn.execute(
        """
        INSERT INTO customers (company_id, name, address, gstin, state_code, phone, email, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        ('Temp Delete Smoke Customer', 'Test Address', '', '24', '', ''),
    )
    temp_id = cur.lastrowid
    conn.commit()
    print(f"Created temp customer id: {temp_id}")

    cur = conn.execute("DELETE FROM customers WHERE id=?", (temp_id,))
    conn.commit()
    print(f"Delete temp id {temp_id} executed, affected rows: {cur.rowcount}")
except Exception as e:
    print("Delete script error:", e)
finally:
    if conn is not None:
        conn.close()

