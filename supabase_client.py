# Supabase接続クライアント
import os
import string
import random
from supabase import create_client, Client
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

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
    """screened_latestにデータを登録/更新（is_dividendフラグを保持）"""
    client = get_supabase_client()
    # 既存のis_dividendフラグを保持
    if 'is_dividend' not in data and data.get('company_code'):
        existing = get_screened_data(data['company_code'])
        if existing and existing.get('is_dividend'):
            data['is_dividend'] = True
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
    investing_cf = None
    # トップレベル値がない場合はcf_historyからフォールバック
    if operating_cf is None:
        cf_history_cf = data.get('cf_history')
        if cf_history_cf:
            if isinstance(cf_history_cf, str):
                try:
                    cf_history_cf = json.loads(cf_history_cf)
                except:
                    cf_history_cf = {}
            op_cf_list = cf_history_cf.get('operating_cf', [])
            if op_cf_list and len(op_cf_list) > 0:
                sorted_cf = sorted(op_cf_list, key=lambda x: x.get('date', ''), reverse=True)
                val = sorted_cf[0].get('value')
                if val is not None:
                    operating_cf = val / 1e8
            inv_cf_list = cf_history_cf.get('investing_cf', [])
            if inv_cf_list and len(inv_cf_list) > 0:
                sorted_inv = sorted(inv_cf_list, key=lambda x: x.get('date', ''), reverse=True)
                val = sorted_inv[0].get('value')
                if val is not None:
                    investing_cf = val / 1e8
    if operating_cf is not None:
        if operating_cf > 0:
            score += 10

    # 9. フリーCF前期 > 0億円（10点）
    free_cf = data.get('free_cf')
    # トップレベル値がない場合はoperating_cf + investing_cfから算出
    if free_cf is None and operating_cf is not None and investing_cf is not None:
        free_cf = operating_cf + investing_cf
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
    """screened_latestにデータを登録/更新（合致度を自動計算、is_dividendフラグ保持）"""
    # 既存データとマージして合致度を計算（新データにないフィールドも考慮）
    company_code = data.get('company_code')
    if company_code:
        existing = get_screened_data(company_code) or {}
        merged = {**existing, **data}
        data['match_rate'] = calculate_match_rate(merged)
        # 既存のis_dividendフラグを保持
        if 'is_dividend' not in data and existing.get('is_dividend'):
            data['is_dividend'] = True
    else:
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


def get_technical_stocks() -> list:
    """GC/DC形成日を持つ銘柄を一覧取得"""
    client = get_supabase_client()
    result = client.table('screened_latest').select('*').or_(
        'gc_date.not.is.null,dc_date.not.is.null'
    ).order('company_code').execute()
    return result.data


# =============================================
# signal_stocks統合テーブル操作
# =============================================

def get_signal_gc_stocks() -> list:
    """signal_stocksからGC銘柄を取得"""
    client = get_supabase_client()
    result = client.table('signal_stocks').select('*').not_.is_(
        'gc_date', 'null'
    ).order('company_code').execute()
    return result.data


def get_signal_dc_stocks() -> list:
    """signal_stocksからDC銘柄を取得"""
    client = get_supabase_client()
    result = client.table('signal_stocks').select('*').not_.is_(
        'dc_date', 'null'
    ).order('company_code').execute()
    return result.data


def upsert_signal_stocks(stocks: list) -> list:
    """signal_stocksに銘柄をupsert"""
    client = get_supabase_client()
    if stocks:
        result = client.table('signal_stocks').upsert(stocks).execute()
        return result.data
    return []


# =============================================
# 高配当企業操作
# =============================================

def get_dividend_stocks() -> list:
    """高配当フラグが立っている銘柄を取得"""
    client = get_supabase_client()
    result = client.table('screened_latest').select('*').eq(
        'is_dividend', True
    ).order('company_code').execute()
    return result.data


def set_dividend_flag(company_code: str, flag: bool = True) -> dict:
    """screened_latestの高配当フラグを設定"""
    client = get_supabase_client()
    # 既存レコードがあればupdate、なければinsert
    existing = client.table('screened_latest').select('company_code').eq(
        'company_code', company_code
    ).execute()
    if existing.data:
        result = client.table('screened_latest').update({
            'is_dividend': flag
        }).eq('company_code', company_code).execute()
    else:
        result = client.table('screened_latest').insert({
            'company_code': company_code,
            'company_name': company_code,
            'is_dividend': flag
        }).execute()
    return result.data


