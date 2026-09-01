# -*- coding: utf-8 -*-
"""有価証券報告書の取り込みを、毎晩少しずつ続ける。

なぜ要るか:
  2026-09-01 に一括で入れて役員99.7%まで埋めたが、**有報は年に1回出る**。
  放っておくと来年の有報で古くなるし、新規上場も拾えない。

やること:
  直近数日の提出一覧を見て、**新しい有報を出した会社だけ**取り直す。
  外部への負荷は一覧が数リクエスト＋その会社ぶんだけ。ふだんは数社で終わる。

⚠️ **初回の穴埋めはここの仕事ではない。** まだ一度も取り込めていない会社は
   「いつ有報を出したか」が分からず、docIDを引くには日付を325日ぶん走査する
   必要がある。それは一括スクリプト（backfill_edinet_reports.py）の担当で、
   ここは件数を数えて見せるだけにしてある。

⚠️ **判定は対象決算期（periodEnd）で行う。** 提出日ではない。訂正報告書や
   再提出で提出日は動くが、対象の決算期は変わらない。提出日で見ると、
   訂正が出るたびに取り直すことになる。

⚠️ **取れなかった項目は触らない。** 空で上書きすると別経路の正常値が消える。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import edinet_api
import edinet_report

# 1晩に処理する上限と、かける時間の上限。
#
# 1件あたり実測1.7秒。150件で約5分。決算期（6月）は新規の有報が1日数百件
# 出るので、そこは数晩に分けて崩す。時間の上限は、他の定期実行と
# かち合わせないための保険。
NIGHTLY_LIMIT = 150
NIGHTLY_MINUTES = 25

# 前日ぶんだけでなく、少し遡って拾う。
# 1日でも取りこぼすと、その会社は次の有報（1年後）まで古いままになるため。
LOOKBACK_DAYS = 4


def _to_date(value):
    try:
        return datetime.strptime((value or '')[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def load_state(client):
    """{company_code: {'doc_id', 'period_end'}} を返す。"""
    out, start = {}, 0
    while True:
        page = (client.table('edinet_codes')
                .select('company_code, report_doc_id, report_period_end')
                .range(start, start + 999).execute().data or [])
        for r in page:
            out[r['company_code']] = {
                'doc_id': r.get('report_doc_id'),
                'period_end': _to_date(r.get('report_period_end')),
            }
        if len(page) < 1000:
            break
        start += 1000
    return out


def find_new_reports(state, key=None, days=LOOKBACK_DAYS, today=None, log=print):
    """直近数日に出た有報のうち、取り込むべきものを返す。

    取り込むべき＝まだ取っていない、または**対象決算期がより新しい**もの。
    """
    today = today or date.today()
    todo = {}
    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        try:
            docs = edinet_api.annual_reports(day.isoformat(), key)
        except Exception as e:
            log('  %s の一覧を取れません: %s' % (day, str(e)[:100]))
            continue
        for code, doc in docs.items():
            have = state.get(code) or {}
            period = _to_date(doc.get('periodEnd'))
            if have.get('period_end') and period and period <= have['period_end']:
                continue          # 同じ決算期。訂正や再提出では取り直さない
            todo[code] = doc
    return todo


def count_backlog(client, state):
    """まだ一度も取り込めていない会社の数を返す（処理はしない）。

    ⚠️ **ここで処理しようとしないこと。** 積み残しの会社は「いつ有報を出したか」
       が分からず、docIDを引くには日付を325日ぶん走査する必要がある。
       毎晩それをやるのは相手にも自分にも過剰。初回の穴埋めは一括スクリプト
       （backfill_edinet_reports.py）の仕事で、ここは**件数を見せるだけ**。
       拾うふりをして黙って飛ばす作りにすると、いつまでも埋まらないのに
       「毎晩動いている」ように見える。
    """
    rows, start = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, market_cap, market_segment, delisted_at')
                .range(start, start + 999).execute().data or [])
        rows.extend(page)
        if len(page) < 1000:
            break
        start += 1000
    live = [r for r in rows
            if not r.get('delisted_at')
            and (r.get('market_segment') or '') in ('プライム', 'スタンダード', 'グロース')
            and not (state.get(r['company_code']) or {}).get('doc_id')]
    return len(live)


def save_company(client, code, doc, data):
    """抽出結果と「どの書類を取り込んだか」を保存する。

    ⚠️ 取れなかった項目は触らない。空で上書きすると正常値が消える。
    """
    import json

    updates = {}
    if data.get('company_officers'):
        updates['company_officers'] = json.dumps(
            data['company_officers'], ensure_ascii=False)
    if data.get('major_shareholders_jp'):
        updates['major_shareholders_jp'] = json.dumps(
            data['major_shareholders_jp'], ensure_ascii=False)
    if data.get('employees'):
        updates['employees'] = data['employees']
    if updates:
        (client.table('screened_latest').update(updates)
         .eq('company_code', code).execute())

    # ⚠️ 印は**中身が取れたときだけ**付ける。取れていないのに付けると、
    #    その会社は次の有報まで二度と試されない。
    if updates:
        (client.table('edinet_codes').update({
            'report_doc_id': doc.get('docID'),
            'report_period_end': (doc.get('periodEnd') or None),
            'report_fetched_at': datetime.now(timezone.utc).isoformat(),
        }).eq('company_code', code).execute())
    return bool(updates)


def run(client=None, limit=NIGHTLY_LIMIT, minutes=NIGHTLY_MINUTES, log=print):
    """1晩ぶんを処理する。{'updated', 'skipped', 'failed'} を返す。"""
    import time as _time

    if client is None:
        from supabase_client import get_supabase_client
        client = get_supabase_client()

    key = edinet_api.subscription_key()
    state = load_state(client)

    todo = find_new_reports(state, key, log=log)
    backlog = count_backlog(client, state)
    log('新しい有報: %d社 / 未取得の積み残し: %d社（積み残しは一括スクリプトの担当）'
        % (len(todo), backlog))

    deadline = _time.monotonic() + minutes * 60
    updated = skipped = failed = 0

    for code, doc in list(todo.items())[:limit]:
        if _time.monotonic() >= deadline:
            log('時間切れ。残りは翌晩に持ち越します')
            break
        try:
            data = edinet_report.extract(edinet_api.fetch_report(doc['docID'], key))
            if save_company(client, code, doc, data):
                updated += 1
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            log('  %s 失敗: %s' % (code, str(e)[:110]))
        _time.sleep(edinet_api.DEFAULT_SLEEP)

    return {'updated': updated, 'skipped': skipped, 'failed': failed,
            'new_reports': len(todo), 'backlog': backlog}
