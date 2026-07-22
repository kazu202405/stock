"""
株価履歴の取得・間引き・保存。

背景:
  従来チャートのデータは output/snapshot_*.json（ローカルファイル）から読んでいたが、
  Renderのディスクは揮発するため本番ではチャートが表示されなかった。
  ここでDB(stock_price_history)に持たせて解消する。

粒度の考え方:
  長期チャートを日足で描くと本数が多すぎて潰れる（10年=約2,500本）。
  期間に応じて足を間引く。
      〜1年   日足   (約244本)
      2〜5年  週足   (約104〜260本)
      10年    月足   (約120本)
"""

from datetime import datetime, timezone

# 取引所ローカルの日付に正規化するためのオフセット。
# price_history の time は「取引所ローカル0時」のUNIX秒で、
# そのままUTC解釈すると日付が1日ずれる（フロント側の toBusinessDay と同じ補正）。
_LOCAL_DATE_OFFSET = 12 * 3600

DAILY_RANGES = ('1m', '3m', '6m', '1y')
WEEKLY_RANGES = ('2y', '3y', '5y')
MONTHLY_RANGES = ('10y',)


def granularity_for_range(range_key):
    """表示期間から必要な足の粒度を返す"""
    if range_key in WEEKLY_RANGES:
        return 'weekly'
    if range_key in MONTHLY_RANGES:
        return 'monthly'
    return 'daily'


def fetch_ohlc(symbol, period='1y'):
    """yfinanceからOHLCを取得する。失敗時は空リスト。"""
    import yfinance as yf
    import pandas as pd

    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period)
    if hist is None or hist.empty:
        return []

    rows = []
    for idx, row in hist.iterrows():
        if pd.isna(row.get('Close')):
            continue
        rows.append({
            'time': int(idx.timestamp()),
            'open': float(row['Open']) if pd.notna(row['Open']) else None,
            'high': float(row['High']) if pd.notna(row['High']) else None,
            'low': float(row['Low']) if pd.notna(row['Low']) else None,
            'close': float(row['Close']),
        })
    return rows


def fetch_ohlc_batch(codes, period='1y', chunk_size=100):
    """複数銘柄の日足をまとめて取得する。{code: rows} を返す。

    1銘柄ずつ取ると3,900件で約40分かかる。yfinanceのバッチ取得なら
    リクエスト数が銘柄数分の1になり、大幅に短縮できる。
    """
    import yfinance as yf
    import pandas as pd
    import warnings
    warnings.filterwarnings('ignore')

    result = {}
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i:i + chunk_size]
        symbols = [to_symbol(c) for c in chunk]
        try:
            df = yf.download(' '.join(symbols), period=period, progress=False,
                             threads=True, auto_adjust=False, group_by='ticker')
        except Exception as e:
            print(f'日足のバッチ取得エラー ({i}-{i + len(chunk)}): {e}')
            continue

        for code, sym in zip(chunk, symbols):
            try:
                sub = df[sym] if len(symbols) > 1 else df
                rows = []
                for idx, row in sub.iterrows():
                    if pd.isna(row.get('Close')):
                        continue
                    rows.append({
                        'time': int(idx.timestamp()),
                        'open': float(row['Open']) if pd.notna(row['Open']) else None,
                        'high': float(row['High']) if pd.notna(row['High']) else None,
                        'low': float(row['Low']) if pd.notna(row['Low']) else None,
                        'close': float(row['Close']),
                    })
                if rows:
                    result[code] = rows
            except Exception:
                continue
    return result


def downsample(rows, granularity):
    """日足を週足/月足に集約する。
    open=期間最初の始値 / high=期間最高値 / low=期間最安値 / close=期間最後の終値
    """
    if granularity == 'daily' or not rows:
        return rows

    buckets = {}
    order = []
    for r in rows:
        d = datetime.fromtimestamp(r['time'] + _LOCAL_DATE_OFFSET, tz=timezone.utc)
        if granularity == 'weekly':
            iso = d.isocalendar()
            key = (iso[0], iso[1])
        else:
            key = (d.year, d.month)

        if key not in buckets:
            buckets[key] = {
                'time': r['time'],
                'open': r['open'],
                'high': r['high'],
                'low': r['low'],
                'close': r['close'],
            }
            order.append(key)
            continue

        b = buckets[key]
        if r['high'] is not None:
            b['high'] = r['high'] if b['high'] is None else max(b['high'], r['high'])
        if r['low'] is not None:
            b['low'] = r['low'] if b['low'] is None else min(b['low'], r['low'])
        b['close'] = r['close']

    return [buckets[k] for k in order]


# ---------------------------------------------------------------
# DB入出力
# ---------------------------------------------------------------

def get_stored(company_code):
    """保存済みの株価履歴レコードを返す。無ければ None。"""
    from supabase_client import get_supabase_client
    client = get_supabase_client()
    res = (client.table('stock_price_history')
           .select('*')
           .eq('company_code', company_code)
           .execute())
    return res.data[0] if res.data else None


def save_daily(company_code, rows):
    from supabase_client import get_supabase_client
    client = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    client.table('stock_price_history').upsert({
        'company_code': company_code,
        'daily_1y': rows,
        'daily_updated_at': now,
        'updated_at': now,
    }).execute()


def save_long_term(company_code, weekly, monthly):
    from supabase_client import get_supabase_client
    client = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    client.table('stock_price_history').upsert({
        'company_code': company_code,
        'weekly_10y': weekly,
        'monthly_10y': monthly,
        'long_term_updated_at': now,
        'updated_at': now,
    }).execute()


def _is_stale(timestamp_str, max_age_days):
    if not timestamp_str:
        return True
    try:
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age > max_age_days * 86400
    except Exception:
        return True


def to_symbol(company_code):
    code = (company_code or '').strip()
    if code.endswith('.T'):
        return code
    # 日本株は4桁数字 or 数字+英字（例: 367A）。それ以外は海外ティッカーとして扱う
    return f'{code}.T' if len(code) == 4 and code[0].isdigit() else code


def get_daily(company_code, max_age_days=2):
    """日足を返す。未取得または古い場合はyfinanceから取得して保存する。"""
    stored = get_stored(company_code)
    if stored and stored.get('daily_1y') and not _is_stale(stored.get('daily_updated_at'), max_age_days):
        return stored['daily_1y']

    rows = fetch_ohlc(to_symbol(company_code), period='1y')
    if rows:
        try:
            save_daily(company_code, rows)
        except Exception as e:
            print(f'日足の保存エラー {company_code}: {e}')
        return rows

    # 取得に失敗したら、古くても保存済みがあればそれを返す
    return (stored or {}).get('daily_1y') or []


def get_long_term(company_code, granularity, max_age_days=7):
    """週足/月足を返す。未取得または古い場合は10年分を取得して間引き保存する。"""
    column = 'weekly_10y' if granularity == 'weekly' else 'monthly_10y'

    stored = get_stored(company_code)
    if stored and stored.get(column) and not _is_stale(stored.get('long_term_updated_at'), max_age_days):
        return stored[column]

    daily = fetch_ohlc(to_symbol(company_code), period='10y')
    if daily:
        weekly = downsample(daily, 'weekly')
        monthly = downsample(daily, 'monthly')
        try:
            save_long_term(company_code, weekly, monthly)
        except Exception as e:
            print(f'長期足の保存エラー {company_code}: {e}')
        return weekly if granularity == 'weekly' else monthly

    return (stored or {}).get(column) or []
