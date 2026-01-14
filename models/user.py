from config import *
from flask import render_template, redirect, url_for, request, flash, jsonify
from models.common import *
from models.model import *
import traceback


@app.route('/user', methods=['GET', 'POST'])
@login_required
def user():
    """ユーザー情報ページ"""
    users = User.query.all()
    for user in users:
        user.settlements = Settlement.query.filter_by(company_id=user.id).all()
    return render_template('user.html', users=users)


@app.route('/user/save', methods=['POST'])
@login_required
def save_user():
    user_id = request.form.get('user_id')
    if user_id and User.query.get(user_id):
        # 既存のユーザーを取得して更新する処理
        user = User.query.get(user_id)
    elif user_id:
        flash('ユーザーが見つかりません', 'danger')
        return redirect(url_for('user'))
    else:
        # 新規ユーザーを作成する処理
        password = generate_password_hash("Password_2024")  # ハッシュ化したパスワード
        user = User(password_hash=password)
        db.session.add(user)
    
    user.company_name = request.form.get('company_name') # 会社名
    user.company_name_kana = request.form.get('company_name_kana') # 会社名（カナ）
    user.representative_name = request.form.get('representative_name') # 代表者名
    user.email = request.form.get('email') # メールアドレス
    user.phone_number = request.form.get('phone_number') # 電話番号
    user.postal_code = request.form.get('postal_code') # 郵便番号
    user.prefecture = request.form.get('prefecture') # 都道府県
    user.city = request.form.get('city') # 市区町村
    user.area = request.form.get('area') # 町域
    user.building = request.form.get('building') # ビル名
    user.manager_name = request.form.get('manager_name') # 管理者名
    user.capital = request.form.get('capital') or 0  # 資本金 
    user.corporate_number = request.form.get('corporate_number')  # 法人番号
    user.invoice_status = request.form.get('invoice_status')  # インボイス番号のステータス
    user.homepage_url = request.form.get('homepage_url')  # 自社ホームページURL
    user.main_industry = request.form.get('main_industry')  # 主たる業種
    user.industry_classification = request.form.get('industry_classification')  # 業種
    user.employee_count = request.form.get('employee_count') or 0  # 社員数
    user.establishment_date = request.form.get('establishment_date') or None  # 設立年月日
    user.revenue = request.form.get('revenue') or 0  # 直近1期の売上高
    user.gross_profit = request.form.get('gross_profit') or 0  # 直近1期の売上総利益
    user.ordinary_profit = request.form.get('ordinary_profit') or 0  # 直近1期の経常利益
    user.office_count = request.form.get('office_count') or 0  # 事業所数
    user.representative_birthdate = request.form.get('representative_birthdate') or None  # 代表者の生年月日
    user.representative_age = request.form.get('representative_age') or 0  # 代表者の満年齢
    user.contact_name = request.form.get('contact_name')  # 連絡担当者氏名
    user.contact_kana = request.form.get('contact_kana')  # 連絡担当者フリガナ
    user.contact_position = request.form.get('contact_position')  # 連絡担当者役職
    user.contact_address = request.form.get('contact_address')  # 連絡担当者住所
    user.contact_phone = request.form.get('contact_phone')  # 連絡担当者電話番号
    user.contact_mobile = request.form.get('contact_mobile')  # 連絡担当者携帯電話番号
    user.contact_fax = request.form.get('contact_fax')  # 連絡担当者FAX番号
    user.contact_email = request.form.get('contact_email')  # 連絡担当者Eメール
    user.successor_name = request.form.get('successor_name')  # 補助事業を中心に行う者の氏名
    user.successor_relation = request.form.get('successor_relation')  # 補助事業を中心に行う者との関係
    user.representative_relation = request.form.get('representative_relation')  # 代表者との関係
    
    try:
        db.session.commit()
        flash('ユーザー情報が更新されました', 'success')
    except Exception as e:
        flash(f'ユーザー情報の更新に失敗しました: {e}', 'danger')
        traceback.print_exc()
    return redirect(url_for('user'))


