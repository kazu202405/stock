"""過去の株価で「いつ買っていたらいくらになっていたか」を計算する。

外部アクセスは一切しない。`stock_price_history` に入っている
日足1年・月足10年（いずれも調整後の株価）だけで計算する。

⚠️ あくまで概算:
  - 手数料・税金・配当を含まない
  - 単元株（100株単位）ではなく小数株で計算する。
    積立で「毎月3万円」を単元株に丸めると、値がさ株では
    ほとんど買えず比較にならないため
  - 指定日に取引が無ければ、その日より前の直近の終値を使う
    （休日・祝日に積立日を指定した場合の実務に合わせる）

このモジュールは純粋な計算だけを持つ。DBの読み出しは呼び出し側の責任。
テストしやすくするためと、同じ計算をAPIとバッチの両方から使えるようにするため。
"""

from datetime import date, datetime, timedelta, timezone

# 取引が無い日にさかのぼって価格を探す上限。
# これを超えて見つからない場合は「その時期のデータが無い」と扱う。
MAX_LOOKBACK_DAYS = 14


def _to_date(value):
    """'2024-01-15' / date / datetime / epoch秒 を date に揃える。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    return date.fromisoformat(str(value)[:10])


def normalize_series(bars):
    """[{time, close, ...}] を [(date, close)] の昇順に整える。

    time は epoch秒。close が無い/0以下の行は捨てる（分割直後などに混ざる）。
    """
    out = []
    for b in bars or []:
        close = b.get('close')
        t = b.get('time')
        if not close or close <= 0 or t is None:
            continue
        out.append((_to_date(t), float(close)))
    out.sort(key=lambda x: x[0])
    return out


def pick_series(history, start, end):
    """要求された期間を賄える中で、いちばん細かい系列を選ぶ。

    日足は1年ぶんしか無いので、それより古い期間を含むなら月足に落とす。
    週足も持っているが、月足で足りる用途に3系列を出し分けても
    読み手が得をしないので使わない。
    """
    start, end = _to_date(start), _to_date(end)
    daily = normalize_series((history or {}).get('daily_1y'))
    monthly = normalize_series((history or {}).get('monthly_10y'))

    if daily and start >= daily[0][0]:
        return daily, 'daily'
    if monthly:
        return monthly, 'monthly'
    return daily, 'daily'


def price_on(series, target, max_lookback_days=MAX_LOOKBACK_DAYS):
    """指定日の価格。取引が無ければ直近の過去へさかのぼる。

    月足を使うときは1か月さかのぼる必要があるので、呼び出し側が
    max_lookback_days を広げること。
    """
    target = _to_date(target)
    best = None
    for d, close in series:
        if d > target:
            break
        best = (d, close)
    if not best:
        return None
    if (target - best[0]).days > max_lookback_days:
        return None
    return best


def purchase_dates(start, end, interval_months=1, day_of_month=1):
    """積立の買付日を並べる。

    「●ヶ月ごとの●日」を素直に並べるだけ。31日など無い月は
    その月の末日に寄せる（1月31日開始で2月が飛ぶのを避ける）。
    """
    start, end = _to_date(start), _to_date(end)
    interval_months = max(1, int(interval_months))
    day_of_month = min(31, max(1, int(day_of_month)))

    dates = []
    y, m = start.year, start.month
    while True:
        # その月に day_of_month が無ければ末日
        if m == 12:
            last = 31
        else:
            last = (date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)).day
        d = date(y, m, min(day_of_month, last))
        if d > end:
            break
        if d >= start:
            dates.append(d)
        m += interval_months
        while m > 12:
            m -= 12
            y += 1
        if y > end.year + 1:
            break
    return dates


def simulate_lump(history, start, end, amount):
    """一括購入。start に amount 円ぶん買って end まで持つ。"""
    series, grain = pick_series(history, start, end)
    lookback = 40 if grain == 'monthly' else MAX_LOOKBACK_DAYS
    if not series:
        return {'ok': False, 'reason': 'この銘柄の株価履歴がありません'}

    buy = price_on(series, start, lookback)
    sell = price_on(series, end, lookback)
    if not buy:
        return {'ok': False, 'reason': f'{_to_date(start)} 時点の株価がありません',
                'available_from': series[0][0].isoformat()}
    if not sell:
        return {'ok': False, 'reason': f'{_to_date(end)} 時点の株価がありません'}

    shares = amount / buy[1]
    value = shares * sell[1]
    return {
        'ok': True, 'mode': 'lump', 'grain': grain,
        'invested': round(amount),
        'value': round(value),
        'profit': round(value - amount),
        'return_pct': round((value / amount - 1) * 100, 1) if amount else 0.0,
        'shares': round(shares, 3),
        'buys': [{'date': buy[0].isoformat(), 'price': buy[1],
                  'amount': round(amount), 'shares': round(shares, 3)}],
        'buy_price': buy[1], 'buy_date': buy[0].isoformat(),
        'sell_price': sell[1], 'sell_date': sell[0].isoformat(),
    }


def simulate_monthly(history, start, end, amount, interval_months=1, day_of_month=1):
    """積立。●ヶ月ごとの●日に amount 円ずつ買い、end 時点で評価する。"""
    series, grain = pick_series(history, start, end)
    lookback = 40 if grain == 'monthly' else MAX_LOOKBACK_DAYS
    if not series:
        return {'ok': False, 'reason': 'この銘柄の株価履歴がありません'}

    sell = price_on(series, end, lookback)
    if not sell:
        return {'ok': False, 'reason': f'{_to_date(end)} 時点の株価がありません'}

    buys, shares_total, invested = [], 0.0, 0
    skipped = 0
    for d in purchase_dates(start, end, interval_months, day_of_month):
        p = price_on(series, d, lookback)
        if not p:
            skipped += 1
            continue
        s = amount / p[1]
        shares_total += s
        invested += amount
        buys.append({'date': p[0].isoformat(), 'price': p[1],
                     'amount': round(amount), 'shares': round(s, 3)})

    if not buys:
        return {'ok': False, 'reason': 'この期間に買える株価データがありませんでした',
                'available_from': series[0][0].isoformat()}

    value = shares_total * sell[1]
    avg_cost = invested / shares_total if shares_total else 0
    return {
        'ok': True, 'mode': 'monthly', 'grain': grain,
        'invested': round(invested),
        'value': round(value),
        'profit': round(value - invested),
        'return_pct': round((value / invested - 1) * 100, 1) if invested else 0.0,
        'shares': round(shares_total, 3),
        'times': len(buys),
        'skipped': skipped,
        'avg_cost': round(avg_cost, 1),
        'buys': buys,
        'sell_price': sell[1], 'sell_date': sell[0].isoformat(),
    }
