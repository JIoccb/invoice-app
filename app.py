# app.py
"""
Invoice App — Flask + SQLAlchemy 2.0 (разнесено по файлам)

Запуск:
  pip install "Flask>=3" "SQLAlchemy>=2" psycopg2-binary Flask-WTF WTForms \
    email-validator bleach Flask-Talisman python-dotenv

  # Подготовьте .env (или задайте переменные окружения):
  # DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/yourdb
  # SECRET_KEY=change-me

  python app.py

По умолчанию, если DATABASE_URL не задан, используется SQLite: invoice_app.sqlite3
"""

from __future__ import annotations

import os
from datetime import datetime

from flask import Flask, request, redirect, url_for, flash, render_template
from flask_wtf import CSRFProtect
from flask_talisman import Talisman
from sqlalchemy.orm import Session
from sqlalchemy import func

from db import engine, SessionLocal, Base
from models import Company, Product, Invoice, InvoiceLine
from forms import CompanyForm, ProductForm
from utils import sanitize_text, get_pagination_params, calc_pages

# ------------------------
# Security / CSP
# ------------------------
CSP = {
    "default-src": ["'self'"],
    "script-src": ["'self'"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'"],
}

# ------------------------
# Flask app
# ------------------------
app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

csrf = CSRFProtect(app)

Talisman(
    app,
    content_security_policy=CSP,
    frame_options="DENY",
    force_https=False,
    strict_transport_security=False,
    session_cookie_secure=False,
    session_cookie_http_only=True,
    content_security_policy_nonce_in=["script-src"],
)

# Создаём таблицы после импорта моделей
Base.metadata.create_all(bind=engine)


# ------------------------
# Routes
# ------------------------
@app.route("/")
def index():
    s: Session = SessionLocal()
    total_companies = s.query(Company).count()
    total_products = s.query(Product).count()
    total_invoices = s.query(Invoice).count()
    top_products = (
        s.query(
            Product.sku,
            Product.name,
            func.sum(InvoiceLine.quantity).label("qty_sum"),
        )
        .join(InvoiceLine, InvoiceLine.product_sku == Product.sku)
        .group_by(Product.sku, Product.name)
        .order_by(func.sum(InvoiceLine.quantity).desc())
        .limit(5)
        .all()
    )
    recent_invoices = (
        s.query(Invoice).order_by(Invoice.doc_date.desc()).limit(5).all()
    )
    return render_template(
        "index.html",
        total_companies=total_companies,
        total_products=total_products,
        total_invoices=total_invoices,
        top_products=top_products,
        recent_invoices=recent_invoices,
    )


# ------------------------
# Companies
# ------------------------
@app.route("/companies")
def companies_list():
    s: Session = SessionLocal()
    q = (request.args.get("q") or "").strip()
    page, per_page = get_pagination_params()
    query = s.query(Company)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Company.name.ilike(like))
            | (Company.inn_kpp.ilike(like))
            | (Company.contact_name.ilike(like))
        )
    total = query.count()
    items = (
        query.order_by(Company.company_id)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    pages = calc_pages(total, per_page)
    return render_template(
        "companies_list.html",
        items=items,
        total=total,
        page=page,
        pages=pages,
        per_page=per_page,
        q=q,
    )


@app.route("/companies/create", methods=["GET", "POST"])
def companies_create():
    s: Session = SessionLocal()
    form = CompanyForm()
    if form.validate_on_submit():
        company = Company(
            company_id=form.company_id.data,
            name=sanitize_text(form.name.data),
            inn_kpp=sanitize_text(form.inn_kpp.data),
            address=sanitize_text(form.address.data),
            phone=sanitize_text(form.phone.data),
            email=sanitize_text(form.email.data),
            contact_name=sanitize_text(form.contact_name.data),
        )
        s.add(company)
        try:
            s.commit()
            flash("Компания создана", "success")
            return redirect(url_for("companies_list"))
        except Exception as e:
            s.rollback()
            flash(f"Ошибка: {e}", "danger")
    return render_template("companies_form.html", form=form, mode="create")


@app.route("/companies/<int:company_id>/edit", methods=["GET", "POST"])
def companies_edit(company_id: int):
    s: Session = SessionLocal()
    company = s.get(Company, company_id)
    if not company:
        flash("Компания не найдена", "warning")
        return redirect(url_for("companies_list"))
    form = CompanyForm(obj=company)
    if form.validate_on_submit():
        company.name = sanitize_text(form.name.data)
        company.inn_kpp = sanitize_text(form.inn_kpp.data)
        company.address = sanitize_text(form.address.data)
        company.phone = sanitize_text(form.phone.data)
        company.email = sanitize_text(form.email.data)
        company.contact_name = sanitize_text(form.contact_name.data)
        try:
            s.commit()
            flash("Изменения сохранены", "success")
            return redirect(url_for("companies_list"))
        except Exception as e:
            s.rollback()
            flash(f"Ошибка: {e}", "danger")
    return render_template("companies_form.html", form=form, mode="edit")


@app.route("/companies/<int:company_id>/delete", methods=["POST"])
def companies_delete(company_id: int):
    s: Session = SessionLocal()
    company = s.get(Company, company_id)
    if not company:
        flash("Компания не найдена", "warning")
        return redirect(url_for("companies_list"))
    try:
        s.delete(company)
        s.commit()
        flash("Удалено", "success")
    except Exception as e:
        s.rollback()
        flash(f"Ошибка: {e}", "danger")
    return redirect(url_for("companies_list"))


# ------------------------
# Products
# ------------------------
@app.route("/products")
def products_list():
    s: Session = SessionLocal()
    sku = (request.args.get("sku") or "").strip()
    name = (request.args.get("name") or "").strip()
    page, per_page = get_pagination_params()
    query = s.query(Product)
    if sku:
        query = query.filter(Product.sku.ilike(f"%{sku}%"))
    if name:
        query = query.filter(Product.name.ilike(f"%{name}%"))
    total = query.count()
    items = (
        query.order_by(Product.sku)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    pages = calc_pages(total, per_page)
    return render_template(
        "products_list.html",
        items=items,
        total=total,
        page=page,
        pages=pages,
        per_page=per_page,
        sku=sku,
        name=name,
        form=None,
        create_mode=False,
        edit_mode=False,
    )


@app.route("/products/create", methods=["GET", "POST"])
def products_create():
    s: Session = SessionLocal()
    form = ProductForm()
    if form.validate_on_submit():
        product = Product(
            sku=sanitize_text(form.sku.data),
            name=sanitize_text(form.name.data),
            unit=sanitize_text(form.unit.data),
            weight=form.weight.data,
            shelf_life_days=form.shelf_life_days.data,
            storage_conditions=sanitize_text(form.storage_conditions.data),
        )
        s.add(product)
        try:
            s.commit()
            flash("Товар создан", "success")
            return redirect(url_for("products_list"))
        except Exception as e:
            s.rollback()
            flash(f"Ошибка: {e}", "danger")
    return render_template(
        "products_list.html",
        form=form,
        create_mode=True,
        items=[],
        total=0,
        page=1,
        pages=1,
        per_page=10,
        sku="",
        name="",
    )


@app.route("/products/<string:sku>/edit", methods=["GET", "POST"])
def products_edit(sku: str):
    s: Session = SessionLocal()
    product = s.get(Product, sku)
    if not product:
        flash("Товар не найден", "warning")
        return redirect(url_for("products_list"))
    form = ProductForm(obj=product)
    if form.validate_on_submit():
        product.name = sanitize_text(form.name.data)
        product.unit = sanitize_text(form.unit.data)
        product.weight = form.weight.data
        product.shelf_life_days = form.shelf_life_days.data
        product.storage_conditions = sanitize_text(form.storage_conditions.data)
        try:
            s.commit()
            flash("Изменения сохранены", "success")
            return redirect(url_for("products_list"))
        except Exception as e:
            s.rollback()
            flash(f"Ошибка: {e}", "danger")
    return render_template(
        "products_list.html",
        form=form,
        edit_mode=True,
        items=[],
        total=0,
        page=1,
        pages=1,
        per_page=10,
        sku="",
        name="",
    )


@app.route("/products/<string:sku>/delete", methods=["POST"])
def products_delete(sku: str):
    s: Session = SessionLocal()
    product = s.get(Product, sku)
    if not product:
        flash("Товар не найден", "warning")
        return redirect(url_for("products_list"))
    try:
        s.delete(product)
        s.commit()
        flash("Удалено", "success")
    except Exception as e:
        s.rollback()
        flash(f"Ошибка: {e}", "danger")
    return redirect(url_for("products_list"))


# ------------------------
# Invoices (browse + filters + detail)
# ------------------------
@app.route("/invoices")
def invoices_list():
    s: Session = SessionLocal()
    num = (request.args.get("num") or "").strip()
    buyer = (request.args.get("buyer") or "").strip()
    status = (request.args.get("status") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    page, per_page = get_pagination_params()

    query = s.query(Invoice)
    if num:
        query = query.filter(Invoice.num.ilike(f"%{num}%"))
    if buyer:
        query = query.join(Invoice.buyer_company, isouter=True)
        if buyer.isdigit():
            query = query.filter(
                (Invoice.buyer_company_id == int(buyer))
                | (Company.name.ilike(f"%{buyer}%"))
            )
        else:
            query = query.filter(Company.name.ilike(f"%{buyer}%"))
    if status:
        query = query.filter(Invoice.status == status)

    def parse_date(sval: str):
        try:
            return datetime.strptime(sval, "%Y-%m-%d").date()
        except Exception:
            return None

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)
    if d_from:
        query = query.filter(Invoice.doc_date >= d_from)
    if d_to:
        query = query.filter(Invoice.doc_date <= d_to)

    total = query.count()
    items = (
        query.order_by(Invoice.doc_date.desc(), Invoice.invoice_id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    pages = calc_pages(total, per_page)

    return render_template(
        "invoices_list.html",
        items=items,
        total=total,
        page=page,
        pages=pages,
        per_page=per_page,
        num=num,
        buyer=buyer,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )


@app.route("/invoices/<int:invoice_id>")
def invoice_detail(invoice_id: int):
    s: Session = SessionLocal()
    invoice = s.get(Invoice, invoice_id)
    if not invoice:
        flash("Счёт не найден", "warning")
        return redirect(url_for("invoices_list"))
    return render_template("invoice_detail.html", invoice=invoice)


if __name__ == "__main__":
    app.run(port=5000, debug=True)
