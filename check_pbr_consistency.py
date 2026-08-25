"""保存済みPBRを「時価総額 ÷ 純資産」で検算する（読むだけ。書き込まない）。

なぜ要るか:
  2026-08-12、3939 でPBRが 48.65倍（Yahoo）と 5.26倍（貸借対照表から算出）で
  食い違った。どちらが正しいか決めるのに、**株数を経由しない基準**が要る。
  ROEでの検算は使えない（EPSとBPSが同じ倍率で狂うとROEは変わらない）。
  「時価総額 ÷ 純資産」なら株数を経由しないので検出できるが、当時
  `equity` 列は全3,879件で空だった。2026-08-25 に埋めたので、初めて回せる。

  PBR = 今日の株価 ÷ 直近決算のBPS
  時価総額 ÷ 純資産 = 今日の時価総額 ÷ 直近決算の純資産
  → 同じものを別の道筋で出している。大きく食い違えばどちらかが壊れている。

⚠️ 少しの差は正常。純資産に非支配株主持分を含むかどうかで数%は動く。
   問題にするのは**倍以上ずれている**もの。
"""

import os
import sys
import argparse

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

PAGE_SIZE = 500

# これ以上ずれたら「どちらかが壊れている」とみなす。
# 2026-08-12 の方針（2つが1.5倍以上食い違ったら判定不能にする）に合わせる。
GAP_THRESHOLD = 1.5

# スコアのPBRの合格ライン（12項目の定義）。ここをまたぐ誤判定が実害。
PBR_SCORE_LINE = 10


def load_rows(client):
    rows, offset = [], 0
    select = ('company_code, company_name, pbr, market_cap, equity, '
              'total_assets, equity_ratio, delisted_at')
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=25, help='並べる件数')
    args = parser.parse_args()

    from supabase_client import get_supabase_client
    rows = load_rows(get_supabase_client())

    checkable, gaps, wrong_fail, wrong_pass = [], [], [], []
    for r in rows:
        pbr, cap, equity = r.get('pbr'), r.get('market_cap'), r.get('equity')
        if pbr is None or cap is None or equity is None or equity <= 0 or pbr <= 0:
            continue
        implied = cap / equity          # どちらも億円
        checkable.append(r)
        gap = max(pbr, implied) / min(pbr, implied)
        if gap >= GAP_THRESHOLD:
            r['_implied'] = implied
            r['_gap'] = gap
            gaps.append(r)
            # スコアの合格ラインをまたいでいるか
            if pbr >= PBR_SCORE_LINE > implied:
                wrong_fail.append(r)
            elif implied >= PBR_SCORE_LINE > pbr:
                wrong_pass.append(r)

    total = len(rows)
    print('生きている銘柄 %d / 検算できた %d (%.1f%%)'
          % (total, len(checkable), len(checkable) / total * 100 if total else 0))
    if not checkable:
        print('純資産がまだ埋まっていません。バックフィルの完了を待ってください。')
        return 0

    print('%.1f倍以上ずれている %d件 (%.1f%%)'
          % (GAP_THRESHOLD, len(gaps), len(gaps) / len(checkable) * 100))
    print('  うちPBR>=%d で不合格だが、時価総額÷純資産では合格 … %d件 ← 減点されすぎ'
          % (PBR_SCORE_LINE, len(wrong_fail)))
    print('  うちPBR<%d で合格だが、時価総額÷純資産では不合格 … %d件'
          % (PBR_SCORE_LINE, len(wrong_pass)))

    for label, group in (('減点されすぎている銘柄', wrong_fail),
                         ('甘く見えている銘柄', wrong_pass)):
        if not group:
            continue
        print('\n[%s]' % label)
        print('  %-6s %-22s %8s %10s %8s' % ('コード', '会社名', '保存PBR', '時価/純資産', 'ずれ'))
        for r in sorted(group, key=lambda x: -x['_gap'])[:args.limit]:
            print('  %-6s %-22s %8.2f %10.2f %7.1f倍'
                  % (r['company_code'], (r['company_name'] or '')[:20],
                     r['pbr'], r['_implied'], r['_gap']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
