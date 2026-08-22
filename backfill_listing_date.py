"""上場日（listing_date）だけを埋める。

背景（2026-08-21）:
  stock_analyzer が読んでいたキーが古く（firstTradeDateEpochUtc）、
  いまの yfinance は firstTradeDateMilliseconds を返すため、
  **上場日が 3,879件中2件しか入っていなかった**。キー名は修正済み。

  ただし修正しただけでは既存行は埋まらない。上場日を書くのは
  「フル分析（analyze）」の経路だけで、`backfill_yahoo_fields.py`
  （Yahoo日本版のプロフィール穴埋め）は yfinance の info を叩かないため。
  決算発表のあった銘柄から自然に埋まってはいくが、全体は待てない。

  かといって `backfill_all_stocks.py`（1銘柄10リクエスト・約4.5時間）を
  上場日のためだけに回すのは重い。**必要なのは info の1リクエストだけ**なので、
  それだけを取りに行く。

⚠️ info は「個別系」＝レート制限に当たる側。yfinance_guard で待って続ける。
   `backfill_yahoo_fields.py`（Yahoo日本版HTML）とは叩き先が別なので
   同時に走らせても衝突はしないが、合計の負荷は上がる。

使い方:
    python backfill_listing_date.py --limit 20      # まず20銘柄で試す
    python backfill_listing_date.py                 # 未取得を全部
    python backfill_listing_date.py --dry-run
"""

import argparse
import os
import time
from datetime import datetime

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

import yfinance as yf

from yfinance_guard import RateLimitGuard, RateLimitExhausted
from supabase_client import get_supabase_client, update_screened_data


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}時間{m}分' if h else (f'{m}分{s}秒' if m else f'{s}秒')


def load_targets():
    """上場日が未取得の銘柄コードを返す。取得済みは触らない。"""
    client = get_supabase_client()
    codes, page = [], 0
    while page < 20:
        res = (client.table('screened_latest')
               .select('company_code')
               .is_('listing_date', 'null')
               .order('company_code')
               .range(page * 1000, page * 1000 + 999)
               .execute())
        rows = res.data or []
        codes += [r['company_code'] for r in rows]
        if len(rows) < 1000:
            break
        page += 1
    return codes


def extract_listing_date(info):
    """yfinance の info から上場日を取り出す。

    キーは2種類あり、単位が違う（ミリ秒 / 秒）。旧名しか見ていなかったのが
    「2件しか埋まっていない」原因だったので、両方見て桁で判定する。
    """
    raw = (info.get('firstTradeDateMilliseconds')
           or info.get('firstTradeDateEpochUtc'))
    if not raw:
        return None
    try:
        epoch = int(raw)
    except (TypeError, ValueError):
        return None
    if epoch > 1e11:            # ミリ秒とみなす境界（1e11秒＝西暦5138年）
        epoch //= 1000
    try:
        return datetime.fromtimestamp(epoch).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description='上場日だけを埋める')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--sleep', type=float, default=1.2,
                        help='銘柄間の待機秒数。infoは個別系APIなので詰めすぎない')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    targets = load_targets()
    if args.limit:
        targets = targets[:args.limit]

    print('=' * 60)
    print('上場日の穴埋め')
    print('=' * 60)
    print(f'対象: {len(targets)}件 / 待機 {args.sleep}秒')
    print(f'概算所要: {fmt_duration(len(targets) * (args.sleep + 1.0))}')
    if args.dry_run:
        print('\n--dry-run のため実行せず終了します')
        return

    guard = RateLimitGuard(
        base_sleep=args.sleep,
        on_wait=lambda sec, attempt: print(
            f'  … レート制限。{fmt_duration(sec)} 待ちます（{attempt}回目）', flush=True))

    ok = skipped = failed = 0
    started = time.time()
    try:
        for i, code in enumerate(targets, 1):
            symbol = code if code.endswith('.T') else f'{code}.T'
            try:
                info = guard.run(lambda: yf.Ticker(symbol).info or {})
            except RateLimitExhausted:
                print('\n[中断] レート制限が続いています。時間を置いて再実行してください。'
                      '済んだ分はスキップされます。')
                break
            except Exception as e:
                failed += 1
                print(f'[{i}/{len(targets)}] {code} エラー: {str(e)[:50]}', flush=True)
                guard.pause()
                continue

            listing = extract_listing_date(info)
            if not listing:
                skipped += 1
                status = '上場日なし'
            else:
                update_screened_data(code, {'listing_date': listing})
                ok += 1
                status = listing

            remain = ((time.time() - started) / i) * (len(targets) - i)
            print(f'[{i}/{len(targets)}] {code} {status} | '
                  f'成功{ok} なし{skipped} 失敗{failed} | 残り約{fmt_duration(remain)}',
                  flush=True)
            guard.pause()
    except KeyboardInterrupt:
        print('\n[中断] Ctrl+C を検知しました。')

    print('\n' + '=' * 60)
    print(f'完了: 成功 {ok}件 / 上場日なし {skipped}件 / 失敗 {failed}件 '
          f'/ 所要 {fmt_duration(time.time() - started)}')
    print(f'レート制限に当たった回数: {guard.rate_limit_hits}')
    print('再実行すれば、済んだ分はスキップして続きから処理します。')


if __name__ == '__main__':
    main()
