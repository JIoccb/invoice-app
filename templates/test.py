"""
Invoice App — single-file Flask + SQLAlchemy 2.0

Запуск:
  pip install "Flask>=3" "SQLAlchemy>=2" psycopg2-binary Flask-WTF WTForms email-validator bleach Flask-Talisman python-dotenv
  # Подготовьте .env (или задайте переменные окружения):
  # DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/yourdb
  # SECRET_KEY=change-me
  python test.py

По умолчанию, если DATABASE_URL не задан, используется SQLite: invoice_app.sqlite3
"""
from __future__ import annotations

import os, math
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from flask import Flask, request, redirect, url_for, flash, render_template
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from jinja2 import DictLoader
from wtforms import StringField, IntegerField, DecimalField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length, Optional as Opt, Email, NumberRange
from flask_talisman import Talisman
import bleach

from sqlalchemy import (
    BigInteger, Integer, String, Text, Date, Numeric,
    CheckConstraint, UniqueConstraint, Index, text,
    create_engine, func, ForeignKey,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session, scoped_session, sessionmaker
from sqlalchemy.ext.hybrid import hybrid_property

# .env (если есть)
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# ------------------------
# Security / CSP
# ------------------------
CSP = {
    'default-src': ["'self'"],
    'script-src': ["'self'"],
    'style-src': ["'self'", "'unsafe-inline'"],
    'img-src': ["'self'"],
}

# ------------------------
# SQLAlchemy base
# ------------------------
class Base(DeclarativeBase):
    pass


# ------------------------
# Models
# ------------------------
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


# ------------------------
# DB engine & session
# ------------------------
def _strip_invisibles(s: str) -> str:
    # защита от невидимых символов (которые иногда попадают из копипаста)
    return "".join(c for c in s if c.isprintable())


raw_url = os.getenv("DATABASE_URL", "")
if raw_url:
    db_url = _strip_invisibles(raw_url)
    if any(c in db_url for c in ["“", "”", "«", "»", "„"]):
        raise RuntimeError("DATABASE_URL contains smart quotes. Replace with standard ' or \" quotes.")
else:
    # fallback (локальный SQLite)
    db_url = "sqlite:///invoice_app.sqlite3"

engine = create_engine(db_url, echo=False, future=True)
SessionLocal: sessionmaker[Session] = scoped_session(
    sessionmaker(bind=engine, autoflush=False, autocommit=False)
)

Base.metadata.create_all(engine)


# ------------------------
# Helpers
# ------------------------
def sanitize_text(text_value: Optional[str]) -> Optional[str]:
    if text_value is None:
        return None
    # базовая очистка от HTML (XSS)
    return bleach.clean(text_value, tags=[], strip=True)


def get_pagination_params(default_per_page: int = 10) -> tuple[int, int]:
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get("per_page", default_per_page))
    except ValueError:
        per_page = default_per_page
    page = max(page, 1)
    per_page = max(min(per_page, 100), 1)
    return page, per_page


# ------------------------
# Forms
# ------------------------
class CompanyForm(FlaskForm):
    company_id = IntegerField("ID компании", validators=[DataRequired()])
    name = StringField("Название", validators=[DataRequired(), Length(max=200)])
    inn_kpp = StringField("ИНН/КПП", validators=[Opt(), Length(max=20)])
    address = TextAreaField("Адрес", validators=[Opt(), Length(max=5000)])
    phone = StringField("Телефон", validators=[Opt(), Length(max=30)])
    email = StringField("Email", validators=[Opt(), Email(), Length(max=254)])
    contact_name = StringField("Контактное лицо", validators=[Opt(), Length(max=100)])
    submit = SubmitField("Сохранить")


class ProductForm(FlaskForm):
    sku = StringField("SKU", validators=[DataRequired(), Length(max=50)])
    name = StringField("Название", validators=[DataRequired(), Length(max=200)])
    unit = StringField("Ед.", validators=[DataRequired(), Length(max=20)])
    weight = DecimalField("Вес", validators=[Opt(), NumberRange(min=0)], places=3)
    shelf_life_days = IntegerField("Срок годности (дн.)", validators=[Opt(), NumberRange(min=0)])
    storage_conditions = TextAreaField("Условия хранения", validators=[Opt(), Length(max=5000)])
    submit = SubmitField("Сохранить")


