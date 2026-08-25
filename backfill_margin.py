"""JPXの週次信用残高を全銘柄に流し込む。

なぜ要るか:
  信用倍率は 3,859銘柄のうち **22件（0.6%）** しか入っていなかった。
  銘柄ページを開いたときの後追い取得しか経路が無く、開かれた銘柄だけが
  埋まる形だったため。回転日数（信用買残 ÷ 平均出来高）は出来高が99%
  埋まったので、分子さえあれば全銘柄で出せる。

⚠️ **外部へのリクエストは1回だけ。** JPXが公開しているPDFに全銘柄
   （4,221件）が載っており、jpx_margin が1プロセス分キャッシュする。
   銘柄ごとに叩く必要はない。

⚠️ 週次データなので、必ず基準日（as_of）を source_status に残す。
   「いつ時点の残高か」が分からない数字は並べない。

使い方:
    python backfill_margin.py --dry-run
    python backfill_margin.py
"""

import os
import sys
import argparse

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

import jpx_margin
from supabase_client import get_supabase_client, merge_source_status

PAGE_SIZE = 500
FIELDS = ('margin_trading_buy', 'margin_trading_sell', 'margin_trading_ratio')


def load_rows(client):
    rows, offset = [], 0
    select = 'company_code, delisted_at, source_status, ' + ', '.join(FIELDS)
    while True:
        page = (client.table('screened_latest').select(select)
                .order('company_code')
                .range(offset, offset + PAGE_SIZE - 1).execute().data)
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return [r for r in rows if not r.get('delisted_at')]


def run(client, dry_run=False):
    # ここで1回だけPDFを取りに行く。以降は全部キャッシュから読む。
    _, diagnostic = jpx_margin.get_margin_balance('7203')
    if diagnostic.get('status') not in ('success', 'no_data'):
        print('JPXから取得できませんでした: %s' % diagnostic.get('status'))
        return 0

    table = jpx_margin._cache.get('rows') or {}
    as_of = jpx_margin._cache.get('as_of')
    if not table:
        print('PDFに銘柄が1件もありませんでした。')
        return 0
    print('JPXの基準日 %s / PDFの銘柄数 %d' % (as_of, len(table)))

    rows = load_rows(client)
    updated = unchanged = missing = 0

    for row in rows:
        code = row['company_code']
        found = table.get(code)
        if not found:
            # 信用取引の対象でない銘柄。取得失敗ではないので静かに飛ばす
            missing += 1
            continue

        diff = {}
        for field in FIELDS:
            new = found.get(field)
            if new is None:
                continue
            old = row.get(field)
            if old is None or abs(float(old) - float(new)) >= 0.005:
                diff[field] = new
        if not diff:
            unchanged += 1
            continue

        # いつ時点の残高かを必ず残す。週次なので、これが無いと
        # 「今日の数字」と読み違える
        diff['source_status'] = merge_source_status(row.get('source_status'), {
            'margin_trading': {
                'status': 'success',
                'source': 'JPX 銘柄別信用取引週末残高',
                'as_of': as_of,
                'frequency': 'weekly',
            },
        })

        if not dry_run:
            (client.table('screened_latest').update(diff)
             .eq('company_code', code).execute())
        updated += 1

    print('対象 %d銘柄 / %s %d件 / 変化なし %d件 / 信用取引の対象外 %d件'
          % (len(rows), '変わる' if dry_run else '更新', updated, unchanged, missing))
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='書き込まない')
    args = parser.parse_args()

    client = get_supabase_client()
    if client is None:
        print('Supabaseに接続できません。')
        return 1
    run(client, dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
