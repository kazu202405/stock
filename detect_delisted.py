"""上場廃止になった銘柄に印を付ける。

2026-08-24。2026年のTOB・MBOの波で5〜7月だけで22社が上場廃止になっていたが、
アプリは生きた銘柄として表示し続けていた。株価は最終売買日で凍結されたまま、
検索にもスクリーナーにも出て、上場廃止だとはどこにも書かれていなかった。

## いまの判定（2026-09-03 に作り直した）

    候補      = JPXの一覧に無い内国株  または  日足が30日以上止まっている
    印を付ける = 候補 かつ JPXの一覧に無い かつ 直近5営業日に値が付かない
    印を外す   = 印つき かつ JPXの一覧に載っている

決め手は**JPXの公式な上場銘柄一覧**。載っていなければ上場していない。
日足やYahooは補助で、単独では判定に使わない。

⚠️ **JPXの一覧が取れないときは何もしない（fail-closed）。**
   以前は「この条件を使わない」に倒していたが、それをやると
   PRO Market を廃止扱いにするうえ、**正しく付いた印を全部外す**。
   2026-09-03 の実測では、JPXが .xls → .xlsx に変わって一覧が404になり、
   40件の印を外す判定になっていた（実際にはどれも廃止のまま）。

## 過去に踏んだ間違い

⚠️ **日足だけで絞ると PRO Market を巻き込む。**
   TOKYO PRO Market はプロ投資家向けで**売買が成立しない日が続くのが正常**。
   実測で「足が30日以上止まっている」52件は全件が PRO Market だった。
   2026-07-17 には約40社を一斉に「上場廃止」と誤判定している（そんな日は無い）。

⚠️ **1年ぶんの足で生死を見ると、廃止から1年は「上場中」を返し続ける。**
   2026年6月に廃止された18件を「上場中」と誤判定していた。
   ∴ 生死の確認は**直近5営業日**で見る。PRO Market を巻き込む心配は、
   先にJPXの一覧で落としているので無い（この順番が要る）。

⚠️ **日足の最終足は「出来高がある足」で見る。**
   出来高0の足が埋まっている銘柄があり、最終足だけ見ると新しく見える。
   実測で PALTAC(8283) は 2026-08-07 が最後の売買なのに、足の日付は 08-10 だった。

⚠️ **JPXに無い＝廃止、とは限らない。** 実測で3件（nmsホールディングス・
   ディーブイエックス・神鋼鋼線工業）はJPXの一覧に無いのに Yahoo は値を返した。
   ∴ 値が付かないことも併せて確かめ、食い違うものは**保留して人が見る**。

⚠️ JPX一覧は `jpx_master.fetch_all()` から取る。**`static/companies.json` は使わない。**
   あれはこちらが取ってきた時点のスナップショットで更新が遅れ、ETFを
   意図的に外しているので「載っていない＝廃止」にならない。

戻すとき:
    JPXの一覧に戻った銘柄は印を外す。判定を間違えたまま直せないと、
    その銘柄はアプリから消えたままになる。

前提: supabase/migration_delisted.sql を適用済みであること。

使い方:
    python detect_delisted.py            # 候補を出すだけ
    python detect_delisted.py --apply
"""

import argparse
import json
import os
from datetime import datetime, timezone

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

import delisting
import supabase_client as sc

PAGE_SIZE = 100

# 内国普通株の市場区分。ここに入らないもの（PRO Market・ETF・REIT・外国株）は
# そもそも分析対象ではないので、印の対象にもしない。
DOMESTIC = ('プライム', 'スタンダード', 'グロース')

# 生死を確かめる期間。
#
# ⚠️ **1年で見てはいけない。** 廃止から1年は古い足が残っていて「値が返る」に
#    なり、廃止したばかりの銘柄に永遠に印が付かない（実測で8件が該当）。
#    5営業日で見ると、廃止済みは Yahoo が何も返さない。
#
# ⚠️ 5日判定が使えるのは**JPXの一覧で先に PRO Market を落としているから**。
#    順番を入れ替えると、売買が年に数回の PRO Market を全部廃止扱いにする。
PROBE_PERIOD = '1mo'

