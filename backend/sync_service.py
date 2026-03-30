import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FMT)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in (ISO_FMT, "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _is_incoming_newer(existing_ts: Optional[str], incoming_ts: Optional[str]) -> bool:
    existing = _parse_iso(existing_ts)
    incoming = _parse_iso(incoming_ts)
    if incoming is None:
        return False
    if existing is None:
        return True
    return incoming >= existing


def enqueue_sync(conn, company_id: int, entity: str, action: str, payload: Dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT INTO sync_queue (company_id, entity, action, payload, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', datetime('now'), datetime('now'))
        """,
        (company_id, entity, action, json.dumps(payload)),
    )
    return cur.lastrowid


def list_pending_sync(conn, company_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM sync_queue
        WHERE company_id = ? AND status = 'pending'
        ORDER BY id
        LIMIT ?
        """,
        (company_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_sync_status(conn, queue_id: int, status: str, error: str = "") -> None:
    conn.execute(
        """
        UPDATE sync_queue
        SET status = ?, error = ?, last_attempt_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ?
        """,
        (status, error, queue_id),
    )


def _upsert_customer(conn, company_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    customer_id = payload.get("id")
    existing = None
    if customer_id:
        existing = conn.execute(
            "SELECT id, updated_at FROM customers WHERE id = ? AND company_id = ?",
            (customer_id, company_id),
        ).fetchone()

    if existing and not _is_incoming_newer(existing["updated_at"], payload.get("updated_at")):
        return {"entity": "customer", "status": "skipped", "id": existing["id"], "reason": "stale_update"}

    fields = (
        payload.get("name", ""),
        payload.get("address", ""),
        payload.get("gstin", ""),
        payload.get("state_code", "24"),
        payload.get("phone", ""),
        payload.get("email", ""),
    )

    if existing:
        conn.execute(
            """
            UPDATE customers
            SET name = ?, address = ?, gstin = ?, state_code = ?, phone = ?, email = ?, updated_at = datetime('now')
            WHERE id = ? AND company_id = ?
            """,
            (*fields, existing["id"], company_id),
        )
        return {"entity": "customer", "status": "updated", "id": existing["id"]}

    if customer_id:
        try:
            conn.execute(
                """
                INSERT INTO customers (id, company_id, name, address, gstin, state_code, phone, email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (customer_id, company_id, *fields),
            )
            return {"entity": "customer", "status": "created", "id": customer_id}
        except sqlite3.IntegrityError:
            # Remote IDs can collide with existing local IDs across tenants.
            cur = conn.execute(
                """
                INSERT INTO customers (company_id, name, address, gstin, state_code, phone, email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (company_id, *fields),
            )
            return {"entity": "customer", "status": "created", "id": cur.lastrowid, "remapped_id": customer_id}

    cur = conn.execute(
        """
        INSERT INTO customers (company_id, name, address, gstin, state_code, phone, email, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (company_id, *fields),
    )
    return {"entity": "customer", "status": "created", "id": cur.lastrowid}


def _upsert_product(conn, company_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    product_id = payload.get("id")
    existing = None
    if product_id:
        existing = conn.execute(
            "SELECT id, updated_at FROM products WHERE id = ? AND company_id = ?",
            (product_id, company_id),
        ).fetchone()

    if existing and not _is_incoming_newer(existing["updated_at"], payload.get("updated_at")):
        return {"entity": "product", "status": "skipped", "id": existing["id"], "reason": "stale_update"}

    fields = (
        payload.get("name", ""),
        payload.get("hsn_code", ""),
        float(payload.get("default_rate", 0) or 0),
        float(payload.get("gst_percent", 18) or 18),
        payload.get("unit", "PCS"),
    )

    if existing:
        conn.execute(
            """
            UPDATE products
            SET name = ?, hsn_code = ?, default_rate = ?, gst_percent = ?, unit = ?, updated_at = datetime('now')
            WHERE id = ? AND company_id = ?
            """,
            (*fields, existing["id"], company_id),
        )
        return {"entity": "product", "status": "updated", "id": existing["id"]}

    if product_id:
        try:
            conn.execute(
                """
                INSERT INTO products (id, company_id, name, hsn_code, default_rate, gst_percent, unit, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (product_id, company_id, *fields),
            )
            return {"entity": "product", "status": "created", "id": product_id}
        except sqlite3.IntegrityError:
            cur = conn.execute(
                """
                INSERT INTO products (company_id, name, hsn_code, default_rate, gst_percent, unit, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (company_id, *fields),
            )
            return {"entity": "product", "status": "created", "id": cur.lastrowid, "remapped_id": product_id}

    cur = conn.execute(
        """
        INSERT INTO products (company_id, name, hsn_code, default_rate, gst_percent, unit, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (company_id, *fields),
    )
    return {"entity": "product", "status": "created", "id": cur.lastrowid}


def _upsert_invoice(conn, company_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    invoice_id = payload.get("id")
    existing = None
    if invoice_id:
        existing = conn.execute(
            "SELECT id, updated_at FROM invoices WHERE id = ? AND company_id = ?",
            (invoice_id, company_id),
        ).fetchone()

    if existing and not _is_incoming_newer(existing["updated_at"], payload.get("updated_at")):
        return {"entity": "invoice", "status": "skipped", "id": existing["id"], "reason": "stale_update"}

    header_values = (
        payload.get("invoice_no", ""),
        payload.get("invoice_type", "TAX INVOICE"),
        payload.get("date", ""),
        payload.get("customer_id"),
        payload.get("customer_name", ""),
        payload.get("customer_address", ""),
        payload.get("customer_gstin", ""),
        payload.get("customer_state_code", "24"),
        payload.get("place_of_supply", "24-Gujarat"),
        float(payload.get("taxable_amount", 0) or 0),
        float(payload.get("cgst", 0) or 0),
        float(payload.get("sgst", 0) or 0),
        float(payload.get("igst", 0) or 0),
        float(payload.get("grand_total", 0) or 0),
        payload.get("status", "final"),
        payload.get("notes", ""),
        payload.get("pdf_url", ""),
        payload.get("sync_status", "synced"),
    )

    if existing:
        conn.execute(
            """
            UPDATE invoices
            SET invoice_no = ?, invoice_type = ?, date = ?, customer_id = ?, customer_name = ?,
                customer_address = ?, customer_gstin = ?, customer_state_code = ?, place_of_supply = ?,
                taxable_amount = ?, cgst = ?, sgst = ?, igst = ?, grand_total = ?, status = ?,
                notes = ?, pdf_url = ?, sync_status = ?, updated_at = datetime('now')
            WHERE id = ? AND company_id = ?
            """,
            (*header_values, existing["id"], company_id),
        )
        target_id = existing["id"]
    elif invoice_id:
        try:
            conn.execute(
                """
                INSERT INTO invoices (
                    id, company_id, invoice_no, invoice_type, date, customer_id, customer_name, customer_address,
                    customer_gstin, customer_state_code, place_of_supply, taxable_amount, cgst, sgst, igst,
                    grand_total, status, notes, pdf_url, sync_status, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now')
                )
                """,
                (invoice_id, company_id, *header_values),
            )
            target_id = invoice_id
        except sqlite3.IntegrityError:
            cur = conn.execute(
                """
                INSERT INTO invoices (
                    company_id, invoice_no, invoice_type, date, customer_id, customer_name, customer_address,
                    customer_gstin, customer_state_code, place_of_supply, taxable_amount, cgst, sgst, igst,
                    grand_total, status, notes, pdf_url, sync_status, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now')
                )
                """,
                (company_id, *header_values),
            )
            target_id = cur.lastrowid
    else:
        cur = conn.execute(
            """
            INSERT INTO invoices (
                company_id, invoice_no, invoice_type, date, customer_id, customer_name, customer_address,
                customer_gstin, customer_state_code, place_of_supply, taxable_amount, cgst, sgst, igst,
                grand_total, status, notes, pdf_url, sync_status, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now')
            )
            """,
            (company_id, *header_values),
        )
        target_id = cur.lastrowid

    if payload.get("items"):
        conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (target_id,))
        for item in payload.get("items", []):
            conn.execute(
                """
                INSERT INTO invoice_items (
                    invoice_id, product_id, product_name, hsn_code, qty, rate, taxable_amount,
                    gst_percent, cgst, sgst, igst, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    target_id,
                    item.get("product_id"),
                    item.get("product_name", ""),
                    item.get("hsn_code", ""),
                    float(item.get("qty", 1) or 1),
                    float(item.get("rate", 0) or 0),
                    float(item.get("taxable_amount", 0) or 0),
                    float(item.get("gst_percent", 18) or 18),
                    float(item.get("cgst", 0) or 0),
                    float(item.get("sgst", 0) or 0),
                    float(item.get("igst", 0) or 0),
                ),
            )

    return {"entity": "invoice", "status": "upserted", "id": target_id}


def _upsert_payment(conn, company_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    payment_id = payload.get("id")
    existing = None
    if payment_id:
        existing = conn.execute(
            "SELECT id, updated_at FROM payments WHERE id = ? AND company_id = ?",
            (payment_id, company_id),
        ).fetchone()

    if existing and not _is_incoming_newer(existing["updated_at"], payload.get("updated_at")):
        return {"entity": "payment", "status": "skipped", "id": existing["id"], "reason": "stale_update"}

    values = (
        payload.get("invoice_id"),
        float(payload.get("amount", 0) or 0),
        payload.get("payment_date", ""),
        payload.get("mode", "Cash"),
        payload.get("reference", ""),
    )

    if existing:
        conn.execute(
            """
            UPDATE payments
            SET invoice_id = ?, amount = ?, payment_date = ?, mode = ?, reference = ?, updated_at = datetime('now')
            WHERE id = ? AND company_id = ?
            """,
            (*values, existing["id"], company_id),
        )
        return {"entity": "payment", "status": "updated", "id": existing["id"]}

    if payment_id:
        try:
            conn.execute(
                """
                INSERT INTO payments (id, company_id, invoice_id, amount, payment_date, mode, reference, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (payment_id, company_id, *values),
            )
            return {"entity": "payment", "status": "created", "id": payment_id}
        except sqlite3.IntegrityError:
            cur = conn.execute(
                """
                INSERT INTO payments (company_id, invoice_id, amount, payment_date, mode, reference, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (company_id, *values),
            )
            return {"entity": "payment", "status": "created", "id": cur.lastrowid, "remapped_id": payment_id}

    cur = conn.execute(
        """
        INSERT INTO payments (company_id, invoice_id, amount, payment_date, mode, reference, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (company_id, *values),
    )
    return {"entity": "payment", "status": "created", "id": cur.lastrowid}


def _upsert_ledger(conn, company_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    ledger_id = payload.get("id")
    existing = None
    if ledger_id:
        existing = conn.execute(
            "SELECT id, updated_at FROM customer_ledger WHERE id = ? AND company_id = ?",
            (ledger_id, company_id),
        ).fetchone()

    if existing and not _is_incoming_newer(existing["updated_at"], payload.get("updated_at")):
        return {"entity": "ledger", "status": "skipped", "id": existing["id"], "reason": "stale_update"}

    values = (
        payload.get("customer_id"),
        payload.get("type", "credit"),
        float(payload.get("amount", 0) or 0),
        payload.get("description", ""),
        payload.get("reference_id", ""),
    )

    if existing:
        conn.execute(
            """
            UPDATE customer_ledger
            SET customer_id = ?, type = ?, amount = ?, description = ?, reference_id = ?, updated_at = datetime('now')
            WHERE id = ? AND company_id = ?
            """,
            (*values, existing["id"], company_id),
        )
        return {"entity": "ledger", "status": "updated", "id": existing["id"]}

    if ledger_id:
        try:
            conn.execute(
                """
                INSERT INTO customer_ledger (id, company_id, customer_id, type, amount, description, reference_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (ledger_id, company_id, *values),
            )
            return {"entity": "ledger", "status": "created", "id": ledger_id}
        except sqlite3.IntegrityError:
            cur = conn.execute(
                """
                INSERT INTO customer_ledger (company_id, customer_id, type, amount, description, reference_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (company_id, *values),
            )
            return {"entity": "ledger", "status": "created", "id": cur.lastrowid, "remapped_id": ledger_id}

    cur = conn.execute(
        """
        INSERT INTO customer_ledger (company_id, customer_id, type, amount, description, reference_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (company_id, *values),
    )
    return {"entity": "ledger", "status": "created", "id": cur.lastrowid}


def _delete_entity(conn, company_id: int, entity: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    entity_id = payload.get("id")
    if not entity_id:
        return {"entity": entity, "status": "skipped", "reason": "missing_id"}

    table_map = {
        "customer": "customers",
        "product": "products",
        "invoice": "invoices",
        "payment": "payments",
        "ledger": "customer_ledger",
    }
    table = table_map.get(entity)
    if not table:
        return {"entity": entity, "status": "skipped", "reason": "unknown_entity"}

    conn.execute(f"DELETE FROM {table} WHERE id = ? AND company_id = ?", (entity_id, company_id))
    return {"entity": entity, "status": "deleted", "id": entity_id}


def apply_push_payload(conn, company_id: int, changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []
    for change in changes:
        entity = (change.get("entity") or "").lower()
        action = (change.get("action") or "").lower()
        payload = change.get("payload") or {}

        if action == "delete":
            results.append(_delete_entity(conn, company_id, entity, payload))
            continue

        if entity == "customer":
            results.append(_upsert_customer(conn, company_id, payload))
        elif entity == "product":
            results.append(_upsert_product(conn, company_id, payload))
        elif entity == "invoice":
            results.append(_upsert_invoice(conn, company_id, payload))
        elif entity == "payment":
            results.append(_upsert_payment(conn, company_id, payload))
        elif entity in ("ledger", "customer_ledger"):
            results.append(_upsert_ledger(conn, company_id, payload))
        else:
            results.append({"entity": entity, "status": "skipped", "reason": "unsupported_entity"})

    return results


def build_pull_payload(conn, company_id: int, since: str = "") -> Dict[str, Any]:
    since_clause = ""
    params: List[Any] = [company_id]

    if since:
        since_clause = " AND datetime(updated_at) >= datetime(?)"
        params.append(since)

    def fetch(query: str, query_params: List[Any]) -> List[Dict[str, Any]]:
        return [dict(r) for r in conn.execute(query, query_params).fetchall()]

    customers = fetch(
        "SELECT * FROM customers WHERE company_id = ?" + since_clause + " ORDER BY id",
        params,
    )
    products = fetch(
        "SELECT * FROM products WHERE company_id = ?" + since_clause + " ORDER BY id",
        params,
    )
    invoices = fetch(
        "SELECT * FROM invoices WHERE company_id = ?" + since_clause + " ORDER BY id",
        params,
    )
    payments = fetch(
        "SELECT * FROM payments WHERE company_id = ?" + since_clause + " ORDER BY id",
        params,
    )
    ledger = fetch(
        "SELECT * FROM customer_ledger WHERE company_id = ?" + since_clause + " ORDER BY id",
        params,
    )

    invoice_ids = [inv["id"] for inv in invoices]
    items: List[Dict[str, Any]] = []
    if invoice_ids:
        placeholders = ",".join("?" for _ in invoice_ids)
        items = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM invoice_items WHERE invoice_id IN ({placeholders}) ORDER BY id",
                invoice_ids,
            ).fetchall()
        ]

    return {
        "server_time": utc_now_iso(),
        "customers": customers,
        "products": products,
        "invoices": invoices,
        "invoice_items": items,
        "payments": payments,
        "ledger": ledger,
    }
