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

import threading
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


# 画面からの取得に許す最大秒数。
#
# これを入れる前は Yahoo への取得に上限が無かった。本番は
# gunicorn --workers 1 --timeout 120 で動いており、1本の遅い
# リクエストが120秒を超えると **worker ごと落とされる**。worker が1本
# しか無いのでアプリ全体が落ち、その間の他ページも 503 になる
# （2026-08-14 にキオクシアのチャートで実際に発生）。
#
# 外部が遅いのは避けられない。避けられるのは「待ち続けること」なので、
# ここで切る。取れなければ保存済みの古い足を返せばよい。
FETCH_TIMEOUT_SECONDS = 20


def fetch_ohlc(symbol, period='1y', timeout=FETCH_TIMEOUT_SECONDS):
    """yfinanceからOHLCを取得する。失敗・時間切れは空リスト。"""
    import yfinance as yf
    import pandas as pd

    ticker = yf.Ticker(symbol)
    try:
        # yfinance の timeout は1リクエストあたり。リトライや複数回の
        # 通信で合計はこれより延びるため、呼び出し側でも上限をかける
        # （_call_with_deadline）。
        hist = ticker.history(period=period, timeout=timeout)
    except TypeError:
        # timeout を受け取らない版のための保険
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
            # 出来高は取得元のレスポンスに最初から入っている。
            # 読まずに捨てていたため、流動性を測る術が無かった。
            'volume': int(row['Volume']) if pd.notna(row.get('Volume')) else None,
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
                        # 出来高は取得元のレスポンスに最初から入っている。
                        # 読まずに捨てていたため、流動性を測る術が無かった。
                        'volume': int(row['Volume']) if pd.notna(row.get('Volume')) else None,
                    })
                if rows:
                    result[code] = rows
            except Exception:
                continue
    return result


def downsample(rows, granularity):
    """日足を週足/月足に集約する。
    open=期間最初の始値 / high=期間最高値 / low=期間最安値 / close=期間最後の終値
    volume=期間の合計（平均でも最後の値でもない。週の商いの総量を見るため）
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
                'volume': r.get('volume'),
            }
            order.append(key)
            continue

        b = buckets[key]
        if r['high'] is not None:
            b['high'] = r['high'] if b['high'] is None else max(b['high'], r['high'])
        if r['low'] is not None:
            b['low'] = r['low'] if b['low'] is None else min(b['low'], r['low'])
        b['close'] = r['close']
        if r.get('volume') is not None:
            b['volume'] = r['volume'] if b.get('volume') is None else b['volume'] + r['volume']

    return [buckets[k] for k in order]


# 流動性を測る窓。1か月ぶんの営業日。
# 1日だけの値では決算発表や指数入れ替えの日を拾ってしまい、
# 「普段どれだけ売買されている銘柄か」が分からない。
LIQUIDITY_WINDOW_DAYS = 20


def liquidity_summary(rows, days=LIQUIDITY_WINDOW_DAYS):
    """日足から流動性の目安を出す。取れなければ None。

    - avg_volume   … 1日あたりの平均出来高（株）
    - avg_turnover … 1日あたりの平均売買代金（円・概算）
    - days         … 実際に使った営業日数

    ⚠️ 売買代金は**概算**。正しくは約定ごとの価格で積み上げるが、ここでは
    その日の出来高×終値で代用している。「機関投資家が入れる規模か」を
    見るのが目的なので、この粒度で足りる。画面にも概算と書く。
    """
    if not rows:
        return None
    usable = [r for r in rows
              if r.get('volume') is not None and r.get('close') is not None]
    if not usable:
        return None
    window = usable[-days:]
    volumes = [r['volume'] for r in window]
    turnovers = [r['volume'] * r['close'] for r in window]
    n = len(window)
    return {
        'avg_volume': sum(volumes) / n,
        'avg_turnover': sum(turnovers) / n,
        'days': n,
    }


def margin_turnover_days(margin_buy_shares, avg_volume):
    """信用買残が平均何日分の出来高にあたるか（回転日数）。

    信用倍率だけでは「重いのか軽いのか」が決まらない。買残10万株でも
    1日の出来高が500万株なら1時間で消化される。出来高と比べてはじめて
    上値の重さの目安になる。
    """
    if not margin_buy_shares or not avg_volume or avg_volume <= 0:
        return None
    return margin_buy_shares / avg_volume


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


_refreshing = set()
_refreshing_lock = threading.Lock()


def _refresh_in_background(key, work):
    """保存済みを返した後ろで取り直す。同じ銘柄の多重起動はしない。

    画面を待たせないための仕組み。ユーザーには古い足がすぐ出て、
    次に開いたときには新しくなっている。
    """
    with _refreshing_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def run():
        try:
            work()
        except Exception as e:
            print(f'株価履歴の裏更新エラー {key}: {e}')
        finally:
            with _refreshing_lock:
                _refreshing.discard(key)

    threading.Thread(target=run, daemon=True, name=f'price-refresh-{key}').start()


def call_with_deadline(func, seconds):
    """funcをseconds以内に終わらせる。超えたら諦めて None を返す。

    スレッドは止められないので走り続けるが、**リクエストは返る**。
    worker が gunicorn のタイムアウトで殺されるのを防ぐのが目的。
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _Timeout
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(func)
        try:
            return future.result(timeout=seconds)
        except _Timeout:
            return None
    finally:
        # 走り続けているスレッドの完了は待たない（待つと意味が無い）
        executor.shutdown(wait=False)