def remove_dividend_flag(company_code: str) -> dict:
    """高配当フラグを解除"""
    return set_dividend_flag(company_code, False)


# =============================================
# お気に入り銘柄操作
# =============================================

def add_favorite_stock(user_id: str, company_code: str) -> dict:
    """お気に入り銘柄を追加（upsert）"""
    client = get_supabase_client()
    result = client.table('favorite_stocks').upsert({
        'user_id': user_id,
        'company_code': company_code
    }).execute()
    return result.data


def remove_favorite_stock(user_id: str, company_code: str) -> dict:
    """お気に入り銘柄を削除"""
    client = get_supabase_client()
    result = client.table('favorite_stocks').delete().eq(
        'user_id', user_id
    ).eq('company_code', company_code).execute()
    return result.data


def get_favorite_stocks(user_id: str) -> list:
    """お気に入り銘柄を詳細データ付きで取得"""
    client = get_supabase_client()

    # お気に入り一覧を取得
    favorites = client.table('favorite_stocks').select(
        'company_code, created_at'
    ).eq('user_id', user_id).order('created_at', desc=True).execute()

    if not favorites.data:
        return []

    # 銘柄コードのリストを作成
    codes = [item['company_code'] for item in favorites.data]

    # screened_latestから詳細データを取得
    details = client.table('screened_latest').select('*').in_(
        'company_code', codes
    ).execute()

    # 詳細データをマップ化
    details_map = {item['company_code']: item for item in details.data}

    # お気に入りと詳細データを結合
    result = []
    for item in favorites.data:
        code = item['company_code']
        detail = details_map.get(code, {})
        result.append({
            'company_code': code,
            'favorited_at': item['created_at'],
            **detail
        })

    return result


def is_favorite_stock(user_id: str, company_code: str) -> bool:
    """銘柄がお気に入りに登録されているか確認"""
    client = get_supabase_client()
    result = client.table('favorite_stocks').select('id').eq(
        'user_id', user_id
    ).eq('company_code', company_code).execute()
    return len(result.data) > 0


# =============================================
# ノート（notes）テーブル操作
# =============================================

def create_note(user_id: str, data: dict) -> dict:
    """ノートを新規作成"""
    client = get_supabase_client()
    note_data = {
        'user_id': user_id,
        'title': data['title'],
        'content': data['content'],
        'company_code': data.get('company_code'),
        'company_name': data.get('company_name'),
        'stars': data.get('stars', 0),
        'tags': data.get('tags', []),
        'is_public': data.get('is_public', False),
        'is_anonymous': data.get('is_anonymous', False),
    }
    if data.get('poster_name'):
        note_data['poster_name'] = data['poster_name']
    result = client.table('notes').insert(note_data).execute()
    return result.data[0] if result.data else {}


def get_user_notes(user_id: str) -> list:
    """ユーザーのノート一覧を取得（新しい順）"""
    client = get_supabase_client()
    result = client.table('notes').select('*').eq(
        'user_id', user_id
    ).order('created_at', desc=True).execute()
    return result.data


def get_public_notes(limit: int = 50, offset: int = 0) -> list:
    """公開ノート一覧を取得（コミュニティ用、新しい順）"""
    client = get_supabase_client()
    result = client.table('notes').select('*').eq(
        'is_public', True
    ).order('created_at', desc=True).range(offset, offset + limit - 1).execute()
    return result.data


def get_notes_by_company(company_code: str) -> list:
    """企業別の公開ノート一覧を取得"""
    client = get_supabase_client()
    result = client.table('notes').select('*').eq(
        'company_code', company_code
    ).eq('is_public', True).order('created_at', desc=True).execute()
    return result.data


def update_note(note_id: str, user_id: str, data: dict) -> dict:
    """ノートを更新（所有者チェック付き）"""
    client = get_supabase_client()
    update_data = {}
    for key in ['title', 'content', 'company_code', 'company_name',
                'stars', 'tags', 'is_public', 'is_anonymous', 'poster_name']:
        if key in data:
            update_data[key] = data[key]
    result = client.table('notes').update(update_data).eq(
        'id', note_id
    ).eq('user_id', user_id).execute()
    return result.data[0] if result.data else {}


def delete_note(note_id: str, user_id: str) -> bool:
    """ノートを削除（所有者チェック付き）"""
    client = get_supabase_client()
    result = client.table('notes').delete().eq(
        'id', note_id
    ).eq('user_id', user_id).execute()
    return len(result.data) > 0


# =============================================
# 認証・ユーザー管理（app_usersテーブル）
# =============================================

