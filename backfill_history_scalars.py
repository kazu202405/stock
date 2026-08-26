# -*- coding: utf-8 -*-
"""履歴から作るスカラー列（eps など）を、履歴の最新期に合わせ直す。

2026-08-26。eps 列が履歴より1期古い銘柄が **1,263件（34.5%）** あった。
4288 アズジェントは列が -115.44、履歴の最新期は +44.04 で、
**黒字の会社が赤字として企業比較ページに出ていた。**

分析した瞬間は合っている（get_latest_value が日付で正しく選ぶ）。
その後に履歴だけが新しくなり、列が取り残されて起きる。
これから先は supabase_client.sync_history_scalars() が保存のたびに
揃えるので、ここは**溜まったぶんの手当て**。

⚠️ 列ごとにルールが違う。dps・payout_ratio は進行中の年度を除く。
   実測では両者ともズレ0%なので、実際に直るのは eps だけ。

使い方:
    python backfill_history_scalars.py --dry-run
    python backfill_history_scalars.py
"""

import argparse
import json
import os

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

PAGE = 500


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    import supabase_client as sc
    client = sc.get_supabase_client()

    rows, start = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, eps, dps, payout_ratio, financial_history')
                .range(start, start + PAGE - 1).execute().data or [])
        if not page:
            break
        rows.extend(page)
        start += PAGE
    print(f'対象 {len(rows)}件')

    fixes = []
    for row in rows:
        history = row.get('financial_history')
        if isinstance(history, str):
            try:
                history = json.loads(history)
            except (ValueError, TypeError):
                continue
        if not isinstance(history, dict):
            continue
        for col, _hist_col, key, skip in sc._HISTORY_SCALARS:
            want = sc._pick_from_history(history.get(key), skip)
            got = row.get(col)
            if want is None:
                continue
            try:
                w, g = float(want), float(got)
            except (TypeError, ValueError):
                if got is None:
                    fixes.append((row['company_code'], col, got, want))
                continue
            scale = max(abs(w), abs(g))
            if scale == 0:
                continue
            if abs(w - g) / scale >= 0.02 or (w < 0) != (g < 0):
                fixes.append((row['company_code'], col, g, w))

    by_col = {}
    for _, col, _, _ in fixes:
        by_col[col] = by_col.get(col, 0) + 1
    print('\n直すもの:')
    for col, n in sorted(by_col.items(), key=lambda kv: -kv[1]):
        print(f'    {col:<16} {n:5d}件')
    print('\n例:')
    for code, col, got, want in fixes[:8]:
        print(f'    {code:<6} {col:<14} {got} → {want}')

    if args.dry_run:
        print('\n--dry-run のため書き込みません')
        return

    updates = {}
    for code, col, _, want in fixes:
        updates.setdefault(code, {})[col] = want
    done = 0
    for code, data in updates.items():
        client.table('screened_latest').update(data).eq(
            'company_code', code).execute()
        done += 1
        if done % 200 == 0:
            print(f'  {done}/{len(updates)}件', flush=True)
    print(f'\n完了: {done}銘柄を直しました')


if __name__ == '__main__':
    main()
