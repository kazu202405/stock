from config import *
from flask import render_template, redirect, url_for, request, flash, jsonify, session
from flask_login import login_required, current_user
from models.common import *
from models.model import *
from datetime import datetime, timedelta
from tqdm import tqdm
from math import ceil
from sqlalchemy.orm import joinedload
import time
from sqlalchemy import cast, Integer, func
import pandas as pd

# Excelファイルの読み込みとカラムのリネーム
file_path = 'tools/基準値.xlsx'
df = pd.read_excel(file_path)
df = df.rename(columns={
    '大分類': 'large_category', 
    '小分類': 'small_category', 
    '事業規模': 'business_scale',
    '売上増加率_中央値': 'sales_growth_rate_median',
    '売上増加率_標準偏差': 'sales_growth_rate_std',
    '売上増加率_ⅳ': 'sales_growth_rate_iv',
    '売上増加率_ⅲ': 'sales_growth_rate_iii',
    '売上増加率_ⅱ': 'sales_growth_rate_ii',
    '売上増加率_ⅰ': 'sales_growth_rate_i',
    '営業利益率_中央値': 'operating_profit_margin_median',
    '営業利益率_標準偏差': 'operating_profit_margin_std',
    '営業利益率_ⅳ': 'operating_profit_margin_iv',
    '営業利益率_ⅲ': 'operating_profit_margin_iii',
    '営業利益率_ⅱ': 'operating_profit_margin_ii',
    '営業利益率_ⅰ': 'operating_profit_margin_i',
    '労働生産性_中央値': 'labor_productivity_median',
    '労働生産性_標準偏差': 'labor_productivity_std',
    '労働生産性_ⅳ': 'labor_productivity_iv',
    '労働生産性_ⅲ': 'labor_productivity_iii',
    '労働生産性_ⅱ': 'labor_productivity_ii',
    '労働生産性_ⅰ': 'labor_productivity_i',
    'ＥＢＴＤＡ有利子負債倍率_中央値': 'ebitda_interest_bearing_debt_ratio_median',
    'ＥＢＴＤＡ有利子負債倍率_標準偏差': 'ebitda_interest_bearing_debt_ratio_std',
    'ＥＢＴＤＡ有利子負債倍率_ⅳ': 'ebitda_interest_bearing_debt_ratio_iv',
    'ＥＢＴＤＡ有利子負債倍率_ⅲ': 'ebitda_interest_bearing_debt_ratio_iii',
    'ＥＢＴＤＡ有利子負債倍率_ⅱ': 'ebitda_interest_bearing_debt_ratio_ii',
    'ＥＢＴＤＡ有利子負債倍率_ⅰ': 'ebitda_interest_bearing_debt_ratio_i',
    '営業運転資本回転期間_中央値': 'operating_working_capital_turnover_period_median',
    '営業運転資本回転期間_標準偏差': 'operating_working_capital_turnover_period_std',
    '営業運転資本回転期間_ⅳ': 'operating_working_capital_turnover_period_iv',
    '営業運転資本回転期間_ⅲ': 'operating_working_capital_turnover_period_iii',
    '営業運転資本回転期間_ⅱ': 'operating_working_capital_turnover_period_ii',
    '営業運転資本回転期間_ⅰ': 'operating_working_capital_turnover_period_i',
    '自己資本比率_中央値': 'equity_ratio_median',
    '自己資本比率_標準偏差': 'equity_ratio_std',
    '自己資本比率_ⅳ': 'equity_ratio_iv',
    '自己資本比率_ⅲ': 'equity_ratio_iii',
    '自己資本比率_ⅱ': 'equity_ratio_ii',
    '自己資本比率_ⅰ': 'equity_ratio_i'
})

def get_median_value(df, indicators, median_column_name):
    matching_row = df[
        (df['large_category'] == indicators['large_category']) &
        (df['small_category'] == indicators['small_category']) &
        (df['business_scale'] == indicators['business_scale'])
    ]

    if not matching_row.empty:
        median_value = matching_row[median_column_name].values[0]
        return median_value
    else:
        # 該当する行が見つからない場合の処理
        print(f"基準値のExcelファイルに該当する行が見つかりませんでした。")
        return None
    
    
