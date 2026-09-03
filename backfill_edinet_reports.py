# -*- coding: utf-8 -*-
"""有価証券報告書（EDINET公式API）から役員・大株主・従業員数を埋める。

なぜ要るか:
  役員は52.1%、英語の大株主は0.6%しか埋まっていない。取得元の
  yahooquery / J-LiC / Strainer が日本の中小型株を収録していないため。
  有報には構造化されて入っている（247A Ａｉロボティクス・従業員12名でも取れた）。

使い方:
    py -3 backfill_edinet_reports.py --limit 500            # 時価総額の大きい順
    py -3 backfill_edinet_reports.py --code 7203            # 1銘柄だけ
    py -3 backfill_edinet_reports.py --limit 100 --dry-run  # 書き込まない

⚠️ **既存の値を空で上書きしないこと。** 取れなかった項目は触らない。
   このリポジトリで何度も踏んでいる形。

⚠️ **公式APIは認証エラーでもHTTP 200を返す。** 本文の StatusCode を見る。
   status_code だけで判定すると「全件成功なのに中身が空」になる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

import requests

import edinet_report

API_BASE = 'https://api.edinet-fsa.go.jp/api/v2'

# EDINETへの間隔。公式に明示の上限は無いが、相手は官庁のAPIなので
# 詰めて叩かない。1件あたり2〜5秒かかるので、これで律速にはならない。
SLEEP_SECONDS = 0.4

# 書類一覧を遡る月数。決算期がばらけるので、12か月では3月期しか拾えない。
# 15か月あれば、どの決算期の会社も直近の有報が1本は入る。
LOOKBACK_MONTHS = 15


def _key():
    key = (os.getenv('EDINET_SUBSCRIPTION_KEY') or '').strip()
    if not key:
        sys.exit('EDINET_SUBSCRIPTION_KEY が設定されていません。'
                 '（edb_ で始まる EDINET_API_KEY は第三者サービスのもので別物）')
    return key


def _get_json(path, params, key):
    """EDINETのJSONを取る。

    ⚠️ 認証エラーでも HTTP 200 が返り、本文の StatusCode が401になる。
       ここを見落とすと、鍵が無効なまま「0件でした」と静かに終わる。
    """
    res = requests.get(API_BASE + path, params=params,
                       headers={'Ocp-Apim-Subscription-Key': key}, timeout=60)
    res.raise_for_status()
    body = res.json()
    status = str((body.get('metadata') or {}).get('status')
                 or body.get('StatusCode') or '')
    if status != '200':
        raise RuntimeError('EDINETが %s を返しました: %s'
                           % (status, body.get('message') or body))
    return body


def _flushing_print(*a):
    # ⚠️ 既定の print はファイルへ流すとバッファされ、**進捗が何も見えない**。
    #    長い処理ほど「動いているのか止まっているのか」が分からなくなる。
    print(*a, flush=True)


def load_or_build_index(key, months, cache_path, log=_flushing_print):
    """書類一覧を作る。キャッシュがあれば読む。

    ⚠️ 一覧の走査だけで325リクエスト・約10分かかる。落ちるたびに走査し直すと
       相手にも自分にも無駄なので、いったん作ったらファイルに残す。
    """
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding='utf-8') as f:
            index = json.load(f)
        log('書類一覧をキャッシュから読みました: %d社（%s）' % (len(index), cache_path))
        return index
    index = build_index(key, months, log)
    if cache_path:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(index, f, ensure_ascii=False)
        log('書類一覧を保存しました: %s' % cache_path)
    return index


def build_index(key, months=LOOKBACK_MONTHS, log=_flushing_print):
    """{company_code: 書類情報} を作る。新しい提出ほど優先。

    EDINETは**日付でしか引けない**（会社を指定して最新の有報、は取れない）。
    ∴ 期間を1日ずつ走査して、有価証券報告書だけを拾う。
    """
    index = {}
    today = date.today()
    start = today - timedelta(days=int(months * 30.5))
    day, scanned = start, 0
    while day <= today:
        if day.weekday() < 5:
            try:
                body = _get_json('/documents.json',
                                 {'date': day.isoformat(), 'type': '2'}, key)
            except Exception as e:
                log('  %s 一覧の取得に失敗: %s' % (day, str(e)[:90]))
                day += timedelta(days=1)
                continue
            for doc in (body.get('results') or []):
                if doc.get('docTypeCode') != edinet_report.DOC_TYPE_ANNUAL_REPORT:
                    continue
                sec = doc.get('secCode')
                if not sec or len(sec) != 5:
                    continue
                code = sec[:-1]
                # 古い日から走査するので、後に来たもの（新しい提出）で上書きする
                index[code] = doc
            scanned += 1
            if scanned % 40 == 0:
                log('  走査 %d日 / 有報 %d社' % (scanned, len(index)))
            time.sleep(SLEEP_SECONDS)
        day += timedelta(days=1)
    log('一覧の走査おわり: %d日 / 有報 %d社' % (scanned, len(index)))
    return index


# EDINETから入れた役員データの目印。
# 旧経路（yahooquery）は英語の name / title しか持たないので、
# name_jp があれば「EDINETで入れ直した後」だと分かる。
EDINET_MARK = 'name_jp'


def is_edinet_done(row):
    """この銘柄はもうEDINETで入れ直したか。

    ⚠️ 「役員が空でないか」で判定しない。旧経路の英語データが入っている
       銘柄（52.1%）を飛ばしてしまい、いちばん直したいものが直らない。
    """
    return EDINET_MARK in (row.get('company_officers') or '')


def targets(client, limit, only_code=None, skip_done=False):
    """埋める対象を、時価総額の大きい順に返す。

    会員が実際に見る銘柄から埋める。全件流して途中で落ちるより、
    効く順に入れて様子を見るほうが安全。
    """
    rows, start = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, company_name, market_cap, market_segment, '
                        'delisted_at, company_officers, major_shareholders_jp')
                .range(start, start + 999).execute().data or [])
        rows.extend(page)
        if len(page) < 1000:
            break
        start += 1000

    if only_code:
        return [r for r in rows if r['company_code'] == only_code]

    live = [r for r in rows
            if not r.get('delisted_at')
            and (r.get('market_segment') or '') in ('プライム', 'スタンダード', 'グロース')]
    if skip_done:
        live = [r for r in live if not is_edinet_done(r)]
    live.sort(key=lambda r: (r.get('market_cap') or 0), reverse=True)
    return live[:limit] if limit else live


def fetch_report(doc_id, key):
    """有報のCSV（ZIP）を取る。"""
    res = requests.get('%s/documents/%s' % (API_BASE, doc_id),
                       params={'type': '5'},
                       headers={'Ocp-Apim-Subscription-Key': key}, timeout=120)
    res.raise_for_status()
    if res.content[:2] != b'PK':
        # 認証・在庫切れのときはJSONが返る。ZIPでなければ中身を見せて止める。
        raise RuntimeError('ZIPではありません: %s' % res.content[:180])
    return res.content


def updates_for(row, data):
    """保存する列だけを返す。

    ⚠️ **取れなかった項目は触らない。** 空で上書きすると、
       別経路で入っていた正常値が消える。
    """
    updates = {}
    officers = data.get('company_officers') or []
    holders = data.get('major_shareholders_jp') or []
    if officers:
        updates['company_officers'] = json.dumps(officers, ensure_ascii=False)
    if holders:
        updates['major_shareholders_jp'] = json.dumps(holders, ensure_ascii=False)
    if data.get('employees'):
        updates['employees'] = data['employees']
    return updates


def mark_ingested(client, code, doc, dry_run=False):
    """毎晩のジョブが見る「取り込み済み」の印を書く。

    ⚠️ **中身（screened_latest）だけ書いて、ここを書かないと積み残しが減らない。**
       毎晩の edinet_sync は edinet_codes.report_doc_id の有無で数えるので、
       印が無い限り何社取り込んでも「積み残し19社」と言い続ける。
       実際 2026-09-03 まで、この印が無いせいで件数が固定されていた。

    印が書けなくても中身の取り込みは成功しているので、ここで落とさない。
    ただし黙って飛ばすと同じことが起きるので、必ず画面に出す。
    """
    if dry_run:
        return
    from datetime import datetime, timezone
    payload = {'report_doc_id': doc.get('docID'),
               'report_fetched_at': datetime.now(timezone.utc).isoformat()}
    period = doc.get('periodEnd')
    if period:
        payload['report_period_end'] = period
    try:
        (client.table('edinet_codes').update(payload)
         .eq('company_code', code).execute())
    except Exception as e:
        print('  ※ %s の取り込み済みの印を書けませんでした: %s' % (code, str(e)[:110]),
              flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=500)
    ap.add_argument('--code', default=None, help='1銘柄だけ処理する')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--months', type=int, default=LOOKBACK_MONTHS)
    ap.add_argument('--skip-done', action='store_true',
                    help='EDINETで入れ直し済みの銘柄を飛ばす（再開用）')
    ap.add_argument('--index-cache', default=None,
                    help='書類一覧を保存/再利用するファイル')
    ap.add_argument('--sleep', type=float, default=SLEEP_SECONDS)
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv('.env')
    load_dotenv('.env.local', override=True)
    from supabase_client import get_supabase_client

    key = _key()
    client = get_supabase_client()

    globals()['SLEEP_SECONDS'] = args.sleep
    rows = targets(client, args.limit, args.code, skip_done=args.skip_done)
    print('対象 %d銘柄' % len(rows), flush=True)

    print('EDINETの書類一覧を走査します（過去%dか月）…' % args.months, flush=True)
    index = load_or_build_index(key, args.months, args.index_cache)

    have = [r for r in rows if r['company_code'] in index]
    print('うち有報が見つかった: %d銘柄' % len(have), flush=True)

    ok = skipped = failed = 0
    officers_filled = holders_filled = 0
    for i, row in enumerate(have, 1):
        code = row['company_code']
        doc = index[code]
        try:
            data = edinet_report.extract(fetch_report(doc['docID'], key))
            updates = updates_for(row, data)
            if not updates:
                # ⚠️ 有報は読めている（既に埋まっているだけ）。ここで印を書かないと
                #    永久に積み残しに残り、毎晩同じ会社を数え続ける。
                mark_ingested(client, code, doc, dry_run=args.dry_run)
                skipped += 1
                print('  [%d/%d] %s %s 取れる項目なし' % (
                    i, len(have), code, (row.get('company_name') or '')[:12]), flush=True)
                continue
            if 'company_officers' in updates:
                officers_filled += 1
            if 'major_shareholders_jp' in updates:
                holders_filled += 1
            if not args.dry_run:
                (client.table('screened_latest').update(updates)
                 .eq('company_code', code).execute())
            # 中身と同じタイミングで、毎晩のジョブが見る印も書く
            mark_ingested(client, code, doc, dry_run=args.dry_run)
            ok += 1
            print('  [%d/%d] %s %s 役員%d人 / 大株主%d件 / 従業員%s' % (
                i, len(have), code, (row.get('company_name') or '')[:12],
                len(data['company_officers']), len(data['major_shareholders_jp']),
                data.get('employees')), flush=True)
        except Exception as e:
            failed += 1
            print('  [%d/%d] %s 失敗: %s' % (i, len(have), code, str(e)[:110]), flush=True)
        time.sleep(SLEEP_SECONDS)

    print('\n=== おわり ===', flush=True)
    print('保存 %d / 項目なし %d / 失敗 %d' % (ok, skipped, failed), flush=True)
    print('役員を入れた %d銘柄 / 大株主を入れた %d銘柄' % (officers_filled, holders_filled), flush=True)
    if args.dry_run:
        print('※ --dry-run のため書き込んでいません', flush=True)


if __name__ == '__main__':
    main()
