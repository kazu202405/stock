"""上場廃止になった銘柄に印を付ける。

2026-08-24。2026年のTOB・MBOの波で5〜7月だけで22社が上場廃止になっていたが、
アプリは生きた銘柄として表示し続けていた。株価は最終売買日で凍結されたまま、
検索にもスクリーナーにも出て、上場廃止だとはどこにも書かれていなかった。

3段構えで判定する:
    1. **日足が30日以上止まっている**銘柄を候補にする（取引が無い＝足も付かない）
    2. **JPXの公式な上場銘柄一覧に載っていない**ことを確かめる
    3. yfinance に問い合わせて値が返らないことも見る（補助）

1だけでは足りない。取得に失敗し続けているだけかもしれず、上場中の会社を
アプリから消すことになる。逆に3だけだと全銘柄を個別に叩くことになり、
レート制限に当たる（1で数十件まで絞れる）。

⚠️ **2 を足したのは 2026-08-26。** 1だけで絞ると PRO Market を巻き込む。
   TOKYO PRO Market はプロ投資家向けで**売買が成立しない日が続くのが正常**。
   実測すると「足が30日以上止まっている」52件は**全件が PRO Market**で、
   上場廃止は1件も無かった。JPXの一覧と突き合わせるとこの52件が消え、
   残る37件がすべて本当の上場廃止だった。

⚠️ **3 は単独では信用できない。** `probe_is_alive` は1年ぶんの足を見るので、
   **廃止から1年経つまで「値が返る＝上場中」を返し続ける**。
   実際 2026年6月に廃止された18件を「上場中」と誤判定していた。

⚠️ JPX一覧は `jpx_master.fetch_all()` から取る。**`static/companies.json` は使わない。**
   あれはこちらが取ってきた時点のスナップショットで更新が遅れ、ETFを
   意図的に外しているので「載っていない＝廃止」にならない。
   使うのはJPXが公開している生の一覧（PRO Market・ETF・REITも含む）。

⚠️ **daily_updated_at は候補の絞り込みに使えない。** 実測すると上場廃止銘柄でも
8月の日付が入っていた（保存が走った記録であって、足が付いた記録ではない）。
日足の中身を読んで最終足の日付を見ること。

戻すとき:
    値が返るようになった銘柄は印を外す。判定を間違えたまま直せないと、
    その銘柄はアプリから消えたままになる。

前提: supabase/migration_delisted.sql を適用済みであること。

使い方:
    python detect_delisted.py            # 候補を出すだけ
    python detect_delisted.py --apply
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

import delisting
import supabase_client as sc

PAGE_SIZE = 100
PROBE_SLEEP = 1.5


def _bars(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    return raw if isinstance(raw, list) else []


def find_candidates(client, today=None):
    """日足が止まっている銘柄を返す。[(code, name, 最終足の日付)]"""
    last_bar = {}
    offset = 0
    while True:
        page = (client.table('stock_price_history')
                .select('company_code, daily_1y')
                .range(offset, offset + PAGE_SIZE - 1)
                .execute().data or [])
        for row in page:
            bars = _bars(row.get('daily_1y'))
            if delisting.is_chart_stale(bars, today=today):
                last_bar[row['company_code']] = delisting.last_bar_date(bars)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    # 日足の行そのものが無い銘柄も候補（1本も足が無いのと同じ）
    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, company_name, delisted_at')
                .range(offset, offset + 999).execute().data or [])
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    have_history = _codes_with_history(client)

    out = []
    for row in rows:
        code = row['company_code']
        if code in last_bar or code not in have_history:
            out.append((code, row.get('company_name'), last_bar.get(code),
                        row.get('delisted_at')))
    return out, rows


def _codes_with_history(client):
    codes, offset = set(), 0
    while True:
        page = (client.table('stock_price_history')
                .select('company_code')
                .range(offset, offset + 999).execute().data or [])
        codes.update(r['company_code'] for r in page)
        if len(page) < 1000:
            break
        offset += 1000
    return codes


# 上場しているかを確かめるときに見る期間。
#
# ⚠️ **5日で見てはいけない。** TOKYO PRO Market の銘柄は売買が年に数回しかなく、
# 直近5日に足が無いのが普通。実測で1年に1本しか付いていない銘柄が多数あり、
# 5日判定だと動力・横浜ライト工業・サトウ産業など約40社を一斉に
# 「2026-07-17 上場廃止」と誤判定した（そんな日は無い）。
#
# 1年で見ると差がはっきり出る:
#   本当に廃止   6670 MCJ / 6201 豊田自動織機 / 4384 ラクスル → 1年でも 0本
#   売買が少ない 1432 動力 / 1452 横浜ライト工業 / 5135 AIR-U → 1年で 1本
PROBE_PERIOD = '1y'


def listed_codes():
    """JPXが公開している上場銘柄コードの集合。取れなければ None。

    None のときは**この条件を使わない**（全部を廃止扱いにしない）。
    取得に失敗しただけで銘柄を消すことになるため。
    """
    try:
        import jpx_master
        return {r['code'] for r in jpx_master.fetch_all()}
    except Exception as e:
        print(f'JPXの一覧を取得できませんでした（この条件は使いません）: {e}')
        return None


def probe_is_alive(code):
    """いま Yahoo にこの銘柄が存在するか。1本でも足があれば上場中。

    例外は「取れなかった」側に倒す。ここで True に倒すと永遠に印が付かない。
    取得に失敗しただけの銘柄は、翌週の検出でまた確かめられる。
    """
    import yfinance as yf
    try:
        hist = yf.Ticker(f'{code}.T').history(period=PROBE_PERIOD)
        return len(hist) > 0
    except Exception:
        return False


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
    candidates, all_rows = find_candidates(client)
    marked = {r['company_code'] for r in all_rows if r.get('delisted_at')}
    print(f'日足が{delisting.STALE_CHART_DAYS}日以上止まっている: {len(candidates)}件'
          f'（うち印つき {len([c for c in candidates if c[0] in marked])}件）')

    todo = [c for c in candidates if c[0] not in marked]

    # JPXの一覧に「載っている」銘柄は候補から外す。
    # 足が止まっているだけで、PRO Market なら正常な状態。
    listed = listed_codes()
    if listed:
        before = len(todo)
        todo = [c for c in todo if c[0] not in listed]
        if before != len(todo):
            print(f'JPXの一覧に載っているため候補から外した: {before - len(todo)}件'
                  f'（PRO Market など、売買が無いのが正常な銘柄）')

    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print('新しく印を付ける候補はありません')
    else:
        print(f'\nyfinanceで確認する: {len(todo)}件')

    to_mark, alive = [], []
    for i, (code, name, last, _) in enumerate(todo, 1):
        if probe_is_alive(code):
            alive.append((code, name))
            print(f'  [{i}/{len(todo)}] {code} {str(name)[:16]} → 値が返る（上場中）')
        else:
            stamp = (delisting.delisted_timestamp(
                [{'time': int(datetime(last.year, last.month, last.day,
                                       15, 0, tzinfo=delisting.JST).timestamp())}])
                if last else datetime.now(timezone.utc).isoformat())
            to_mark.append((code, name, stamp))
            print(f'  [{i}/{len(todo)}] {code} {str(name)[:16]} → '
                  f'上場廃止（最終売買 {delisting.describe(stamp) or "不明"}）')
        time.sleep(PROBE_SLEEP)

    # 生き返った銘柄の印を外す。
    # ⚠️ probe_is_alive は廃止から1年は「値が返る」を返し続けるので、
    #    これだけで外すと**正しく付いた印を全部外してしまう**。
    #    JPXの一覧に戻っていることを必ず併せて確かめる。
    to_clear = []
    recheck = ([c for c in sorted(marked) if c in listed] if listed
               else sorted(marked))
    if listed and len(recheck) != len(marked):
        print(f'印つき{len(marked)}件のうち、JPXの一覧に戻っている'
              f'{len(recheck)}件だけ再確認します')
    for code in recheck:
        if probe_is_alive(code):
            to_clear.append(code)
            print(f'  {code} → 値が返るようになったので印を外します')
        time.sleep(PROBE_SLEEP)

    print(f'\n印を付ける: {len(to_mark)}件 / 外す: {len(to_clear)}件 / '
          f'上場中だった: {len(alive)}件')

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