# ------------------------
# Flask app
# ------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
csrf = CSRFProtect(app)
Talisman(
    app,
    content_security_policy=CSP,
    frame_options='DENY',
    force_https=False,
    strict_transport_security=False,
    session_cookie_secure=False,
    session_cookie_http_only=True,
    content_security_policy_nonce_in=['script-src'],
)


# ------------------------
# Templates (inline)
# ------------------------
base_tpl = r"""
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <title>{{ title or "Invoice App" }}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      body { font-family: system-ui, -apple-system, "Segoe UI", "Roboto", "Helvetica Neue", Arial, sans-serif; margin: 0; padding: 0; background:#f6f7fb; }
      header { background:#111827; color:#fff; padding:14px 20px; }
      header a { color:#fff; text-decoration:none; margin-right:16px; }
      .container { max-width: 1100px; margin: 24px auto; background:#fff; padding:20px; border-radius: 12px; box-shadow: 0 6px 24px rgba(0,0,0,0.06); }
      .grid { display: grid; gap: 12px; }
      .grid-3 { grid-template-columns: repeat(3, 1fr); }
      .stat { background:#f3f4f6; padding:12px; border-radius:10px; }
      table { width:100%; border-collapse: collapse; }
      th, td { padding:8px; border-bottom:1px solid #e5e7eb; text-align:left; }
      th { background:#f9fafb; }
      .btn { display:inline-block; padding:6px 10px; border-radius: 999px; border:none; text-decoration:none; cursor:pointer; }
      .btn-primary { background:#2563eb; color:#fff; }
      .btn-secondary { background:#6b7280; color:#fff; }
      .btn-danger { background:#dc2626; color:#fff; }
      .flash { padding:10px; margin-bottom:10px; border-radius:8px; }
      .flash.success { background:#dcfce7; }
      .flash.warning { background:#fef9c3; }
      .flash.danger { background:#fee2e2; }
      form .row { display:flex; gap:10px; margin-bottom:8px; flex-wrap:wrap; }
      input, select, textarea { padding:8px; border:1px solid #e5e7eb; border-radius:8px; width:100%; }
      label { font-size: 12px; color:#6b7280; display:block; margin-bottom:4px; }
      .pagination a, .pagination span { margin-right:4px; }
      .pagination .active { font-weight:bold; }
    </style>
  </head>
  <body>
    <header>
      <a href="{{ url_for('index') }}">Главная</a>
      <a href="{{ url_for('companies_list') }}">Компании</a>
      <a href="{{ url_for('products_list') }}">Товары</a>
      <a href="{{ url_for('invoices_list') }}">Счета</a>
    </header>
    <div class="container">
      {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
          {% for cat, msg in messages %}
            <div class="flash {{ cat }}">{{ msg }}</div>
          {% endfor %}
        {% endif %}
      {% endwith %}
      {% block content %}{% endblock %}
    </div>
  </body>
</html>
"""

index_tpl = r"""
{% extends "base.html" %}
{% block content %}
<h2>Панель</h2>
<div class="grid grid-3">
  <div class="stat">Компаний: <strong>{{ total_companies }}</strong></div>
  <div class="stat">Товаров: <strong>{{ total_products }}</strong></div>
  <div class="stat">Счетов: <strong>{{ total_invoices }}</strong></div>
</div>
<h3 style="margin-top:18px;">Популярные товары (по сумме qty)</h3>
<table>
  <tr><th>SKU</th><th>Название</th><th>Суммарное количество</th></tr>
  {% for sku, name, qty_sum in top_products %}
    <tr><td>{{ sku }}</td><td>{{ name }}</td><td>{{ qty_sum }}</td></tr>
  {% else %}
    <tr><td colspan="3">Нет данных</td></tr>
  {% endfor %}
</table>

<h3 style="margin-top:18px;">Последние счета</h3>
<table>
  <tr><th>ID</th><th>Номер</th><th>Дата</th><th>Покупатель</th><th>Сумма</th></tr>
  {% for inv in recent_invoices %}
    <tr>
      <td><a href="{{ url_for('invoice_detail', invoice_id=inv.invoice_id) }}">{{ inv.invoice_id }}</a></td>
      <td>{{ inv.num }}</td>
      <td>{{ inv.doc_date }}</td>
      <td>{{ inv.buyer_company.name if inv.buyer_company else '' }}</td>
      <td>{{ "%.2f"|format(inv.total_amount or 0) }}</td>
    </tr>
  {% else %}
    <tr><td colspan="5">Пока нет счетов</td></tr>
  {% endfor %}
</table>
{% endblock %}
"""

