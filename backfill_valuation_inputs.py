"""PER・PBRの推移に必要な EPS / BPS を既存銘柄に埋める。

背景:
    PER/PBRの履歴は「株価履歴 × 決算期ごとのEPS・BPS」で自前計算する。
    株価履歴は全銘柄に入っているが、
      - EPS は最新決算期が欠けている銘柄が約35%ある（Yahooが返さないため）
      - BPS はそもそも保存していなかった
    ので、この2つだけを埋める。

    フル再分析（_analyze_stock_and_save）は1銘柄あたり10前後の呼び出しが要るが、
    ここは損益計算書と貸借対照表の2つしか触らないため軽い。
    概要・株主・役員・業績予想・信用倍率には一切触れない。

使い方:
    python backfill_valuation_inputs.py                # BPSが無い銘柄すべて
    python backfill_valuation_inputs.py --eps-only     # 一株益が --- の銘柄だけ
    python backfill_valuation_inputs.py --limit 20     # まず20銘柄で試す
    python backfill_valuation_inputs.py --sleep 1.0    # もっと安全側のレートで
    python backfill_valuation_inputs.py --code 5261    # 1銘柄だけ
    python backfill_valuation_inputs.py --all          # BPS済みも作り直す
    python backfill_valuation_inputs.py --dry-run      # 書き込まずに件数だけ
"""

import argparse
import json
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
# 連続でこの回数失敗したらレート制限を疑って止まる
CONSECUTIVE_FAIL_ABORT = 15


def _as_obj(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value or {}


def _has_eps_gap(history):
    """最新の決算期に純利益はあるのにEPSが無い＝画面で一株益が --- になる状態"""
    net_income = {d['date'] for d in (history.get('net_income') or [])
                  if d.get('value') is not None}
    eps = {d['date'] for d in (history.get('eps') or [])
           if d.get('value') is not None}
    return bool(net_income) and max(net_income) not in eps


def load_targets(client, only_missing, code=None, eps_only=False):
    """financial_history を持つ銘柄を取り切る（1000行上限にかからないようページング）"""
    if code:
        rows = (client.table('screened_latest')
                .select('company_code, financial_history')
                .eq('company_code', code).execute().data)
        return rows

    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, financial_history')
                .order('company_code')
                .range(offset, offset + PAGE_SIZE - 1).execute().data)
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    if eps_only:
        # 一株益が --- になっている銘柄だけ。BPSはついでに入るが対象選定には使わない。
        return [r for r in rows if _has_eps_gap(_as_obj(r.get('financial_history')))]

    if not only_missing:
        return rows

    targets = []
    for row in rows:
        history = _as_obj(row.get('financial_history'))
        if not history.get('bps'):
            targets.append(row)
    return targets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='先頭N銘柄だけ処理する')
    parser.add_argument('--sleep', type=float, default=0.6,
                        help='1銘柄ごとの待ち時間（秒）')
    parser.add_argument('--code', help='この銘柄だけ処理する')
    parser.add_argument('--all', action='store_true', help='BPS済みも作り直す')
    parser.add_argument('--eps-only', action='store_true',
                        help='一株益が --- になっている銘柄だけに絞る')
    parser.add_argument('--dry-run', action='store_true', help='書き込まない')
    args = parser.parse_args()

    client = get_supabase_client()
    if client is None:
        print('Supabaseに接続できません。')
        return 1

    targets = load_targets(client, only_missing=not args.all,
                           code=args.code, eps_only=args.eps_only)
    if args.limit:
        targets = targets[:args.limit]

    print(f'対象: {len(targets)}銘柄')
    if args.dry_run:
        return 0

    analyzer = StockAnalyzer()
    updated = eps_filled = bps_filled = failed = 0
    consecutive_fail = 0

    for i, row in enumerate(targets, 1):
        code = row['company_code']
        history = _as_obj(row.get('financial_history'))
        try:
            ticker = yf.Ticker(f'{code}.T')
            result = {
                'eps': [dict(d) for d in (history.get('eps') or [])],
                'bps': [],
                'net_income': [dict(d) for d in (history.get('net_income') or [])],
                'source_status': {},
            }
            financials = ticker.financials

            before_eps = len(result['eps'])
            if not financials.empty:
                analyzer._fill_missing_eps(ticker, financials, result)
            analyzer._build_bps_series(ticker, result)

            update = {}
            if len(result['eps']) > before_eps:
                history['eps'] = result['eps']
                eps_filled += 1
            if result['bps']:
                history['bps'] = result['bps']
                bps_filled += 1
            if not history.get('eps') and not history.get('bps'):
                consecutive_fail = 0
                continue

            update['financial_history'] = json.dumps(history, ensure_ascii=False)
            client.table('screened_latest').update(update).eq(
                'company_code', code).execute()
            updated += 1
            consecutive_fail = 0
        except Exception as e:
            failed += 1
            consecutive_fail += 1
            print(f'  {code} 失敗: {e}')
            if consecutive_fail >= CONSECUTIVE_FAIL_ABORT:
                print(f'\n連続{CONSECUTIVE_FAIL_ABORT}件失敗したため中断します。'
                      'レート制限の可能性があります。時間を置いて再実行してください。')
                break

        if i % 50 == 0:
            print(f'  {i}/{len(targets)} 更新{updated} EPS補完{eps_filled} '
                  f'BPS作成{bps_filled} 失敗{failed}')
        time.sleep(args.sleep)

    print(f'\n更新 {updated}件 / EPS補完 {eps_filled}件 / BPS作成 {bps_filled}件 '
          f'/ 失敗 {failed}件')
    print('途中で止まっても、再実行すればBPSが無い銘柄から続きを処理します。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
