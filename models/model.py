# ここにデータベースの作成情報入れる。
from sqlalchemy.dialects.postgresql import UUID
import uuid
import sys
sys.path.append('./')
sys.path.append('../..')
from config import *
from flask_login import UserMixin
from datetime import datetime as dt
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship


class User(db.Model, UserMixin):
    """ユーザー情報"""
    __tablename__ = 'users'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # ユーザーID
    company_name = db.Column(db.String(128), nullable=False)  # 会社名
    company_name_kana = db.Column(db.String(128), nullable=False)  # 会社名(カナ)
    representative_name = db.Column(db.String(128), nullable=False)  # 代表者名
    email = db.Column(db.String(128), nullable=False, unique=True)  # メールアドレス
    phone_number = db.Column(db.String(32))  # 電話番号
    password_hash = db.Column(db.String(128), nullable=False)  # パスワード
    email_confirmed = db.Column(db.Boolean, default=False)  # メールアドレス確認フラグ
    last_login = db.Column(db.DateTime)  # 最終ログイン日時
    is_system_admin = db.Column(db.Boolean, default=False)  # システム管理者フラグ
    is_user_admin = db.Column(db.Boolean, default=False)  # ユーザー管理者フラグ
    contract_start = db.Column(db.DateTime)  # 契約開始日
    contract_end = db.Column(db.DateTime)  # 契約終了日
    postal_code = db.Column(db.String(32))  # 郵便番号
    prefecture = db.Column(db.String(128))  # 県名
    city = db.Column(db.String(128))  # 市区町村
    area = db.Column(db.String(128))  # 町域
    building = db.Column(db.String(128))  # 建物名・部屋番号
    manager_name = db.Column(db.String(128))  # 担当者名
    invoice_number = db.Column(db.String(64))  # インボイス番号
    capital = db.Column(db.Integer)  # 資本金
    created_at = db.Column(db.DateTime, default=dt.now())  # 登録日時
    updated_at = db.Column(db.DateTime, default=dt.now())  # 更新日時

    # 新たに追加するカラム
    corporate_number = db.Column(db.String(13))  # 法人番号（13桁）
    invoice_status = db.Column(db.String(32))  # インボイス番号のステータス（発行済、未発行）
    homepage_url = db.Column(db.String(256))  # 自社ホームページのURL
    main_industry = db.Column(db.String(64))  # 主たる業種
    industry_classification = db.Column(db.String(64))  # 業種（日本標準産業分類）
    employee_count = db.Column(db.Integer)  # 常時使用する従業員数
    establishment_date = db.Column(db.DateTime)  # 設立年月日
    revenue = db.Column(db.Integer)  # 直近1期の売上高
    gross_profit = db.Column(db.Integer)  # 直近1期の売上総利益
    ordinary_profit = db.Column(db.Integer)  # 直近1期の経常利益
    office_count = db.Column(db.Integer)  # 事業所数
    representative_birthdate = db.Column(db.DateTime)  # 代表者の生年月日
    representative_age = db.Column(db.Integer)  # 代表者の満年齢
    contact_name = db.Column(db.String(128))  # 連絡担当者氏名
    contact_kana = db.Column(db.String(128))  # 連絡担当者フリガナ
    contact_position = db.Column(db.String(128))  # 連絡担当者役職
    contact_address = db.Column(db.String(256))  # 連絡担当者住所
    contact_phone = db.Column(db.String(32))  # 連絡担当者電話番号
    contact_mobile = db.Column(db.String(32))  # 連絡担当者携帯電話番号
    contact_fax = db.Column(db.String(32))  # 連絡担当者FAX番号
    contact_email = db.Column(db.String(128))  # 連絡担当者Eメール
    successor_name = db.Column(db.String(128))  # 補助事業を中心に行う者の氏名
    successor_relation = db.Column(db.String(64))  # 補助事業を中心に行う者との関係
    representative_relation = db.Column(db.String(64))  # 代表者との関係

    def __init__(self, company_name="", company_name_kana="", representative_name="", email="", phone_number="", password_hash="", email_confirmed=False, last_login=None, is_system_admin=False, is_user_admin=False, contract_start=None, contract_end=None, postal_code="", prefecture="", city="", area="", building="", manager_name="", invoice_number="", capital="", created_at=dt.now(), updated_at=dt.now(), corporate_number="", invoice_status="未発行", homepage_url="", main_industry="", industry_classification="", employee_count=0, establishment_date=None, revenue=0, gross_profit=0, ordinary_profit=0, office_count=0, representative_birthdate=None, representative_age=0, contact_name="", contact_kana="", contact_position="", contact_address="", contact_phone="", contact_mobile="", contact_fax="", contact_email="", successor_name="", successor_relation="", representative_relation=""):
        self.company_name = company_name
        self.company_name_kana = company_name_kana
        self.representative_name = representative_name
        self.email = email
        self.phone_number = phone_number
        self.password_hash = password_hash
        self.email_confirmed = email_confirmed
        self.last_login = last_login
        self.is_system_admin = is_system_admin
        self.is_user_admin = is_user_admin
        self.contract_start = contract_start
        self.contract_end = contract_end
        self.postal_code = postal_code
        self.prefecture = prefecture
        self.city = city
        self.area = area
        self.building = building
        self.manager_name = manager_name
        self.invoice_number = invoice_number
        self.capital = capital
        self.created_at = created_at
        self.updated_at = updated_at
        self.corporate_number = corporate_number  # 法人番号
        self.invoice_status = invoice_status  # インボイス番号のステータス
        self.homepage_url = homepage_url  # 自社ホームページのURL
        self.main_industry = main_industry  # 主たる業種
        self.industry_classification = industry_classification  # 業種
        self.employee_count = employee_count  # 従業員数
        self.establishment_date = establishment_date  # 設立年月日
        self.revenue = revenue  # 直近1期の売上高
        self.gross_profit = gross_profit  # 直近1期の売上総利益
        self.ordinary_profit = ordinary_profit  # 直近1期の経常利益
        self.office_count = office_count  # 事業所数
        self.representative_birthdate = representative_birthdate  # 代表者の生年月日
        self.representative_age = representative_age  # 代表者の満年齢
        self.contact_name = contact_name  # 連絡担当者氏名
        self.contact_kana = contact_kana  # 連絡担当者フリガナ
        self.contact_position = contact_position  # 連絡担当者役職
        self.contact_address = contact_address  # 連絡担当者住所
        self.contact_phone = contact_phone  # 連絡担当者電話番号
        self.contact_mobile = contact_mobile  # 連絡担当者携帯電話番号
        self.contact_fax = contact_fax  # 連絡担当者FAX番号
        self.contact_email = contact_email  # 連絡担当者Eメール
        self.successor_name = successor_name  # 補助事業を中心に行う者の氏名
        self.successor_relation = successor_relation
        self.representative_relation = representative_relation

    def __repr__(self):
        return f"<User {self.id}>"
    
    def to_dict(self):
        return {
            "id": str(self.id),
            "company_name": self.company_name,
            "company_name_kana": self.company_name_kana,
            "representative_name": self.representative_name,
            "email": self.email,
            "phone_number": self.phone_number,
            "email_confirmed": self.email_confirmed,
            "last_login": self.last_login,
            "is_system_admin": self.is_system_admin,
            "is_user_admin": self.is_user_admin,
            "contract_start": self.contract_start,
            "contract_end": self.contract_end,
            "postal_code": self.postal_code,
            "prefecture": self.prefecture,
            "city": self.city,
            "area": self.area,
            "building": self.building,
            "manager_name": self.manager_name,
            "invoice_number": self.invoice_number,
            "capital": self.capital,
            "corporate_number": self.corporate_number,  # 法人番号
            "invoice_status": self.invoice_status,  # インボイス番号のステータス
            "homepage_url": self.homepage_url,  # 自社ホームページのURL
            "main_industry": self.main_industry,  # 主たる業種
            "industry_classification": self.industry_classification,  # 業種
            "employee_count": self.employee_count,  # 従業員数
            "establishment_date": self.establishment_date,  # 設立年月日
            "revenue": self.revenue,  # 売上高
            "gross_profit": self.gross_profit,  # 売上総利益
            "ordinary_profit": self.ordinary_profit,  # 経常利益
            "office_count": self.office_count,  # 事業所数
            "representative_birthdate": self.representative_birthdate,  # 代表者の生年月日
            "representative_age": self.representative_age,  # 代表者の満年齢
            "contact_name": self.contact_name,  # 連絡担当者氏名
            "contact_kana": self.contact_kana,  # 連絡担当者フリガナ
            "contact_position": self.contact_position,  # 連絡担当者役職
            "contact_address": self.contact_address,  # 連絡担当者住所
            "contact_phone": self.contact_phone,  # 連絡担当者電話番号
            "contact_mobile": self.contact_mobile,  # 連絡担当者携帯電話番号
            "contact_fax": self.contact_fax,  # 連絡担当者FAX番号
            "contact_email": self.contact_email,  # 連絡担当者Eメール
            "successor_name": self.successor_name,  # 補助事業を中心に行う者の氏名
            "successor_relation": self.successor_relation,
            "representative_relation": self.representative_relation
        }