# 直近の足がこの営業日数より古ければ「もう取引されていない」とみなす。
#
# ⚠️ **「期間内に足が1本でもあるか」で見てはいけない。** 廃止直後は期間内に
#    古い足が残っているので「生きている」と読んでしまう。実測で、2026-08-27〜28に
#    取引が終わった3社（nmsHD・ディーブイエックス・神鋼鋼線工業）が5日判定を
#    すり抜けた。**最後の足がいつかを見る。**
PROBE_STALE_DAYS = 3


class ListingUnavailable(RuntimeError):
    """JPXの上場銘柄一覧が取れなかった。判定を中止する合図。"""


def _bars(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    return raw if isinstance(raw, list) else []


def traded_bars(bars):
    """出来高がある足だけ返す。出来高0は「取引が無かった日」の埋め草。"""
    return [b for b in bars or [] if (b.get('volume') or 0) > 0]


def listed_codes():
    """JPXが公開している上場銘柄コードの集合。取れなければ None。

    None は「判定できない」であって「一覧が空」ではない。
    ⚠️ **None のときは印を付けも外しもしないこと（fail-closed）。**
       空の集合として扱うと全銘柄が「一覧に無い」になり、全部を廃止にする。
    """
    try:
        import jpx_master
        return {r['code'] for r in jpx_master.fetch_all()}
    except Exception as e:
        print(f'JPXの一覧を取得できませんでした: {e}')
        return None


def _all_rows(client):
    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, company_name, delisted_at, market_segment')
                .range(offset, offset + 999).execute().data or [])
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return rows


def _last_traded(client):
    """銘柄ごとの「最後に売買があった日」。日足が無い銘柄は None を入れる。"""
    out, offset = {}, 0
    while True:
        page = (client.table('stock_price_history')
                .select('company_code, daily_1y')
                .range(offset, offset + PAGE_SIZE - 1).execute().data or [])
        for row in page:
            out[row['company_code']] = delisting.last_bar_date(
                traded_bars(_bars(row.get('daily_1y'))))
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return out


def find_candidates(client, today=None, listed=None):
    """印を付ける候補を返す。[(code, name, 最終売買日, delisted_at)]

    候補にする条件は2つのどちらか:
      - JPXの一覧に無い内国株（決め手。廃止直後でもその日から拾える）
      - 日足が30日以上止まっている（JPXの反映が遅れた場合の保険）
    """
    today = today or datetime.now(delisting.JST).date()
    rows = _all_rows(client)
    last_traded = _last_traded(client)

    out = []
    for row in rows:
        code = row['company_code']
        if (row.get('market_segment') or '') not in DOMESTIC:
            continue
        last = last_traded.get(code)
        stale = last is None or (today - last).days > delisting.STALE_CHART_DAYS
        missing = listed is not None and code not in listed
        if stale or missing:
            out.append((code, row.get('company_name'), last, row.get('delisted_at')))
    return out, rows


def probe_has_recent_trading(codes, today=None):
    """直近に値が付いている銘柄の集合を返す（最後の足の日付で見る）。

    ⚠️ 一括の yf.download を使う（200銘柄で1リクエスト）。銘柄ごとに
       Ticker().history() を叩くとレート制限に当たる。
    ⚠️ 例外は「値が付かなかった」側に倒さない。取得に失敗しただけの銘柄を
       廃止にしないため、失敗時は**全部を「値が付いた」扱い**にして、
       その回は印を付けない（fail-closed）。
    """
    codes = list(codes)
    if not codes:
        return set()
    import yfinance as yf
    try:
        data = yf.download([c + '.T' for c in codes], period=PROBE_PERIOD,
                           interval='1d', auto_adjust=False, progress=False,
                           group_by='ticker')
    except Exception as e:
        print(f'値の確認に失敗しました（今回は印を付けません）: {e}')
        return set(codes)

    today = today or datetime.now(delisting.JST).date()
    alive = set()
    for code in codes:
        try:
            closes = data[code + '.T']['Close'].dropna()
            if not len(closes):
                continue
            last = closes.index[-1].date()
            if delisting.business_days_between(last, today) <= PROBE_STALE_DAYS:
                alive.add(code)
        except Exception:
            pass
    return alive


