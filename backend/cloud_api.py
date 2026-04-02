from datetime import datetime, timezone
import os
import re
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import AuthError, decode_token, hash_password, issue_token, verify_password
from database import SessionLocal, engine
from models import (
    Base,
    CompanySetting,
    Customer,
    CustomerLedger,
    Invoice,
    InvoiceItem,
    InvoiceNumberBlock,
    Payment,
    Product,
    User,
)


load_dotenv()


DEFAULT_CORS_ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
    'http://localhost:5000',
    'http://127.0.0.1:5000',
    'null',
]


def _load_allowed_cors_origins() -> List[str]:
    configured = os.getenv('CORS_ALLOWED_ORIGINS', '')
    values = [origin.strip() for origin in configured.split(',') if origin.strip()]
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


app = FastAPI(title="Billing Cloud API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


Base.metadata.create_all(bind=engine)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH)
    company_id: int = 1

    @field_validator('password')
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if not re.search(r'[A-Z]', value):
            raise ValueError('password must include at least one uppercase letter')
        if not re.search(r'[a-z]', value):
            raise ValueError('password must include at least one lowercase letter')
        if not re.search(r'\d', value):
            raise ValueError('password must include at least one digit')
        if not re.search(r'[^A-Za-z0-9]', value):
            raise ValueError('password must include at least one special character')
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class CompanyPayload(BaseModel):
    data: Dict[str, Any]


class SyncPushRequest(BaseModel):
    changes: List[Dict[str, Any]] = Field(default_factory=list)


class NumberBlockRequest(BaseModel):
    size: int = Field(default=50, ge=1, le=5000)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_auth(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return decode_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def ensure_setting(db: Session, company_id: int, key: str, value: str) -> None:
    row = (
        db.query(CompanySetting)
        .filter(CompanySetting.company_id == company_id, CompanySetting.key == key)
        .one_or_none()
    )
    if row:
        row.value = value
        return
    db.add(CompanySetting(company_id=company_id, key=key, value=value))


def get_setting_map(db: Session, company_id: int) -> Dict[str, str]:
    rows = db.query(CompanySetting).filter(CompanySetting.company_id == company_id).all()
    return {r.key: r.value for r in rows}


def _normalize_invoice_prefix(prefix: str) -> str:
    value = (prefix or 'GT/').strip()
    if not value:
        value = 'GT/'
    return value if value.endswith('/') else f"{value}/"


def _validate_company_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    unknown_keys = sorted([k for k in payload.keys() if k not in ALLOWED_COMPANY_SETTING_KEYS])
    if unknown_keys:
        raise HTTPException(status_code=400, detail=f"unsupported company setting keys: {', '.join(unknown_keys)}")

    normalized: Dict[str, str] = {}
    for key, value in payload.items():
        text = str(value if value is not None else '').strip()
        if key == 'state_code' and text and not re.fullmatch(r'\d{2}', text):
            raise HTTPException(status_code=400, detail='state_code must be a 2-digit code')
        if key == 'next_invoice_no':
            try:
                next_no = int(text)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail='next_invoice_no must be a positive integer')
            if next_no < 1:
                raise HTTPException(status_code=400, detail='next_invoice_no must be a positive integer')
            text = str(next_no)
        if key == 'invoice_prefix':
            text = _normalize_invoice_prefix(text)
        normalized[key] = text

    return normalized


def format_invoice_number(counter: int, prefix: str = 'GT/') -> str:
    year = datetime.utcnow().year
    return f"{_normalize_invoice_prefix(prefix)}{year}/{int(counter):05d}"


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _to_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_incoming_newer(existing_ts: Optional[datetime], incoming_ts: Any) -> bool:
    incoming = _parse_datetime(incoming_ts)
    if incoming is None:
        return True
    if existing_ts is None:
        return True
    existing = existing_ts
    if existing.tzinfo is not None:
        existing = existing.astimezone(timezone.utc).replace(tzinfo=None)
    return incoming >= existing


def _id_available(db: Session, model: Any, row_id: Optional[int]) -> bool:
    if row_id is None:
        return True
    return db.query(model.id).filter(model.id == int(row_id)).one_or_none() is None