# 会社ごとの決算情報を管理するテーブル
class Settlement(db.Model):
    """決算情報"""
    __tablename__ = 'settlements'
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # 決算ID
    company_id = db.Column(UUID(as_uuid=True), nullable=False)  # 会社ID
    year = db.Column(db.String(10), nullable=False)  # 決算年
    month = db.Column(db.String(10), nullable=False)  # 決算月
    sales = db.Column(db.Integer)  # 売上高
    previous_year_sales = db.Column(db.Integer)  # 前年度売上高
    cost_of_sales = db.Column(db.Integer)  # 売上原価
    employee_number = db.Column(db.Integer)  # 従業員数
    business_scale = db.Column(db.String(128))  # 事業規模
    operating_income = db.Column(db.Integer)  # 営業利益
    depreciation_expense = db.Column(db.Integer)  # 減価償却費
    cash_deposit = db.Column(db.Integer)  # 預金
    bills_recivable = db.Column(db.Integer)  # 受取手形
    accounts_receivable = db.Column(db.Integer)  # 売掛金
    inventory = db.Column(db.Integer)  # 棚卸資産
    securities = db.Column(db.Integer)  # 有価証券
    prepaid_expenses = db.Column(db.Integer)  # 前払費用
    current_assets_total = db.Column(db.Integer)  # 流動資産合計
    tangible_assets = db.Column(db.Integer)  # 有形固定資産
    intangible_assets = db.Column(db.Integer)  # 無形固定資産
    investment_assets = db.Column(db.Integer)  # 投資その他の資産
    fixed_assets_total = db.Column(db.Integer)  # 固定資産合計
    total_assets = db.Column(db.Integer)  # 資産合計
    short_term_debt = db.Column(db.Integer)  # 短期借入金
    accrued_expenses = db.Column(db.Integer)  # 未払費用
    advance_received = db.Column(db.Integer)  # 前受金
    current_liabilities_total = db.Column(db.Integer)  # 流動負債合計
    long_term_debt = db.Column(db.Integer)  # 長期借入金
    corporate_bond = db.Column(db.Integer)  # 社債
    retirement_reserve = db.Column(db.Integer)  # 退職給付引当金
    fixed_liabilities_total = db.Column(db.Integer)  # 固定負債合計
    total_liabilities = db.Column(db.Integer)  # 負債合計
    capital_stock = db.Column(db.Integer)  # 資本金
    capital_surplus = db.Column(db.Integer)  # 資本剰余金
    retained_earnings = db.Column(db.Integer)  # 利益剰余金
    valuation_conversion = db.Column(db.Integer)  # 評価・換算差額等
    treasury_stock = db.Column(db.Integer)  # 自己株式
    total_net_assets = db.Column(db.Integer)  # 純資産合計
    gross_profit = db.Column(db.Integer)  # 売上総利益
    non_operating_income = db.Column(db.Integer)  # 営業外収益
    total_revenue = db.Column(db.Integer)  # 収益合計
    general_administrative_expenses = db.Column(db.Integer)  # 販売費及び一般管理費
    non_operating_expenses = db.Column(db.Integer)  # 営業外費用
    special_loss = db.Column(db.Integer)  # 特別損失
    total_expense = db.Column(db.Integer)  # 費用合計
    ordinary_profit = db.Column(db.Integer)  # 経常利益
    income_before_taxes = db.Column(db.Integer)  # 税引前当期純利益
    net_income = db.Column(db.Integer)  # 当期純利益

    large_category = db.Column(db.String(128))
    small_category = db.Column(db.String(128))

    

    def __init__(self, company_id, year="2024", month="1", sales=0, previous_year_sales=0, cost_of_sales=0,
                 employee_number=0, business_scale="", operating_income=0, depreciation_expense=0, cash_deposit=0,
                 bills_recivable=0, accounts_receivable=0, inventory=0, securities=0, prepaid_expenses=0,
                 current_assets_total=0, tangible_assets=0, intangible_assets=0, investment_assets=0,
                 fixed_assets_total=0, total_assets=0, short_term_debt=0, accrued_expenses=0, advance_received=0,
                 current_liabilities_total=0, long_term_debt=0, corporate_bond=0, retirement_reserve=0,
                 fixed_liabilities_total=0, total_liabilities=0, capital_stock=0, capital_surplus=0,
                 retained_earnings=0, valuation_conversion=0, treasury_stock=0, total_net_assets=0, gross_profit=0,
                 non_operating_income=0, total_revenue=0, general_administrative_expenses=0, non_operating_expenses=0,
                 special_loss=0, total_expense=0, ordinary_profit=0, income_before_taxes=0, net_income=0,large_category=None, small_category=None):
        self.company_id = company_id
        self.year = year
        self.month = month
        self.sales = sales
        self.previous_year_sales = previous_year_sales
        self.cost_of_sales = cost_of_sales
        self.employee_number = employee_number
        self.business_scale = business_scale
        self.operating_income = operating_income
        self.depreciation_expense = depreciation_expense
        self.cash_deposit = cash_deposit
        self.bills_recivable = bills_recivable
        self.accounts_receivable = accounts_receivable
        self.inventory = inventory
        self.securities = securities
        self.prepaid_expenses = prepaid_expenses
        self.current_assets_total = current_assets_total
        self.tangible_assets = tangible_assets
        self.intangible_assets = intangible_assets
        self.investment_assets = investment_assets
        self.fixed_assets_total = fixed_assets_total
        self.total_assets = total_assets
        self.short_term_debt = short_term_debt
        self.accrued_expenses = accrued_expenses
        self.advance_received = advance_received
        self.current_liabilities_total = current_liabilities_total
        self.long_term_debt = long_term_debt
        self.corporate_bond = corporate_bond
        self.retirement_reserve = retirement_reserve
        self.fixed_liabilities_total = fixed_liabilities_total
        self.total_liabilities = total_liabilities
        self.capital_stock = capital_stock
        self.capital_surplus = capital_surplus
        self.retained_earnings = retained_earnings
        self.valuation_conversion = valuation_conversion
        self.treasury_stock = treasury_stock
        self.total_net_assets = total_net_assets
        self.gross_profit = gross_profit
        self.non_operating_income = non_operating_income
        self.total_revenue = total_revenue
        self.general_administrative_expenses = general_administrative_expenses
        self.non_operating_expenses = non_operating_expenses
        self.special_loss = special_loss
        self.total_expense = total_expense
        self.ordinary_profit = ordinary_profit
        self.income_before_taxes = income_before_taxes
        self.net_income = net_income
        self.large_category = large_category
        self.small_category = small_category

    def __repr__(self):
        return f"<Settlement {self.id}>"

    def to_dict(self):
        return {
            "id": str(self.id),
            "company_id": str(self.company_id),
            "year": self.year,
            "month": self.month,
            "sales": self.sales,
            "previous_year_sales": self.previous_year_sales,
            "cost_of_sales": self.cost_of_sales,
            "employee_number": self.employee_number,
            "business_scale": self.business_scale,
            "operating_income": self.operating_income,
            "depreciation_expense": self.depreciation_expense,
            "cash_deposit": self.cash_deposit,
            "bills_recivable": self.bills_recivable,
            "accounts_receivable": self.accounts_receivable,
            "inventory": self.inventory,
            "securities": self.securities,
            "prepaid_expenses": self.prepaid_expenses,
            "current_assets_total": self.current_assets_total,
            "tangible_assets": self.tangible_assets,
            "intangible_assets": self.intangible_assets,
            "investment_assets": self.investment_assets,
            "fixed_assets_total": self.fixed_assets_total,
            "total_assets": self.total_assets,
            "short_term_debt": self.short_term_debt,
            "accrued_expenses": self.accrued_expenses,
            "advance_received": self.advance_received,
            "current_liabilities_total": self.current_liabilities_total,
            "long_term_debt": self.long_term_debt,
            "corporate_bond": self.corporate_bond,
            "retirement_reserve": self.retirement_reserve,
            "fixed_liabilities_total": self.fixed_liabilities_total,
            "total_liabilities": self.total_liabilities,
            "capital_stock": self.capital_stock,
            "capital_surplus": self.capital_surplus,
            "retained_earnings": self.retained_earnings,
            "valuation_conversion": self.valuation_conversion,
            "treasury_stock": self.treasury_stock,
            "total_net_assets": self.total_net_assets,
            "gross_profit": self.gross_profit,
            "non_operating_income": self.non_operating_income,
            "total_revenue": self.total_revenue,
            "general_administrative_expenses": self.general_administrative_expenses,
            "non_operating_expenses": self.non_operating_expenses,
            "special_loss": self.special_loss,
            "total_expense": self.total_expense,
            "ordinary_profit": self.ordinary_profit,
            "income_before_taxes": self.income_before_taxes,
            "net_income": self.net_income,
            "large_category":self.large_category, 
            "small_category":self.small_category
        }


