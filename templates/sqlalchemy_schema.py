"""
SQLAlchemy 2.0 models + DDL for the invoice schema (PostgreSQL)
---------------------------------------------------------------
- Tables: public.company, public.product, public.invoice, public.invoice_line
- PK/FK/AK/Indexes/Checks match the recommendations we discussed
- Includes a view public.invoice_totals to avoid storing redundant totals
Usage:
  1) pip install "sqlalchemy>=2.0" psycopg2-binary
  2) Set env: export DATABASE_URL="postgresql+psycopg2://user:pass@host:5432/dbname"
  3) python sqlalchemy_schema.py
"""
from __future__ import annotations

import os
from typing import List, Optional
from decimal import Decimal
from datetime import date

from sqlalchemy import (
    BigInteger, Integer, String, Text, Date, Numeric,
    CheckConstraint, UniqueConstraint, Index, Computed, text,
    create_engine, MetaData, DDL, event
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session
from sqlalchemy.schema import ForeignKey

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s__%(column_0_N_name)s",
    "uq": "uq_%(table_name)s__%(column_0_N_name)s",
    "ck": "ck_%(table_name)s__%(constraint_name)s",
    "fk": "fk_%(table_name)s__%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata



class Company(Base):
    __tablename__ = "company"
    __table_args__ = (
        UniqueConstraint("inn_kpp", name="ak_company__inn_kpp"),
        {"schema": "public"},
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


class Product(Base):
    __tablename__ = "product"
    __table_args__ = ({"schema": "public"},)

    sku: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    weight: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3))
    shelf_life_days: Mapped[Optional[int]] = mapped_column(Integer)
    storage_conditions: Mapped[Optional[str]] = mapped_column(Text)

    lines: Mapped[List["InvoiceLine"]] = relationship(back_populates="product")


class Invoice(Base):
    __tablename__ = "invoice"
    __table_args__ = (
        UniqueConstraint("buyer_company_id", "num", name="uq_invoice__buyer_num"),
        CheckConstraint(
            "status IN ('draft','approved','cancelled','paid')",
            name="ck_invoice_status",
        ),
        Index("ix_invoice__buyer_company_id", "buyer_company_id"),
        Index("ix_invoice__num", "num"),
        Index("ix_invoice__doc_date", "doc_date"),
        {"schema": "public"},
    )

    invoice_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    num: Mapped[str] = mapped_column(String(20), nullable=False)
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)

    buyer_company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "public.company.company_id",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
            name="fk_invoice__company",
        ),
        nullable=False,
    )
    warehouse: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(12), server_default=text("'draft'"))
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))

    buyer_company: Mapped["Company"] = relationship(back_populates="invoices")
    lines: Mapped[List["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_line"
    __table_args__ = (
        # Составной PK: (invoice_id, line_no)
        CheckConstraint("unit_price >= 0", name="price_nonneg"),
        CheckConstraint("qty > 0", name="qty_positive"),
        Index("ix_invoice_line__product_sku", "product_sku"),
        {"schema": "public"},
    )

    invoice_id: Mapped[int] = mapped_column(
        ForeignKey(
            "public.invoice.invoice_id",
            onupdate="CASCADE",
            ondelete="CASCADE",
            name="fk_invoice_line__invoice",
        ),
        primary_key=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)

    product_sku: Mapped[str] = mapped_column(
        ForeignKey(
            "public.product.sku",
            onupdate="CASCADE",
            ondelete="RESTRICT",
            name="fk_invoice_line__product",
        ),
        nullable=False,
    )

    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        Computed("qty * unit_price", persisted=True),
        nullable=False,
    )

    invoice: Mapped["Invoice"] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship(back_populates="lines")



invoice_totals_view = DDL("""
CREATE OR REPLACE VIEW public.invoice_totals AS
SELECT i.invoice_id,
       COALESCE(SUM(l.line_total), 0)::numeric(14,2) AS total
FROM public.invoice i
LEFT JOIN public.invoice_line l ON l.invoice_id = i.invoice_id
GROUP BY i.invoice_id;
""")
event.listen(Base.metadata, "after_create", invoice_totals_view)


def main():
    url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres",
    )
    engine = create_engine(url, echo=True, future=True)

    Base.metadata.create_all(engine)


if __name__ == "__main__":
    main()
