"""予想値バックフィルの効果と副作用を、保存せずに測る。

**DBには一切書かない。** 保存関数を差し替えてから分析を呼ぶ。

--------------------------------------------------------------------------
なぜこれが要るか（2026-08-19）
--------------------------------------------------------------------------
screened_latest 3,879件のうち今期予想が入っているのは 348件（9.0%）。
規模とは無関係（Core30でも9.7%）。全件を再分析すべきか判断するために、
「本当に埋まるのか」と「スコアがどう動くのか」を先に測る。

これまでに分かったこと:
  1回目 … 「予想が埋まった 0件」。原因は Yahoo!JP のブレーカーが数件で開き、
           以降の予想取得が全部skipされていたこと（forecast=circuit_open）。
  修正   … `yahoo_jp_guard` に半開放を入れた（10分→倍々・上限60分）。
  2回目 … success 30/50 に改善。ただし**欠けている22件の主因は no_data 13件**
           （Yahooにその銘柄の業績ページが無い）で、埋まったのは2件だけ。
           スコア副作用は PER/PBR 判定不能 0件＝全件やっても安全。
  残る問い … 2回目のサンプルはコード順先頭50件で、28件（56%）が既に予想持ち。
           母集団は9%。**偏っていて全件の判断に使えない。**

--------------------------------------------------------------------------
使い方
--------------------------------------------------------------------------
  python backfill_impact_dryrun.py --missing-only --spread 100 --sleep 1.5
  python backfill_impact_dryrun.py --report          # 途中でも集計だけ出す

  --missing-only  いま予想が入っていない銘柄だけを対象にする（母集団の実態を測る）
  --spread N      コード帯を散らして N 件サンプリングする（先頭に寄せない）
  --head N        コード順に先頭 N 件（旧挙動。比較用に残す）
  --sleep 秒      1銘柄ごとの間隔。空けないとブレーカーが開く（既定 1.5）
  --resume        すでに結果がある銘柄を飛ばして続きから
  --report        取得済みの結果だけを集計して終わる

**1銘柄ごとに追記する（JSONL）。** 途中で落ちても、そこまでの結果は残る。
中断したら同じコマンドに --resume を足せば続きから走る。
"""

import json
import os
import random
import sys
import time
from collections import Counter

from dotenv import load_dotenv
load_dotenv()

import app as appmod
from stock_analyzer import StockAnalyzer
import supabase_client as sc

OUT = 'claudedocs/backfill_impact_dryrun.jsonl'
CUTOFF = '2026-08-05'


# ------------------------------------------------------------------ 引数
def arg(name, default=None, cast=str):
    if name in sys.argv:
        return cast(sys.argv[sys.argv.index(name) + 1])
    return default


MISSING_ONLY = '--missing-only' in sys.argv
RESUME = '--resume' in sys.argv
REPORT_ONLY = '--report' in sys.argv
SLEEP = arg('--sleep', 1.5, float)
SPREAD = arg('--spread', None, int)
HEAD = arg('--head', None, int)
SAMPLE = SPREAD or HEAD or 100