class LoginAttempt(db.Model):
    """ログイン試行"""
    __tablename__ = 'login_attempts'
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), nullable=True)
    ip_address = db.Column(db.String(45))
    attempt_time = db.Column(db.DateTime, default=dt.utcnow)
    user_agent = db.Column(db.String(256))
    status = db.Column(db.String(45))

    def __init__(self, user_id=None, ip_address="", attempt_time=dt.utcnow(), user_agent="", status=""):
        self.user_id = user_id
        self.ip_address = ip_address
        self.attempt_time = attempt_time
        self.user_agent = user_agent
        self.status = status
        
    def __repr__(self):
        return f"<LoginAttempt {self.id}>"
    
    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "ip_address": self.ip_address,
            "attempt_time": self.attempt_time,
            "user_agent": self.user_agent,
            "status": self.status
        }


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(UUID(as_uuid=True), nullable=False)
    user_type = db.Column(db.String(50), nullable=False)  # 'user'または'bot'
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=dt.now())
    
    def __init__(self, user_id, user_type, message, timestamp=dt.now()):
        self.user_id = user_id
        self.user_type = user_type
        self.message = message
        self.timestamp = timestamp
    
    def __repr__(self):
        return f"<Message {self.id}>"
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": str(self.user_id),
            "user_type": self.user_type,
            "message": self.message,
            "timestamp": self.timestamp
        }


# DBなしで起動するためコメントアウト
# with app.app_context():
#     db.create_all()