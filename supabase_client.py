# Supabase接続クライアント
import os
from supabase import create_client, Client
from dotenv import load_dotenv

# .envファイルを読み込み
load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

_client: Client = None

def get_supabase_client() -> Client:
    """Supabaseクライアントを取得（シングルトン）"""
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


# ウォッチリスト操作関数
def add_to_watchlist(company_code: str) -> dict:
    """銘柄をウォッチリストに追加"""
    client = get_supabase_client()
    result = client.table('watched_tickers').upsert({
        'company_code': company_code
    }).execute()
    return result.data


def remove_from_watchlist(company_code: str) -> dict:
    """銘柄をウォッチリストから削除"""
    client = get_supabase_client()
    result = client.table('watched_tickers').delete().eq(
        'company_code', company_code
    ).execute()
    return result.data


def get_watchlist() -> list:
    """ウォッチリスト一覧を取得"""
    client = get_supabase_client()
    result = client.table('watched_tickers').select('*').order(
        'created_at', desc=True
    ).execute()
    return result.data


def is_in_watchlist(company_code: str) -> bool:
    """銘柄がウォッチリストに登録されているか確認"""
    client = get_supabase_client()
    result = client.table('watched_tickers').select('company_code').eq(
        'company_code', company_code
    ).execute()
    return len(result.data) > 0


# screened_latestテーブル操作
def get_screened_data(company_code: str) -> dict:
    """screened_latestから銘柄データを取得"""
    client = get_supabase_client()
    result = client.table('screened_latest').select('*').eq(
        'company_code', company_code
    ).execute()
    return result.data[0] if result.data else None


def upsert_screened_data(data: dict) -> dict:
    """screened_latestにデータを登録/更新"""
    client = get_supabase_client()
    result = client.table('screened_latest').upsert(data).execute()
    return result.data


def update_screened_data(company_code: str, data: dict) -> dict:
    """screened_latestの指定フィールドを更新"""
    client = get_supabase_client()
    result = client.table('screened_latest').update(data).eq(
        'company_code', company_code
    ).execute()
    return result.data


def get_watchlist_with_details() -> list:
    """ウォッチリストの銘柄を詳細データ付きで取得"""
    client = get_supabase_client()

    # watched_tickersの銘柄コード一覧を取得
    watchlist = client.table('watched_tickers').select('company_code, created_at').order(
        'created_at', desc=True
    ).execute()

    if not watchlist.data:
        return []

    # 銘柄コードのリストを作成
    codes = [item['company_code'] for item in watchlist.data]

    # screened_latestから詳細データを取得
    details = client.table('screened_latest').select('*').in_(
        'company_code', codes
    ).execute()

    # 詳細データをマップ化
    details_map = {item['company_code']: item for item in details.data}

    # ウォッチリストと詳細データを結合
    result = []
    for item in watchlist.data:
        code = item['company_code']
        detail = details_map.get(code, {})
        result.append({
            'company_code': code,
            'created_at': item['created_at'],
            **detail
        })

    return result


# =============================================
# 合致度計算関数（yomu.md基準）
# =============================================

