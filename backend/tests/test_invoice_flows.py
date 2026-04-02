import sqlite3
from datetime import datetime


def _invoice_payload():
    return {
        "date": "2026-03-30",
        "customer_id": 1,
        "customer_state_code": "24",
        "customer_state_name": "Gujarat",
        "items": [
            {
                "product_id": 1,
                "product_name": "Bangles Acrylic Paip",
                "hsn_code": "3906",
                "qty": 2,
                "rate": 100,
                "gst_percent": 18,
            }
        ],
    }


def _table_count(db_path, table_name):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        return int(row[0])
    finally:
        conn.close()


def test_invoice_pdf_failure_rolls_back(client, app_module, monkeypatch):
    def _explode(_invoice_data, mode="local"):
        raise RuntimeError("pdf failed")

    monkeypatch.setattr(app_module, "generate_pdf", _explode)

    response = client.post("/api/invoices", json=_invoice_payload())

    assert response.status_code == 502
    assert _table_count(app_module.DB_PATH, "invoices") == 0
    assert _table_count(app_module.DB_PATH, "invoice_items") == 0
    assert _table_count(app_module.DB_PATH, "customer_ledger") == 0


def test_delete_invoice_with_payments_returns_409(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "generate_pdf", lambda *_args, **_kwargs: "local://invoice.pdf")

    created = client.post("/api/invoices", json=_invoice_payload())
    assert created.status_code == 201
    invoice_id = created.get_json()["id"]

    payment = client.post(
        "/api/payments",
        json={
            "invoice_id": invoice_id,
            "amount": 50,
            "payment_date": "2026-03-30",
            "mode": "Cash",
            "reference": "SMOKE-1",
        },
    )
    assert payment.status_code == 201

    deleted = client.delete(f"/api/invoices/{invoice_id}")
    assert deleted.status_code == 409

    fetch = client.get(f"/api/invoices/{invoice_id}")
    assert fetch.status_code == 200


def test_add_payment_validation(client):
    missing_invoice = client.post("/api/payments", json={"amount": 100})
    assert missing_invoice.status_code == 400

    invalid_invoice = client.post(
        "/api/payments",
        json={"invoice_id": 999999, "amount": 100},
    )
    assert invalid_invoice.status_code == 404

    invalid_amount = client.post(
        "/api/payments",
        json={"invoice_id": 1, "amount": "bad-number"},
    )
    assert invalid_amount.status_code == 400


def test_next_invoice_number_uses_counter_and_prefix(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "generate_pdf", lambda *_args, **_kwargs: "local://invoice.pdf")

    set_prefix = client.post("/api/company", json={"invoice_prefix": "API"})
    assert set_prefix.status_code == 200

    next_before = client.get("/api/invoices/next-number")
    assert next_before.status_code == 200
    invoice_no_before = next_before.get_json()["invoice_no"]

    current_year = datetime.utcnow().year
    assert invoice_no_before.startswith(f"API/{current_year}/")

    created = client.post("/api/invoices", json=_invoice_payload())
    assert created.status_code == 201

    next_after = client.get("/api/invoices/next-number")
    assert next_after.status_code == 200
    invoice_no_after = next_after.get_json()["invoice_no"]

    before_counter = int(invoice_no_before.split("/")[-1])
    after_counter = int(invoice_no_after.split("/")[-1])
    assert after_counter == before_counter + 1


def test_register_disabled_in_login_only_mode(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "strong@example.com", "password": "Strong#1234", "company_id": 1},
    )
    assert response.status_code == 403


def test_register_policy_status_is_consistent_when_auth_required(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "AUTH_REQUIRED", True, raising=False)
    monkeypatch.setattr(app_module, "ALLOW_SELF_REGISTER", False, raising=False)

    register_response = client.post(
        "/api/auth/register",
        json={"email": "blocked@example.com", "password": "Strong#1234", "company_id": 1},
    )
    assert register_response.status_code == 403
    assert "self registration is disabled" in register_response.get_json()["error"]

    protected_response = client.get("/api/customers")
    assert protected_response.status_code == 401


def test_legacy_sync_endpoints_are_retired(client):
    assert client.get("/api/sync/pull").status_code == 410
    assert client.post("/api/sync/push", json={"changes": []}).status_code == 410
    assert client.post("/api/sync/queue/1", json={"status": "synced"}).status_code == 410


def test_company_update_rejects_unknown_keys(client):
    response = client.post(
        "/api/company",
        json={"name": "Acme", "unsupported_key": "value"},
    )
    assert response.status_code == 400
    assert "unsupported company setting keys" in response.get_json()["error"]


def test_list_invoices_rejects_invalid_date_filters(client):
    bad_format = client.get("/api/invoices?start_date=2026/04/01")
    assert bad_format.status_code == 400

    bad_range = client.get("/api/invoices?start_date=2026-04-02&end_date=2026-04-01")
    assert bad_range.status_code == 400


def test_reports_reject_invalid_period_params(client):
    monthly_bad = client.get("/api/reports/monthly?period_type=date&start_date=2026-04-05&end_date=2026-04-01")
    assert monthly_bad.status_code == 400

    gstr1_bad = client.get("/api/reports/gstr1?period_type=date&start_date=bad&end_date=2026-04-01")
    assert gstr1_bad.status_code == 400