def get_daily(company_code, max_age_days=2):
    """日足を返す。

    保存済みがあれば**古くてもすぐ返し**、取り直しは裏で行う。
    未取得の銘柄だけ、上限つきでその場で取りに行く。

    以前は古いというだけでその場で取りに行っており、Yahooが遅いと
    リクエストが何十秒も返らなかった。本番は worker 1本なので、
    それが120秒を超えると worker ごと落ちてアプリ全体が 503 になる。
    """
    stored = get_stored(company_code)
    cached = (stored or {}).get('daily_1y')

    if cached:
        if _is_stale((stored or {}).get('daily_updated_at'), max_age_days):
            _refresh_in_background(f'daily:{company_code}',
                                   lambda: _fetch_and_save_daily(company_code))
        return cached

    # 保存が無い＝出すものが何も無いので、ここだけ待つ（上限つき）
    rows = call_with_deadline(
        lambda: _fetch_and_save_daily(company_code), FETCH_TIMEOUT_SECONDS)
    return rows or []


def _fetch_and_save_daily(company_code):
    rows = fetch_ohlc(to_symbol(company_code), period='1y')
    if rows:
        try:
            save_daily(company_code, rows)
        except Exception as e:
            print(f'日足の保存エラー {company_code}: {e}')
    return rows


def get_long_term(company_code, granularity, max_age_days=7):
    """週足/月足を返す。日足と同じく、保存済みを優先して裏で取り直す。

    長期足は10年分を取ってから週足・月足に間引くので、日足より重い。
    その場で待たせると 503 の原因になりやすい。
    """
    column = 'weekly_10y' if granularity == 'weekly' else 'monthly_10y'

    stored = get_stored(company_code)
    cached = (stored or {}).get(column)

    if cached:
        if _is_stale((stored or {}).get('long_term_updated_at'), max_age_days):
            _refresh_in_background(f'long:{company_code}',
                                   lambda: _fetch_and_save_long_term(company_code))
        return cached

    result = call_with_deadline(
        lambda: _fetch_and_save_long_term(company_code), FETCH_TIMEOUT_SECONDS)
    if result:
        return result[0] if granularity == 'weekly' else result[1]
    return []


def _fetch_and_save_long_term(company_code):
    """10年分を取って週足・月足に間引き保存する。(weekly, monthly) を返す。"""
    daily = fetch_ohlc(to_symbol(company_code), period='10y')
    if not daily:
        return None
    weekly = downsample(daily, 'weekly')
    monthly = downsample(daily, 'monthly')
    try:
        save_long_term(company_code, weekly, monthly)
    except Exception as e:
        print(f'長期足の保存エラー {company_code}: {e}')
    return weekly, monthly


# 旧名。呼び出し元を移し終えるまで残す。
_call_with_deadline = call_with_deadline
