from datetime import datetime
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


class TimestampMixin:
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CompanySetting(Base):
    __tablename__ = "company_settings"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, nullable=False, default=1, index=True)
    key = Column(String(128), nullable=False)
    value = Column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("company_id", "key", name="uq_company_settings_company_key"),)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    company_id = Column(Integer, nullable=False, default=1, index=True)


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, nullable=False, default=1, index=True)
    name = Column(String(255), nullable=False)
    address = Column(Text)
    gstin = Column(String(32))
    state_code = Column(String(8), default="24")
    phone = Column(String(32))
    email = Column(String(255))


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, nullable=False, default=1, index=True)
    name = Column(String(255), nullable=False)
    hsn_code = Column(String(64))
    default_rate = Column(Float, default=0)
    gst_percent = Column(Float, default=18)
    unit = Column(String(16), default="PCS")


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, nullable=False, default=1, index=True)
    invoice_no = Column(String(128), nullable=False)
    invoice_type = Column(String(64), default="TAX INVOICE")
    date = Column(String(16), nullable=False)

    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    customer_name = Column(String(255))
    customer_address = Column(Text)
    customer_gstin = Column(String(32))
    customer_state_code = Column(String(8), default="24")
    place_of_supply = Column(String(64), default="24-Gujarat")

    taxable_amount = Column(Float, default=0)
    cgst = Column(Float, default=0)
    sgst = Column(Float, default=0)
    igst = Column(Float, default=0)
    grand_total = Column(Float, default=0)

    status = Column(String(32), default="final")
    notes = Column(Text)
    pdf_url = Column(Text)
    sync_status = Column(String(32), default="pending")

    items = relationship("InvoiceItem", cascade="all, delete-orphan", back_populates="invoice")

    __table_args__ = (UniqueConstraint("company_id", "invoice_no", name="uq_invoices_company_invoice_no"),)


class InvoiceItem(Base, TimestampMixin):
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    product_name = Column(String(255))
    hsn_code = Column(String(64))
    qty = Column(Float, default=1)
    rate = Column(Float, default=0)
    taxable_amount = Column(Float, default=0)
    gst_percent = Column(Float, default=18)
    cgst = Column(Float, default=0)
    sgst = Column(Float, default=0)
    igst = Column(Float, default=0)

    invoice = relationship("Invoice", back_populates="items")


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, nullable=False, default=1, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True)
    amount = Column(Float, nullable=False)
    payment_date = Column(String(16))
    mode = Column(String(64), default="Cash")
    reference = Column(String(255))


class CustomerLedger(Base, TimestampMixin):
    __tablename__ = "customer_ledger"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, nullable=False, default=1, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(16), nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(Text)
    reference_id = Column(String(255))

    __table_args__ = (CheckConstraint("type IN ('credit','debit')", name="ck_ledger_type"),)


class SyncQueue(Base, TimestampMixin):
    __tablename__ = "sync_queue"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, nullable=False, default=1, index=True)
    entity = Column(String(64), nullable=False)
    action = Column(String(16), nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    error = Column(Text)
    last_attempt_at = Column(DateTime)


class InvoiceNumberBlock(Base, TimestampMixin):
    __tablename__ = "invoice_number_blocks"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, nullable=False, default=1, index=True)
    year = Column(Integer, nullable=False, index=True)
    start_no = Column(Integer, nullable=False)
    end_no = Column(Integer, nullable=False)
    next_no = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="active")

    __table_args__ = (
        CheckConstraint("start_no <= end_no", name="ck_invoice_blocks_range"),
    )