def calculate_indicators(settlement, previous_settlement, df):
    if not settlement:
        return {"error": "Data not found"}

    # 前期売上高を取得
    previous_sales = previous_settlement.sales if previous_settlement and previous_settlement.sales not in [None, 0] else 0

    # 売上高成長率: （当期売上高 - 前期売上高）÷ 前期売上高
    sales_growth_rate = round(((settlement.sales - previous_sales) / previous_sales) * 100, 2) if previous_sales != 0 else 0

    # 営業利益率: 営業利益 ÷ 売上高
    operating_profit_margin = round((settlement.operating_income / settlement.sales) * 100, 2) if settlement.sales not in [None, 0] else 0

    # 労働生産性： 付加価値 ÷ 従業員数
    value_added = settlement.operating_income  # または ebitda
    labor_productivity = round(value_added / settlement.employee_number, 2) if settlement.employee_number not in [None, 0] else 0

    # EBITDA有利子負債倍率: 有利子負債 ÷ EBITDA
    ebitda = settlement.operating_income + settlement.depreciation_expense
    ebitda_interest_bearing_debt_ratio = round((settlement.short_term_debt + settlement.long_term_debt) / ebitda, 2) if ebitda not in [None, 0] else 0

    # 営業運転資本回転期間： （営業運転資本 ÷ 売上高） × 365日
    operating_working_capital = settlement.accounts_receivable + settlement.inventory - settlement.accrued_expenses
    operating_working_capital_turnover_period = round((operating_working_capital / settlement.sales) * 365 / 30, 2) if settlement.sales not in [None, 0] else 0  # 月換算

    # 自己資本比率: 自己資本 ÷ 総資産
    equity_ratio = round((settlement.total_net_assets / settlement.total_assets) * 100, 2) if settlement.total_assets not in [None, 0] else 0

    # インジケーター辞書を作成
    indicators = {
        "sales_growth_rate": sales_growth_rate,
        "operating_profit_margin": operating_profit_margin,
        "labor_productivity": labor_productivity,
        "ebitda_interest_bearing_debt_ratio": ebitda_interest_bearing_debt_ratio,
        "operating_working_capital_turnover_period": operating_working_capital_turnover_period,
        "equity_ratio": equity_ratio,
        "large_category": settlement.large_category,
        "small_category": settlement.small_category,
        "business_scale": settlement.business_scale,
        "year": settlement.year,
        "month": settlement.month,
        "sales": settlement.sales,
        "operating_income": settlement.operating_income,
        "employee_number": settlement.employee_number
    }

    # 該当する行を取得
    row = df[
        (df['large_category'] == indicators['large_category']) & 
        (df['small_category'] == indicators['small_category']) & 
        (df['business_scale'] == indicators['business_scale'])
    ]
        # デバッグ情報の追加
    print(f"Processing settlement for year: {settlement.year}")
    print(f"Large Category: {indicators['large_category']}")
    print(f"Small Category: {indicators['small_category']}")
    print(f"Business Scale: {indicators['business_scale']}")
    print("Matching row in Excel:")
    print(row)
    

    if not row.empty:
        # 指標とExcel列名のマッピング
        indicator_columns = {
            'sales_growth_rate': 'sales_growth_rate',
            'operating_profit_margin': 'operating_profit_margin',
            'labor_productivity': 'labor_productivity',
            'ebitda_interest_bearing_debt_ratio': 'ebitda_interest_bearing_debt_ratio',
            'operating_working_capital_turnover_period': 'operating_working_capital_turnover_period',
            'equity_ratio': 'equity_ratio'
        }

        # スコアを計算する関数を定義
        def score_value(value, iv, iii, ii, i):
            if value < iv:
                return 1
            elif iv <= value < iii:
                return 2
            elif iii <= value < ii:
                return 3
            elif ii <= value < i:
                return 4
            else:
                return 5

        # 各指標についてスコアと中央値を取得し、indicatorsに追加
        for ind_key, col_prefix in indicator_columns.items():
            # 該当する閾値を取得
            iv = row[f'{col_prefix}_iv'].values[0]
            iii = row[f'{col_prefix}_iii'].values[0]
            ii = row[f'{col_prefix}_ii'].values[0]
            i = row[f'{col_prefix}_i'].values[0]

            # 指標の値を取得
            value = indicators[ind_key]

            # スコアを計算
            score = score_value(value, iv, iii, ii, i)
            indicators[f'{ind_key}_score'] = score

            # 中央値を取得
            median_column_name = f'{col_prefix}_median'
            median_value = row[median_column_name].values[0]
            indicators[f'{ind_key}_median'] = median_value
    else:
        # 該当する行が見つからない場合の処理
        print("基準値のExcelファイルに該当する行が見つかりませんでした。")
        # 該当するキーにデフォルト値を設定
        indicator_columns = {
            'sales_growth_rate': 'sales_growth_rate',
            'operating_profit_margin': 'operating_profit_margin',
            'labor_productivity': 'labor_productivity',
            'ebitda_interest_bearing_debt_ratio': 'ebitda_interest_bearing_debt_ratio',
            'operating_working_capital_turnover_period': 'operating_working_capital_turnover_period',
            'equity_ratio': 'equity_ratio'
        }
        for ind_key in indicator_columns.keys():
            indicators[f'{ind_key}_score'] = None
            indicators[f'{ind_key}_median'] = None
            
    print("Indicators:", indicators)        
    return indicators





@app.route('/financial_analysis', methods=['GET', 'POST'])
def financial_analysis():
    # ログイン状態をチェック
    if not current_user.is_authenticated:
        flash('この機能を利用するにはログインが必要です', 'warning')
        return redirect(url_for('login'))
    
    # current_user からログインユーザーの company_id を取得
    company_id = current_user.id  # current_user は users テーブルのレコードを表している

    # company_id を使って settlements テーブルから最新の決算情報を取得
    latest_settlements = Settlement.query.filter_by(company_id=company_id).order_by(Settlement.year.desc()).limit(3).all()
    
    indicators_list = []

    if latest_settlements:
        # Excelファイルは既に読み込み済み（ファイルの先頭で）

        for settlement in latest_settlements:
            # 前期の決算情報を取得
            previous_settlement = Settlement.query.filter_by(company_id=company_id, year=int(settlement.year) - 1).first()

            # インジケーターを計算
            indicators = calculate_indicators(settlement, previous_settlement, df)
            indicators_list.append(indicators)

        # テンプレートに渡すデータ
        return render_template('financial_analysis.html', indicators=indicators_list)

    else:
        flash('決算情報が3件以上登録されている必要があります。', 'danger')
        return render_template('financial_analysis.html', indicators=indicators_list)