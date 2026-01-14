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
