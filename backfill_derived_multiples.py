"""PER・PBRが空の銘柄を、DB内のデータだけで計算して埋める。

外部サイトへは一切アクセスしない。したがってレート制限とは無関係に、
いつ実行しても安全。

    PER = 株価 ÷ 最新決算期のEPS
    PBR = 株価 ÷ 最新決算期のBPS

背景:
    PER/PBRは `ticker.info` からしか取れていない（FastInfoにこの2つは無い）。
    infoは重くレート制限にも当たりやすいが、EPS・BPSは財務諸表から作っており、
    株価も株価バッチで別に取れている。定義どおり割れば取りに行く必要がない。

    赤字の銘柄はPERが存在しないため埋めない（推測しない）。

使い方:
    python backfill_derived_multiples.py            # 空いている銘柄を埋める
    python backfill_derived_multiples.py --dry-run  # 書き込まずに件数だけ
"""

import argparse
import json
import os
import sys

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

from supabase_client import get_supabase_client

PAGE_SIZE = 500

# 分母がほぼゼロの銘柄で桁外れの倍率が出る。指標として使えないので採らない。
LIMITS = {'per_forward': 300.0, 'pbr': 50.0}
DENOMINATOR = {'per_forward': 'eps', 'pbr': 'bps'}


def _as_obj(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value or {}


def _latest(history, key):
    rows = [r for r in (history.get(key) or [])
            if isinstance(r, dict) and r.get('value') is not None]
    return max(rows, key=lambda r: r['date']) if rows else None


def load_rows(client, column):
    """その指標が空の銘柄を取り切る（1000行上限にかからないようページング）"""
    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, company_name, financial_history, stock_price')
                .is_(column, 'null')
                .order('company_code')
                .range(offset, offset + PAGE_SIZE - 1).execute().data)
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='書き込まない')
    args = parser.parse_args()

    client = get_supabase_client()
    if client is None:
        print('Supabaseに接続できません。')
        return 1

    total_updated = 0
    for column, series in DENOMINATOR.items():
        label = 'PER' if column == 'per_forward' else 'PBR'
        rows = load_rows(client, column)

        updated = no_denominator = not_positive = no_price = out_of_range = 0
        for row in rows:
            history = _as_obj(row.get('financial_history'))
            price = row.get('stock_price')
            denominator = _latest(history, series)

            if not price or price <= 0:
                no_price += 1
                continue
            if not denominator:
                no_denominator += 1
                continue
            if denominator['value'] <= 0:
                # 赤字ならPERは存在しない。純資産がマイナスならPBRも同様。
                not_positive += 1
                continue

            value = round(price / denominator['value'], 4)
            if value > LIMITS[column]:
                out_of_range += 1
                continue

            if not args.dry_run:
                client.table('screened_latest').update({column: value}).eq(
                    'company_code', row['company_code']).execute()
            updated += 1

        total_updated += updated
        print(f'{label}が空: {len(rows)}件')
        print(f'  {"計算できる" if args.dry_run else "計算して更新"}: {updated}')
        print(f'  分母({series})が無い          : {no_denominator}')
        print(f'  分母がマイナス/ゼロ（存在しない）: {not_positive}')
        print(f'  株価が無い                    : {no_price}')
        print(f'  桁外れのため採らない            : {out_of_range}')
        print()

    print(f'合計 {total_updated}件{"（試算）" if args.dry_run else "を更新しました"}')
    print('このスクリプトは外部サイトへアクセスしません。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
