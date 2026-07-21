"""
全銘柄バックフィル: companies.json の全銘柄を軽量パスで分析し screened_latest に保存する。

初回に一度だけ流して「最初からデータが揃っている」状態を作るためのスクリプト。
以降の更新は決算発表イベント起点＋ローリング更新で回す想定。

特徴:
  - 途中で止めても再実行すれば続きから（DBの analyzed_at を見てスキップ）
  - 連続失敗を検知して自動でバックオフ（yfinanceのレート制限対策）
  - 失敗した銘柄はログに残す（無音で欠損させない）
  - Ctrl+C で安全に中断

使い方:
    python backfill_all_stocks.py                  # 全銘柄（7日以内に分析済みはスキップ）
    python backfill_all_stocks.py --limit 20       # まず20銘柄で試す
    python backfill_all_stocks.py --sleep 0.5      # もっと安全側のレートで
    python backfill_all_stocks.py --skip-days 0    # 分析済みも含めて全部やり直す
    python backfill_all_stocks.py --dry-run        # 対象件数だけ確認して終了
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone, timedelta

# app.py を import するとAPSchedulerが起動してcronが動き出すため、先に無効化する
os.environ['ENABLE_SCHEDULER'] = 'false'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANIES_JSON = os.path.join(BASE_DIR, 'static', 'companies.json')
FAILED_LOG = os.path.join(BASE_DIR, 'backfill_failed.json')

# 連続でこの回数失敗したら一時停止して様子を見る（レート制限に当たった可能性）
CONSECUTIVE_FAIL_PAUSE = 10
PAUSE_SECONDS = 90
# 連続でこの回数失敗したら中断する（ブロックされたと判断）
CONSECUTIVE_FAIL_ABORT = 40


# ETF・REIT・投信の判定用キーワード（銘柄名に含まれるもの）
# これらは事業会社ではないため財務データが存在せず、分析しても中身が空になる
ETF_KEYWORDS = (
    'ETF', 'ＥＴＦ', 'ETN', 'ＥＴＮ', '上場投信', '投資信託', 'インデックス',
    '連動型', 'リート', 'REIT', 'ＲＥＩＴ', '投資法人', 'ブル', 'ベア',
    'ダブル・インバース', 'レバレッジ', 'インバース',
)


def load_companies(skip_etf=False):
    with open(COMPANIES_JSON, encoding='utf-8') as f:
        data = json.load(f)
    rows = [c for c in data if c.get('c')]
    if skip_etf:
        rows = [c for c in rows
                if not any(k in (c.get('n') or '') for k in ETF_KEYWORDS)]
    return [c['c'] for c in rows]


def load_already_analyzed(skip_days):
    """skip_days以内に分析済みの銘柄コードの集合を返す"""
    if skip_days <= 0:
        return set()
    from supabase_client import get_supabase_client
    client = get_supabase_client()
    cutoff = datetime.now(timezone.utc) - timedelta(days=skip_days)
    done = set()
    # Supabaseは1回1000行までなのでページングして全件取る
    page = 0
    while True:
        res = (client.table('screened_latest')
               .select('company_code, analyzed_at')
               .range(page * 1000, page * 1000 + 999)
               .execute())
        rows = res.data or []
        if not rows:
            break
        for r in rows:
            ts = r.get('analyzed_at')
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
    parser = argparse.ArgumentParser(description='全銘柄バックフィル')
    parser.add_argument('--limit', type=int, default=0, help='先頭N銘柄だけ処理（0=全件）')
    parser.add_argument('--sleep', type=float, default=0.35, help='銘柄間の待機秒数')
    parser.add_argument('--skip-days', type=int, default=7, help='N日以内に分析済みならスキップ（0=スキップしない）')
    parser.add_argument('--dry-run', action='store_true', help='対象件数を表示して終了')
    parser.add_argument('--skip-etf', action='store_true',
                        help='ETF/REIT/投信を除外する（事業会社ではないため財務データが空になる）')
    args = parser.parse_args()

    print('=' * 60)
    print('全銘柄バックフィル')
    print('=' * 60)

    codes = load_companies(skip_etf=args.skip_etf)
    label = '銘柄マスタ（ETF等を除外）' if args.skip_etf else '銘柄マスタ'
    print(f'{label}: {len(codes)}件')

    print(f'分析済みを確認中（{args.skip_days}日以内）...')
    try:
        done = load_already_analyzed(args.skip_days)
        print(f'  スキップ対象: {len(done)}件')
    except Exception as e:
        print(f'  [警告] 分析済みの取得に失敗したため全件を対象にします: {e}')
        done = set()

    targets = [c for c in codes if c not in done]
    if args.limit:
        targets = targets[:args.limit]

    est_low = len(targets) * (4.4 + args.sleep - 0.35)
    est_high = len(targets) * (8.4 + args.sleep - 0.35)
    print(f'処理対象: {len(targets)}件')
    print(f'推定所要時間: {fmt_duration(est_low)} 〜 {fmt_duration(est_high)}')
    print(f'待機: {args.sleep}秒/銘柄')

    if args.dry_run:
        print('\n--dry-run のため実行せず終了します')
        return

    if not targets:
        print('\n対象がありません。完了しています。')
        return

    from stock_analyzer import StockAnalyzer
    from app import _analyze_stock_and_save

    analyzer = StockAnalyzer()
    started = time.time()
    ok = fail = 0
    consecutive_fail = 0
    failed_codes = []

    print('\n開始します（Ctrl+C で安全に中断できます）\n')

    try:
        for i, code in enumerate(targets, 1):
            t0 = time.time()
            try:
                result = _analyze_stock_and_save(analyzer, code)
                if result:
                    ok += 1
                    consecutive_fail = 0
                    status = 'OK'
                else:
                    fail += 1
                    consecutive_fail += 1
                    failed_codes.append({'code': code, 'reason': 'no_data'})
                    status = 'データなし'
            except Exception as e:
                fail += 1
                consecutive_fail += 1
                failed_codes.append({'code': code, 'reason': str(e)[:200]})
                status = f'エラー: {str(e)[:60]}'

            elapsed = time.time() - started
            avg = elapsed / i
            remain = avg * (len(targets) - i)
            print(f'[{i}/{len(targets)}] {code} {status} '
                  f'({time.time() - t0:.1f}秒) | 成功{ok} 失敗{fail} | 残り約{fmt_duration(remain)}',
                  flush=True)

            # 連続失敗が続く場合はレート制限を疑ってバックオフする
            if consecutive_fail and consecutive_fail % CONSECUTIVE_FAIL_PAUSE == 0:
                if consecutive_fail >= CONSECUTIVE_FAIL_ABORT:
                    print(f'\n[中断] {consecutive_fail}件連続で失敗しました。'
                          f'レート制限またはブロックの可能性が高いため停止します。')
                    break
                print(f'\n[警告] {consecutive_fail}件連続失敗。{PAUSE_SECONDS}秒待機します...\n', flush=True)
                time.sleep(PAUSE_SECONDS)

            time.sleep(args.sleep)

    except KeyboardInterrupt:
        print('\n\n[中断] Ctrl+C を検知しました。ここまでの結果を保存します。')

    total = time.time() - started
    print('\n' + '=' * 60)
    print(f'完了: 成功 {ok}件 / 失敗 {fail}件 / 所要 {fmt_duration(total)}')
    print('=' * 60)

    if failed_codes:
        with open(FAILED_LOG, 'w', encoding='utf-8') as f:
            json.dump(failed_codes, f, ensure_ascii=False, indent=1)
        print(f'失敗した銘柄を書き出しました: {FAILED_LOG}')

    print('\n再実行すれば、成功済みをスキップして続きから処理します。')


if __name__ == '__main__':
    main()
