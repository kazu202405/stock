"""予想配当（年換算）と予想配当利回りを全銘柄に入れる。

2026-08-14。画面に出していた配当利回りは実績（直近12か月に実際に
支払われた配当÷株価）だけだった。実績は決算期をまたぐため、期末配当と
翌期の中間配当が重なった年は実態より高く出る
（367A: 実績165円で6.18% ／ 予想120円で4.24%）。

**yahooquery のバッチ取得を使う。** `Ticker([...]).summary_detail` は
複数銘柄を1リクエストで返すため、3,879銘柄でも数十回で済む。
yfinance の `ticker.info` は銘柄ごとに1回で、レート制限に当たるのは
こちら（CLAUDE.md「既知の制約」）。同じ情報がバッチで取れるなら
バッチを使う。

**利回りは Yahoo の利回り値を使わず自分で計算する。**
yfinance は % （4.24）、yahooquery は小数（0.0424）で返す。この単位の
推測が 2026-08-12 の「利回り47%」事故の原因だった。配当額（円）には
曖昧さが無いので、額÷株価で出せば推測が要らない。

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

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

import yahooquery as yq

from stock_analyzer import forward_dividend_yield
from supabase_client import get_supabase_client

PAGE_SIZE = 500

# 1リクエストで問い合わせる銘柄数。多くするほど回数は減るが、
# 1回失敗したときに巻き添えになる銘柄も増える。
BATCH_SIZE = 100

# バッチ間の待ち。連続で叩かないための最低限の間隔。
SLEEP_BETWEEN_BATCHES = 1.0


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
                .select('company_code, company_name, stock_price, dps, financial_history')
                .range(offset, offset + PAGE_SIZE - 1)
                .execute())
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def fetch_forward_rates(symbols):
    """銘柄ごとの forward dividend rate（円）をまとめて取る。

    取れなかった銘柄は結果に入れない（Noneで上書きしないため）。
    """
    out = {}
    try:
        detail = yq.Ticker(symbols, asynchronous=False).summary_detail
    except Exception as e:
        print(f'  バッチ取得に失敗（{len(symbols)}銘柄・スキップ）: {e}')
        return out

    if not isinstance(detail, dict):
        return out

    for symbol, data in detail.items():
        # 取得できない銘柄は文字列でエラーが返る
        if isinstance(data, dict):
            out[symbol] = data.get('dividendRate')
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
    print(f'対象: {len(rows)}銘柄')

    by_symbol = {}
    for row in rows:
        by_symbol[to_symbol(row['company_code'])] = row

    symbols = list(by_symbol.keys())
    rates = {}
    batches = (len(symbols) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(symbols), BATCH_SIZE):
        chunk = symbols[i:i + BATCH_SIZE]
        rates.update(fetch_forward_rates(chunk))
        done = i // BATCH_SIZE + 1
        print(f'  取得 {done}/{batches} バッチ（{len(rates)}銘柄）')
        if done < batches:
            time.sleep(SLEEP_BETWEEN_BATCHES)

    updates, rejected, no_data = [], [], 0
    for symbol, row in by_symbol.items():
        rate = rates.get(symbol)
        if rate is None:
            no_data += 1
            continue

        confirmed = confirmed_annual_dps(row)
        value = forward_dividend_yield(rate, row.get('stock_price'), confirmed)
        if value is None:
            rejected.append((row['company_code'], row.get('company_name'),
                             rate, row.get('stock_price'), confirmed))
            continue
        updates.append((row['company_code'], {
            'dps_forecast': rate,
            'dividend_yield_forward': value,
        }))

    print(f'\n採用: {len(updates)}件 / 検証で不採用: {len(rejected)}件 / '
          f'予想配当なし（無配等）: {no_data}件')

    for code, name, rate, price, confirmed in rejected[:15]:
        print(f'  不採用 {code} {name}: 予想{rate}円 株価{price} 確定年度{confirmed}円')
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

    print(f'\n更新: {written}件 / 失敗: {failed}件')


if __name__ == '__main__':
    main()
