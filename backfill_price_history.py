"""
株価履歴（日足1年）の全銘柄バックフィル。

目的:
  チャートのデータは従来 output/snapshot_*.json（ローカルファイル）にあり、
  Renderのディスクが揮発するため本番でチャートが表示されなかった。
  日足1年をDBに事前投入して、全銘柄でチャートが即表示される状態を作る。

長期（週足/月足）はここでは取得しない。閲覧されたときにオンデマンドで取得・
キャッシュする設計のため（全銘柄×10年を持つと容量が重いため）。

⚠️ backfill_all_stocks.py と同時に実行しないこと。
   yfinanceを2ジョブで同時に叩くとレート制限に当たる。必ず片方ずつ流す。

使い方:
    python backfill_price_history.py --skip-etf            # 全銘柄
    python backfill_price_history.py --limit 20            # まず20銘柄で試す
    python backfill_price_history.py --dry-run             # 件数だけ確認
"""

import os
import time
import argparse
from datetime import datetime, timezone, timedelta

os.environ['ENABLE_SCHEDULER'] = 'false'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONSECUTIVE_FAIL_PAUSE = 10
PAUSE_SECONDS = 90
CONSECUTIVE_FAIL_ABORT = 40


def load_already_done(skip_days):
    """skip_days以内に日足を取得済みの銘柄コードの集合"""
    if skip_days <= 0:
        return set()
    from supabase_client import get_supabase_client
    client = get_supabase_client()
    cutoff = datetime.now(timezone.utc) - timedelta(days=skip_days)
    done = set()
    page = 0
    while True:
        res = (client.table('stock_price_history')
               .select('company_code, daily_updated_at')
               .range(page * 1000, page * 1000 + 999)
               .execute())
        rows = res.data or []
        if not rows:
            break
        for r in rows:
            ts = r.get('daily_updated_at')
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    done.add(r['company_code'])
            except Exception:
                continue
        if len(rows) < 1000:
            break
        page += 1
    return done


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}時間{m}分' if h else (f'{m}分{s}秒' if m else f'{s}秒')


def main():
    parser = argparse.ArgumentParser(description='株価履歴（日足1年）バックフィル')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--sleep', type=float, default=0.35)
    parser.add_argument('--skip-days', type=int, default=3,
                        help='N日以内に取得済みならスキップ')
    parser.add_argument('--skip-etf', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    print('=' * 60)
    print('株価履歴（日足1年）バックフィル')
    print('=' * 60)

    from backfill_all_stocks import load_companies
    import price_history as ph

    codes = load_companies(skip_etf=args.skip_etf)
    print(f'銘柄マスタ: {len(codes)}件')

    print(f'取得済みを確認中（{args.skip_days}日以内）...')
    try:
        done = load_already_done(args.skip_days)
        print(f'  スキップ対象: {len(done)}件')
    except Exception as e:
        print(f'  [警告] 取得済みの確認に失敗したため全件を対象にします: {e}')
        print(f'  ※ migration_price_history.sql を適用済みか確認してください')
        done = set()

    targets = [c for c in codes if c not in done]
    if args.limit:
        targets = targets[:args.limit]

    print(f'処理対象: {len(targets)}件')
    print(f'推定所要時間: {fmt_duration(len(targets) * (1.2 + args.sleep))}')

    if args.dry_run:
        print('\n--dry-run のため実行せず終了します')
        return
    if not targets:
        print('\n対象がありません。完了しています。')
        return

    started = time.time()
    ok = fail = 0
    consecutive_fail = 0

    print('\n開始します（Ctrl+C で安全に中断できます）\n')

    try:
        for i, code in enumerate(targets, 1):
            t0 = time.time()
            try:
                rows = ph.fetch_ohlc(ph.to_symbol(code), period='1y')
                if rows:
                    ph.save_daily(code, rows)
                    ok += 1
                    consecutive_fail = 0
                    status = f'OK ({len(rows)}本)'
                else:
                    fail += 1
                    consecutive_fail += 1
                    status = 'データなし'
            except Exception as e:
                fail += 1
                consecutive_fail += 1
                status = f'エラー: {str(e)[:60]}'

            elapsed = time.time() - started
            remain = (elapsed / i) * (len(targets) - i)
            print(f'[{i}/{len(targets)}] {code} {status} '
                  f'({time.time() - t0:.1f}秒) | 成功{ok} 失敗{fail} | 残り約{fmt_duration(remain)}',
                  flush=True)

            if consecutive_fail and consecutive_fail % CONSECUTIVE_FAIL_PAUSE == 0:
                if consecutive_fail >= CONSECUTIVE_FAIL_ABORT:
                    print(f'\n[中断] {consecutive_fail}件連続失敗。レート制限の可能性が高いため停止します。')
                    break
                print(f'\n[警告] {consecutive_fail}件連続失敗。{PAUSE_SECONDS}秒待機します...\n', flush=True)
                time.sleep(PAUSE_SECONDS)

            time.sleep(args.sleep)

    except KeyboardInterrupt:
        print('\n\n[中断] Ctrl+C を検知しました。')

    print('\n' + '=' * 60)
    print(f'完了: 成功 {ok}件 / 失敗 {fail}件 / 所要 {fmt_duration(time.time() - started)}')
    print('=' * 60)
    print('\n再実行すれば、取得済みをスキップして続きから処理します。')


if __name__ == '__main__':
    main()
