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


def evaluation_price(history, end):
    """評価日の株価。**買付とは別に、いちばん新しい系列から探す。**

    買付は期間全体を賄える系列（多くは月足）で行うが、評価まで月足で
    引くと、月足の最終バーから数十日空いたときに「その日の株価がありません」
    となる。実際には日足に最新の株価がある。

    さらに、評価日が持っているデータより後（今日を指定した等）なら、
    いちばん新しいバーに寄せる。エラーで止めるより、いつ時点で評価したかを
    返して画面に出すほうが読み手の役に立つ。
    """
    end = _to_date(end)
    for key, lookback in (('daily_1y', MAX_LOOKBACK_DAYS), ('monthly_10y', 40)):
        series = normalize_series((history or {}).get(key))
        got = price_on(series, end, lookback)
        if got:
            return got

    # どの系列でも指定日に届かない場合は、持っている中で最も新しいバー
    latest = None
    for key in ('daily_1y', 'monthly_10y'):
        series = normalize_series((history or {}).get(key))
        if series and (latest is None or series[-1][0] > latest[0]):
            latest = series[-1]
    return latest


# 買い方。実際には小数株は買えないので、既定は1株単位。
#   'fraction' … 端数も買える。ドルコスト平均法の理論値を見るとき用
#   'carry'    … 1株単位。買えなかった端数は次回に回す（口座に残るので現実的）
#   'floor'    … 1株単位。端数はその回では使わない（現金として積み上がる）
#
# 単元株(100株)は入れていない。単元未満株（S株・かぶミニ等）で1株から
# 買えるため、100株単位を出しても選ぶ理由がない。
BUY_MODES = ('fraction', 'carry', 'floor')


def buyable_shares(cash, price, buy_mode):
    """その金額で実際に買える株数。小数株は買えないので切り捨てる。"""
    if price <= 0:
        return 0.0
    if buy_mode == 'fraction':
        return cash / price
    return float(int(cash // price))


def simulate_lump(history, start, end, amount, buy_mode='carry'):
    """一括購入。start に amount 円ぶん買って end まで持つ。

    一括なので carry と floor に差は出ない（次回が無い）。買えなかった
    端数は現金として残し、総資産に含める。
    """
    series, grain = pick_series(history, start, end)
    lookback = 40 if grain == 'monthly' else MAX_LOOKBACK_DAYS
    if not series:
        return {'ok': False, 'reason': 'この銘柄の株価履歴がありません'}

    buy = price_on(series, start, lookback)
    sell = evaluation_price(history, end)
    if not buy:
        return {'ok': False, 'reason': f'{_to_date(start)} 時点の株価がありません',
                'available_from': series[0][0].isoformat()}
    if not sell:
        return {'ok': False, 'reason': f'{_to_date(end)} 時点の株価がありません'}

    shares = buyable_shares(amount, buy[1], buy_mode)
    spent = shares * buy[1]
    cash = amount - spent
    value = shares * sell[1]
    # 買えなかったぶんは消えるわけではない。現金として総資産に入れる。
    # ここを外すと、切り捨てを選んだときだけ成績が良く見えてしまう。
    total = value + cash
    return {
        'ok': True, 'mode': 'lump', 'grain': grain, 'buy_mode': buy_mode,
        'deposited': round(amount),
        'invested': round(spent),
        'cash': round(cash),
        'value': round(value),
        'total': round(total),
        'profit': round(total - amount),
        'return_pct': round((total / amount - 1) * 100, 1) if amount else 0.0,
        'shares': round(shares, 3),
        'buys': ([{'date': buy[0].isoformat(), 'price': buy[1],
                   'amount': round(spent), 'shares': round(shares, 3)}]
                 if shares > 0 else []),
        'buy_price': buy[1], 'buy_date': buy[0].isoformat(),
        'sell_price': sell[1], 'sell_date': sell[0].isoformat(),
    }


def simulate_monthly(history, start, end, amount, interval_months=1, day_of_month=1,
                     buy_mode='carry'):
    """積立。●ヶ月ごとの●日に amount 円ずつ積み、end 時点で評価する。

    buy_mode で端数の扱いが変わる:
      fraction … 端数も買う。ドルコスト平均法の理論値
      carry    … 1株単位。買えなかった端数を次回に回す。口座に残るので現実的
      floor    … 1株単位。端数はその回では使わず、現金として積み上がる

    どのモードでも**積み立てた総額を分母にする**。端数を勘定から外すと、
    切り捨てを選んだときだけ成績が良く見えてしまうため。
    """
    series, grain = pick_series(history, start, end)
    lookback = 40 if grain == 'monthly' else MAX_LOOKBACK_DAYS
    if not series:
        return {'ok': False, 'reason': 'この銘柄の株価履歴がありません'}

    sell = evaluation_price(history, end)
    if not sell:
        return {'ok': False, 'reason': f'{_to_date(end)} 時点の株価がありません'}

    buys, shares_total, spent_total = [], 0.0, 0.0
    deposited = 0        # 積み立てた総額
    cash = 0.0           # まだ株になっていない現金
    deposits = 0         # 積み立てた回数（買えた回数とは別）
    skipped = 0
    for d in purchase_dates(start, end, interval_months, day_of_month):
        p = price_on(series, d, lookback)
        if not p:
            skipped += 1
            continue

        deposited += amount
        deposits += 1
        # carry は前回までの余りに足して買う。floor はその回の金額だけで買う
        budget = (cash + amount) if buy_mode == 'carry' else amount
        sh = buyable_shares(budget, p[1], buy_mode)
        spent = sh * p[1]
        cash = cash + amount - spent      # 使わなかったぶんは現金として残る

        if sh <= 0:
            continue                      # 今回は買えなかった（現金は残る）
        shares_total += sh
        spent_total += spent
        buys.append({'date': p[0].isoformat(), 'price': p[1],
                     'amount': round(spent), 'shares': round(sh, 3)})

    if not buys:
        reason = 'この期間に買える株価データがありませんでした'
        if deposits:
            reason = ('積み立てた金額では1回も買えませんでした。'
                      '1回の金額を増やすか、買い方を「端数も買う」にしてください')
        return {'ok': False, 'reason': reason,
                'available_from': series[0][0].isoformat()}

    value = shares_total * sell[1]
    total = value + cash
    avg_cost = spent_total / shares_total if shares_total else 0
    return {
        'ok': True, 'mode': 'monthly', 'grain': grain, 'buy_mode': buy_mode,
        # 指定した期間より前のデータが無いことは普通に起きる（月足は10年ぶん）。
        # 「1984年から」と指定して実際は直近10年だけ、というズレを黙って
        # 損益に混ぜないよう、実際に買えた期間を返して画面に出す。
        'first_buy': buys[0]['date'],
        'last_buy': buys[-1]['date'],
        'deposited': round(deposited),      # 積み立てた総額（これを分母にする）
        'invested': round(spent_total),     # 実際に株を買った金額
        'cash': round(cash),                # まだ株になっていない現金
        'value': round(value),              # 株の評価額
        'total': round(total),              # 総資産＝株＋現金
        'profit': round(total - deposited),
        'return_pct': (round((total / deposited - 1) * 100, 1) if deposited else 0.0),
        'shares': round(shares_total, 3),
        'times': len(buys),                 # 実際に買えた回数
        'deposits': deposits,               # 積み立てた回数
        'skipped': skipped,
        'avg_cost': round(avg_cost, 1),
        'buys': buys,
        'sell_price': sell[1], 'sell_date': sell[0].isoformat(),
    }
