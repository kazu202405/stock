"""上場廃止の判定を1か所に集める。

2026-08-24。2026年のTOB・MBOの波で5〜7月だけで22社が上場廃止になっていたが、
アプリは生きた銘柄として表示し続けていた。株価は最終売買日で凍結されたまま、
検索にもスクリーナーにも出て、上場廃止だとはどこにも書かれていなかった。

判定の材料:
    **日足が動かなくなったこと**が一次の証拠。取引が無いのだから足も付かない。
    JPXの上場企業リスト（static/companies.json）との突き合わせは使わない。
    あれはこちらが取ってきた時点のスナップショットで、更新が遅れるうえ
    ETFを意図的に外しているので「載っていない＝廃止」にはならない
    （実測で46件が載っておらず、うち25件は上場中だった）。

    足が止まっているだけでは足りない。**取得に失敗し続けているだけ**かもしれない。
    そこで yfinance に問い合わせて値が返らないことを確かめてから印を付ける。
    detect_delisted.py がその2段構えを担う。ここには判断の中身だけを置く。

戻すとき:
    値が返るようになったら印を外す。上場廃止の判定を間違えたときに
    手で直せないと、その銘柄はアプリから消えたままになる。
"""

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# 日足がこの日数ぶん止まっていたら「取引が無い」とみなす。
# 年末年始の連休でも9日程度なので、30日あれば通常の休みで誤判定しない。
# 短くすると、取得に失敗しただけの銘柄を廃止扱いにしてしまう。
STALE_CHART_DAYS = 30


def business_days_between(start, end):
    """土日を除いた日数（start から end まで）。祝日は見ない。

    ⚠️ 暦日で数えると、月曜に「金曜から3日」となって常に古く見える。
    """
    days, cur = 0, start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def last_bar_date(bars):
    """日足のいちばん新しい足の日付（JST）。1本も無ければ None。"""
    latest = None
    for bar in bars or []:
        try:
            day = datetime.fromtimestamp(bar['time'], JST).date()
        except (KeyError, TypeError, ValueError, OSError):
            continue
        if latest is None or day > latest:
            latest = day
    return latest


def is_chart_stale(bars, today=None, days=STALE_CHART_DAYS):
    """日足が止まっているか。1本も無い場合も「止まっている」とみなす。"""
    today = today or datetime.now(JST).date()
    latest = last_bar_date(bars)
    if latest is None:
        return True
    return (today - latest).days > days


# 日足から導いた「本物の最終売買日」の印。取引終了時刻(15:00 JST)で作る。
# 分からないときは「いま」の時刻が入るので、時刻を見れば本物かどうかが分かる。
# ⚠️ **日付の新しさで見分けないこと。** 廃止の当日でも印を付けられるように
#    なったので、「新しい日付＝分からない印」という推測はもう成り立たない。
LAST_TRADE_HOUR = 15


def delisted_timestamp(bars, today=None):
    """印に入れる日時。最終売買日があればその日の15:00 JST、無ければ今。

    最終売買日を入れておくと「いつまでの数字か」が画面に出せる。
    """
    latest = last_bar_date(bars)
    if latest is None:
        return (today or datetime.now(JST)).isoformat() if isinstance(
            today, datetime) else datetime.now(timezone.utc).isoformat()
    return datetime(latest.year, latest.month, latest.day,
                    LAST_TRADE_HOUR, 0, tzinfo=JST).isoformat()


def describe(delisted_at, today=None):
    """画面に出す最終売買日。'2026-06-15' のような日付だけを取り出す。

    ⚠️ **分からないものは返さない。** 日足が1本も無い銘柄は最終売買日が
    分からず、印を付けた時刻がそのまま入る。それを「最終売買日」として出すと
    「今日まで売買されていた会社が上場廃止」という嘘になる（実際に
    7420 佐鳥電機・2692 伊藤忠食品で出た）。

    見分けるのは**時刻**。日足から導いた本物は 15:00 JST で作っている。
    ⚠️ 以前は「30日以内の日付なら分からない印」と推測していたが、
       JPXの一覧に無ければ廃止の当日から印を付けられるようになったので、
       その推測は成り立たなくなった（本物の日付まで隠していた）。
    """
    if not delisted_at:
        return None
    text = str(delisted_at)
    try:
        day = datetime.strptime(text[:10], '%Y-%m-%d').date()
    except ValueError:
        return None
    if not _is_last_trade_stamp(text):
        return None          # 印を付けた時刻＝最終売買日は分からない
    return text[:10]


def _is_last_trade_stamp(text):
    """15:00 JST（＝06:00 UTC）で作られた本物の印か。"""
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        # DBから素の文字列で戻る場合。保存はUTCなので06:00がJSTの15:00。
        return (stamp.hour, stamp.minute) == (LAST_TRADE_HOUR - 9, 0)
    return (stamp.astimezone(JST).hour, stamp.astimezone(JST).minute) == \
        (LAST_TRADE_HOUR, 0)
