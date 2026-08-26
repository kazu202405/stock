# -*- coding: utf-8 -*-
"""市場区分（market_segment）を JPX の公式一覧から全銘柄に入れる。

なぜ要るか:
  `sync_jpx_master.py` は**内国普通株だけ**を対象にする。そのため
  PRO Market・ETF・REIT・外国株には市場区分が一切入らず、空欄のままだった。

  ⚠️ 2026-08-26、この空欄が原因で誤診をやった。PRO Market は
     プロ投資家向け市場で**売買が成立しない日が続くのが正常**、
     Yahoo・kabutan も扱っていない。区分を持っていなかったため
     「出来高ゼロが1年続く103銘柄＝上場廃止」と読み違えた。
     区分さえ入っていれば起きなかった。

  ∴ 分析の対象外であっても**区分は必ず入れる**。
     「対象外だから空でいい」ではなく「対象外だと分かる状態にする」。

JPXの一覧に無いコードは、上場廃止か、持株会社化などでコードが変わったもの。
ここでは印を付けるだけで消さない（確かめられていないと分かる状態を残す）。

使い方:
    python sync_market_segments.py --dry-run
    python sync_market_segments.py
"""

import os
import argparse
from collections import defaultdict

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

CHUNK = 100


def chunked(items, size=CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def load_existing(client):
    rows, page = [], 0
    while page < 20:
        res = (client.table('screened_latest')
               .select('company_code, market_segment')
               .range(page * 1000, page * 1000 + 999).execute())
        chunk = res.data or []
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        page += 1
    return {r['company_code']: r for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    import jpx_master
    from supabase_client import get_supabase_client

    print('JPXの上場銘柄一覧を取得しています...')
    rows = jpx_master.fetch_all()
    jpx = {r['code']: r for r in rows}
    print(f'  JPX側 {len(rows)}件')

    client = get_supabase_client()
    existing = load_existing(client)
    print(f'  DB側 {len(existing)}件')

    matched = {c: jpx[c] for c in existing if c in jpx}
    not_in_jpx = sorted(c for c in existing if c not in jpx)

    counts = defaultdict(int)
    for r in matched.values():
        counts[r['market']] += 1
    print('\n■ 市場区分の内訳（DBにある銘柄）')
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f'    {k:<14} {v:5d}件')

    already = sum(1 for c, r in matched.items()
                  if existing[c].get('market_segment') == r['market'])
    print(f'\n  すでに正しい値が入っている: {already}件')
    print(f'  更新が要る:                {len(matched) - already}件')
    print(f'\n■ JPXの一覧に無い: {len(not_in_jpx)}件'
          '（上場廃止・コード変更の候補。ここでは触らない）')
    print('    ' + ' '.join(not_in_jpx[:20]) + (' ...' if len(not_in_jpx) > 20 else ''))

    if args.dry_run:
        print('\n--dry-run のため書き込みません')
        return

    groups = defaultdict(list)
    for code, r in matched.items():
        if existing[code].get('market_segment') != r['market']:
            groups[r['market']].append(code)

    updated = 0
    for market, codes in groups.items():
        for part in chunked(codes):
            client.table('screened_latest').update(
                {'market_segment': market}).in_('company_code', part).execute()
            updated += len(part)
        print(f'  {market:<14} {len(codes):5d}件 反映')
    print(f'\n完了: {updated}件に市場区分を入れました')


if __name__ == '__main__':
    main()
