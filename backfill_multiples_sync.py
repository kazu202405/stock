"""分析時のまま止まっている PER・PBR・時価総額・配当利回りを、いまの株価に揃える。

2026-08-24。stock_price は毎日の cron が更新していたが、そこから計算される
指標は分析した日のまま置かれていた。銘柄ページには「今日の株価」と
「1か月前の株価で計算したPER」が並んで表示されていた。

    2477 手間いらず: 表示PER 13.8倍（7/29の株価2,416円ベース）
                     今日の株価3,180円で計算すると 18.2倍

基準にする株価:
    **stock_price_history.daily_1y の、analyzed_at 当日（無ければ直前の営業日）の
    終値**を使う。「per_forward × eps」で逆算してはいけない。EPSは報告通貨で
    入っており、米ドル建ての会社（6269 三井海洋開発など）では桁が壊れる。

    updated_at ではなく analyzed_at を見ること。updated_at は一部の保存経路でしか
    書かれておらず、2月のまま止まっている行が186件ある（中身は7〜8月のもの）。

二重適用の防止:
    処理した行には price_updated_at を入れる。この列がNULLの行だけを対象にする
    ので、二度流しても同じ行を二度伸縮させない。

外部アクセス: 無し。すべてDB内の値だけで計算する。

使い方:
    python backfill_multiples_sync.py            # 何が変わるか見るだけ
    python backfill_multiples_sync.py --apply    # 書き込む
    python backfill_multiples_sync.py --limit 50 --verbose
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

import multiples
import supabase_client as sc

PAGE_SIZE = 200
JST = timezone(timedelta(hours=9))

# 分析日の終値がこの日数より前にしか無いなら、基準として使わない。
# 日足は1年ぶんなので、それより古い分析日は遡れない。
MAX_LOOKBACK_DAYS = 10

# いまの stock_price が、日足のいちばん新しい終値からこれ以上離れていたら触らない。
# 離れる原因は2つある。(1) 日足が古いまま止まっている (2) 株式分割・併合があった。
# どちらの場合も「株価が何倍になったか」を正しく測れない。株式併合なら株価は
# 5倍になるがPERは変わらないので、伸縮させると5倍の嘘になる。
# 実例: 5135 AIR-U は日足が7/17で止まったまま株価が5倍になっていた。
MAX_PRICE_VS_CHART_GAP = 0.25


def _bars(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    return raw if isinstance(raw, list) else []


def price_on_or_before(bars, target_day):
    """target_day 当日、無ければ直前の営業日の終値を返す。

    休日に分析していることがあるので当日固定にはしない。
    離れすぎているものは返さない（古い分析日を今の株価と比べても意味がない）。
    """
    best_day, best_close = None, None
    for bar in bars:
        try:
            day = datetime.fromtimestamp(bar['time'], JST).date()
            close = float(bar['close'])
        except (KeyError, TypeError, ValueError, OSError):
            continue
        if day <= target_day and (best_day is None or day > best_day):
            best_day, best_close = day, close
    if best_day is None or (target_day - best_day).days > MAX_LOOKBACK_DAYS:
        return None, None
    return best_close, best_day


def load_targets(client, limit=None):
    """まだ揃えていない行（price_updated_at が NULL）を返す"""
    rows, offset = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('*')
                .is_('price_updated_at', 'null')
                .order('company_code')
                .range(offset, offset + PAGE_SIZE - 1)
                .execute())
        batch = page.data or []
        rows.extend(batch)
        if limit and len(rows) >= limit:
            return rows[:limit]
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def history_for(client, codes):
    """銘柄コード → 日足の対応表。100件ずつまとめて引く"""
    out = {}
    for i in range(0, len(codes), 100):
        rows = (client.table('stock_price_history')
                .select('company_code, daily_1y')
                .in_('company_code', codes[i:i + 100])
                .execute().data or [])
        for row in rows:
            out[row['company_code']] = _bars(row.get('daily_1y'))
    return out


def plan_one(row, bars):
    """1銘柄ぶんの書き換え内容を決める。

    戻り値: (updates, 理由) — updates が空なら理由に何もしない訳が入る
    """
    analyzed = row.get('analyzed_at')
    if not analyzed:
        return {}, '分析日が無い'
    try:
        analyzed_day = datetime.fromisoformat(
            str(analyzed).replace('Z', '+00:00')).astimezone(JST).date()
    except ValueError:
        return {}, '分析日が読めない'

    now_price = row.get('stock_price')
    if not now_price:
        return {}, '株価が無い'

    base_price, base_day = price_on_or_before(bars, analyzed_day)
    if base_price is None:
        return {}, '分析日の株価が日足に無い'

    # 日足の最新終値と突き合わせて、いまの株価が地続きかを確かめる
    latest_close, latest_day = price_on_or_before(
        bars, datetime.now(JST).date() + timedelta(days=1))
    if latest_close is None:
        return {}, '日足が古すぎて確かめられない'
    if abs(now_price - latest_close) / latest_close > MAX_PRICE_VS_CHART_GAP:
        return ({}, f'株価がチャートと合わない（{latest_day} {latest_close:.0f}円 '
                    f'/ DB {now_price:.0f}円）分割・併合か日足が古い')

    try:
        updates = multiples.rescale_with_score(
            row, now_price, base_price=base_price,
            max_ratio=multiples.BACKFILL_MAX_RATIO)
    except multiples.ImplausibleRatio as e:
        # 株式分割の疑い。分割では株価もEPSも同じ比で動くのでPERは変わらない。
        # 指標は触らず、印だけ付けて次回以降の伸縮に乗せる。
        return ({'price_updated_at': datetime.now(timezone.utc).isoformat()},
                f'株価が飛んでいる（分割の疑い）: {e}')

    if not updates:
        # ズレていない。印だけ付けて対象から外す
        return ({'price_updated_at': datetime.now(timezone.utc).isoformat()},
                'ズレなし')
    return updates, f'{base_day} {base_price:.0f}円 → {now_price:.0f}円'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='実際に書き込む')
    parser.add_argument('--limit', type=int, help='処理する件数の上限')
    parser.add_argument('--verbose', action='store_true', help='1件ずつ表示')
    args = parser.parse_args()

    client = sc.get_supabase_client()
    rows = load_targets(client, args.limit)
    print(f'対象（まだ揃えていない行）: {len(rows)}件')
    if not rows:
        print('すべて揃っています')
        return

    hist = history_for(client, [r['company_code'] for r in rows])

    planned, skipped, drift = [], {}, []
    for row in rows:
        updates, reason = plan_one(row, hist.get(row['company_code'], []))
        if 'per_forward' in updates:
            old = row.get('per_forward')
            new = updates['per_forward']
            if old:
                drift.append(abs(new - old) / old)
            if args.verbose:
                print(f"  {row['company_code']} {str(row.get('company_name'))[:14]:<14} "
                      f"PER {old:.1f} → {new:.1f}   {reason}")
        elif not updates:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        planned.append((row['company_code'], updates))

    fixed = len(drift)
    print(f'\n指標を直す: {fixed}件 / 印だけ付ける: {len(planned) - fixed}件')
    for reason, n in sorted(skipped.items(), key=lambda x: -x[1]):
        print(f'  手つかず（{reason}）: {n}件')
    if drift:
        drift.sort()
        print(f'\nPERのズレ  中央値 {drift[len(drift)//2]*100:.1f}%  '
              f'最大 {drift[-1]*100:.0f}%  '
              f'20%以上 {sum(1 for d in drift if d >= 0.2)}件')

    if not args.apply:
        print('\n--apply を付けると書き込みます（いまは何も変えていません）')
        return

    written = failed = 0
    for code, updates in planned:
        try:
            (client.table('screened_latest').update(updates)
             .eq('company_code', code).execute())
            written += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f'  失敗 {code}: {e}')
            if failed > 50:
                print('  失敗が多いので中断します')
                break
    print(f'\n更新: {written}件 / 失敗: {failed}件')


if __name__ == '__main__':
    main()
