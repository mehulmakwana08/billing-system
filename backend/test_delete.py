import sqlite3
import os

db_path = r'c:\Users\moria\Downloads\billing-system\billing-system\backend\billing.db'
print("Exists:", os.path.exists(db_path))

try:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM customers WHERE id=2")
    conn.commit()
    print("Deleted id 2 successfully")
except Exception as e:
    print("Error deleting id 2:", e)

try:
    conn.execute("DELETE FROM customers WHERE id=1")
    conn.commit()
    print("Deleted id 1 successfully")
except Exception as e:
    print("Error deleting id 1:", e)

