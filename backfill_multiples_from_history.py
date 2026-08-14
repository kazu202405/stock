"""空欄のままのPER・PBRを、保存済みのEPS・BPSから埋める。

2026-08-14。3939 カナミックネットワークのPBRが空欄だった。

経緯:
  Yahoo の `bookValue` は 10.319 だが、貸借対照表から作ったBPSは 97.97。
  Yahoo の値だとPBRが48.65倍になる（正しくは5倍台）。2026-08-12 に
  「外から来た値をそのまま信じない」修正を入れ、桁のおかしい外部値は
  捨てるようにした。**3939の分析は7/29＝その修正より前**なので、
  弾かれた結果の空欄だけが残っていた。

  現行コードで分析し直すと PBR 5.48倍（537 ÷ 97.97）が出る。
  つまり**コードは直っていて、保存済みデータが古いだけ**。

このスクリプトは `financial_history` の EPS・BPS（すでにDBにある）から
株価で割って埋めるだけなので、**Yahooには一切アクセスしない**。

  PER = 株価 ÷ 最新決算期のEPS
  PBR = 株価 ÷ 最新決算期のBPS

すでに値が入っている銘柄は触らない。空欄だけを埋める
（分析側は Yahoo の要約値があればそちらを優先する。その判断を
  ここで上書きしない）。

⚠️ EPS・BPSの系列そのものが同じ倍率で狂っている銘柄は検出できない
（1773の例。ROEは EPS÷BPS なので倍率が揃って狂うと変わらず、検算に
使えない）。ここで入れるのは分析側が同じ状況で入れるのと同じ値なので、
アプリ内の判断としては一貫している。

使い方:
    python backfill_multiples_from_history.py            # 対象を出すだけ
    python backfill_multiples_from_history.py --apply    # 書き込む
"""

import argparse
import json
import os

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

from stock_analyzer import StockAnalyzer
from supabase_client import get_supabase_client

PAGE_SIZE = 500

# 上限は分析側と同じ値を使う。ここだけ別の基準にすると、
# 再分析したときに値が消えたり戻ったりする。
MAX_PER = StockAnalyzer.MAX_PER
MAX_PBR = StockAnalyzer.MAX_PBR


def parse_history(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, dict) else None


def latest_value(history, key):
    """最新決算期の値。日付の無い行は使わない。"""
    series = (history or {}).get(key)
    if not isinstance(series, list) or not series:
        return None
    rows = [x for x in series if x.get('value') is not None and x.get('date')]
    if not rows:
        return None
    return max(rows, key=lambda x: str(x['date']))


def load_rows(client):
    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, company_name, stock_price, pbr, per_forward, '
                        'financial_history')
                .range(offset, offset + PAGE_SIZE - 1)
                .execute())
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='実際に書き込む（付けなければ対象を出すだけ）')
    args = parser.parse_args()

    client = get_supabase_client()
    rows = load_rows(client)
    print(f'読み込み: {len(rows)}件')

    updates, skipped = [], 0
    for row in rows:
        price = row.get('stock_price')
        if not price or price <= 0:
            continue
        history = parse_history(row.get('financial_history'))
        patch = {}

        if row.get('per_forward') is None:
            eps = latest_value(history, 'eps')
            # 赤字（EPSがマイナス）ならPERは存在しない。作らない
            if eps and eps['value'] > 0:
                value = price / eps['value']
                if value <= MAX_PER:
                    patch['per_forward'] = round(value, 4)

        if row.get('pbr') is None:
            bps = latest_value(history, 'bps')
            if bps and bps['value'] > 0:
                value = price / bps['value']
                if value <= MAX_PBR:
                    patch['pbr'] = round(value, 4)

        if patch:
            updates.append((row['company_code'], row.get('company_name'), patch))
        elif row.get('pbr') is None or row.get('per_forward') is None:
            skipped += 1

    per_count = sum(1 for _, _, p in updates if 'per_forward' in p)
    pbr_count = sum(1 for _, _, p in updates if 'pbr' in p)
    print(f'埋められる銘柄: {len(updates)}件（PER {per_count}件 / PBR {pbr_count}件）')
    print(f'空欄のままにする（元データが無い・赤字・桁外れ）: {skipped}件')

    for code, name, patch in updates[:10]:
        print(f'  {code} {name}: {patch}')
    if len(updates) > 10:
        print(f'  ... 他 {len(updates) - 10}件')

    if not args.apply:
        print('\n--apply を付けると書き込みます（いまは何も変えていません）')
        return

    written, failed = 0, 0
    for code, _, patch in updates:
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
