import os
import tempfile
from pathlib import Path

import pdf_generator


def test_generate_pdf_local_uses_billing_bills_dir(monkeypatch, tmp_path):
    bills_dir = tmp_path / "custom-bills"
    monkeypatch.setenv("BILLING_BILLS_DIR", str(bills_dir))
    monkeypatch.delenv("VERCEL", raising=False)

    invoice_data = {
        "invoice_no": "GT/2026/00031",
        "date": "2026-04-02",
        "customer_name": "Test Customer",
        "customer_address": "Test Address",
        "customer_gstin": "24ABCDE1234F1Z5",
        "place_of_supply": "24-Gujarat",
        "items": [
            {
                "product_name": "Test Product",
                "hsn_code": "3906",
                "qty": 1,
                "rate": 100,
                "taxable_amount": 100,
                "gst_percent": 18,
                "cgst": 9,
                "sgst": 9,
                "igst": 0,
            }
        ],
        "grand_total": 118,
        "amount_words": "One Hundred Eighteen Rupees",
        "gst_words": "Eighteen Rupees",
        "company": {
            "name": "Test Company",
            "address": "Test",
            "gstin": "24AAAAA0000A1Z5",
            "phone": "",
            "terms": "1. Test",
        },
    }

    pdf_path = pdf_generator.generate_pdf(invoice_data, mode="local")

    assert Path(pdf_path).exists()
    assert str(Path(pdf_path).parent) == str(bills_dir)


def test_local_bills_dir_falls_back_when_configured_path_is_not_writable(monkeypatch):
    configured_dir = os.path.join("readonly", "bills")
    fallback_dir = os.path.join(tempfile.gettempdir(), "bills")
    monkeypatch.setenv("BILLING_BILLS_DIR", configured_dir)

    def _fake_is_writable(path):
        return os.path.abspath(path) == os.path.abspath(fallback_dir)

    monkeypatch.setattr(pdf_generator, "_is_writable_dir", _fake_is_writable)

    resolved = pdf_generator._local_bills_dir()

    assert os.path.abspath(resolved) == os.path.abspath(fallback_dir)