def _generate_referral_code(length: int = 6) -> str:
    """紹介コードを生成（6文字英数字大文字）"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))


def create_user(name: str, email: str, password: str, referred_by_code: str = None) -> dict:
    """ユーザーを新規登録"""
    client = get_supabase_client()

    # メール重複チェック
    existing = client.table('app_users').select('id').eq('email', email).execute()
    if existing.data:
        raise ValueError("このメールアドレスは既に登録されています")

    # パスワードハッシュ化
    password_hash = generate_password_hash(password)

    # 紹介コード自動生成（ユニークになるまでリトライ）
    for _ in range(10):
        referral_code = _generate_referral_code()
        dup = client.table('app_users').select('id').eq('referral_code', referral_code).execute()
        if not dup.data:
            break
    else:
        raise ValueError("紹介コードの生成に失敗しました。再度お試しください")

    # 紹介者の解決
    referred_by = None
    if referred_by_code:
        referrer = client.table('app_users').select('id').eq(
            'referral_code', referred_by_code.upper().strip()
        ).execute()
        if referrer.data:
            referred_by = referrer.data[0]['id']

    user_data = {
        'name': name,
        'email': email,
        'password_hash': password_hash,
        'referral_code': referral_code,
    }
    # referred_byがある場合のみ含める（Noneを送るとスキーマキャッシュエラーになる場合がある）
    if referred_by:
        user_data['referred_by'] = referred_by

    result = client.table('app_users').insert(user_data).execute()
    if not result.data:
        raise ValueError("ユーザー登録に失敗しました")
    return result.data[0]


def authenticate_user(email: str, password: str) -> dict:
    """メール＋パスワードで認証。成功時ユーザーデータ、失敗時None"""
    client = get_supabase_client()
    result = client.table('app_users').select('*').eq('email', email).execute()
    if not result.data:
        return None
    user = result.data[0]
    if not check_password_hash(user['password_hash'], password):
        return None
    return user


def get_user_by_id(user_id: str) -> dict:
    """IDでユーザー取得"""
    client = get_supabase_client()
    result = client.table('app_users').select('*').eq('id', user_id).execute()
    return result.data[0] if result.data else None


def get_user_by_email(email: str) -> dict:
    """メールアドレスでユーザー取得"""
    client = get_supabase_client()
    result = client.table('app_users').select('*').eq('email', email).execute()
    return result.data[0] if result.data else None


def get_user_by_referral_code(code: str) -> dict:
    """紹介コードでユーザー取得"""
    client = get_supabase_client()
    result = client.table('app_users').select('*').eq(
        'referral_code', code.upper().strip()
    ).execute()
    return result.data[0] if result.data else None


# =============================================
# 紹介ツリー
# =============================================

def get_direct_referrals(user_id: str) -> list:
    """直接紹介したユーザー一覧"""
    client = get_supabase_client()
    result = client.table('app_users').select('*').eq(
        'referred_by', user_id
    ).order('created_at', desc=True).execute()
    return result.data


def get_referral_tree(user_id: str, max_depth: int = 5) -> list:
    """再帰的に紹介ツリーを取得（アプリ層で深さ制限付き探索）"""
    client = get_supabase_client()

    def _build_tree(uid, depth):
        if depth >= max_depth:
            return []
        children = client.table('app_users').select('*').eq(
            'referred_by', uid
        ).order('created_at', desc=True).execute()
        tree = []
        for child in children.data:
            node = {**child, 'depth': depth + 1, 'children': _build_tree(child['id'], depth + 1)}
            tree.append(node)
        return tree

    return _build_tree(user_id, 0)


def get_referral_chain(user_id: str) -> list:
    """上位紹介者チェーン（自分→紹介者→その紹介者→...）"""
    client = get_supabase_client()
    chain = []
    current_id = user_id
    seen = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        user = client.table('app_users').select('*').eq(
            'id', current_id
        ).execute()
        if not user.data:
            break
        chain.append(user.data[0])
        current_id = user.data[0].get('referred_by')
    return chain


# =============================================
# ユーザー管理（管理者用）
# =============================================

def get_all_users(role: str = None) -> list:
    """ユーザー一覧取得（ロールでフィルタ可能）"""
    client = get_supabase_client()
    query = client.table('app_users').select('*')
    if role:
        query = query.eq('role', role)
    result = query.execute()
    return result.data


def update_display_name(user_id: str, display_name: str) -> dict:
    """ユーザーの表示名を更新"""
    client = get_supabase_client()
    result = client.table('app_users').update(
        {'display_name': display_name.strip() if display_name else None}
    ).eq('id', user_id).execute()
    return result.data[0] if result.data else None


def update_user_email(user_id: str, new_email: str, current_password: str) -> dict:
    """メールアドレスを変更（現パスワードで本人確認）"""
    client = get_supabase_client()
    new_email = new_email.strip().lower()
    if not new_email:
        raise ValueError("メールアドレスを入力してください")

    # 本人確認
    user = client.table('app_users').select('*').eq('id', user_id).execute()
    if not user.data:
        raise ValueError("ユーザーが見つかりません")
    if not check_password_hash(user.data[0]['password_hash'], current_password):
        raise ValueError("現在のパスワードが正しくありません")

    # 重複チェック
    existing = client.table('app_users').select('id').eq('email', new_email).execute()
    if existing.data and existing.data[0]['id'] != user_id:
        raise ValueError("このメールアドレスは既に使用されています")

    result = client.table('app_users').update(
        {'email': new_email}
    ).eq('id', user_id).execute()
    return result.data[0] if result.data else None


def update_user_password(user_id: str, current_password: str, new_password: str) -> dict:
    """パスワードを変更（現パスワードで本人確認）"""
    client = get_supabase_client()
    if len(new_password) < 6:
        raise ValueError("新しいパスワードは6文字以上で入力してください")

    # 本人確認
    user = client.table('app_users').select('*').eq('id', user_id).execute()
    if not user.data:
        raise ValueError("ユーザーが見つかりません")
    if not check_password_hash(user.data[0]['password_hash'], current_password):
        raise ValueError("現在のパスワードが正しくありません")

    new_hash = generate_password_hash(new_password)
    result = client.table('app_users').update(
        {'password_hash': new_hash}
    ).eq('id', user_id).execute()
    return result.data[0] if result.data else None


def update_user_role(user_id: str, new_role: str) -> dict:
    """ユーザーのロールを変更"""
    if new_role not in ('user', 'agent', 'admin'):
        raise ValueError(f"無効なロール: {new_role}")
    client = get_supabase_client()
    result = client.table('app_users').update(
        {'role': new_role}
    ).eq('id', user_id).execute()
    return result.data[0] if result.data else None


def migrate_guest_notes(guest_user_id: str, real_user_id: str) -> int:
    """ゲストIDのノートを本ユーザーIDに引き継ぎ"""
    client = get_supabase_client()
    result = client.table('notes').update(
        {'user_id': real_user_id}
    ).eq('user_id', guest_user_id).execute()
    return len(result.data)


# =============================================
# コミュニティQ&A（質問・回答・いいね）
# =============================================

def create_question(user_id: str, data: dict) -> dict:
    """質問を新規作成"""
    client = get_supabase_client()
    q_data = {
        'user_id': user_id,
        'title': data['title'],
        'content': data['content'],
        'company_code': data.get('company_code') or None,
        'company_name': data.get('company_name') or None,
        'tags': data.get('tags', []),
        'is_anonymous': data.get('is_anonymous', False),
    }
    if data.get('poster_name'):
        q_data['poster_name'] = data['poster_name']
    result = client.table('community_questions').insert(q_data).execute()
    return result.data[0] if result.data else {}


def get_public_questions(limit: int = 50, offset: int = 0, filter_resolved: str = 'all') -> list:
    """質問一覧を取得（新しい順）"""
    client = get_supabase_client()
    query = client.table('community_questions').select('*')
    if filter_resolved == 'resolved':
        query = query.eq('is_resolved', True)
    elif filter_resolved == 'unresolved':
        query = query.eq('is_resolved', False)
    result = query.order('created_at', desc=True).range(
        offset, offset + limit - 1
    ).execute()
    return result.data


def get_questions_by_company(company_code: str) -> list:
    """企業別の質問一覧を取得"""
    client = get_supabase_client()
    result = client.table('community_questions').select('*').eq(
        'company_code', company_code
    ).order('created_at', desc=True).execute()
    return result.data


def get_question_by_id(question_id: str) -> dict:
    """質問を1件取得"""
    client = get_supabase_client()
    result = client.table('community_questions').select('*').eq(
        'id', question_id
    ).execute()
    return result.data[0] if result.data else None


def delete_question(question_id: str, user_id: str) -> bool:
    """質問を削除（所有者チェック付き）"""
    client = get_supabase_client()
    result = client.table('community_questions').delete().eq(
        'id', question_id
    ).eq('user_id', user_id).execute()
    return len(result.data) > 0


def create_answer(question_id: str, user_id: str, data: dict) -> dict:
    """回答を作成し、質問のanswer_countを更新"""
    client = get_supabase_client()
    a_data = {
        'question_id': question_id,
        'user_id': user_id,
        'content': data['content'],
        'is_anonymous': data.get('is_anonymous', False),
    }
    if data.get('poster_name'):
        a_data['poster_name'] = data['poster_name']
    result = client.table('community_answers').insert(a_data).execute()
    if result.data:
        # answer_countを+1
        q = client.table('community_questions').select('answer_count').eq(
            'id', question_id
        ).execute()
        if q.data:
            new_count = (q.data[0].get('answer_count') or 0) + 1
            client.table('community_questions').update(
                {'answer_count': new_count}
            ).eq('id', question_id).execute()
    return result.data[0] if result.data else {}


def get_answers_for_question(question_id: str) -> list:
    """質問に対する回答一覧を取得（ベストアンサー優先、古い順）"""
    client = get_supabase_client()
    result = client.table('community_answers').select('*').eq(
        'question_id', question_id
    ).order('is_best', desc=True).order('created_at').execute()
    return result.data


def delete_answer(answer_id: str, user_id: str) -> bool:
    """回答を削除（所有者チェック付き）"""
    client = get_supabase_client()
    # 回答情報を取得（question_idが必要）
    ans = client.table('community_answers').select('question_id').eq(
        'id', answer_id
    ).eq('user_id', user_id).execute()
    if not ans.data:
        return False
    question_id = ans.data[0]['question_id']
    # 削除
    result = client.table('community_answers').delete().eq(
        'id', answer_id
    ).eq('user_id', user_id).execute()
    if result.data:
        # answer_countを-1
        q = client.table('community_questions').select('answer_count').eq(
            'id', question_id
        ).execute()
        if q.data:
            new_count = max(0, (q.data[0].get('answer_count') or 0) - 1)
            client.table('community_questions').update(
                {'answer_count': new_count}
            ).eq('id', question_id).execute()
    return len(result.data) > 0


def set_best_answer(question_id: str, answer_id: str, user_id: str) -> bool:
    """ベストアンサーを設定（質問者のみ可能）"""
    client = get_supabase_client()
    # 質問の所有者チェック
    q = client.table('community_questions').select('user_id').eq(
        'id', question_id
    ).execute()
    if not q.data or q.data[0]['user_id'] != user_id:
        return False
    # 既存のベストアンサーを解除
    client.table('community_answers').update(
        {'is_best': False}
    ).eq('question_id', question_id).eq('is_best', True).execute()
    # 新しいベストアンサーを設定
    client.table('community_answers').update(
        {'is_best': True}
    ).eq('id', answer_id).eq('question_id', question_id).execute()
    # 質問を解決済みに
    client.table('community_questions').update(
        {'is_resolved': True}
    ).eq('id', question_id).execute()
    return True


def toggle_like(user_id: str, target_type: str, target_id: str) -> dict:
    """いいねをトグル（付ける/外す）。新しいlike_countとliked状態を返す"""
    client = get_supabase_client()
    # 既存のいいねを確認
    existing = client.table('community_likes').select('id').eq(
        'user_id', user_id
    ).eq('target_type', target_type).eq('target_id', target_id).execute()

    if existing.data:
        # いいね解除
        client.table('community_likes').delete().eq(
            'id', existing.data[0]['id']
        ).execute()
        liked = False
    else:
        # いいね追加
        client.table('community_likes').insert({
            'user_id': user_id,
            'target_type': target_type,
            'target_id': target_id,
        }).execute()
        liked = True

    # like_countを再計算して対象テーブルを更新
    count_result = client.table('community_likes').select('id').eq(
        'target_type', target_type
    ).eq('target_id', target_id).execute()
    new_count = len(count_result.data)

    table = 'community_questions' if target_type == 'question' else 'community_answers'
    client.table(table).update(
        {'like_count': new_count}
    ).eq('id', target_id).execute()

    return {'liked': liked, 'like_count': new_count}


def get_user_likes(user_id: str, target_type: str, target_ids: list) -> set:
    """ユーザーが指定ターゲットにいいねしているかをセットで返す"""
    if not target_ids:
        return set()
    client = get_supabase_client()
    result = client.table('community_likes').select('target_id').eq(
        'user_id', user_id
    ).eq('target_type', target_type).in_('target_id', target_ids).execute()
    return set(r['target_id'] for r in result.data)
