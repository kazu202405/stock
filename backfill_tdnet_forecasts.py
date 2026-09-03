# -*- coding: utf-8 -*-
"""TDnetの決算短信から通期予想をまとめて取り込む（取りこぼしの拾い直し）。

⚠️ **TDnetが公開しているのは直近31日ぶんだけ。** それより前は取れない
   （有料サービスの領域）。落ちた日があっても31日以内なら拾い直せる。

使い方:
    py -3 backfill_tdnet_forecasts.py --days 7            # 直近7日
    py -3 backfill_tdnet_forecasts.py --days 31 --dry-run # 書き込まずに見る
    py -3 backfill_tdnet_forecasts.py --date 2026-08-14   # 1日だけ
"""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timedelta

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

# TDnetが公開している期間。これより前を指定しても404になるだけ。
PUBLIC_DAYS = 31


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--date', default=None, help='YYYY-MM-DD（1日だけ）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--sleep', type=float, default=0.4)
    args = ap.parse_args()

    import requests

    import tdnet
    import tdnet_forecast
    from supabase_client import get_supabase_client

    client = get_supabase_client()
    session = requests.Session()

    if args.date:
        days = [datetime.strptime(args.date, '%Y-%m-%d').date()]
    else:
        if args.days > PUBLIC_DAYS:
            print('※ TDnetは直近%d日ぶんしか公開されていません。%d日に切り詰めます'
                  % (PUBLIC_DAYS, PUBLIC_DAYS))
        span = min(args.days, PUBLIC_DAYS)
        today = date.today()
        days = [today - timedelta(days=i) for i in range(span)]

    total = {'reports': 0, 'with_forecast': 0, 'updated': 0,
             'skipped': 0, 'unknown': 0, 'failed': 0}
    for day in days:
        if day.weekday() >= 5:
            continue                      # 土日は開示が無い
        try:
            rows = tdnet.collect(day, session=session, sleep=args.sleep)
        except Exception as e:
            print('%s 一覧が取れませんでした: %s' % (day, str(e)[:100]), flush=True)
            continue
        if not rows:
            print('%s 決算短信なし' % day, flush=True)
            continue
        stats = tdnet_forecast.apply(client, rows, dry_run=args.dry_run)
        stats['reports'] = len(rows)
        stats['with_forecast'] = len([r for r in rows if r.get('forecast')])
        print('%s 短信%d本 / 予想あり%d本 / 更新%d件 / 据え置き%d件 / 未登録%d件'
              % (day, stats['reports'], stats['with_forecast'], stats['updated'],
                 stats['skipped'], stats['unknown']), flush=True)
        for k in total:
            total[k] += stats.get(k, 0)

    print()
    print('=== おわり ===')
    print('短信%d本 / 予想あり%d本 / 更新%d件 / 据え置き%d件 / 未登録%d件 / 失敗%d件'
          % (total['reports'], total['with_forecast'], total['updated'],
             total['skipped'], total['unknown'], total['failed']))
    if args.dry_run:
        print('※ --dry-run のため書き込んでいません')


if __name__ == '__main__':
    main()
