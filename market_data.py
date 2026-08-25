"""
マーケット指数（日経平均・S&P500など）の取得とキャッシュ。

背景:
  個別銘柄の株価は stock_price_history に貯めているが、指数はそこへ入れない。
  ma_cross.calculate_for_all() が stock_price_history を全行スキャンして
  GC/DCを計算するため、指数を混ぜると「^N225のゴールデンクロス」のような
  銘柄でない行が ma_crosses に紛れ込む。

  指数は6本しかなく、取得も1回のバッチリクエストで済むため、
  DBに持たずプロセス内キャッシュで扱う。マイグレーション不要。

粒度と期間の考え方は price_history と同じものを再利用する。
"""

import threading
import time

import price_history as ph

# 表示する指数の定義。
#   symbol   … yfinanceに渡すティッカー
#   currency … 表示単位（'JPY' / 'USD'）
#   note     … 指数そのものではない等、誤解を招く場合の注記
INDEXES = [
    {
        'key': 'n225',
        'prefix': '',
        'decimals': 0,
        'symbol': '^N225',
        'name': '日経平均株価',
        'short_name': '日経平均',
        'currency': 'JPY',
        'region': '日本',
        'description': '東証プライムの主要225銘柄の平均。値がさ株（1株の価格が高い銘柄）の影響を受けやすい。',
        'note': '',
    },
    {
        'key': 'dji',
        'prefix': '',
        'decimals': 0,
        'symbol': '^DJI',
        'name': 'ダウ工業株30種平均',
        'short_name': 'ダウ平均',
        'currency': 'USD',
        'region': '米国',
        'description': '米国を代表する30銘柄の平均。歴史が長く報道で最もよく使われる。',
        'note': '',
    },
    {
        'key': 'sp500',
        'prefix': '',
        'decimals': 0,
        'symbol': '^GSPC',
        'name': 'S&P500',
        'short_name': 'S&P500',
        'currency': 'USD',
        'region': '米国',
        'description': '米国の主要500銘柄で構成。米国市場全体を見るならダウよりこちら。',
        'note': '',
    },
    {
        'key': 'usdjpy',
        'prefix': '¥',
        'decimals': 2,
        'symbol': 'USDJPY=X',
        'name': '米ドル／円',
        'short_name': 'ドル円',
        'currency': 'JPY',
        'region': '為替',
        'description': '円安になると輸出企業の売上・利益が円換算で膨らむ。決算を読むときの背景。',
        'note': '',
    },
    {
        'key': 'gold',
        'prefix': '$',
        'decimals': 2,
        'symbol': 'GC=F',
        'name': '金（COMEX先物）',
        'short_name': '金',
        'currency': 'USD',
        'region': '商品',
        'description': '有事や利下げ局面で買われやすい。株から資金が逃げているかの温度感が読める。',
        'note': '先物なので限月が変わるときに価格が少し飛ぶことがある。1トロイオンス（約31.1g）あたりのドル建て。',
    },
    {
        'key': 'wti',
        'prefix': '$',
        'decimals': 2,
        'symbol': 'CL=F',
        'name': 'WTI原油先物',
        'short_name': '原油',
        'currency': 'USD',
        'region': '商品',
        'description': '多くの日本企業にとっては原価そのもの。電力・運輸・海運・化学・素材の決算を読むときの背景になる。',
        'note': '先物なので限月が変わるときに価格が少し飛ぶことがある。1バレルあたりのドル建て。',
    },
    {
        'key': 'btcjpy',
        'prefix': '¥',
        'decimals': 0,
        'symbol': 'BTC-JPY',
        'name': 'ビットコイン／円',
        'short_name': 'ビットコイン',
        'currency': 'JPY',
        'region': '暗号資産',
        'description': '株式ではないが、リスク資産全体の温度感を映す指標として並べている。',
        'note': '土日も取引されるため、株式市場が休みの日にも値が動く。',
    },
]

INDEX_BY_KEY = {i['key']: i for i in INDEXES}

# キャッシュ。{cache_key: (取得時刻, rows)}
# 日足は場中に更新されるので短め、10年分は動きが遅いので長めに持つ。
_DAILY_TTL = 30 * 60
_LONG_TTL = 12 * 3600

