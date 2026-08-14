"""1株配当・配当性向が「まだ終わっていない決算年度」の値になっているのを直す。

2026-08-14。367A（8月決算）の銘柄ページで、1株配当が 105円 → 60円 の
減配に見えていた。実際には減配ではなく、60円は進行中の2026年8月期の
**中間配当**で、年度が終わっていないだけだった。

原因:
  `stock_analyzer` は配当を決算年度ごとに合計し、行の日付を期末
  （例 `2026-08-28`）にしている。`app.get_latest_value()` は日付が
  最も新しい行を拾うため、**未来の日付＝進行中の年度**を最新値として
  保存していた。年間配当のつもりで中間配当だけが入る。

  同じ画面の配当性向は前期（確定した年度）のものだったため、
  1株配当60円・配当性向51.4%（実際は105円÷EPS204.25）という、
  読者が検算できない組み合わせになっていた。

`app.get_latest_completed_value()` を入れて取り出し方は直したが、
**保存済みの行は再分析しないと直らない**。このスクリプトは
`financial_history`（すでにDBにある）から計算し直すだけなので、
**Yahooには一切アクセスしない**＝レート制限に当たらず全銘柄を直せる。

使い方:
    python backfill_dps_completed_fy.py            # 対象を数えるだけ
    python backfill_dps_completed_fy.py --apply    # 書き込む
"""

import argparse
import json
import os

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

from app import get_latest_completed_value
from supabase_client import get_supabase_client

PAGE_SIZE = 500

# 直す対象。どちらも「決算年度ごとの合計」なので同じ問題を持つ。
TARGET_FIELDS = ('dps', 'payout_ratio')


def parse_history(value):
    """financial_history は文字列で入っていることがある。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, dict) else None


# 「年度が違う」と言えるだけの差。これ未満は同じ値とみなす。
#
# 保存済みの payout_ratio は小数4桁に丸められている一方、
# financial_history には丸めない値が入っている（35.823 と
# 35.823034210997676）。この差で更新すると、中身が同じ3,000件を
# 書き換えたうえ、画面の桁数まで変わる。
# 年度の取り違えは 1株配当なら円単位、配当性向なら%単位で動くので、
# 0.01 を境にすれば丸め誤差だけを落とせる。
ROUNDING_TOLERANCE = 0.01


def differs(stored, correct):
    """保存値と再計算値が「別の年度の数字」と言えるほど違うか。"""
    if stored is None and correct is None:
        return False
    if stored is None or correct is None:
        return True
    return abs(float(stored) - float(correct)) > ROUNDING_TOLERANCE


def load_rows(client):
    """全銘柄を取り切る。Supabaseは1リクエスト既定1000行までなのでページングする。"""
    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, company_name, dps, payout_ratio, financial_history')
                .range(offset, offset + PAGE_SIZE - 1)
                .execute())
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='実際に書き込む（付けなければ対象を出すだけ）')
    parser.add_argument('--limit', type=int, default=0,
                        help='先頭N件だけ処理する（動作確認用）')
    args = parser.parse_args()

    client = get_supabase_client()
    rows = load_rows(client)
    print(f'読み込み: {len(rows)}件')

    targets = []
    for row in rows:
        history = parse_history(row.get('financial_history'))
        if not history:
            continue

        patch = {}
        for field in TARGET_FIELDS:
            series = history.get(field)
            if not isinstance(series, list) or not series:
                continue
            correct = get_latest_completed_value(series)
            if differs(row.get(field), correct):
                # 保存済みの桁数にそろえる（既存行と見た目が変わらないように）
                patch[field] = round(correct, 4) if correct is not None else None

        if patch:
            targets.append((row, patch))

    print(f'ズレている銘柄: {len(targets)}件')
    if args.limit:
        targets = targets[:args.limit]

    for row, patch in targets[:20]:
        before = {f: row.get(f) for f in patch}
        print(f"  {row['company_code']} {row.get('company_name')}: {before} -> {patch}")
    if len(targets) > 20:
        print(f'  ... 他 {len(targets) - 20}件')

    if not args.apply:
        print('\n--apply を付けると書き込みます（いまは何も変えていません）')
        return

    updated, failed = 0, 0
    for row, patch in targets:
        try:
            (client.table('screened_latest')
             .update(patch)
             .eq('company_code', row['company_code'])
             .execute())
            updated += 1
        except Exception as e:
            failed += 1
            print(f"  失敗 {row['company_code']}: {e}")

    print(f'\n更新: {updated}件 / 失敗: {failed}件')


if __name__ == '__main__':
    main()