companies_list_tpl = r"""
{% extends "base.html" %}
{% block content %}
<h2>Компании</h2>
<form method="get">
  <div class="row">
    <div style="flex:1">
      <label>Поиск (название / ИНН-КПП / контакт)</label>
      <input type="text" name="q" value="{{ q|e }}">
    </div>
    <div><label>&nbsp;</label><button class="btn btn-secondary" type="submit">Искать</button></div>
    <div><label>&nbsp;</label><a class="btn btn-primary" href="{{ url_for('companies_create') }}">+ Новая компания</a></div>
  </div>
</form>
<table>
  <tr><th>ID</th><th>Название</th><th>ИНН/КПП</th><th>Email</th><th>Контакт</th><th></th></tr>
  {% for it in items %}
  <tr>
    <td>{{ it.company_id }}</td>
    <td>{{ it.name }}</td>
    <td>{{ it.inn_kpp or "" }}</td>
    <td>{{ it.email or "" }}</td>
    <td>{{ it.contact_name or "" }}</td>
    <td>
      <a class="btn btn-secondary" href="{{ url_for('companies_edit', company_id=it.company_id) }}">Ред.</a>
      <form method="post" action="{{ url_for('companies_delete', company_id=it.company_id) }}" style="display:inline" onsubmit="return confirm('Удалить?')">
        {{ csrf_token() }}
        <button class="btn btn-danger" type="submit">Удалить</button>
      </form>
    </td>
  </tr>
  {% else %}
  <tr><td colspan="6">Не найдено</td></tr>
  {% endfor %}
</table>
<div class="pagination">
  {% for p in range(1, pages+1) %}
    {% if p == page %}<span class="active">{{ p }}</span>
    {% else %}<a href="{{ url_for('companies_list', page=p, per_page=per_page, q=q) }}">{{ p }}</a>
    {% endif %}
  {% endfor %}
</div>
{% endblock %}
"""

companies_form_tpl = r"""
{% extends "base.html" %}
{% block content %}
<h2>{{ 'Новая компания' if mode=='create' else 'Редактирование компании' }}</h2>
<form method="post" novalidate>
  {{ form.csrf_token }}
  <div class="row">
    <div style="flex:1">{{ form.company_id.label }} {{ form.company_id() }}</div>
    <div style="flex:2">{{ form.name.label }} {{ form.name() }}</div>
  </div>
  <div class="row">
    <div style="flex:1">{{ form.inn_kpp.label }} {{ form.inn_kpp() }}</div>
    <div style="flex:1">{{ form.phone.label }} {{ form.phone() }}</div>
  </div>
  <div class="row">
    <div style="flex:1">{{ form.email.label }} {{ form.email() }}</div>
    <div style="flex:1">{{ form.contact_name.label }} {{ form.contact_name() }}</div>
  </div>
  <div class="row">
    <div style="flex:1">{{ form.address.label }} {{ form.address(rows=3) }}</div>
  </div>
  <button class="btn btn-primary" type="submit">{{ form.submit.label.text }}</button>
  <a class="btn btn-secondary" href="{{ url_for('companies_list') }}">Отмена</a>
</form>
{% endblock %}
"""