def calculate_match_rate(data: dict) -> int:
    """
    財務指標の投資基準への合致度を計算（0-100点）

    yomu.md基準（12項目、100点満点）:
    1. 時価総額 <= 700億円
    2. 自己資本比率 >= 30%
    3. 売上高増減率(2期前→前期) > 0%
    4. 売上高増減率(前期→今期予) > 0%  ★NEW
    5. 売上高営業利益率 >= 10%
    6. 営業利益増減率(2期前→前期) > 0%
    7. 営業利益増減率(前期→今期予) > 0%  ★NEW
    8. 営業CF前期 > 0億円
    9. フリーCF前期 > 0億円
    10. ROA(前期) > 4.5%
    11. PER(来期) < 40倍
    12. PBR < 10倍
    """
    import json

    score = 0

    # 1. 時価総額 <= 700億円（10点）
    market_cap = data.get('market_cap')
    if market_cap is not None:
        if market_cap <= 700:
            score += 10

    # 2. 自己資本比率 >= 30%（10点）
    equity_ratio = data.get('equity_ratio')
    if equity_ratio is not None:
        if equity_ratio >= 30:
            score += 10

    # 3. 売上高増減率(2期前→前期) > 0%（10点）
    # 4. 売上高営業利益率 >= 10%（10点）
    # 5. 営業利益増減率(2期前→前期) > 0%（10点）
    financial_history = data.get('financial_history')
    if financial_history:
        if isinstance(financial_history, str):
            try:
                financial_history = json.loads(financial_history)
            except:
                financial_history = {}

        # 売上高増減率(2期前→前期) > 0%
        revenue_list = financial_history.get('revenue', [])
        if len(revenue_list) >= 2:
            sorted_revenue = sorted(revenue_list, key=lambda x: x.get('date', ''), reverse=True)
            if len(sorted_revenue) >= 2:
                # sorted_revenue[0] = 前期, sorted_revenue[1] = 2期前
                current = sorted_revenue[0].get('value')
                previous = sorted_revenue[1].get('value')
                if current and previous and previous > 0:
                    growth_rate = ((current - previous) / previous) * 100
                    if growth_rate > 0:
                        score += 10

        # 営業利益増減率(2期前→前期) > 0%
        op_income_list = financial_history.get('op_income', [])
        if len(op_income_list) >= 2:
            sorted_op = sorted(op_income_list, key=lambda x: x.get('date', ''), reverse=True)
            if len(sorted_op) >= 2:
                current = sorted_op[0].get('value')
                previous = sorted_op[1].get('value')
                if current and previous and previous > 0:
                    growth_rate = ((current - previous) / previous) * 100
                    if growth_rate > 0:
                        score += 10

    # 4. 売上高増減率(前期→今期予) > 0%（10点）★NEW
    forecast_revenue = data.get('forecast_revenue')
    if forecast_revenue and financial_history:
        revenue_list = financial_history.get('revenue', [])
        if revenue_list:
            sorted_rev = sorted(revenue_list, key=lambda x: x.get('date', ''), reverse=True)
            if sorted_rev:
                last_revenue = sorted_rev[0].get('value')
                if last_revenue and last_revenue > 0:
                    # forecast_revenueは億円単位、last_revenueは円単位なので変換
                    forecast_rev_yen = forecast_revenue * 1e8
                    growth_rate = ((forecast_rev_yen - last_revenue) / last_revenue) * 100
                    if growth_rate > 0:
                        score += 10

    # 7. 営業利益増減率(前期→今期予) > 0%（10点）★NEW
    forecast_op_income = data.get('forecast_op_income')
    if forecast_op_income and financial_history:
        op_income_list = financial_history.get('op_income', [])
        if op_income_list:
            sorted_op = sorted(op_income_list, key=lambda x: x.get('date', ''), reverse=True)
            if sorted_op:
                last_op_income = sorted_op[0].get('value')
                if last_op_income and last_op_income > 0:
                    # forecast_op_incomeは億円単位、last_op_incomeは円単位なので変換
                    forecast_op_yen = forecast_op_income * 1e8
                    growth_rate = ((forecast_op_yen - last_op_income) / last_op_income) * 100
                    if growth_rate > 0:
                        score += 10

    # 5. 売上高営業利益率 >= 10%（10点）
    operating_margin = data.get('operating_margin')
    if operating_margin is not None:
        if operating_margin >= 10:
            score += 10

    # 8. 営業CF前期 > 0億円（10点）
    operating_cf = data.get('operating_cf')
    if operating_cf is not None:
        if operating_cf > 0:
            score += 10

    # 9. フリーCF前期 > 0億円（10点）
    free_cf = data.get('free_cf')
    if free_cf is not None:
        if free_cf > 0:
            score += 10

    # 10. ROA(前期) > 4.5%（10点）
    # roaはcf_historyに格納されている場合がある
    roa = data.get('roa')
    if roa is None:
        cf_history = data.get('cf_history')
        if cf_history:
            if isinstance(cf_history, str):
                try:
                    cf_history = json.loads(cf_history)
                except:
                    cf_history = {}
            roa_list = cf_history.get('roa', [])
            if roa_list and len(roa_list) > 0:
                sorted_roa = sorted(roa_list, key=lambda x: x.get('date', ''), reverse=True)
                roa = sorted_roa[0].get('value')
    if roa is not None:
        if roa > 4.5:
            score += 10

    # 11. PER(来期) < 40倍（10点）
    per = data.get('per_forward')
    if per is not None and per > 0:
        if per < 40:
            score += 10

    # 12. PBR < 10倍（10点）
    pbr = data.get('pbr')
    if pbr is not None and pbr > 0:
        if pbr < 10:
            score += 10

    # 12項目×10点=120点を100点満点に正規化
    return round(score * 100 / 120)


def upsert_screened_data_with_match_rate(data: dict) -> dict:
    """screened_latestにデータを登録/更新（合致度を自動計算）"""
    # 合致度を計算して追加
    data['match_rate'] = calculate_match_rate(data)

    client = get_supabase_client()
    result = client.table('screened_latest').upsert(data).execute()
    return result.data


# =============================================
# GC銘柄テーブル操作
# =============================================

def upsert_gc_stocks(stocks: list) -> list:
    """GC銘柄データを全削除後に一括登録（スナップショット方式）"""
    client = get_supabase_client()
    client.table('gc_stocks').delete().neq('company_code', '').execute()
    if stocks:
        result = client.table('gc_stocks').insert(stocks).execute()
        return result.data
    return []


def get_gc_stocks() -> list:
    """GC銘柄一覧を取得"""
    client = get_supabase_client()
    result = client.table('gc_stocks').select('*').order(
        'company_code', desc=False
    ).execute()
    return result.data


# =============================================
# DC銘柄テーブル操作
# =============================================

def upsert_dc_stocks(stocks: list) -> list:
    """DC銘柄データを全削除後に一括登録（スナップショット方式）"""
    client = get_supabase_client()
    client.table('dc_stocks').delete().neq('company_code', '').execute()
    if stocks:
        result = client.table('dc_stocks').insert(stocks).execute()
        return result.data
    return []


def get_dc_stocks() -> list:
    """DC銘柄一覧を取得"""
    client = get_supabase_client()
    result = client.table('dc_stocks').select('*').order(
        'company_code', desc=False
    ).execute()
    return result.data
