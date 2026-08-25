"""増減率・流動比率など、絞り込みに使う派生列を埋める。

なぜ要るか:
  スコアの12項目は financial_history から都度計算しているので列が要らない。
  だがスクリーナーはDB側で絞るため、列が空だと「増収率10%以上」で探せない。
  2026-08-25 時点で成長率の列は**全銘柄で空**（25件だけ古い値が残っていた）。

⚠️ **外部アクセスは一切しない。** 手元の financial_history / cf_history と
   forecast_* から計算するだけ。Yahoo にもEDINETにも行かない。

⚠️ 派生値なので、元の値と一緒に動かすこと。財務履歴を取り直したらこの列も
   作り直す。片方だけ更新すると、画面には正しい売上高と古い増減率が並ぶ
   （片方が正しいので壊れて見えない）。毎晩の scheduled_update_daily_and_crosses
   から呼ぶようにしてある。

使い方:
    python backfill_growth_columns.py --dry-run   # 何件変わるか見るだけ
    python backfill_growth_columns.py             # 適用
    python backfill_growth_columns.py --code 7203
"""

import os
import sys
import argparse

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

from analysis_quality import GROWTH_COLUMNS, derive_growth_columns

PAGE_SIZE = 500

# 丸め誤差だけの書き換えを避ける。0.01%未満の差は「変わっていない」とみなす。
# （閾値を決めずに差分を取ると、ほぼ全件を無意味に更新することになる）
EPSILON = 0.01

SELECT = ('company_code, financial_history, cf_history, '
          'forecast_revenue, forecast_op_income, delisted_at, '
          + ', '.join(GROWTH_COLUMNS))


def load_rows(client, code=None):
    if code:
        return (client.table('screened_latest').select(SELECT)
                .eq('company_code', code).execute().data)

    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest').select(SELECT)
                .order('company_code')
                .range(offset, offset + PAGE_SIZE - 1).execute().data)
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def changed_fields(row, derived):
    """実際に値が変わる列だけを返す。"""
    out = {}
    for key, new in derived.items():
        old = row.get(key)
        if new is None and old is None:
            continue
        if new is None or old is None:
            out[key] = new
            continue
        if abs(float(old) - float(new)) >= EPSILON:
            out[key] = new
    return out


def run(client, code=None, dry_run=False, verbose=False):
    rows = load_rows(client, code)
    # 上場廃止は株価も財務も止まっているので触らない
    rows = [r for r in rows if not r.get('delisted_at')]

    updated = unchanged = 0
    filled = {key: 0 for key in GROWTH_COLUMNS}

    for row in rows:
        derived = derive_growth_columns(row)
        for key, value in derived.items():
            if value is not None:
                filled[key] += 1

        diff = changed_fields(row, derived)
        if not diff:
            unchanged += 1
            continue
        if not dry_run:
            (client.table('screened_latest').update(diff)
             .eq('company_code', row['company_code']).execute())
        updated += 1
        if verbose:
            print('  %s %s' % (row['company_code'], sorted(diff)))

    total = len(rows)
    print('対象 %d銘柄 / %s %d件 / 変化なし %d件'
          % (total, '変わる' if dry_run else '更新', updated, unchanged))
    print('埋まる件数:')
    for key in GROWTH_COLUMNS:
        print('  %-22s %5d  (%4.1f%%)' % (key, filled[key], filled[key] / total * 100))
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', help='この銘柄だけ')
    parser.add_argument('--dry-run', action='store_true', help='書き込まない')
    parser.add_argument('--verbose', action='store_true', help='変わった列を並べる')
    args = parser.parse_args()

    from supabase_client import get_supabase_client
    client = get_supabase_client()
    if client is None:
        print('Supabaseに接続できません。')
        return 1

    run(client, code=args.code, dry_run=args.dry_run, verbose=args.verbose)
    return 0


if __name__ == '__main__':
    sys.exit(main())