products_list_tpl = r"""
{% extends "base.html" %}
{% block content %}
<h2>Товары</h2>
<form method="get">
  <div class="row">
    <div style="flex:1">
      <label>SKU</label>
      <input type="text" name="sku" value="{{ sku|e }}">
    </div>
    <div style="flex:2">
      <label>Название содержит</label>
      <input type="text" name="name" value="{{ name|e }}">
    </div>
    <div><label>&nbsp;</label><button class="btn btn-secondary" type="submit">Искать</button></div>
    <div><label>&nbsp;</label><a class="btn btn-primary" href="{{ url_for('products_create') }}">+ Новый товар</a></div>
  </div>
</form>

{% if form %}
<h3>{{ 'Новый товар' if create_mode else 'Редактирование товара' }}</h3>
<form method="post" novalidate>
  {{ form.csrf_token }}
  <div class="row">
    <div style="flex:1">{{ form.sku.label }} {{ form.sku(readonly=not create_mode) }}</div>
    <div style="flex:2">{{ form.name.label }} {{ form.name() }}</div>
  </div>
  <div class="row">
    <div style="flex:1">{{ form.unit.label }} {{ form.unit() }}</div>
    <div style="flex:1">{{ form.weight.label }} {{ form.weight() }}</div>
  </div>
  <div class="row">
    <div style="flex:1">{{ form.shelf_life_days.label }} {{ form.shelf_life_days() }}</div>
  </div>
  <div class="row">
    <div style="flex:1">{{ form.storage_conditions.label }} {{ form.storage_conditions(rows=3) }}</div>
  </div>
  <button class="btn btn-primary" type="submit">{{ form.submit.label.text }}</button>
  <a class="btn btn-secondary" href="{{ url_for('products_list') }}">Отмена</a>
</form>
{% endif %}

<h3 style="margin-top:16px;">Список</h3>
<table>
  <tr><th>SKU</th><th>Название</th><th>Ед.</th><th>Вес</th><th>Срок годн., дн.</th><th></th></tr>
  {% for it in items %}
  <tr>
    <td>{{ it.sku }}</td>
    <td>{{ it.name }}</td>
    <td>{{ it.unit }}</td>
    <td>{{ it.weight or '' }}</td>
    <td>{{ it.shelf_life_days or '' }}</td>
    <td>
      <a class="btn btn-secondary" href="{{ url_for('products_edit', sku=it.sku) }}">Ред.</a>
      <form method="post" action="{{ url_for('products_delete', sku=it.sku) }}" style="display:inline" onsubmit="return confirm('Удалить?')">
        {{ csrf_token() }}
        <button class="btn btn-danger" type="submit">Удалить</button>
      </form>
    </td>
  </tr>
  {% else %}
  <tr><td colspan="6">Не найдено</td></tr>
  {% endfor %}
</table>
<div class="pagination">
  {% for p in range(1, pages+1) %}
    {% if p == page %}<span class="active">{{ p }}</span>
    {% else %}<a href="{{ url_for('products_list', page=p, per_page=per_page, sku=sku, name=name) }}">{{ p }}</a>
    {% endif %}
  {% endfor %}
</div>
{% endblock %}
"""

invoices_list_tpl = r"""
{% extends "base.html" %}
{% block content %}
<h2>Счета</h2>
<form method="get">
  <div class="row">
    <div style="flex:1">
      <label>Номер содержит</label>
      <input type="text" name="num" value="{{ num|e }}">
    </div>
    <div style="flex:1">
      <label>Покупатель (ID или имя)</label>
      <input type="text" name="buyer" value="{{ buyer|e }}">
    </div>
    <div style="flex:1">
      <label>Статус</label>
      <select name="status">
        <option value="">-- любой --</option>
        <option value="draft" {{ 'selected' if status=='draft' else '' }}>draft</option>
        <option value="sent" {{ 'selected' if status=='sent' else '' }}>sent</option>
        <option value="paid" {{ 'selected' if status=='paid' else '' }}>paid</option>
      </select>
    </div>
  </div>
  <div class="row">
    <div style="flex:1">
      <label>Дата с</label>
      <input type="date" name="date_from" value="{{ date_from|e }}">
    </div>
    <div style="flex:1">
      <label>Дата по</label>
      <input type="date" name="date_to" value="{{ date_to|e }}">
    </div>
    <div><label>&nbsp;</label><button class="btn btn-secondary" type="submit">Фильтр</button></div>
  </div>
</form>

<table>
  <tr><th>ID</th><th>Номер</th><th>Дата</th><th>Покупатель</th><th>Статус</th><th>Сумма</th></tr>
  {% for it in items %}
  <tr>
    <td><a href="{{ url_for('invoice_detail', invoice_id=it.invoice_id) }}">{{ it.invoice_id }}</a></td>
    <td>{{ it.num }}</td>
    <td>{{ it.doc_date }}</td>
    <td>{{ it.buyer_company.name if it.buyer_company else '' }}</td>
    <td>{{ it.status }}</td>
    <td>{{ "%.2f"|format(it.total_amount or 0) }}</td>
  </tr>
  {% else %}
  <tr><td colspan="6">Не найдено</td></tr>
  {% endfor %}
</table>
<div class="pagination">
  {% for p in range(1, pages+1) %}
    {% if p == page %}<span class="active">{{ p }}</span>
    {% else %}<a href="{{ url_for('invoices_list', page=p, per_page=per_page, num=num, buyer=buyer, status=status, date_from=date_from, date_to=date_to) }}">{{ p }}</a>
    {% endif %}
  {% endfor %}
</div>
{% endblock %}
"""

