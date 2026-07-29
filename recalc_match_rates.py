"""screened_latest 全銘柄のスコア（match_rate）を再計算する。

なぜ必要か（2026-07-29）:
スコアの定義を「12項目固定の分母」から「**判定できた項目数を分母**」に変えた。
以前は値が無い項目も 0点＝不合格 として数えていたため、キャッシュに今期予想や
CFが入っていない銘柄は不当に低く出て、更新した瞬間に跳ね上がっていた（83→100）。

DBに保存済みの match_rate は旧ルールのままなので、そのままだと
/screener の並び順が新旧混在になる。Yahoo等の外部APIは一切叩かず、
既存の保存データだけで計算し直すので安全かつ高速。

使い方:
    python recalc_match_rates.py            # 実行（DBを更新）
    python recalc_match_rates.py --dry-run  # 差分を見るだけ（更新しない）
"""

import argparse
import sys

from supabase_client import get_supabase_client, calculate_match_rate

# 1回のfetch件数。Supabaseのデフォルト上限(1000)に当たらないよう明示的にページングする
PAGE_SIZE = 500

# 計算に必要な列だけ取る（business_summary等の重い列を引かない）
COLUMNS = (
    'company_code, match_rate, market_cap, equity_ratio, operating_margin, '
    'per_forward, pbr, roa, operating_cf, free_cf, '
    'financial_history, cf_history, forecast_revenue, forecast_op_income'
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                        help='更新せず、変わる件数と例だけ表示する')
    parser.add_argument('--limit', type=int, default=0,
                        help='先頭N件だけ処理（動作確認用）')
    args = parser.parse_args()

    client = get_supabase_client()

    rows = []
    offset = 0
    while True:
        q = (client.table('screened_latest')
             .select(COLUMNS)
             .order('company_code')
             .range(offset, offset + PAGE_SIZE - 1))
        chunk = q.execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        if args.limit and len(rows) >= args.limit:
            break

    if args.limit:
        rows = rows[:args.limit]
    print(f'対象: {len(rows)}件')

    changed, unchanged, became_null, samples = 0, 0, 0, []
    for row in rows:
        code = row.get('company_code')
        old = row.get('match_rate')
        new = calculate_match_rate(row)

        if new == old:
            unchanged += 1
            continue
        changed += 1
        if new is None:
            became_null += 1
        if len(samples) < 15:
            samples.append(f'  {code}: {old} → {new}')
        if not args.dry_run:
            (client.table('screened_latest')
             .update({'match_rate': new})
             .eq('company_code', code)
             .execute())

    print(f'変更: {changed}件 / 据え置き: {unchanged}件 / 判定不能(null)になった: {became_null}件')
    if samples:
        print('例:')
        print('\n'.join(samples))
    if args.dry_run:
        print('\n--dry-run のためDBは更新していません。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