@app.route('/user/auto-save', methods=['POST'])
@login_required
def auto_save_user():
    user_id = request.form.get('user_id')
    
    if user_id:
        # 既存のユーザーを取得して更新する処理
        user = User.query.get(user_id)
        if user:
            user.company_name = request.form['company_name']
            user.company_name_kana = request.form['company_name_kana']
            user.representative_name = request.form['representative_name']
            user.email = request.form['email']
            user.phone_number = request.form['phone_number']
            user.postal_code = request.form['postal_code']
            user.prefecture = request.form['prefecture']
            user.city = request.form['city']
            user.area = request.form['area']
            user.building = request.form['building']
            user.manager_name = request.form['manager_name']
            user.capital = request.form['capital']
            
            # 新たに追加されたフィールド
            user.corporate_number = request.form['corporate_number']  # 法人番号
            user.invoice_status = request.form['invoice_status']  # インボイス番号のステータス
            user.homepage_url = request.form['homepage_url']  # 自社ホームページURL
            user.main_industry = request.form['main_industry']  # 主たる業種
            user.industry_classification = request.form['industry_classification']  # 業種
            user.employee_count = request.form['employee_count']  # 従業員数
            user.establishment_date = request.form['establishment_date']  # 設立年月日
            user.revenue = request.form['revenue']  # 直近1期の売上高
            user.gross_profit = request.form['gross_profit']  # 直近1期の売上総利益
            user.ordinary_profit = request.form['ordinary_profit']  # 直近1期の経常利益
            user.office_count = request.form['office_count']  # 事業所数
            user.representative_birthdate = request.form['representative_birthdate']  # 代表者の生年月日
            user.representative_age = request.form['representative_age']  # 代表者の満年齢
            user.contact_name = request.form['contact_name']  # 連絡担当者氏名
            user.contact_kana = request.form['contact_kana']  # 連絡担当者フリガナ
            user.contact_position = request.form['contact_position']  # 連絡担当者役職
            user.contact_address = request.form['contact_address']  # 連絡担当者住所successor_name
            user.contact_phone = request.form['contact_phone']  # 連絡担当者電話番号
            user.contact_mobile = request.form['contact_mobile']  # 連絡担当者携帯電話番号
            user.contact_fax = request.form['contact_fax']  # 連絡担当者FAX番号
            user.contact_email = request.form['contact_email']  # 連絡担当者Eメール
            user.successor_name = request.form['successor_name']  # 補助事業を中心に行う者の氏名
            user.successor_relation = request.form['successor_relation']  # 補助事業を中心に行う者との関係
            user.representative_relation = request.form['representative_relation']  # 代表者との関係

            db.session.commit()
            return jsonify({'success': True, 'message': 'ユーザー情報が自動保存されました'})
        else:
            return jsonify({'success': False, 'message': 'ユーザーが見つかりません'})
    else:
        # 新規ユーザーを作成する処理
        password = generate_password_hash("Password_2024")  # ハッシュ化したパスワード
        user = User(
            company_name=request.form['company_name'],
            company_name_kana=request.form['company_name_kana'],
            representative_name=request.form['representative_name'],
            email=request.form['email'],
            phone_number=request.form['phone_number'],
            password_hash=password,
            postal_code=request.form['postal_code'],
            prefecture=request.form['prefecture'],
            city=request.form['city'],
            area=request.form['area'],
            building=request.form['building'],
            manager_name=request.form['manager_name'],
            capital=request.form['capital'],
            
            # 新たに追加されたフィールド
            corporate_number=request.form['corporate_number'],  # 法人番号
            invoice_status=request.form['invoice_status'],  # インボイス番号のステータス
            homepage_url=request.form['homepage_url'],  # 自社ホームページURL
            main_industry=request.form['main_industry'],  # 主たる業種
            industry_classification=request.form['industry_classification'],  # 業種
            employee_count=request.form['employee_count'],  # 従業員数
            establishment_date=request.form['establishment_date'],  # 設立年月日
            revenue=request.form['revenue'],  # 直近1期の売上高
            gross_profit=request.form['gross_profit'],  # 直近1期の売上総利益
            ordinary_profit=request.form['ordinary_profit'],  # 直近1期の経常利益
            office_count=request.form['office_count'],  # 事業所数
            representative_birthdate=request.form['representative_birthdate'],  # 代表者の生年月日
            representative_age=request.form['representative_age'],  # 代表者の満年齢
            contact_name=request.form['contact_name'],  # 連絡担当者氏名
            contact_kana=request.form['contact_kana'],  # 連絡担当者フリガナ
            contact_position=request.form['contact_position'],  # 連絡担当者役職
            contact_address=request.form['contact_address'],  # 連絡担当者住所
            contact_phone=request.form['contact_phone'],  # 連絡担当者電話番号
            contact_mobile=request.form['contact_mobile'],  # 連絡担当者携帯電話番号
            contact_fax=request.form['contact_fax'],  # 連絡担当者FAX番号
            contact_email=request.form['contact_email'],  # 連絡担当者Eメール
            successor_name=request.form['successor_name'],  # 補助事業を中心に行う者の氏名
            successor_relation=request.form['successor_relation'],  # 補助事業を中心に行う者との関係
            representative_relation=request.form['representative_relation']  # 代表者との関係
        )
        db.session.add(user)
        db.session.commit()
        return jsonify({'success': True, 'message': '新しいユーザーが自動保存されました'})