def _upsert_customer(db: Session, company_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    incoming_id = payload.get("id")
    existing = None
    if incoming_id is not None:
        existing = (
            db.query(Customer)
            .filter(Customer.id == int(incoming_id), Customer.company_id == company_id)
            .one_or_none()
        )

    if existing and not _is_incoming_newer(existing.updated_at, payload.get("updated_at")):
        return {"entity": "customer", "status": "skipped", "id": existing.id, "reason": "stale_update"}

    incoming_updated = _parse_datetime(payload.get("updated_at")) or datetime.utcnow()
    incoming_created = _parse_datetime(payload.get("created_at")) or incoming_updated

    if existing:
        existing.name = payload.get("name", existing.name or "")
        existing.address = payload.get("address", existing.address or "")
        existing.gstin = payload.get("gstin", existing.gstin or "")
        existing.state_code = payload.get("state_code", existing.state_code or "24")
        existing.phone = payload.get("phone", existing.phone or "")
        existing.email = payload.get("email", existing.email or "")
        existing.updated_at = incoming_updated
        db.flush()
        return {"entity": "customer", "status": "updated", "id": existing.id}

    use_incoming_id = incoming_id is not None and _id_available(db, Customer, int(incoming_id))
    row = Customer(
        id=int(incoming_id) if use_incoming_id else None,
        company_id=company_id,
        name=payload.get("name", ""),
        address=payload.get("address", ""),
        gstin=payload.get("gstin", ""),
        state_code=payload.get("state_code", "24"),
        phone=payload.get("phone", ""),
        email=payload.get("email", ""),
        created_at=incoming_created,
        updated_at=incoming_updated,
    )
    db.add(row)
    db.flush()

    result = {"entity": "customer", "status": "created", "id": row.id}
    if incoming_id is not None and not use_incoming_id:
        result["remapped_id"] = int(incoming_id)
    return result


def _upsert_product(db: Session, company_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    incoming_id = payload.get("id")
    existing = None
    if incoming_id is not None:
        existing = (
            db.query(Product)
            .filter(Product.id == int(incoming_id), Product.company_id == company_id)
            .one_or_none()
        )

    if existing and not _is_incoming_newer(existing.updated_at, payload.get("updated_at")):
        return {"entity": "product", "status": "skipped", "id": existing.id, "reason": "stale_update"}

    incoming_updated = _parse_datetime(payload.get("updated_at")) or datetime.utcnow()
    incoming_created = _parse_datetime(payload.get("created_at")) or incoming_updated

    if existing:
        existing.name = payload.get("name", existing.name or "")
        existing.hsn_code = payload.get("hsn_code", existing.hsn_code or "")
        existing.default_rate = _to_float(payload.get("default_rate"), existing.default_rate or 0.0)
        existing.gst_percent = _to_float(payload.get("gst_percent"), existing.gst_percent or 18.0)
        existing.unit = payload.get("unit", existing.unit or "PCS")
        existing.updated_at = incoming_updated
        db.flush()
        return {"entity": "product", "status": "updated", "id": existing.id}

    use_incoming_id = incoming_id is not None and _id_available(db, Product, int(incoming_id))
    row = Product(
        id=int(incoming_id) if use_incoming_id else None,
        company_id=company_id,
        name=payload.get("name", ""),
        hsn_code=payload.get("hsn_code", ""),
        default_rate=_to_float(payload.get("default_rate"), 0.0),
        gst_percent=_to_float(payload.get("gst_percent"), 18.0),
        unit=payload.get("unit", "PCS"),
        created_at=incoming_created,
        updated_at=incoming_updated,
    )
    db.add(row)
    db.flush()

    result = {"entity": "product", "status": "created", "id": row.id}
    if incoming_id is not None and not use_incoming_id:
        result["remapped_id"] = int(incoming_id)
    return result


def _upsert_invoice(db: Session, company_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    incoming_id = payload.get("id")
    existing = None
    if incoming_id is not None:
        existing = (
            db.query(Invoice)
            .filter(Invoice.id == int(incoming_id), Invoice.company_id == company_id)
            .one_or_none()
        )

    invoice_no = payload.get("invoice_no") or (existing.invoice_no if existing else "")
    if not existing and invoice_no:
        existing = (
            db.query(Invoice)
            .filter(Invoice.company_id == company_id, Invoice.invoice_no == invoice_no)
            .one_or_none()
        )

    if existing and not _is_incoming_newer(existing.updated_at, payload.get("updated_at")):
        return {"entity": "invoice", "status": "skipped", "id": existing.id, "reason": "stale_update"}

    if not invoice_no:
        return {"entity": "invoice", "status": "skipped", "reason": "missing_invoice_no"}

    incoming_updated = _parse_datetime(payload.get("updated_at")) or datetime.utcnow()
    incoming_created = _parse_datetime(payload.get("created_at")) or incoming_updated
    invoice_date = payload.get("date") or (existing.date if existing else datetime.utcnow().date().isoformat())

    if existing:
        existing.invoice_no = invoice_no
        existing.invoice_type = payload.get("invoice_type", existing.invoice_type or "TAX INVOICE")
        existing.date = invoice_date
        existing.customer_id = payload.get("customer_id", existing.customer_id)
        existing.customer_name = payload.get("customer_name", existing.customer_name or "")
        existing.customer_address = payload.get("customer_address", existing.customer_address or "")
        existing.customer_gstin = payload.get("customer_gstin", existing.customer_gstin or "")
        existing.customer_state_code = payload.get("customer_state_code", existing.customer_state_code or "24")
        existing.place_of_supply = payload.get("place_of_supply", existing.place_of_supply or "24-Gujarat")
        existing.taxable_amount = _to_float(payload.get("taxable_amount"), existing.taxable_amount or 0.0)
        existing.cgst = _to_float(payload.get("cgst"), existing.cgst or 0.0)
        existing.sgst = _to_float(payload.get("sgst"), existing.sgst or 0.0)
        existing.igst = _to_float(payload.get("igst"), existing.igst or 0.0)
        existing.grand_total = _to_float(payload.get("grand_total"), existing.grand_total or 0.0)
        existing.status = payload.get("status", existing.status or "final")
        existing.notes = payload.get("notes", existing.notes or "")
        existing.pdf_url = payload.get("pdf_url", existing.pdf_url or "")
        existing.sync_status = payload.get("sync_status", existing.sync_status or "synced")
        existing.updated_at = incoming_updated
        target = existing
        result = {"entity": "invoice", "status": "updated", "id": target.id}
    else:
        use_incoming_id = incoming_id is not None and _id_available(db, Invoice, int(incoming_id))
        target = Invoice(
            id=int(incoming_id) if use_incoming_id else None,
            company_id=company_id,
            invoice_no=invoice_no,
            invoice_type=payload.get("invoice_type", "TAX INVOICE"),
            date=invoice_date,
            customer_id=payload.get("customer_id"),
            customer_name=payload.get("customer_name", ""),
            customer_address=payload.get("customer_address", ""),
            customer_gstin=payload.get("customer_gstin", ""),
            customer_state_code=payload.get("customer_state_code", "24"),
            place_of_supply=payload.get("place_of_supply", "24-Gujarat"),
            taxable_amount=_to_float(payload.get("taxable_amount"), 0.0),
            cgst=_to_float(payload.get("cgst"), 0.0),
            sgst=_to_float(payload.get("sgst"), 0.0),
            igst=_to_float(payload.get("igst"), 0.0),
            grand_total=_to_float(payload.get("grand_total"), 0.0),
            status=payload.get("status", "final"),
            notes=payload.get("notes", ""),
            pdf_url=payload.get("pdf_url", ""),
            sync_status=payload.get("sync_status", "synced"),
            created_at=incoming_created,
            updated_at=incoming_updated,
        )
        db.add(target)
        db.flush()
        result = {"entity": "invoice", "status": "created", "id": target.id}
        if incoming_id is not None and not use_incoming_id:
            result["remapped_id"] = int(incoming_id)

    if "items" in payload:
        db.query(InvoiceItem).filter(InvoiceItem.invoice_id == target.id).delete(synchronize_session=False)
        for item in payload.get("items") or []:
            item_updated = _parse_datetime(item.get("updated_at")) or incoming_updated
            item_created = _parse_datetime(item.get("created_at")) or item_updated
            db.add(
                InvoiceItem(
                    invoice_id=target.id,
                    product_id=item.get("product_id"),
                    product_name=item.get("product_name", ""),
                    hsn_code=item.get("hsn_code", ""),
                    qty=_to_float(item.get("qty"), 1.0),
                    rate=_to_float(item.get("rate"), 0.0),
                    taxable_amount=_to_float(item.get("taxable_amount"), 0.0),
                    gst_percent=_to_float(item.get("gst_percent"), 18.0),
                    cgst=_to_float(item.get("cgst"), 0.0),
                    sgst=_to_float(item.get("sgst"), 0.0),
                    igst=_to_float(item.get("igst"), 0.0),
                    created_at=item_created,
                    updated_at=item_updated,
                )
            )
        db.flush()

    return result


def _upsert_payment(db: Session, company_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    incoming_id = payload.get("id")
    existing = None
    if incoming_id is not None:
        existing = (
            db.query(Payment)
            .filter(Payment.id == int(incoming_id), Payment.company_id == company_id)
            .one_or_none()
        )

    if existing and not _is_incoming_newer(existing.updated_at, payload.get("updated_at")):
        return {"entity": "payment", "status": "skipped", "id": existing.id, "reason": "stale_update"}

    incoming_updated = _parse_datetime(payload.get("updated_at")) or datetime.utcnow()
    incoming_created = _parse_datetime(payload.get("created_at")) or incoming_updated

    if existing:
        existing.invoice_id = payload.get("invoice_id", existing.invoice_id)
        existing.amount = _to_float(payload.get("amount"), existing.amount or 0.0)
        existing.payment_date = payload.get("payment_date", existing.payment_date or "")
        existing.mode = payload.get("mode", existing.mode or "Cash")
        existing.reference = payload.get("reference", existing.reference or "")
        existing.updated_at = incoming_updated
        db.flush()
        return {"entity": "payment", "status": "updated", "id": existing.id}

    use_incoming_id = incoming_id is not None and _id_available(db, Payment, int(incoming_id))
    row = Payment(
        id=int(incoming_id) if use_incoming_id else None,
        company_id=company_id,
        invoice_id=payload.get("invoice_id"),
        amount=_to_float(payload.get("amount"), 0.0),
        payment_date=payload.get("payment_date", ""),
        mode=payload.get("mode", "Cash"),
        reference=payload.get("reference", ""),
        created_at=incoming_created,
        updated_at=incoming_updated,
    )
    db.add(row)
    db.flush()

    result = {"entity": "payment", "status": "created", "id": row.id}
    if incoming_id is not None and not use_incoming_id:
        result["remapped_id"] = int(incoming_id)
    return result


def _upsert_ledger(db: Session, company_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    incoming_id = payload.get("id")
    existing = None
    if incoming_id is not None:
        existing = (
            db.query(CustomerLedger)
            .filter(CustomerLedger.id == int(incoming_id), CustomerLedger.company_id == company_id)
            .one_or_none()
        )

    if existing and not _is_incoming_newer(existing.updated_at, payload.get("updated_at")):
        return {"entity": "ledger", "status": "skipped", "id": existing.id, "reason": "stale_update"}

    incoming_updated = _parse_datetime(payload.get("updated_at")) or datetime.utcnow()
    incoming_created = _parse_datetime(payload.get("created_at")) or incoming_updated

    if existing:
        existing.customer_id = payload.get("customer_id", existing.customer_id)
        existing.type = payload.get("type", existing.type or "credit")
        existing.amount = _to_float(payload.get("amount"), existing.amount or 0.0)
        existing.description = payload.get("description", existing.description or "")
        existing.reference_id = payload.get("reference_id", existing.reference_id or "")
        existing.updated_at = incoming_updated
        db.flush()
        return {"entity": "ledger", "status": "updated", "id": existing.id}

    use_incoming_id = incoming_id is not None and _id_available(db, CustomerLedger, int(incoming_id))
    row = CustomerLedger(
        id=int(incoming_id) if use_incoming_id else None,
        company_id=company_id,
        customer_id=payload.get("customer_id"),
        type=payload.get("type", "credit"),
        amount=_to_float(payload.get("amount"), 0.0),
        description=payload.get("description", ""),
        reference_id=payload.get("reference_id", ""),
        created_at=incoming_created,
        updated_at=incoming_updated,
    )
    db.add(row)
    db.flush()

    result = {"entity": "ledger", "status": "created", "id": row.id}
    if incoming_id is not None and not use_incoming_id:
        result["remapped_id"] = int(incoming_id)
    return result


def _delete_entity(db: Session, company_id: int, entity: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    row_id = payload.get("id")
    if row_id is None:
        return {"entity": entity, "status": "skipped", "reason": "missing_id"}

    if entity == "customer":
        row = db.query(Customer).filter(Customer.id == int(row_id), Customer.company_id == company_id).one_or_none()
    elif entity == "product":
        row = db.query(Product).filter(Product.id == int(row_id), Product.company_id == company_id).one_or_none()
    elif entity == "invoice":
        row = db.query(Invoice).filter(Invoice.id == int(row_id), Invoice.company_id == company_id).one_or_none()
    elif entity == "payment":
        row = db.query(Payment).filter(Payment.id == int(row_id), Payment.company_id == company_id).one_or_none()
    elif entity in ("ledger", "customer_ledger"):
        row = (
            db.query(CustomerLedger)
            .filter(CustomerLedger.id == int(row_id), CustomerLedger.company_id == company_id)
            .one_or_none()
        )
        entity = "ledger"
    else:
        return {"entity": entity, "status": "skipped", "reason": "unsupported_entity"}

    if row is None:
        return {"entity": entity, "status": "skipped", "reason": "not_found", "id": int(row_id)}

    db.delete(row)
    db.flush()
    return {"entity": entity, "status": "deleted", "id": int(row_id)}


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "cloud-api"}


@app.post("/api/auth/register")
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email.lower()).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="email already registered")

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        company_id=payload.company_id,
    )
    db.add(user)
    db.flush()

    defaults = {
        "name": "My Company",
        "state_code": "24",
        "next_invoice_no": "1",
        "invoice_prefix": "GT/",
    }
    for key, value in defaults.items():
        ensure_setting(db, payload.company_id, key, value)

    db.commit()
    token = issue_token(user.id, user.company_id, user.email)
    return {"token": token, "user": {"id": user.id, "email": user.email, "company_id": user.company_id}}


@app.post("/api/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    token = issue_token(user.id, user.company_id, user.email)
    return {"token": token, "user": {"id": user.id, "email": user.email, "company_id": user.company_id}}


@app.get("/api/auth/me")
def me(claims: Dict[str, Any] = Depends(parse_auth)):
    return {
        "id": int(claims.get("sub")),
        "email": claims.get("email"),
        "company_id": int(claims.get("company_id", 1)),
    }


@app.get("/api/company")
def get_company(claims: Dict[str, Any] = Depends(parse_auth), db: Session = Depends(get_db)):
    company_id = int(claims.get("company_id", 1))
    return get_setting_map(db, company_id)


@app.post("/api/company")
def update_company(
    payload: Dict[str, Any], claims: Dict[str, Any] = Depends(parse_auth), db: Session = Depends(get_db)
):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='company settings payload must be an object')

    company_id = int(claims.get("company_id", 1))
    normalized = _validate_company_payload(payload)
    for key, value in normalized.items():
        ensure_setting(db, company_id, key, value)
    db.commit()
    return {"success": True}


@app.get("/api/invoices/next-number")
def next_invoice_number(claims: Dict[str, Any] = Depends(parse_auth), db: Session = Depends(get_db)):
    company_id = int(claims.get("company_id", 1))
    settings = get_setting_map(db, company_id)
    next_no = int(settings.get("next_invoice_no", "1") or 1)
    prefix = settings.get('invoice_prefix', 'GT/')
    return {"invoice_no": format_invoice_number(next_no, prefix)}


@app.post("/api/invoices/reserve-number-block")
def reserve_number_block(
    payload: NumberBlockRequest,
    claims: Dict[str, Any] = Depends(parse_auth),
    db: Session = Depends(get_db),
):
    company_id = int(claims.get("company_id", 1))
    year = datetime.utcnow().year

    settings = get_setting_map(db, company_id)
    next_no = int(settings.get("next_invoice_no", "1") or 1)
    prefix = settings.get('invoice_prefix', 'GT/')

    max_existing = (
        db.query(func.max(InvoiceNumberBlock.end_no))
        .filter(InvoiceNumberBlock.company_id == company_id, InvoiceNumberBlock.year == year)
        .scalar()
        or 0
    )
    start_no = max(next_no, int(max_existing) + 1)
    end_no = start_no + payload.size - 1

    block = InvoiceNumberBlock(
        company_id=company_id,
        year=year,
        start_no=start_no,
        end_no=end_no,
        next_no=start_no,
        status="active",
    )
    db.add(block)
    ensure_setting(db, company_id, "next_invoice_no", str(end_no + 1))
    db.commit()

    return {
        "year": year,
        "start_no": start_no,
        "end_no": end_no,
        "format_preview": format_invoice_number(start_no, prefix),
    }


@app.get("/api/customers")
def list_customers(
    claims: Dict[str, Any] = Depends(parse_auth),
    db: Session = Depends(get_db),
    search: str = Query(default=""),
):
    company_id = int(claims.get("company_id", 1))
    query = db.query(Customer).filter(Customer.company_id == company_id)
    if search:
        query = query.filter(Customer.name.ilike(f"%{search}%"))
    rows = query.order_by(Customer.name.asc()).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "address": row.address,
            "gstin": row.gstin,
            "state_code": row.state_code,
            "phone": row.phone,
            "email": row.email,
        }
        for row in rows
    ]