def plan_changes(client, today=None, verbose=True, probe=None):
    """印を付ける／外す銘柄を決める。書き込みはしない。

    Returns: (to_mark, to_clear, held)
        to_mark … [(code, name, stamp)]  廃止と判定
        to_clear… [code]                 印が誤りだったもの
        held    … [(code, name)]         JPXに無いが値は付く＝保留（人が見る）

    Raises:
        ListingUnavailable: JPXの一覧が取れないとき。判定そのものを中止する。

    ⚠️ スケジューラと手動スクリプトの両方がこれを呼ぶ。片方だけに条件を
       足すと、同じ名前の処理が別の判定をするようになる。
    """
    listed = listed_codes()
    if not listed:
        raise ListingUnavailable(
            'JPXの上場銘柄一覧を取得できませんでした。一覧なしで判定すると '
            'PRO Market を廃止扱いにしたり、正しく付いた印を外したりするので、'
            '今回は何もしません。')

    candidates, rows = find_candidates(client, today=today, listed=listed)
    marked = {r['company_code'] for r in rows if r.get('delisted_at')}

    todo = [c for c in candidates if c[0] not in marked and c[0] not in listed]
    skipped = len([c for c in candidates if c[0] not in marked]) - len(todo)
    if verbose:
        print(f'候補 {len(candidates)}件（うち印つき '
              f'{len([c for c in candidates if c[0] in marked])}件）')
        if skipped:
            print(f'JPXの一覧に載っているため候補から外した: {skipped}件'
                  f'（PRO Market など、売買が無いのが正常な銘柄）')

    prober = probe or probe_has_recent_trading
    alive = prober([c[0] for c in todo])

    to_mark, held = [], []
    for code, name, last, _ in todo:
        if code in alive:
            # JPXには無いのに値が付く。廃止の直前後や、コード変更の可能性がある。
            held.append((code, name))
            if verbose:
                print(f'  保留 {code} {str(name)[:16]} → JPXに無いが値は付く')
            continue
        stamp = (delisting.delisted_timestamp(
            [{'time': int(datetime(last.year, last.month, last.day, 15, 0,
                                   tzinfo=delisting.JST).timestamp())}])
            if last else datetime.now(timezone.utc).isoformat())
        to_mark.append((code, name, stamp))
        if verbose:
            print(f'  印 {code} {str(name)[:16]} → '
                  f'上場廃止（最終売買 {delisting.describe(stamp) or "不明"}）')

    # 印つきなのにJPXの一覧に載っているものは、印のほうが誤り。
    # ⚠️ ここで値が付くかは見ない。PRO Market は上場中でも値が付かない日が続く。
    to_clear = sorted(marked & listed)
    if verbose:
        for code in to_clear:
            print(f'  外す {code} → JPXの一覧に載っている（上場中）')

    return to_mark, to_clear, held


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()

    if not sc.has_column('screened_latest', 'delisted_at'):
        print('[未適用] delisted_at 列がありません。'
              'supabase/migration_delisted.sql を先に適用してください')
        return

    client = sc.get_supabase_client()
    try:
        to_mark, to_clear, held = plan_changes(client)
    except ListingUnavailable as e:
        print(f'\n中止: {e}')
        return

    if args.limit:
        to_mark = to_mark[:args.limit]

    print(f'\n印を付ける: {len(to_mark)}件 / 外す: {len(to_clear)}件 / '
          f'保留: {len(held)}件')

    if not args.apply:
        print('\n--apply を付けると書き込みます（いまは何も変えていません）')
        return

    written = failed = 0
    for code, _name, stamp in to_mark:
        try:
            (client.table('screened_latest').update({'delisted_at': stamp})
             .eq('company_code', code).execute())
            written += 1
        except Exception as e:
            failed += 1
            print(f'  失敗 {code}: {e}')
    for code in to_clear:
        try:
            (client.table('screened_latest').update({'delisted_at': None})
             .eq('company_code', code).execute())
            written += 1
        except Exception as e:
            failed += 1
            print(f'  失敗 {code}: {e}')
    print(f'\n更新: {written}件 / 失敗: {failed}件')


if __name__ == '__main__':
    main()