invoice_detail_tpl = r"""
{% extends "base.html" %}
{% block content %}
<h2>Счёт № {{ invoice.num }} от {{ invoice.doc_date }}</h2>
<p>Покупатель: {{ invoice.buyer_company.name if invoice.buyer_company else '' }}</p>
<p>Статус: {{ invoice.status }}</p>
<p>Сумма: {{ "%.2f"|format(invoice.total_amount or 0) }}</p>

<h3>Позиции</h3>
<table>
  <tr><th>#</th><th>SKU</th><th>Название</th><th>Qty</th><th>Цена</th><th>Сумма</th></tr>
  {% for idx, line in enumerate(invoice.lines, start=1) %}
  <tr>
    <td>{{ idx }}</td>
    <td>{{ line.product_sku }}</td>
    <td>{{ line.product.name if line.product else '' }}</td>
    <td>{{ line.quantity }}</td>
    <td>{{ "%.2f"|format(line.price) }}</td>
    <td>{{ "%.2f"|format(line.quantity * line.price) }}</td>
  </tr>
  {% else %}
  <tr><td colspan="6">Нет позиций</td></tr>
  {% endfor %}
</table>
<a class="btn btn-secondary" href="{{ url_for('invoices_list') }}">Назад к списку</a>
{% endblock %}
"""

# Зарегистрировать in-memory шаблоны
app.jinja_loader = DictLoader({
    "base.html": base_tpl,
    "index.html": index_tpl,
    "companies_list.html": companies_list_tpl,
    "companies_form.html": companies_form_tpl,
    "products_list.html": products_list_tpl,
    "invoices_list.html": invoices_list_tpl,
    "invoice_detail.html": invoice_detail_tpl,
})


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
        s.query(Product.sku, Product.name, func.sum(InvoiceLine.quantity).label("qty_sum"))
        .join(InvoiceLine, InvoiceLine.product_sku == Product.sku)
        .group_by(Product.sku, Product.name)
        .order_by(func.sum(InvoiceLine.quantity).desc())
        .limit(5).all()
    )
    recent_invoices = s.query(Invoice).order_by(Invoice.doc_date.desc()).limit(5).all()
    return render_template(
        "index.html",
        total_companies=total_companies,
        total_products=total_products,
        total_invoices=total_invoices,
        top_products=top_products,
        recent_invoices=recent_invoices,
    )


# Companies
@app.route("/companies")
def companies_list():
    s: Session = SessionLocal()
    q = (request.args.get("q") or "").strip()
    page, per_page = get_pagination_params()
    query = s.query(Company)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Company.name.ilike(like)) |
            (Company.inn_kpp.ilike(like)) |
            (Company.contact_name.ilike(like))
        )
    total = query.count()
    items = (
        query.order_by(Company.company_id)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    pages = math.ceil(total / per_page) if per_page else 1
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


# Products
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
    pages = math.ceil(total / per_page) if per_page else 1
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


# Invoices (browse + filters + detail)
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
                (Invoice.buyer_company_id == int(buyer)) |
                (Company.name.ilike(f"%{buyer}%"))
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
    pages = math.ceil(total / per_page) if per_page else 1

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