@app.route('/user/delete/<uuid:id>')
@login_required
def delete_user(id):
    user = User.query.get(id)
    if user:
        db.session.delete(user)
        db.session.commit()
        flash('ユーザーが削除されました', 'success')
    else:
        flash('ユーザーが見つかりません', 'danger')
    return redirect(url_for('user'))


@app.route('/user/get/<uuid:id>', methods=['GET'])
@login_required
def get_user(id):
    user = User.query.get(id)
    if user:
        print(f"User data: {user}")  # デバッグ用にコンソールに出力
        return jsonify({
            'id': str(user.id),
            'company_name': user.company_name,
            'company_name_kana': user.company_name_kana,
            'representative_name': user.representative_name,
            'email': user.email,
            'phone_number': user.phone_number,
            'postal_code': user.postal_code,
            'prefecture': user.prefecture,
            'city': user.city,
            'area': user.area,
            'building': user.building,
            'manager_name': user.manager_name,
            'invoice_number': user.invoice_number,
            'capital': user.capital,
            
            # 新しく追加したフィールドを返す
            'corporate_number': user.corporate_number,  # 法人番号
            'invoice_status': user.invoice_status,  # インボイス番号のステータス
            'homepage_url': user.homepage_url,  # 自社ホームページURL
            'main_industry': user.main_industry,  # 主たる業種
            'industry_classification': user.industry_classification,  # 業種
            'employee_count': user.employee_count,  # 従業員数
            'establishment_date': user.establishment_date,  # 設立年月日
            'revenue': user.revenue,  # 直近1期の売上高
            'gross_profit': user.gross_profit,  # 直近1期の売上総利益
            'ordinary_profit': user.ordinary_profit,  # 直近1期の経常利益
            'office_count': user.office_count,  # 事業所数
            'representative_birthdate': user.representative_birthdate,  # 代表者の生年月日
            'representative_age': user.representative_age,  # 代表者の満年齢
            'contact_name': user.contact_name,  # 連絡担当者氏名
            'contact_kana': user.contact_kana,  # 連絡担当者フリガナ
            'contact_position': user.contact_position,  # 連絡担当者役職
            'contact_address': user.contact_address,  # 連絡担当者住所
            'contact_phone': user.contact_phone,  # 連絡担当者電話番号
            'contact_mobile': user.contact_mobile,  # 連絡担当者携帯電話番号
            'contact_fax': user.contact_fax,  # 連絡担当者FAX番号
            'contact_email': user.contact_email,  # 連絡担当者Eメール
            'successor_name': user.successor_name,  # 補助事業を中心に行う者の氏名
            'successor_relation': user.successor_relation,  # 補助事業を中心に行う者との関係
            'representative_relation': user.representative_relation  # 代表者との関係
        })
    return jsonify({'error': 'ユーザーが見つかりません'}), 404



@app.route('/settlements', methods=['GET'])
@login_required
def settlements():
    """決算情報ページ"""
    settlements = Settlement.query.filter_by(company_id=current_user.id).order_by(Settlement.year).all()
    return render_template('settlements.html', settlements=settlements)


