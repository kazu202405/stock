"""既存の screened_latest に決算月(fiscal_month)を埋める。

外部サイトへは一切アクセスしない。すでにDBにある financial_history /
cf_history の決算日から導出するだけなので、レート制限とも無関係に流せる。

前提:
    supabase/migration_fiscal_month.sql を本番へ適用済みであること。

使い方:
    python backfill_fiscal_month.py            # 未設定の銘柄だけ埋める
    python backfill_fiscal_month.py --all      # 設定済みも含めて計算し直す
    python backfill_fiscal_month.py --dry-run  # 書き込まずに件数と分布だけ出す
"""

import argparse
import os
import sys
from collections import Counter

from dotenv import load_dotenv

load_dotenv()

from analysis_quality import derive_fiscal_month
from supabase_client import get_supabase_client

# Supabaseは1リクエストのデフォルト上限が1000行。全件を一度に取ると
# 黙って切り捨てられるため、必ずページングして取り切る。
PAGE_SIZE = 500


def fetch_rows(client, only_missing):
    """financial_history付きの全銘柄をページングで取り切る"""
    rows = []
    offset = 0
    while True:
        query = client.table('screened_latest').select(
            'company_code, fiscal_month, financial_history, cf_history')
        if only_missing:
            query = query.is_('fiscal_month', 'null')
        page = query.order('company_code').range(
            offset, offset + PAGE_SIZE - 1).execute().data
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--all', action='store_true',
                        help='設定済みの銘柄も計算し直す')
    parser.add_argument('--dry-run', action='store_true',
                        help='書き込まずに結果だけ表示する')
    args = parser.parse_args()

    client = get_supabase_client()
    if client is None:
        print('Supabaseに接続できません。SUPABASE_URL / SUPABASE_KEY を確認してください。')
        return 1

    try:
        rows = fetch_rows(client, only_missing=not args.all)
    except Exception as e:
        if 'fiscal_month' in str(e):
            print('fiscal_month列がありません。'
                  '先に supabase/migration_fiscal_month.sql を適用してください。')
            return 1
        raise

    print(f'対象: {len(rows)}銘柄')

    updated = 0
    undetermined = []
    distribution = Counter()

    for row in rows:
        month = derive_fiscal_month(row.get('financial_history'),
                                    row.get('cf_history'))
        if month is None:
            # 財務履歴自体が無い銘柄。推測せず未設定のまま残す。
            undetermined.append(row['company_code'])
            continue
        distribution[month] += 1
        if row.get('fiscal_month') == month:
            continue
        if not args.dry_run:
            client.table('screened_latest').update(
                {'fiscal_month': month}).eq(
                'company_code', row['company_code']).execute()
        updated += 1
        if updated % 200 == 0:
            print(f'  {updated}件 更新')

    print()
    print(f'{"更新予定" if args.dry_run else "更新"}: {updated}件')
    print(f'決算月を判定できず: {len(undetermined)}件'
          + (f'（例: {", ".join(undetermined[:10])}）' if undetermined else ''))
    print()
    print('決算月の分布:')
    total = sum(distribution.values())
    for month, count in sorted(distribution.items()):
        share = count * 100 / total if total else 0
        print(f'  {month:2}月期: {count:5}銘柄 ({share:5.1f}%)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
