# models.py
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    BigInteger,
    Integer,
    String,
    Text,
    Date,
    Numeric,
    CheckConstraint,
    UniqueConstraint,
    Index,
    ForeignKey,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property

from db import Base


class Company(Base):
    __tablename__ = "company"
    __table_args__ = (
        UniqueConstraint("inn_kpp", name="ak_company__inn_kpp"),
    )

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    inn_kpp: Mapped[Optional[str]] = mapped_column(String(20))
    address: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(String(30))
    email: Mapped[Optional[str]] = mapped_column(String(254))
    contact_name: Mapped[Optional[str]] = mapped_column(String(100))

    invoices: Mapped[List["Invoice"]] = relationship(
        back_populates="buyer_company", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Company {self.company_id} {self.name!r}>"


class Product(Base):
    __tablename__ = "product"

    sku: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="шт")
    weight: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3))
    shelf_life_days: Mapped[Optional[int]] = mapped_column(Integer)
    storage_conditions: Mapped[Optional[str]] = mapped_column(Text)

    invoice_lines: Mapped[List["InvoiceLine"]] = relationship(
        back_populates="product"
    )

    def __repr__(self):
        return f"<Product {self.sku} {self.name!r}>"


class Invoice(Base):
    __tablename__ = "invoice"
    __table_args__ = (
        Index("ix_invoice_doc_date", "doc_date"),
    )

    invoice_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    num: Mapped[str] = mapped_column(String(30), nullable=False)
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)
    buyer_company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("company.company_id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'draft'"),
    )

    buyer_company: Mapped["Company"] = relationship(
        back_populates="invoices"
    )
    lines: Mapped[List["InvoiceLine"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan"
    )

    @hybrid_property
    def total_amount(self) -> Decimal:
        """Сумма по счёту = sum(quantity * price) по всем строкам."""
        total = Decimal("0")
        for line in self.lines:
            q = line.quantity or Decimal("0")
            p = line.price or Decimal("0")
            total += q * p
        return total

    def __repr__(self):
        return f"<Invoice {self.invoice_id} {self.num!r}>"


class InvoiceLine(Base):
    __tablename__ = "invoice_line"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_invoice_line_qty_positive"),
        CheckConstraint("price >= 0", name="ck_invoice_line_price_nonneg"),
    )

    invoice_line_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("invoice.invoice_id"),
        nullable=False,
    )
    product_sku: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("product.sku"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    invoice: Mapped["Invoice"] = relationship(
        back_populates="lines"
    )
    product: Mapped["Product"] = relationship(
        back_populates="invoice_lines"
    )

    def __repr__(self):
        return f"<InvoiceLine {self.invoice_line_id}>"
