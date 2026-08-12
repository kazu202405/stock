"""あり得ない配当利回りになっている銘柄を、支払い実績から計算し直す。

2026-08-12。スクリーナーを配当利回り順に並べると 40%超 の銘柄が並んでいた。

原因（`stock_analyzer._get_basic_info` 側は修正済み）:
  - Yahoo の `dividendYield` は常に%（0.4 は 0.4%）。それを「0.5未満なら小数」と
    推測して100倍する分岐があり、利回り0.5%未満の銘柄を軒並み壊していた
    （9720: 0.4% → 40% / 153A: 0.43% → 43%）
  - `trailingAnnualDividendRate` は分割調整されないことがある
    （4918: 実際15円のところ150円 → 47.5%）

このスクリプトは対象銘柄だけ `ticker.dividends`（分割調整済みの支払い実績）を
取り直して、直近12か月の合計÷株価で入れ替える。対象は上限を超えた銘柄だけなので
外部アクセスは数十件で済む（全銘柄ループはしない＝レート制限に当たらない）。

支払い実績が取れない銘柄は None にする。**誤った数字を出し続けるより、
「不明」の方がよい。**

使い方:
    python backfill_dividend_yield.py --dry-run   # 対象と新しい値を見るだけ
    python backfill_dividend_yield.py             # 書き込む
"""

import argparse
import os
import sys
import time

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

import yfinance as yf

from stock_analyzer import StockAnalyzer
from supabase_client import get_supabase_client

PAGE_SIZE = 500


def to_symbol(code):
    """DB保存形式の銘柄コードを yfinance のシンボルに直す。

    4桁で先頭が数字なら日本株（`367A` のような新形式も日本株）。
    """
    code = (code or '').strip()
    if len(code) == 4 and code[0].isdigit():
        return code + '.T'
    return code


def load_targets(client, limit):
    """利回りが上限を超えている銘柄を取り切る。"""
    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, company_name, stock_price, dividend_yield')
                .gt('dividend_yield', limit)
                .order('company_code')
                .range(offset, offset + PAGE_SIZE - 1).execute().data)
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='書き込まない')
    parser.add_argument('--sleep', type=float, default=1.0,
                        help='1銘柄ごとの待ち秒数（既定1.0）')
    # 保存時の上限(20%)より低くしてある。壊れた値は `0.45 → 45.0` のように
    # 100倍されて出るため、10%台にも取りこぼしがいる（204A: 8.0% → 20.0%）。
    # 支払い実績から取り直すので、正常な高利回り銘柄は正しい値のまま戻る。
    parser.add_argument('--threshold', type=float, default=10.0,
                        help='この%を超える銘柄を検算対象にする（既定10.0）')
    args = parser.parse_args()

    client = get_supabase_client()
    if client is None:
        print('Supabaseに接続できません。')
        return 1

    analyzer = StockAnalyzer()
    limit = args.threshold
    targets = load_targets(client, limit)
    print(f'配当利回りが {limit}% を超えている銘柄: {len(targets)}件')
    print()

    recomputed = cleared = 0
    for row in targets:
        code = row['company_code']
        price = row.get('stock_price')
        before = row.get('dividend_yield')

        value = None
        try:
            ticker = yf.Ticker(to_symbol(code))
            value = analyzer._trailing_dividend_yield(ticker, price)
        except Exception as e:
            print(f'  {code}: 取得エラー {e}')

        if value is None:
            cleared += 1
            note = '不明にする（支払い実績が取れない）'
        else:
            recomputed += 1
            note = f'{value}%'
        print(f'  {code}: {before}% → {note}')

        if not args.dry_run:
            client.table('screened_latest').update(
                {'dividend_yield': value}).eq('company_code', code).execute()

        time.sleep(args.sleep)

    print()
    print(f'計算し直した: {recomputed}件 / 不明にした: {cleared}件'
          f'{"（試算）" if args.dry_run else ""}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
