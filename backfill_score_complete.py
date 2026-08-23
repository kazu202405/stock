"""スコアが「全項目を判定できたか」を保存する（score_complete）。

2026-08-16。一覧を「同じスコアなら緑（全項目を判定できた方）を上」に
並べたいが、緑／橙の区別は読み取り時に score_breakdown() で計算しており
DBに無かった。一覧は50件ずつサーバー側で区切るため、取得後に
ブラウザで並べ替えるとページをまたいで順序が崩れる。

このスクリプトは **DBにあるデータだけで判定する**。外部には一切
アクセスしない（判定に必要な値はすべて screened_latest に入っている）。

判定は supabase_client.score_breakdown() をそのまま呼ぶ。画面の色と
同じ関数を使うので、「緑なのに並び順は橙扱い」が起きない。

前提: supabase/migration_score_complete.sql を適用済みであること。

使い方:
    python backfill_score_complete.py            # 対象を数えるだけ
    python backfill_score_complete.py --apply    # 書き込む
"""

import argparse
import os

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

import supabase_client as sc

PAGE_SIZE = 500


def load_rows(client):
    """全銘柄を取り切る。Supabaseは1リクエスト既定1000行までなのでページングする。"""
    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('*')
                .range(offset, offset + PAGE_SIZE - 1)
                .execute())
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def refresh_score_complete(client, codes=None, verbose=False):
    """score_complete を計算し直して保存する。

    codes を渡すとその銘柄だけを見る。夜間バックフィルの直後に、
    さわった数十件だけを更新するために使う（全3,879件を読み直すと
    毎晩1分ほどかかるうえ、変わらない行まで読むことになる）。

    ⚠️ この関数が唯一の正。CLIも夜間ジョブもここを通す。
    同じ判定を2か所に書くと、片方だけ直してズレる。

    戻り値: (書き換えた件数, 失敗件数, 状態ごとの内訳)
    """
    if codes:
        rows = []
        codes = list(codes)
        for i in range(0, len(codes), 100):
            rows += (client.table('screened_latest').select('*')
                     .in_('company_code', codes[i:i + 100]).execute().data or [])
    else:
        rows = load_rows(client)

    updates = []
    counts = {'complete': 0, 'provisional': 0, 'insufficient': 0, 'other': 0}
    for row in rows:
        status = sc.score_breakdown(row)['status']
        counts[status if status in counts else 'other'] += 1
        complete = status == 'complete'
        if row.get('score_complete') != complete:
            updates.append((row['company_code'], complete))

    if verbose:
        print(f'読み込み: {len(rows)}件')
        print(f"  完全判定(緑): {counts['complete']}件")
        print(f"  暫定(橙)    : {counts['provisional']}件")
        print(f"  判定不足    : {counts['insufficient']}件")
        print(f'書き換えが必要: {len(updates)}件')

    return updates, counts


def apply_updates(client, updates):
    """算出済みの差分を書き込む。戻り値: (成功, 失敗)"""
    written, failed = 0, 0
    for code, complete in updates:
        try:
            (client.table('screened_latest')
             .update({'score_complete': complete})
             .eq('company_code', code)
             .execute())
            written += 1
        except Exception as e:
            failed += 1
            if failed <= 3:
                print(f'  失敗 {code}: {e}')
            if failed > 20:
                print('  失敗が多いので中断します')
                break
    return written, failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='実際に書き込む（付けなければ集計だけ）')
    args = parser.parse_args()

    client = sc.get_supabase_client()
    updates, counts = refresh_score_complete(client, verbose=True)

    if not args.apply:
        print('\n--apply を付けると書き込みます（いまは何も変えていません）')
        return

    written, failed = apply_updates(client, updates)
    print(f'\n更新: {written}件 / 失敗: {failed}件')


if __name__ == '__main__':
    main()
