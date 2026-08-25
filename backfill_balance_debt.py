"""有利子負債と利益剰余金を cf_history に埋める穴埋めパス。

背景:
  銘柄ページの「財務健全性」に出ていたのは現預金と流動負債だけだった。
  流動負債は1年以内に払うもの全部（買掛金・未払金も入る）で、
  **利息の付く借金がいくらあるか**は分からない。
  利益剰余金にいたっては負債ですらなく、純資産の中身なので別の行にある。

取る項目（yfinance の貸借対照表から。追加のリクエストは1銘柄1回）:
  - interest_bearing_debt … 'Total Debt'（短期借入金＋長期借入金＋リース債務）
  - retained_earnings     … 'Retained Earnings'
  - total_assets          … 'Total Assets'（総資産・億円）
  - equity                … 'Stockholders Equity'（純資産・億円）

総資産と純資産は cf_history ではなく **screened_latest の列**に入れる。
理由は2つ:
  1. PBRの検算に使う。3939 でPBRが 48.65倍と 5.26倍で食い違ったとき、
     「時価総額 ÷ 純資産なら株数を経由しないので検出できるが、equity 列は
     全件で空」という理由で**どちらが正しいか機械的に決められなかった**。
  2. 自己資本比率のフォールバックが正しく効く。総資産が取れない銘柄で
     総負債を足して求める経路があり、そこに有利子負債が混ざっていた
     （2026-08-25 修正）。総資産を持てばフォールバックに落ちない。

  ⚠️ yfinance の 'Total Debt' は**総負債ではない**。総負債は
     'Total Liabilities Net Minority Interest'。混同すると自己資本比率が狂う。
  ⚠️ 'Total Debt' はリース債務を含むので、四季報の有利子負債とは数円単位で
     一致しないことがある。画面にも「リース含む」と書いてある。

  純有利子負債（有利子負債−現預金）はここでは保存しない。画面側で
  **同じ決算期の現預金**と引き算して出す。期のずれた値を引くと、
  増資や大型返済のあった年に符号が実態と逆になるため。

使い方:
    python backfill_balance_debt.py --limit 20        # まず20銘柄で試す
    python backfill_balance_debt.py --code 367A
    python backfill_balance_debt.py                   # 未取得を全部
    python backfill_balance_debt.py --dry-run         # 件数だけ見る
    python backfill_balance_debt.py --all             # 取得済みも取り直す

⚠️ 貸借対照表は銘柄ごとに1リクエスト＝レート制限に当たる側。
   backfill_yahoo_fields.py や accel_backfill.sh と**同時に流さない**こと。
"""

import os
import sys
import json
import argparse

os.environ['ENABLE_SCHEDULER'] = 'false'

from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import yfinance as yf

from supabase_client import get_supabase_client
from yfinance_guard import RateLimitExhausted, RateLimitGuard

PAGE_SIZE = 500
CONSECUTIVE_FAIL_ABORT = 15

# 取りたい行と、cf_history 側のキー
BALANCE_ROWS = (
    ('interest_bearing_debt', ('Total Debt',)),
    ('retained_earnings', ('Retained Earnings',)),
)
YEARS = 5

# 直近の1点だけを screened_latest の列に入れるもの。単位は**億円**
# （market_cap と同じ。時価総額÷純資産をそのまま計算できるようにする）。
SCALAR_ROWS = (
    ('total_assets', ('Total Assets',)),
    ('equity', ('Stockholders Equity', 'Total Stockholders Equity',
                'Total Equity Gross Minority Interest')),
)


