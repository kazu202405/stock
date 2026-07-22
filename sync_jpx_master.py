"""
JPXの上場銘柄一覧を取り込み、業種・市場区分をDBに反映する。

業種はLLMに推論させず、JPXの公式データをそのまま入れる。
月に1度ほど流せば、新規上場・市場変更・業種変更に追随できる。

使い方:
    python sync_jpx_master.py --dry-run   # 差分だけ見る
    python sync_jpx_master.py             # 反映する
"""

import os
import argparse
from collections import defaultdict

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

# PostgRESTはURLに条件を載せるため、1回のin_()に詰め込みすぎると長さ制限に当たる
CHUNK = 100


def chunked(items, size=CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def load_existing():
    """screened_latestの現状を銘柄コードで引ける形にする"""
    from supabase_client import get_supabase_client
    client = get_supabase_client()
    rows = []
    page = 0
    while page < 20:
        res = (client.table('screened_latest')
               .select('company_code, industry_jp, market_segment')
               .range(page * 1000, page * 1000 + 999).execute())
        chunk = res.data or []
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        page += 1
    return {r['company_code']: r for r in rows}


def sync_industry_tags(client, names):
    """33業種をタグ・マスタに登録する。

    kind='industry' として theme と分ける。tagging_enabled=false にして
    LLMの候補には出さない（業種は事実データなので推論させない）。

    既存のタグは書き換えない。
    「電気機器」「機械」「化学」「鉄鋼」「精密機器」は33業種にもテーマにも
    同じ名前で存在する。ここでupsertすると kind と tagging_enabled が
    上書きされ、LLMの候補から消えてしまう（実際に一度そうなった）。
    同じ名前のタグをJPXとLLMの両方が付けても、主キーが
    (company_code, tag_name) なので重複しない。
    """
    existing = set()
    page = 0
    while page < 10:
        res = (client.table('stock_tags').select('name')
               .range(page * 1000, page * 1000 + 999).execute())
        chunk = res.data or []
        existing.update(r['name'] for r in chunk)
        if len(chunk) < 1000:
            break
        page += 1

    payload = [{'name': n, 'kind': 'industry', 'category': '業種',
                'tagging_enabled': False, 'display_active': True,
                'sort_order': 1000 + i}
               for i, n in enumerate(names) if n not in existing]
    if payload:
        client.table('stock_tags').insert(payload).execute()
    return len(payload), len(names) - len(payload)


def main():
    parser = argparse.ArgumentParser(description='JPX銘柄マスタの同期')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    print('=' * 60)
    print('JPX 上場銘柄一覧の同期')
    print('=' * 60)

    import jpx_master
    from supabase_client import get_supabase_client

    print('\nJPXから取得しています...')
    rows = jpx_master.fetch()
    industries = jpx_master.industry_names(rows)
    print(f'  内国株式 {len(rows)}件 / 33業種 {len(industries)}種類')

    markets = defaultdict(int)
    for r in rows:
        markets[r['market']] += 1
    print('  ' + ' / '.join(f'{k} {v}' for k, v in markets.items()))

    existing = load_existing()
    print(f'\nDB側の銘柄数: {len(existing)}件')

    targets = [r for r in rows if r['code'] in existing]
    missing = [r for r in rows if r['code'] not in existing]
    filled = sum(1 for r in targets if existing[r['code']].get('industry_jp'))

    print(f'  照合できた: {len(targets)}件（うち業種が既に入っている {filled}件）')
    if missing:
        print(f'  DBに無い銘柄: {len(missing)}件（新規上場など。分析実行時に追加されます）')

    if args.dry_run:
        print('\n--dry-run のため書き込みません')
        print('\n先頭5件の反映内容:')
        for r in targets[:5]:
            print(f"  {r['code']} {r['name'][:14]:16} {r['industry']:10} "
                  f"{r['market']:8} {r['size'] or '-'}")
        return

    client = get_supabase_client()

    added, kept = sync_industry_tags(client, industries)
    print(f'\n業種タグ: 新規{added}件 / 既存テーマと同名のため据え置き{kept}件')

    # 同じ内容の銘柄をまとめて1回で更新する。
    # 1銘柄ずつ更新すると3,800回の通信になるが、業種×市場×規模の
    # 組み合わせは数百通りしかないため、まとめると大幅に減る。
    groups = defaultdict(list)
    for r in targets:
        key = (r['industry'], r['industry17'], r['market'], r['size'])
        groups[key].append(r['code'])

    print(f'更新をまとめました: {len(groups)}通り')

    updated = 0
    for i, (key, codes) in enumerate(groups.items(), 1):
        industry, industry17, market, size = key
        data = {'industry_jp': industry, 'industry17_jp': industry17,
                'market_segment': market, 'size_category': size}
        for part in chunked(codes):
            client.table('screened_latest').update(data).in_(
                'company_code', part).execute()
            updated += len(part)
        if i % 50 == 0 or i == len(groups):
            print(f'  [{i}/{len(groups)}] {updated}件', flush=True)

    # 業種をタグとしても引けるようにする（スクリーナーの絞り込みを統一するため）
    print('\n業種タグを銘柄に紐付けています...')
    mapped = 0
    payload = [{'company_code': r['code'], 'tag_name': r['industry'],
                'source': 'jpx'} for r in targets if r['industry']]
    for part in chunked(payload, 500):
        client.table('stock_tag_map').upsert(part).execute()
        mapped += len(part)
        if mapped % 2000 < 500:
            print(f'  {mapped}件', flush=True)

    print('\n' + '=' * 60)
    print(f'完了: 業種を{updated}件に反映 / タグ紐付け{mapped}件')
    print('=' * 60)


if __name__ == '__main__':
    main()
