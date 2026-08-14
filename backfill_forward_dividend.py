"""予想配当（年換算）と予想配当利回りを全銘柄に入れる。

2026-08-14。画面に出していた配当利回りは実績（直近12か月に実際に
支払われた配当÷株価）だけだった。実績は決算期をまたぐため、期末配当と
翌期の中間配当が重なった年は実態より高く出る
（367A: 実績165円で6.18% ／ 予想120円で4.24%）。

**予想配当は支払い実績から自分で年換算する。Yahoo の要約値は使わない。**
`info` / `summary_detail` の `dividendRate` は株式併合に追随しないことがある。

    5706 三井金属  dividendRate=28   実際の支払い 90 / 100 / 145円
    8377 ほくほく  dividendRate=15   実際の支払い 22.5 / 27.5 / 45 / 65円

いずれも `lastDividendValue`（14 / 7.5）を2倍しただけで、併合前の額のまま
止まっていた。比率で弾けば大きな取り違えは防げるが、1:2 の併合なら
比率0.5で検証を通り抜ける。調整済みの支払い実績だけで計算すれば、
この問題自体が起きない。2026-08-12 の「利回り47%」も同じ種類の事故だった。

**取得は yf.download のバッチを使う。** `actions=True` で配当が付いてくる。
銘柄ごとに1回の `ticker.dividends` はレート制限に当たる側なので使わない
（CLAUDE.md「既知の制約」）。3,879銘柄でも40回程度で済む。

検証は stock_analyzer.forward_dividend_yield() に集約している：
現実的な利回りの範囲に収まり、かつ確定した決算年度の配当と桁が
合っていること。通らなければ NULL。誤った数字より「不明」がよい。

前提: supabase/migration_forward_dividend.sql を適用済みであること。

使い方:
    python backfill_forward_dividend.py                 # 取得して結果を見るだけ
    python backfill_forward_dividend.py --apply         # 書き込む
    python backfill_forward_dividend.py --limit 50      # 先頭50銘柄で試す
"""

import argparse
import json
import os
import time
import warnings

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings('ignore')

import yfinance as yf

from stock_analyzer import forecast_annual_dividend, forward_dividend_yield
from supabase_client import get_supabase_client

PAGE_SIZE = 500

# 1リクエストで問い合わせる銘柄数。price_history.fetch_ohlc_batch と同じ。
BATCH_SIZE = 100

SLEEP_BETWEEN_BATCHES = 1.0

# 支払い回数を数えるのに直近1年分あれば足りるが、期ズレを吸収するため2年取る。
HISTORY_PERIOD = '2y'


def to_symbol(code):
    """DB保存形式の銘柄コードを Yahoo のシンボルに直す。

    4桁で先頭が数字なら日本株（`367A` のような新形式も日本株）。
    """
    code = (code or '').strip()
    if len(code) == 4 and code[0].isdigit():
        return code + '.T'
    return code


def parse_history(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, dict) else None


def confirmed_annual_dps(row):
    """確定した決算年度の年間配当。予想値の検証に使う。

    保存済みの `dps` は 2026-08-14 の修正で「終わった年度」の値に
    そろえてあるので、そのまま使える。取れないときは履歴から引く。
    """
    if row.get('dps'):
        return row['dps']
    history = parse_history(row.get('financial_history'))
    series = (history or {}).get('dps')
    if not isinstance(series, list) or not series:
        return None
    today = time.strftime('%Y-%m-%d')
    done = [x for x in series if str(x.get('date', '')) <= today]
    if not done:
        return None
    return sorted(done, key=lambda x: x.get('date', ''), reverse=True)[0].get('value')


def load_rows(client):
    """全銘柄を取り切る。Supabaseは1リクエスト既定1000行までなのでページングする。"""
    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, company_name, stock_price, dps, fiscal_month, financial_history')
                .range(offset, offset + PAGE_SIZE - 1)
                .execute())
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def fetch_payments(symbols):
    """銘柄ごとの配当支払い実績をまとめて取る。{symbol: [(日付, 金額)]}。

    yf.download の値は分割・併合の調整済み。
    """
    out = {}
    try:
        df = yf.download(' '.join(symbols), period=HISTORY_PERIOD, actions=True,
                         progress=False, threads=True, auto_adjust=False,
                         group_by='ticker')
    except Exception as e:
        print(f'  バッチ取得に失敗（{len(symbols)}銘柄・スキップ）: {e}')
        return out

    if df is None or df.empty:
        return out

    for symbol in symbols:
        try:
            sub = df[symbol] if len(symbols) > 1 else df
            if 'Dividends' not in sub.columns:
                continue
            series = sub['Dividends']
            series = series[series > 0]
            out[symbol] = [(idx.strftime('%Y-%m-%d'), float(v))
                           for idx, v in series.items()]
        except Exception:
            # 上場廃止・データ無しの銘柄。結果に入れない（Noneで上書きしない）
            continue
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='実際に書き込む（付けなければ取得して結果を出すだけ）')
    parser.add_argument('--limit', type=int, default=0,
                        help='先頭N銘柄だけ処理する（動作確認用）')
    args = parser.parse_args()

    client = get_supabase_client()
    rows = load_rows(client)
    if args.limit:
        rows = rows[:args.limit]
    print(f'対象: {len(rows)}銘柄', flush=True)

    by_symbol = {to_symbol(r['company_code']): r for r in rows}
    symbols = list(by_symbol.keys())

    payments_by_symbol = {}
    batches = (len(symbols) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(symbols), BATCH_SIZE):
        chunk = symbols[i:i + BATCH_SIZE]
        payments_by_symbol.update(fetch_payments(chunk))
        done = i // BATCH_SIZE + 1
        print(f'  取得 {done}/{batches} バッチ', flush=True)
        if done < batches:
            time.sleep(SLEEP_BETWEEN_BATCHES)

    updates, rejected, no_dividend = [], [], 0
    for symbol, row in by_symbol.items():
        forecast = forecast_annual_dividend(payments_by_symbol.get(symbol),
                                            row.get('fiscal_month'))
        if forecast is None:
            no_dividend += 1
            continue

        confirmed = confirmed_annual_dps(row)
        value = forward_dividend_yield(forecast, row.get('stock_price'), confirmed)
        if value is None:
            rejected.append((row['company_code'], row.get('company_name'),
                             forecast, row.get('stock_price'), confirmed))
            continue
        updates.append((row['company_code'], {
            'dps_forecast': forecast,
            'dividend_yield_forward': value,
        }))

    print(f'\n採用: {len(updates)}件 / 検証で不採用: {len(rejected)}件 / '
          f'直近1年に配当なし: {no_dividend}件', flush=True)

    for code, name, forecast, price, confirmed in rejected[:15]:
        print(f'  不採用 {code} {name}: 年換算{forecast}円 株価{price} 確定年度{confirmed}円')
    if len(rejected) > 15:
        print(f'  ... 他 {len(rejected) - 15}件')

    if not args.apply:
        print('\n--apply を付けると書き込みます（いまは何も変えていません）')
        return

    written, failed = 0, 0
    for code, patch in updates:
        try:
            (client.table('screened_latest')
             .update(patch)
             .eq('company_code', code)
             .execute())
            written += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f'  失敗 {code}: {e}')

    print(f'\n更新: {written}件 / 失敗: {failed}件', flush=True)


if __name__ == '__main__':
    main()
