"""PER・PBRを、DB内のデータだけで計算し直す。

外部サイトへは一切アクセスしない。したがってレート制限とは無関係に、
いつ実行しても安全。

    PER = 株価 ÷ 最新決算期のEPS
    PBR = 株価 ÷ 最新決算期のBPS

背景:
    PER/PBRは `ticker.info` からしか取れていない（FastInfoにこの2つは無い）。
    infoは重くレート制限にも当たりやすいが、EPS・BPSは財務諸表から作っており、
    株価も株価バッチで別に取れている。定義どおり割れば取りに行く必要がない。

    赤字の銘柄はPERが存在しないため埋めない（推測しない）。

2026-08-12 に対象を「空の銘柄」から**全銘柄**へ広げ、突き合わせを足した:
    きっかけは 3939 で、Yahooの bookValue が 10.319（貸借対照表からは 97.97）
    のため PBR 48.65倍 と表示されていた（正しくは 5.26倍）。
    空欄だけを埋める作りでは、埋まっている誤りに手が届かない。

    ただし調査の結果、**割り算側も壊れることが分かった**。1773 は EPS・BPS の
    系列が同じ倍率で小さく、割り算だとPBR 48.7倍になるが、Yahooの 1.20倍 の
    方がROEと整合する。EPSとBPSが同じ倍率で狂うとROEは変わらないため、
    ROEによる検算ではスケール誤りを検出できない。時価総額÷純資産という
    株数を経由しない基準も、`equity` 列が全銘柄で空のため使えない。

    したがって、どちらが正しいかを機械的に決めない。
    **2つが1.5倍以上食い違ったら値を消して「判定不能」にする。**
    スコアは判定できた項目数を分母にするので、判定不能は減点にならない。

使い方:
    python backfill_derived_multiples.py --dry-run   # 書き込まずに差分だけ見る
    python backfill_derived_multiples.py            # 全銘柄を突き合わせる
    python backfill_derived_multiples.py --only-missing  # 空欄を埋めるだけ（旧挙動）

実行後は `python recalc_match_rates.py` でスコアを再計算すること
（PER/PBRは12項目の判定に入っているため）。
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

# 2つの計算がこれ以上食い違ったら「判定不能」にする（stock_analyzer と同じ値）
DISAGREEMENT = 1.5
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


def load_rows(client, column, only_missing=False):
    """対象銘柄を取り切る（1000行上限にかからないようページング）"""
    rows, offset = [], 0
    while True:
        query = (client.table('screened_latest')
                 .select('company_code, company_name, financial_history, '
                         'stock_price, ' + column))
        if only_missing:
            query = query.is_(column, 'null')
        page = (query.order('company_code')
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
    parser.add_argument('--only-missing', action='store_true',
                        help='空欄を埋めるだけ（2026-08-12以前の挙動）')
    args = parser.parse_args()

    client = get_supabase_client()
    if client is None:
        print('Supabaseに接続できません。')
        return 1

    total_updated = 0
    for column, series in DENOMINATOR.items():
        label = 'PER' if column == 'per_forward' else 'PBR'
        rows = load_rows(client, column, only_missing=args.only_missing)

        updated = filled = corrected = unchanged = 0
        no_denominator = not_positive = no_price = out_of_range = dropped = 0
        worst = []
        for row in rows:
            history = _as_obj(row.get('financial_history'))
            price = row.get('stock_price')
            denominator = _latest(history, series)
            current = row.get(column)

            if not price or price <= 0:
                no_price += 1
                continue
            if not denominator or denominator['value'] <= 0:
                # 割り算で検算できない銘柄。既存値が桁外れなら「不明」に落とす。
                # 誤った数字を出し続けるより、出さない方がよい。
                if not denominator:
                    no_denominator += 1
                else:
                    # 赤字ならPERは存在しない。純資産がマイナスならPBRも同様。
                    not_positive += 1
                if current is not None and (current <= 0 or current > LIMITS[column]):
                    if not args.dry_run:
                        client.table('screened_latest').update({column: None}).eq(
                            'company_code', row['company_code']).execute()
                    dropped += 1
                continue

            value = round(price / denominator['value'], 4)
            if value > LIMITS[column]:
                out_of_range += 1
                continue

            if current is None:
                new_value = value
                filled += 1
            else:
                gap = max(current / value, value / current)
                if gap < DISAGREEMENT:
                    # 一致。要約値の方がTTMで新しいので触らない
                    unchanged += 1
                    continue
                # 食い違った。どちらが正しいか決められないので値を持たせない。
                new_value = None
                corrected += 1
                worst.append((gap, row['company_code'], current, value))

            if not args.dry_run:
                client.table('screened_latest').update({column: new_value}).eq(
                    'company_code', row['company_code']).execute()
            updated += 1

        total_updated += updated
        print(f'{label}: 対象 {len(rows)}件')
        print(f'  {"変わる" if args.dry_run else "更新した"}: {updated}'
              f'（空欄を埋める {filled} / 食い違いで判定不能にする {corrected}）')
        print(f'  2つの計算が一致（触らない）      : {unchanged}')
        print(f'  分母({series})が無い          : {no_denominator}')
        print(f'  分母がマイナス/ゼロ（存在しない）: {not_positive}')
        print(f'  株価が無い                    : {no_price}')
        print(f'  桁外れのため採らない            : {out_of_range}')
        print(f'  検算できず桁も外れるため不明にした: {dropped}')
        worst.sort(reverse=True)
        for gap, code, before, after in worst[:10]:
            print(f'    {code}: Yahoo={before} / 割り算={after}'
                  f'（{gap:.1f}倍の食い違い→判定不能）')
        print()

    print(f'合計 {total_updated}件{"（試算）" if args.dry_run else "を更新しました"}')
    print('このスクリプトは外部サイトへアクセスしません。')
    if not args.dry_run and total_updated:
        print('→ 続けて `python recalc_match_rates.py` でスコアを再計算してください。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