_cache = {}
_lock = threading.Lock()


def _get_cached(cache_key, ttl):
    with _lock:
        entry = _cache.get(cache_key)
    if not entry:
        return None
    fetched_at, rows = entry
    if time.time() - fetched_at > ttl:
        return None
    return rows


def _set_cached(cache_key, rows):
    with _lock:
        _cache[cache_key] = (time.time(), rows)


def _fetch_all_daily():
    """全指数の日足1年分をまとめて取得する。{key: rows} を返す。

    1本ずつ取ると6リクエストになるが、yfinanceのバッチ取得なら1回で済む。
    レート制限に触れにくい。
    """
    symbols = [i['symbol'] for i in INDEXES]
    fetched = ph.fetch_ohlc_batch(symbols, period='1y', chunk_size=len(symbols))
    result = {}
    for idx in INDEXES:
        rows = fetched.get(idx['symbol'])
        if rows:
            result[idx['key']] = rows
    return result


def get_daily(key):
    """指数の日足1年分を返す。キャッシュが切れていれば全指数まとめて取り直す。"""
    cached = _get_cached(f'daily:{key}', _DAILY_TTL)
    if cached is not None:
        return cached

    try:
        fetched = _fetch_all_daily()
    except Exception as e:
        print(f'指数の日足取得エラー: {e}')
        fetched = {}

    for k, rows in fetched.items():
        _set_cached(f'daily:{k}', rows)

    if key in fetched:
        return fetched[key]

    # 取得に失敗しても、期限切れの古いキャッシュがあれば出す（空表示より良い）
    with _lock:
        stale = _cache.get(f'daily:{key}')
    return stale[1] if stale else []


def get_long_term(key, granularity):
    """指数の週足/月足を返す。10年分を1回取得して両方の粒度を作る。"""
    cache_key = f'long:{key}:{granularity}'
    cached = _get_cached(cache_key, _LONG_TTL)
    if cached is not None:
        return cached

    idx = INDEX_BY_KEY.get(key)
    if not idx:
        return []

    try:
        daily = ph.fetch_ohlc(idx['symbol'], period='10y')
    except Exception as e:
        print(f"指数の長期足取得エラー {idx['symbol']}: {e}")
        daily = []

    if daily:
        weekly = ph.downsample(daily, 'weekly')
        monthly = ph.downsample(daily, 'monthly')
        _set_cached(f'long:{key}:weekly', weekly)
        _set_cached(f'long:{key}:monthly', monthly)
        return weekly if granularity == 'weekly' else monthly

    with _lock:
        stale = _cache.get(cache_key)
    return stale[1] if stale else []


def get_rows(key, range_key):
    """表示期間に応じた足を返す。(rows, granularity) を返す。"""
    granularity = ph.granularity_for_range(range_key)
    if granularity == 'daily':
        return get_daily(key), granularity
    return get_long_term(key, granularity), granularity


def _summarize(idx, rows):
    """一覧カード用に最新値と前日比を組み立てる"""
    item = {
        'key': idx['key'],
        'symbol': idx['symbol'],
        'name': idx['name'],
        'short_name': idx['short_name'],
        'currency': idx['currency'],
        'prefix': idx['prefix'],
        'decimals': idx['decimals'],
        'region': idx['region'],
        'description': idx['description'],
        'note': idx['note'],
        'last': None,
        'prev': None,
        'change': None,
        'change_pct': None,
        'time': None,
    }
    valid = [r for r in (rows or []) if r and r.get('close') is not None]
    if not valid:
        return item

    last = valid[-1]
    item['last'] = last['close']
    item['time'] = last['time']
    if len(valid) >= 2:
        prev = valid[-2]['close']
        item['prev'] = prev
        item['change'] = last['close'] - prev
        if prev:
            item['change_pct'] = (last['close'] - prev) / prev * 100
    return item


def get_summaries():
    """全指数の最新値・前日比を返す（一覧カード用）"""
    # 1本目で全指数分をまとめて取りに行くので、以降はキャッシュに当たる
    return [_summarize(idx, get_daily(idx['key'])) for idx in INDEXES]