@app.route('/settlement/save', methods=['POST'])
@login_required
def save_settlement():
    settlement_id = request.form.get('settlement_id')
    if settlement_id:
        settlement = Settlement.query.get(uuid.UUID(settlement_id))
        if settlement:
            settlement.year = request.form['year']
            settlement.month = request.form['month']
            settlement.sales = int(request.form['sales'])
            settlement.previous_year_sales = int(request.form['previous_year_sales'])            
            settlement.employee_number = int(request.form['employee_number'])
            settlement.business_scale = request.form['business_scale']
            # settlement.cost_of_sales = int(request.form['cost_of_sales'])
            settlement.operating_income = int(request.form['operating_income'])
            settlement.depreciation_expense = int(request.form['depreciation_expense'])
            # settlement.cash_deposit = int(request.form['cash_deposit'])
            # settlement.bills_recivable = int(request.form['bills_recivable'])
            settlement.accounts_receivable = int(request.form['accounts_receivable'])
            settlement.inventory = int(request.form['inventory'])
            # settlement.securities = int(request.form['securities'])
            # settlement.prepaid_expenses = int(request.form['prepaid_expenses'])
            # settlement.current_assets_total = int(request.form['current_assets_total'])
            # settlement.tangible_assets = int(request.form['tangible_assets'])
            # settlement.intangible_assets = int(request.form['intangible_assets'])
            # settlement.investment_assets = int(request.form['investment_assets'])
            # settlement.fixed_assets_total = int(request.form['fixed_assets_total'])
            settlement.total_assets = int(request.form['total_assets'])
            settlement.short_term_debt = int(request.form['short_term_debt'])
            settlement.accrued_expenses = int(request.form['accrued_expenses'])
            # settlement.advance_received = int(request.form['advance_received'])
            # settlement.current_liabilities_total = int(request.form['current_liabilities_total'])
            settlement.long_term_debt = int(request.form['long_term_debt'])
            # settlement.corporate_bond = int(request.form['corporate_bond'])
            # settlement.retirement_reserve = int(request.form['retirement_reserve'])
            # settlement.fixed_liabilities_total = int(request.form['fixed_liabilities_total'])
            # settlement.total_liabilities = int(request.form['total_liabilities'])
            settlement.capital_stock = int(request.form['capital_stock'])
            # settlement.capital_surplus = int(request.form['capital_surplus'])
            # settlement.retained_earnings = int(request.form['retained_earnings'])
            # settlement.valuation_conversion = int(request.form['valuation_conversion'])
            # settlement.treasury_stock = int(request.form['treasury_stock'])
            settlement.total_net_assets = int(request.form['total_net_assets'])
            # settlement.gross_profit = int(request.form['gross_profit'])
            # settlement.non_operating_income = int(request.form['non_operating_income'])
            # settlement.total_revenue = int(request.form['total_revenue'])
            settlement.general_administrative_expenses = int(request.form['general_administrative_expenses'])
            # settlement.non_operating_expenses = int(request.form['non_operating_expenses'])
            # settlement.special_loss = int(request.form['special_loss'])
            # settlement.total_expense = int(request.form['total_expense'])
            # settlement.ordinary_profit = int(request.form['ordinary_profit'])
            # settlement.income_before_taxes = int(request.form['income_before_taxes'])
            # settlement.net_income = int(request.form['net_income'])
            
            settlement.large_category = request.form['large_category']
            settlement.small_category = request.form['small_category']

            settlement.updated_at = dt.now()
            db.session.commit()
            flash('決算情報が更新されました', 'success')
    else:
        new_settlement = Settlement(
            company_id=current_user.id,
            year=request.form['year'],
            month=request.form['month'],
            sales=int(request.form['sales']),
            previous_year_sales=int(request.form['previous_year_sales']),            
            employee_number=int(request.form['employee_number']),
            business_scale=request.form['business_scale'],
            # cost_of_sales=int(request.form['cost_of_sales']),
            operating_income=int(request.form['operating_income']),
            depreciation_expense=int(request.form['depreciation_expense']),
            # cash_deposit=int(request.form['cash_deposit']),
            # bills_recivable=int(request.form['bills_recivable']),
            accounts_receivable=int(request.form['accounts_receivable']),
            inventory=int(request.form['inventory']),
            # securities=int(request.form['securities']),
            # prepaid_expenses=int(request.form['prepaid_expenses']),
            # current_assets_total=int(request.form['current_assets_total']),
            # tangible_assets=int(request.form['tangible_assets']),
            # intangible_assets=int(request.form['intangible_assets']),
            # investment_assets=int(request.form['investment_assets']),
            # fixed_assets_total=int(request.form['fixed_assets_total']),
            total_assets=int(request.form['total_assets']),
            short_term_debt=int(request.form['short_term_debt']),
            accrued_expenses=int(request.form['accrued_expenses']),
            # advance_received=int(request.form['advance_received']),
            # current_liabilities_total=int(request.form['current_liabilities_total']),
            long_term_debt=int(request.form['long_term_debt']),
            # corporate_bond=int(request.form['corporate_bond']),
            # retirement_reserve=int(request.form['retirement_reserve']),
            # fixed_liabilities_total=int(request.form['fixed_liabilities_total']),
            # total_liabilities=int(request.form['total_liabilities']),
            capital_stock=int(request.form['capital_stock']),
            # capital_surplus=int(request.form['capital_surplus']),
            # retained_earnings=int(request.form['retained_earnings']),
            # valuation_conversion=int(request.form['valuation_conversion']),
            # treasury_stock=int(request.form['treasury_stock']),
            total_net_assets=int(request.form['total_net_assets']),
            # gross_profit=int(request.form['gross_profit']),
            # non_operating_income=int(request.form['non_operating_income']),
            # total_revenue=int(request.form['total_revenue']),
            general_administrative_expenses=int(request.form['general_administrative_expenses']),
            # non_operating_expenses=int(request.form['non_operating_expenses']),
            # special_loss=int(request.form['special_loss']),
            # total_expense=int(request.form['total_expense']),
            # ordinary_profit=int(request.form['ordinary_profit']),
            # income_before_taxes=int(request.form['income_before_taxes']),
            # net_income=int(request.form['net_income'])
            large_category=request.form['large_category'],
            small_category=request.form['small_category']

        )
        db.session.add(new_settlement)
        db.session.commit()
        flash('新しい決算情報が追加されました', 'success')
    return redirect(url_for('settlements'))



