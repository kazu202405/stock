"""同期できなかった銘柄を分析し直す。

2026-08-24。backfill_multiples_sync.py は「株価が何倍になったか測れない」行を
わざと触らずに残した（日足が古い／分析日が日足の範囲外／株価がチャートと
25%以上離れる＝株式分割・併合の疑い）。伸縮させると嘘が乗るため。

それらは**分析し直せば直る**。分析は株価も倍率も同じ snapshot から書くので、
不変条件（派生値は同じ行の stock_price と同時点）がその場で回復する。

対象は price_updated_at が NULL の行。分析すると印が付くので、
二度流しても済んだ分はやり直さない。

使い方:
    python reanalyze_unsynced.py            # 対象を数えるだけ
    python reanalyze_unsynced.py --apply
"""

import argparse
import os
import time

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

import app
import supabase_client as sc

SLEEP_SECONDS = 1.0


def load_targets(client):
    rows = (client.table('screened_latest')
            .select('company_code, company_name, analyzed_at')
            .is_('price_updated_at', 'null')
            .order('company_code')
            .limit(1000)
            .execute().data or [])
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--sleep', type=float, default=SLEEP_SECONDS,
                        help='1銘柄ごとの待ち秒数。429が出るときは長くする')
    args = parser.parse_args()

    client = sc.get_supabase_client()
    targets = load_targets(client)
    if args.limit:
        targets = targets[:args.limit]
    print(f'対象（印が付いていない行）: {len(targets)}件')
    if not args.apply:
        for row in targets[:20]:
            print(f"  {row['company_code']} {str(row.get('company_name'))[:18]}")
        if len(targets) > 20:
            print(f'  … 他{len(targets) - 20}件')
        print('\n--apply を付けると分析し直します')
        return

    from stock_analyzer import StockAnalyzer
    analyzer = StockAnalyzer()
    ok = fail = 0
    started = time.time()
    for i, row in enumerate(targets, 1):
        code = row['company_code']
        try:
            if app._analyze_stock_and_save(analyzer, code):
                ok += 1
            else:
                fail += 1
                print(f'  [{i}/{len(targets)}] {code} データが取れませんでした')
        except Exception as e:
            fail += 1
            print(f'  [{i}/{len(targets)}] {code} 失敗: {str(e)[:90]}')
        if i % 10 == 0 or i == len(targets):
            per = (time.time() - started) / i
            print(f'  [{i}/{len(targets)}] 成功{ok} 失敗{fail} '
                  f'| 残り約{int(per * (len(targets) - i) / 60)}分')
        time.sleep(args.sleep)

    print(f'\n完了: 成功{ok}件 / 失敗{fail}件 / '
          f'所要{int((time.time() - started) / 60)}分')


if __name__ == '__main__':
    main()
