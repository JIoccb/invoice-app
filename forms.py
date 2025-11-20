# forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, IntegerField, DecimalField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length, Optional as Opt, Email, NumberRange


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
    shelf_life_days = IntegerField(
        "Срок годности (дн.)", validators=[Opt(), NumberRange(min=0)]
    )
    storage_conditions = TextAreaField(
        "Условия хранения", validators=[Opt(), Length(max=5000)]
    )
    submit = SubmitField("Сохранить")
