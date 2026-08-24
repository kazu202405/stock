"""株価に連動する指標を、いまの株価に合わせて伸縮させる。

背景（2026-08-24）:
    PER・PBR・時価総額・配当利回りは「分析した日の株価」で計算され、
    そのまま screened_latest に保存される。ところが stock_price だけは
    毎日 cron（9:25 / 11:45 / 15:20）が更新していた。

    結果、銘柄ページには「今日の株価」と「1か月前の株価で計算したPER」が
    並んで表示されていた。実測では PER が5%以上ずれている銘柄が64%、
    20%以上が31%あった（2477 手間いらず は 13.8倍と表示されていたが、
    今日の株価で計算すると 19.2倍）。

なぜ「株価 ÷ EPS」で計算し直さないのか:
    **EPS は報告通貨で入っている。** 三井海洋開発(6269)は米ドル建てで、
    eps=3.23 は3.23ドル。株価9,881円をこれで割ると3,059倍という嘘が出る。
    Yahooが返した per_forward は通貨を揃えたうえでの値なので、そちらを
    正として**比率で伸縮させる**。この方法は通貨に依存しない。

不変条件:
    per_forward / pbr / market_cap / dividend_yield は、
    **同じ行の stock_price と同じ時点のもの**である。
    株価を書き換えるときは必ずこの関数を通し、5つを一緒に更新すること。
    片方だけ更新すると、上に書いたズレがまた始まる。

    この不変条件が成り立っていることを **price_updated_at** に記録する。
    NULL は「一度も揃えたことがない＝分析時のまま」を意味する。
    さかのぼって直すバックフィルはこの列を見て対象を選ぶので、
    ここで印を付けないと同じ行を二度伸縮させてしまう。
"""

# 株価に比例する（株価が2倍ならこれも2倍）
PROPORTIONAL = ('per_forward', 'pbr', 'market_cap')

# 株価に反比例する（株価が2倍なら半分）
INVERSE = ('dividend_yield', 'dividend_yield_forward')

# 1日ぶんの更新で許容する変動幅。値幅制限を考えれば1日で半分／2倍にはならない。
# これを超えるのは株式分割・併合か、株価の取得ミス。伸縮させると嘘が広がるので
# 触らずに呼び出し側へ知らせる。
DAILY_MAX_RATIO = 2.0

# 過去にさかのぼって直すとき用。数か月ぶんの変動を許す。
BACKFILL_MAX_RATIO = 10.0


class ImplausibleRatio(Exception):
    """株価の比が想定外。株式分割か取得ミスの疑いがある。"""

    def __init__(self, ratio, base_price, new_price):
        self.ratio = ratio
        self.base_price = base_price
        self.new_price = new_price
        super().__init__(
            f'株価の比が想定外です: {base_price} → {new_price} ({ratio:.2f}倍)')


def _as_number(value):
    """数値として扱えるものだけ返す。文字列やNoneは None にする。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # NaN は自分自身と等しくない
    return None if number != number else number


def rescale(row, new_price, base_price=None, max_ratio=DAILY_MAX_RATIO):
    """株価連動の指標を new_price に合わせた値にして返す。

    Args:
        row:        screened_latest の行（dict）
        new_price:  新しい株価
        base_price: いまの指標が基準にしている株価。
                    省略すると row['stock_price'] を使う（不変条件より）
        max_ratio:  許容する変動幅。超えると ImplausibleRatio を投げる

    Returns:
        書き換えるフィールドだけの dict。stock_price と price_updated_at を
        必ず含む。変化が無ければ空の dict（＝更新不要）。

    Raises:
        ImplausibleRatio: 株価の比が max_ratio を超える／下回る場合
    """
    from datetime import datetime, timezone

    new_price = _as_number(new_price)
    if new_price is None or new_price <= 0:
        return {}

    synced_at = datetime.now(timezone.utc).isoformat()

    if base_price is None:
        base_price = row.get('stock_price')
    base_price = _as_number(base_price)

    # 基準が無ければ伸縮のしようがない。株価だけ入れておく
    # （次回からは不変条件が成り立つので伸縮できるようになる）
    if base_price is None or base_price <= 0:
        return {'stock_price': new_price, 'price_updated_at': synced_at}

    ratio = new_price / base_price
    if ratio > max_ratio or ratio < 1.0 / max_ratio:
        raise ImplausibleRatio(ratio, base_price, new_price)

    updates = {'stock_price': new_price, 'price_updated_at': synced_at}
    if ratio == 1.0:
        # 株価が同じでも stock_price は返す。呼び出し側が
        # 「更新不要」を判断できるよう、指標は入れない
        return {} if row.get('stock_price') == new_price else updates

    for key in PROPORTIONAL:
        value = _as_number(row.get(key))
        if value is not None:
            updates[key] = value * ratio
    for key in INVERSE:
        value = _as_number(row.get(key))
        if value is not None:
            updates[key] = value / ratio

    return updates


def rescale_with_score(row, new_price, base_price=None,
                       max_ratio=DAILY_MAX_RATIO):
    """rescale に加えて match_rate と score_complete も計算し直す。

    PERやPBRが動くとスコアの合否が変わることがあるため、指標だけ直して
    スコアを据え置くと「PERは不合格の値なのにスコアは満点のまま」になる。

    row はスコア判定に必要な列をすべて含んでいること（select('*')）。
    含んでいない場合は判定できる項目が減り、スコアが下がってしまう。
    """
    import supabase_client as sc

    updates = rescale(row, new_price, base_price, max_ratio)
    if not updates:
        return {}

    merged = {**row, **updates}
    breakdown = sc.score_breakdown(merged)
    updates['match_rate'] = breakdown['score']
    updates['score_complete'] = breakdown['status'] == 'complete'
    return updates