@app.route('/settlement/get/<uuid:settlement_id>', methods=['GET'])
@login_required
def get_settlement(settlement_id):
    settlement = Settlement.query.get(settlement_id)
    if settlement:
        return jsonify({
            'id': str(settlement.id),
            'year': settlement.year,
            'month': settlement.month,
            'sales': settlement.sales,
            'previous_year_sales': settlement.previous_year_sales,            
            'employee_number': settlement.employee_number,
            'business_scale': settlement.business_scale,
            # 'cost_of_sales': settlement.cost_of_sales,
            'operating_income': settlement.operating_income,
            'depreciation_expense': settlement.depreciation_expense,
            # 'cash_deposit': settlement.cash_deposit,
            # 'bills_recivable': settlement.bills_recivable,
            'accounts_receivable': settlement.accounts_receivable,
            'inventory': settlement.inventory,
            # 'securities': settlement.securities,
            # 'prepaid_expenses': settlement.prepaid_expenses,
            # 'current_assets_total': settlement.current_assets_total,
            # 'tangible_assets': settlement.tangible_assets,
            # 'intangible_assets': settlement.intangible_assets,
            # 'investment_assets': settlement.investment_assets,
            # 'fixed_assets_total': settlement.fixed_assets_total,
            'total_assets': settlement.total_assets,
            'short_term_debt': settlement.short_term_debt,
            'accrued_expenses': settlement.accrued_expenses,
            # 'advance_received': settlement.advance_received,
            # 'current_liabilities_total': settlement.current_liabilities_total,
            'long_term_debt': settlement.long_term_debt,
            # 'corporate_bond': settlement.corporate_bond,
            # 'retirement_reserve': settlement.retirement_reserve,
            # 'fixed_liabilities_total': settlement.fixed_liabilities_total,
            # 'total_liabilities': settlement.total_liabilities,
            'capital_stock': settlement.capital_stock,
            # 'capital_surplus': settlement.capital_surplus,
            # 'retained_earnings': settlement.retained_earnings,
            # 'valuation_conversion': settlement.valuation_conversion,
            # 'treasury_stock': settlement.treasury_stock,
            'total_net_assets': settlement.total_net_assets,
            # 'gross_profit': settlement.gross_profit,
            # 'non_operating_income': settlement.non_operating_income,
            # 'total_revenue': settlement.total_revenue,
            'general_administrative_expenses': settlement.general_administrative_expenses,
            # 'non_operating_expenses': settlement.non_operating_expenses,
            # 'special_loss': settlement.special_loss,
            # 'total_expense': settlement.total_expense,
            # 'ordinary_profit': settlement.ordinary_profit,
            # 'income_before_taxes': settlement.income_before_taxes,
            # 'net_income': settlement.net_income
            'large_category': settlement.large_category,
            'small_category': settlement.small_category

        })
    else:
        return jsonify({'error': '決算情報が見つかりません'}), 404




@app.route('/settlement/delete/<uuid:settlement_id>', methods=['GET'])
@login_required
def delete_settlement(settlement_id):
    settlement = Settlement.query.get(settlement_id)
    if settlement:
        db.session.delete(settlement)
        db.session.commit()
        flash('決算情報が削除されました', 'success')
    else:
        flash('決算情報が見つかりません', 'danger')
    return redirect(url_for('settlements'))


@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        # 現在のパスワードが正しいかチェック
        if not check_password_hash(current_user.password_hash, current_password):
            flash('現在のパスワードが間違っています', 'danger')
            return redirect(url_for('change_password'))

        # 新しいパスワードが一致しているかチェック
        if new_password != confirm_password:
            flash('新しいパスワードが一致しません', 'danger')
            return redirect(url_for('change_password'))

        # パスワードを更新
        current_user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        flash('パスワードが正常に変更されました', 'success')
        return redirect(url_for('financial_analysis'))  # プロフィールページなどにリダイレクト

    return render_template('change_password.html')