@app.post("/api/customers")
def create_customer(payload: Dict[str, Any], claims: Dict[str, Any] = Depends(parse_auth), db: Session = Depends(get_db)):
    company_id = int(claims.get("company_id", 1))
    customer = Customer(
        company_id=company_id,
        name=payload.get("name", ""),
        address=payload.get("address", ""),
        gstin=payload.get("gstin", ""),
        state_code=payload.get("state_code", "24"),
        phone=payload.get("phone", ""),
        email=payload.get("email", ""),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return {
        "id": customer.id,
        "name": customer.name,
        "address": customer.address,
        "gstin": customer.gstin,
        "state_code": customer.state_code,
        "phone": customer.phone,
        "email": customer.email,
    }


@app.get("/api/sync/pull")
def sync_pull(
    claims: Dict[str, Any] = Depends(parse_auth),
    db: Session = Depends(get_db),
    since: str = Query(default=""),
):
    company_id = int(claims.get("company_id", 1))
    customer_query = db.query(Customer).filter(Customer.company_id == company_id)
    product_query = db.query(Product).filter(Product.company_id == company_id)
    invoice_query = db.query(Invoice).filter(Invoice.company_id == company_id)

    if since:
        customer_query = customer_query.filter(Customer.updated_at >= since)
        product_query = product_query.filter(Product.updated_at >= since)
        invoice_query = invoice_query.filter(Invoice.updated_at >= since)

    return {
        "server_time": datetime.utcnow().isoformat() + "Z",
        "customers": [
            {
                "id": c.id,
                "name": c.name,
                "address": c.address,
                "gstin": c.gstin,
                "state_code": c.state_code,
                "phone": c.phone,
                "email": c.email,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in customer_query.all()
        ],
        "products": [
            {
                "id": p.id,
                "name": p.name,
                "hsn_code": p.hsn_code,
                "default_rate": p.default_rate,
                "gst_percent": p.gst_percent,
                "unit": p.unit,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in product_query.all()
        ],
        "invoices": [
            {
                "id": i.id,
                "invoice_no": i.invoice_no,
                "date": i.date,
                "customer_id": i.customer_id,
                "customer_name": i.customer_name,
                "grand_total": i.grand_total,
                "pdf_url": i.pdf_url,
                "sync_status": i.sync_status,
                "updated_at": i.updated_at.isoformat() if i.updated_at else None,
            }
            for i in invoice_query.all()
        ],
    }


@app.post("/api/sync/push")
def sync_push(
    payload: SyncPushRequest,
    claims: Dict[str, Any] = Depends(parse_auth),
    db: Session = Depends(get_db),
):
    company_id = int(claims.get("company_id", 1))
    results: List[Dict[str, Any]] = []

    try:
        for change in payload.changes:
            entity = (change.get("entity") or "").lower()
            action = (change.get("action") or "update").lower()
            row_payload = change.get("payload") or {}

            if action == "delete":
                results.append(_delete_entity(db, company_id, entity, row_payload))
                continue

            if entity == "customer":
                results.append(_upsert_customer(db, company_id, row_payload))
            elif entity == "product":
                results.append(_upsert_product(db, company_id, row_payload))
            elif entity == "invoice":
                results.append(_upsert_invoice(db, company_id, row_payload))
            elif entity == "payment":
                results.append(_upsert_payment(db, company_id, row_payload))
            elif entity in ("ledger", "customer_ledger"):
                results.append(_upsert_ledger(db, company_id, row_payload))
            else:
                results.append({"entity": entity, "status": "skipped", "reason": "unsupported_entity"})

        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"sync push failed: {exc}") from exc

    return {
        "success": True,
        "accepted_changes": len(payload.changes),
        "results": results,
        "server_time": datetime.utcnow().isoformat() + "Z",
    }
