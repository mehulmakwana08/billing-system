import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app as billing_app  # noqa: E402


@pytest.fixture()
def app_module(monkeypatch, tmp_path):
    db_path = tmp_path / "billing_test.db"
    bills_dir = tmp_path / "bills"
    bills_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(billing_app, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(billing_app, "BILLS_DIR", str(bills_dir), raising=False)
    monkeypatch.setattr(billing_app, "AUTH_REQUIRED", False, raising=False)

    billing_app.init_db()
    return billing_app


@pytest.fixture()
def client(app_module):
    with app_module.app.test_client() as test_client:
        yield test_client