def _as_obj(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value or {}


def _has_debt(cf_history):
    rows = cf_history.get('interest_bearing_debt')
    return bool(rows) and any(r.get('value') is not None for r in rows)


def _needs_scalars(row):
    return any(row.get(name) is None for name, _ in SCALAR_ROWS)


def load_targets(client, only_missing, code=None):
    """cf_history を持つ銘柄を取り切る（1000行上限にかからないようページング）"""
    select = ('company_code, cf_history, delisted_at, '
              + ', '.join(name for name, _ in SCALAR_ROWS))
    if code:
        return (client.table('screened_latest').select(select)
                .eq('company_code', code).execute().data)

    rows, offset = [], 0
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

    # 上場廃止済みは取りにいかない（Yahooに残っていても意味のある更新にならない）
    rows = [r for r in rows if not r.get('delisted_at')]
    if not only_missing:
        return rows
    return [r for r in rows
            if not _has_debt(_as_obj(r.get('cf_history'))) or _needs_scalars(r)]


def extract_scalars(balance_sheet):
    """直近の決算期の総資産・純資産（億円）。取れなければ入れない。"""
    out = {}
    if balance_sheet is None or balance_sheet.empty:
        return out
    col = balance_sheet.columns[0]
    for key, candidates in SCALAR_ROWS:
        for name in candidates:
            if name not in balance_sheet.index:
                continue
            value = balance_sheet.loc[name, col]
            if pd.notna(value):
                out[key] = round(float(value) / 1e8, 2)
            break
    return out


def extract_series(balance_sheet):
    """貸借対照表から {キー: [{date, value}, ...]} を作る。新しい年度が先頭。"""
    out = {key: [] for key, _ in BALANCE_ROWS}
    if balance_sheet is None or balance_sheet.empty:
        return out
    for col in list(balance_sheet.columns)[:YEARS]:
        date_str = col.strftime('%Y-%m-%d')
        for key, candidates in BALANCE_ROWS:
            for name in candidates:
                if name not in balance_sheet.index:
                    continue
                value = balance_sheet.loc[name, col]
                if pd.notna(value):
                    out[key].append({'date': date_str, 'value': float(value)})
                break
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='先頭N銘柄だけ処理する')
    parser.add_argument('--sleep', type=float, default=1.5,
                        help='1銘柄ごとの待ち時間（秒）')
    parser.add_argument('--code', help='この銘柄だけ処理する')
    parser.add_argument('--all', action='store_true', help='取得済みも取り直す')
    parser.add_argument('--dry-run', action='store_true', help='書き込まない')
    args = parser.parse_args()

    client = get_supabase_client()
    if client is None:
        print('Supabaseに接続できません。')
        return 1

    targets = load_targets(client, only_missing=not args.all, code=args.code)
    if args.limit:
        targets = targets[:args.limit]

    print(f'対象: {len(targets)}銘柄')
    if args.dry_run:
        return 0

    updated = debt_filled = retained_filled = empty = failed = 0
    assets_filled = equity_filled = 0
    consecutive_fail = 0

    def _notify_wait(seconds, attempt):
        print(f'  レート制限を検知。{seconds / 60:.1f}分待って再試行します'
              f'（{attempt}回目）')

    guard = RateLimitGuard(base_sleep=args.sleep, on_wait=_notify_wait)

    for i, row in enumerate(targets, 1):
        code = row['company_code']
        cf_history = _as_obj(row.get('cf_history'))
        try:
            balance_sheet = guard.run(lambda: yf.Ticker(f'{code}.T').balance_sheet)
            series = extract_series(balance_sheet)
            scalars = extract_scalars(balance_sheet)

            got = {k: v for k, v in series.items() if v}
            if not got and not scalars:
                # Yahooに貸借対照表が無い銘柄。失敗ではないので連続失敗に数えない
                empty += 1
                consecutive_fail = 0
                guard.pause()
                continue

            cf_history.update(got)
            if 'interest_bearing_debt' in got:
                debt_filled += 1
            if 'retained_earnings' in got:
                retained_filled += 1
            if 'total_assets' in scalars:
                assets_filled += 1
            if 'equity' in scalars:
                equity_filled += 1

            # 触る列だけを更新する。他の列は同時に走る別のバッチが
            # 書いていることがあるので、行ごと上書きしない。
            payload = dict(scalars)
            if got:
                payload['cf_history'] = json.dumps(cf_history, ensure_ascii=False)
            (client.table('screened_latest').update(payload)
             .eq('company_code', code).execute())
            updated += 1
            consecutive_fail = 0
        except RateLimitExhausted as e:
            print(f'\n{e}。ここで中断します。'
                  '再実行すれば未処理の銘柄から続きを拾います。')
            break
        except Exception as e:
            failed += 1
            consecutive_fail += 1
            print(f'  {code} 失敗: {e}')
            if consecutive_fail >= CONSECUTIVE_FAIL_ABORT:
                print(f'\n連続{CONSECUTIVE_FAIL_ABORT}件失敗したため中断します。'
                      'レート制限以外の原因を確認してください。')
                break

        if i % 50 == 0:
            print(f'  {i}/{len(targets)} 更新{updated} 有利子負債{debt_filled} '
                  f'利益剰余金{retained_filled} BS無し{empty} 失敗{failed}'
                  f' | {guard.summary()}')
        guard.pause()

    print(f'\n更新 {updated}件 / 有利子負債 {debt_filled}件 '
          f'/ 利益剰余金 {retained_filled}件 / 総資産 {assets_filled}件 '
          f'/ 純資産 {equity_filled}件 / BS無し {empty}件 / 失敗 {failed}件')
    print(guard.summary())
    print('途中で止まっても、再実行すれば未取得の銘柄から続きを処理します。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