# ------------------------------------------------------------------ 入出力
def load_done():
    """すでに測り終えた銘柄。JSONLなので途中で切れた行は捨てる。"""
    done = {}
    if not os.path.exists(OUT):
        return done
    with open(OUT, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue        # 中断で欠けた最終行
            done[row['code']] = row
    return done


def append(row):
    with open(OUT, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())    # 落ちても残るように


# ------------------------------------------------------------------ 抽出
def pick_codes(client):
    """測る銘柄を決める。

    コード順の先頭から取ると特定の帯に寄る（実測：先頭50件は56%が予想持ち、
    母集団は9%）。帯を散らしてから無作為に選ぶ。
    """
    q = (client.table('screened_latest').select('company_code')
         .lt('analyzed_at', CUTOFF))
    if MISSING_ONLY:
        q = q.is_('forecast_revenue', 'null')

    codes = []
    page = 0
    while page < 20:
        rows = q.range(page * 1000, page * 1000 + 999).execute().data or []
        codes += [r['company_code'] for r in rows]
        if len(rows) < 1000:
            break
        page += 1

    if HEAD:
        return sorted(codes)[:HEAD]

    # 1000番台〜9000番台から均等に取る。特定の業種帯に偏らせない
    buckets = {}
    for c in codes:
        buckets.setdefault(str(c)[0], []).append(c)
    rnd = random.Random(20260819)      # 再現できるように固定
    per = max(1, SAMPLE // max(1, len(buckets)))
    picked = []
    for k in sorted(buckets):
        pool = buckets[k]
        rnd.shuffle(pool)
        picked += pool[:per]
    rnd.shuffle(picked)
    return picked[:SAMPLE]


# ------------------------------------------------------------------ 集計
def report(done):
    if not done:
        print('まだ結果がありません')
        return
    rows = list(done.values())
    fc = Counter(str(r.get('forecast_status')) for r in rows)
    print(f'\n=== 予想の取得結果（{len(rows)}件） ===')
    for k, v in fc.most_common():
        print(f'  {k:16} {v:>4}  ({v * 100 / len(rows):.0f}%)')

    filled = [r for r in rows if r.get('forecast_filled')]
    # 売上だけ取れて利益は取れない（赤字でPERが出ない等）ケースがあるので分けて数える
    partial = [r for r in rows
               if not r.get('forecast_filled')
               and any(k in (r.get('newly_judged') or [])
                       for k in ('revenue_forecast', 'op_forecast'))]
    print(f'\n予想2項目とも判定に乗った: {len(filled)}/{len(rows)} '
          f'({len(filled) * 100 / len(rows):.0f}%)')
    print(f'片方だけ乗った:           {len(partial)}/{len(rows)} '
          f'({len(partial) * 100 / len(rows):.0f}%)')

    unjudged = [r for r in rows if r.get('newly_unjudged')]
    per_pbr = [r for r in rows
               if any(k in (r.get('newly_unjudged') or []) for k in ('per_forward', 'pbr'))]
    print(f'判定不能が増えた: {len(unjudged)}件（うちPER/PBR: {len(per_pbr)}件）')

    moved = [r for r in rows
             if r.get('score_before') is not None and r.get('score_after') is not None
             and r['score_before'] != r['score_after']]
    print(f'スコアが動いた: {len(moved)}/{len(rows)}')
    if moved:
        ds = [r['score_after'] - r['score_before'] for r in moved]
        print(f'  平均 {sum(ds) / len(ds):+.1f}pt  最大 {max(ds):+d}  最小 {min(ds):+d}')
    errs = [r for r in rows if r.get('error')]
    if errs:
        print(f'エラー: {len(errs)}件')


# ------------------------------------------------------------------ 本体
def main():
    done = load_done()
    if REPORT_ONLY:
        report(done)
        return

    client = sc.get_supabase_client()
    codes = pick_codes(client)
    if RESUME:
        codes = [c for c in codes if c not in done]
    print(f'対象 {len(codes)}件'
          f'（missing_only={MISSING_ONLY} / 済み{len(done)}件 / sleep={SLEEP}s）', flush=True)
    print(f'途中で止まっても {OUT} に残ります。続きは --resume を足して同じコマンド。',
          flush=True)

    # 保存を止める。ここを外し忘れると本番を書き換える
    appmod._save_screened_tolerating_new_columns = lambda payload: None

    import yahoo_jp_guard as guard
    print('開始時のブレーカー:', guard.status_snapshot(), flush=True)

    analyzer = StockAnalyzer()

    for i, code in enumerate(codes, 1):
        if i > 1:
            time.sleep(SLEEP)      # 連続で叩くとブレーカーが開く

        # ブレーカーが開いていたら、冷却が明けるまで待つ。
        # 待たずに進むと「取りに行っていないのに測った」件が量産される
        # （実測：99件中48件がこれで無駄になった）。
        snap = guard.status_snapshot()
        if snap['tripped'] and snap['retry_in_seconds'] > 0:
            wait = snap['retry_in_seconds'] + 2
            print(f'[{i:>3}/{len(codes)}] ブレーカーが開いています。{wait}秒待ちます',
                  flush=True)
            time.sleep(wait)
        stored = sc.get_screened_data(code) or {}
        row = {'code': code}
        try:
            fresh = appmod._analyze_stock_and_save(analyzer, code)
        except Exception as e:
            row['error'] = str(e)[:120]
            append(row)
            print(f'[{i:>3}/{len(codes)}] {code:6} ERROR {str(e)[:50]}', flush=True)
            continue

        if not fresh:
            row['error'] = 'no_name'
            append(row)
            print(f'[{i:>3}/{len(codes)}] {code:6} 取得できず', flush=True)
            continue

        raw = fresh.get('raw') or {}
        fc = (raw.get('source_status') or {}).get('forecast') or {}
        row['forecast_status'] = fc.get('status') if isinstance(fc, dict) else None

        fresh = {k: v for k, v in fresh.items() if k != 'raw'}
        merged = dict(stored)
        merged.update({k: v for k, v in fresh.items() if v is not None})

        before = sc.score_breakdown(stored)
        after = sc.score_breakdown(merged)
        miss_b = set(before.get('missing_keys') or [])
        miss_a = set(after.get('missing_keys') or [])

        fc_keys = {'revenue_forecast', 'op_forecast'}
        row['forecast_filled'] = bool((miss_b & fc_keys) and not (miss_a & fc_keys))
        row['newly_judged'] = sorted(miss_b - miss_a)
        row['newly_unjudged'] = sorted(miss_a - miss_b)
        row['score_before'] = before.get('score')
        row['score_after'] = after.get('score')
        row['judged_before'] = before.get('judged')
        row['judged_after'] = after.get('judged')
        append(row)

        print(f'[{i:>3}/{len(codes)}] {code:6} fc={str(row["forecast_status"]):14}'
              f' 埋まった={row["forecast_filled"]!s:5}'
              f' score {row["score_before"]}→{row["score_after"]}'
              f' judged {row["judged_before"]}→{row["judged_after"]}', flush=True)

    print('\n終了時のブレーカー:', guard.status_snapshot(), flush=True)
    report(load_done())


if __name__ == '__main__':
    main()
