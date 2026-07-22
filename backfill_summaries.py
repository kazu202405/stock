"""
事業概要（日本語）とテーマタグを全銘柄に生成する。

Yahoo!ファイナンス日本版へは一切アクセスしない。
yfinanceの英語説明をLLMで日本語化するため、レート制限で止まることがない。

費用の目安:
  gpt-4o-mini で1銘柄あたり0.1円程度。3,800銘柄で約400円。

使い方:
    python backfill_summaries.py --limit 5      # まず5銘柄で確認
    python backfill_summaries.py                # 未生成をすべて
    python backfill_summaries.py --overwrite    # 既存も作り直す
    python backfill_summaries.py --dry-run
"""

import os
import time
import argparse

os.environ['ENABLE_SCHEDULER'] = 'false'

CONSECUTIVE_FAIL_ABORT = 20


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}時間{m}分' if h else (f'{m}分{s}秒' if m else f'{s}秒')


def sg_model():
    import summary_generator
    return summary_generator.MODEL


def load_targets(overwrite=False, retag=False):
    from supabase_client import get_supabase_client
    client = get_supabase_client()
    rows = []
    page = 0
    while page < 20:
        res = (client.table('screened_latest')
               .select('company_code, company_name, sector, industry_jp, '
                       'business_summary_jp')
               .range(page * 1000, page * 1000 + 999)
               .execute())
        chunk = res.data or []
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        page += 1

    if retag:
        # 概要が既にある銘柄だけ。これを使い回してテーマを付け直す
        return [r for r in rows if r.get('business_summary_jp')]
    if overwrite:
        return rows
    return [r for r in rows if not r.get('business_summary_jp')]


def main():
    parser = argparse.ArgumentParser(description='事業概要の日本語生成')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--sleep', type=float, default=0.3,
                        help='銘柄間の待機秒数。yfinanceのレート制限対策')
    parser.add_argument('--retag', action='store_true',
                        help='事業概要はそのままに、テーマだけ付け直す')
    parser.add_argument('--overwrite', action='store_true',
                        help='既に日本語概要がある銘柄も作り直す')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    print('=' * 60)
    print('事業概要の日本語生成（Yahoo!JPは使いません）')
    print('=' * 60)

    import llm
    if not llm.is_available():
        print('\n[中止] OPENAI_API_KEY が設定されていません')
        return

    targets = load_targets(overwrite=args.overwrite, retag=args.retag)
    if args.limit:
        targets = targets[:args.limit]

    if args.retag:
        print('モード: テーマの付け直しのみ（事業概要は変更しません）')
    print(f'処理対象: {len(targets)}件')
    # 付け直しは英語説明の取得が不要なぶん速く、入力も短いので安い
    per_sec = 1.2 if args.retag else 2.0
    per_yen = 0.04 if args.retag else 0.1
    print(f'推定所要時間: {fmt_duration(len(targets) * (per_sec + args.sleep))}')
    print(f'推定費用: 約{len(targets) * per_yen:.0f}円（{sg_model()}）')

    if args.dry_run:
        print('\n--dry-run のため実行せず終了します')
        return
    if not targets:
        print('\n対象がありません。完了しています。')
        return

    import summary_generator as sg
    from supabase_client import update_screened_data, get_supabase_client

    client = get_supabase_client()
    themes = sg.load_taggable_themes()
    print(f'テーマ候補: {len(themes)}件')
    if not themes:
        print('[警告] テーマ候補が0件です。migration_stock_tags.sql を適用済みか確認してください')

    started = time.time()
    ok = fail = skip = 0
    consecutive_fail = 0

    print('\n開始します（Ctrl+C で安全に中断できます）\n')

    try:
        for i, row in enumerate(targets, 1):
            code = row['company_code']
            industry = row.get('industry_jp')
            try:
                if args.retag:
                    text = row.get('business_summary_jp')
                    tags = sg.generate_themes_only(text, row.get('company_name'),
                                                   industry, themes=themes)
                else:
                    result = sg.generate(code, row.get('company_name'), row.get('sector'),
                                         themes=themes, industry=industry)
                    text = result.get('summary')
                    tags = result.get('themes') or []

                # 業種タグはJPXが付けている。同じ名前をllm名義で入れ直すと
                # source が上書きされ、次の付け直しで消えてしまう
                tags = [t for t in tags if t != industry]

                if text:
                    if not args.retag:
                        update_screened_data(code, {'business_summary_jp': text})

                    # タグは付け替えになるので、いったん消してから入れ直す
                    if tags:
                        try:
                            client.table('stock_tag_map').delete().eq(
                                'company_code', code).eq('source', 'llm').execute()
                            client.table('stock_tag_map').upsert(
                                [{'company_code': code, 'tag_name': t, 'source': 'llm'}
                                 for t in tags]).execute()
                        except Exception as e:
                            print(f'  タグ保存エラー ({code}): {e}')

                    ok += 1
                    consecutive_fail = 0
                    tag_label = ('  [' + '/'.join(tags) + ']') if tags else ''
                    status = f'OK  {text[:26]}...{tag_label}'
                else:
                    # 英語説明が無い銘柄（ETF・新規上場など）は失敗ではない
                    skip += 1
                    consecutive_fail += 1
                    status = '英語説明なし'
            except Exception as e:
                fail += 1
                consecutive_fail += 1
                status = f'エラー: {str(e)[:50]}'

            remain = ((time.time() - started) / i) * (len(targets) - i)
            print(f'[{i}/{len(targets)}] {code} {status} | 成功{ok} 対象外{skip} 失敗{fail} | 残り約{fmt_duration(remain)}',
                  flush=True)

            if consecutive_fail >= CONSECUTIVE_FAIL_ABORT:
                print(f'\n[中断] {consecutive_fail}件連続で生成できませんでした。設定を確認してください。')
                break

            time.sleep(args.sleep)

    except KeyboardInterrupt:
        print('\n\n[中断] Ctrl+C を検知しました。')

    print('\n' + '=' * 60)
    print(f'完了: 成功 {ok}件 / 対象外 {skip}件 / 失敗 {fail}件 / 所要 {fmt_duration(time.time() - started)}')
    print('=' * 60)
    print('\n再実行すれば、生成済みをスキップして続きから処理します。')


if __name__ == '__main__':
    main()
