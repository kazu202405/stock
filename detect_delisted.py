"""上場廃止になった銘柄に印を付ける。

2026-08-24。2026年のTOB・MBOの波で5〜7月だけで22社が上場廃止になっていたが、
アプリは生きた銘柄として表示し続けていた。株価は最終売買日で凍結されたまま、
検索にもスクリーナーにも出て、上場廃止だとはどこにも書かれていなかった。

2段構えで判定する:
    1. **日足が30日以上止まっている**銘柄を候補にする（取引が無い＝足も付かない）
    2. yfinance に問い合わせて**値が返らない**ことを確かめる

1だけでは足りない。取得に失敗し続けているだけかもしれず、上場中の会社を
アプリから消すことになる。逆に2だけだと全銘柄を個別に叩くことになり、
レート制限に当たる（1で数十件まで絞れる）。

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


def probe_is_alive(code):
    """いま値が取れるか。取れれば上場中。

    例外は「取れなかった」側に倒す。上場中の会社を消す方が、
    廃止銘柄を残すよりずっと悪い……のだが、ここで True に倒すと
    永遠に印が付かない。呼び出し側で「取れない」が続くことを見ているので、
    ここは素直に取れたかどうかだけを返す。
    """
    import yfinance as yf
    try:
        hist = yf.Ticker(f'{code}.T').history(period='5d')
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

    # 生き返った銘柄の印を外す
    to_clear = []
    for code in sorted(marked):
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
