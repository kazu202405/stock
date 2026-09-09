"""銘柄マスターにあるのに分析データが無い銘柄を抽出する。

初回の全銘柄バックフィルは一度きりなので、その実行で取得元の一時障害などに
当たった銘柄は、これまで自動更新の対象へ戻れなかった。夜間ジョブからこの
モジュールを使い、未取得銘柄を少数ずつ拾い直す。
"""

import json
import os

from security_filter import is_non_operating_name


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANIES_JSON = os.path.join(BASE_DIR, 'static', 'companies.json')
ATTEMPT_JOB_PREFIX = 'missing_stock_backfill:code:'


def load_master_codes(path=COMPANIES_JSON):
    """現在の事業会社マスターを重複なしで返す。"""
    with open(path, encoding='utf-8') as f:
        rows = json.load(f)

    codes = []
    seen = set()
    for row in rows:
        code = str(row.get('c') or '').strip().upper().removesuffix('.T')
        if not code or code in seen or is_non_operating_name(row.get('n')):
            continue
        seen.add(code)
        codes.append(code)
    return codes


def load_analyzed_codes(client, page_size=500):
    """screened_latestで一度でも分析が完了した銘柄を全件取得する。"""
    analyzed = set()
    offset = 0
    while True:
        rows = (client.table('screened_latest')
                .select('company_code, analyzed_at')
                .order('company_code')
                .range(offset, offset + page_size - 1)
                .execute().data or [])
        for row in rows:
            if not row.get('analyzed_at'):
                continue
            code = str(row.get('company_code') or '').strip().upper().removesuffix('.T')
            if code:
                analyzed.add(code)
        if len(rows) < page_size:
            break
        offset += page_size
    return analyzed


def load_recent_attempts(client, limit=1000):
    """銘柄別の最終試行を返す。記録を読めなくても補完自体は止めない。"""
    try:
        rows = (client.table('job_runs')
                .select('job_id, ok, ran_at')
                .like('job_id', ATTEMPT_JOB_PREFIX + '%')
                .order('ran_at', desc=True)
                .limit(limit)
                .execute().data or [])
    except Exception as e:
        print('[Scheduler] 未取得銘柄の試行履歴を読めませんでした: %s' % str(e)[:120])
        return {}

    attempts = {}
    for row in rows:
        job_id = str(row.get('job_id') or '')
        if not job_id.startswith(ATTEMPT_JOB_PREFIX):
            continue
        code = job_id[len(ATTEMPT_JOB_PREFIX):].strip().upper().removesuffix('.T')
        if code and code not in attempts:
            attempts[code] = {
                'ok': bool(row.get('ok')),
                'ran_at': row.get('ran_at') or '',
            }
    return attempts


def select_targets(missing_codes, attempts, limit):
    """失敗分を再試行しつつ、未試行銘柄も先へ進める。"""
    if limit <= 0:
        return []

    never_attempted = sorted(code for code in missing_codes if code not in attempts)
    failed = sorted(
        (code for code in missing_codes
         if code in attempts and not attempts[code].get('ok')),
        key=lambda code: (attempts[code].get('ran_at') or '', code),
    )
    # 永続的に取れない銘柄があっても全体がそこで止まらないよう、再試行は半分まで。
    retry_quota = max(1, limit // 2)
    selected = failed[:retry_quota]
    selected.extend(never_attempted[:limit - len(selected)])

    # すべて一度は試行済みなら、古い失敗から残り枠も使う。
    if len(selected) < limit:
        selected_set = set(selected)
        remaining_failed = [code for code in failed if code not in selected_set]
        selected.extend(remaining_failed[:limit - len(selected)])
    return selected


def find_missing_analysis_targets(client, limit, companies_path=COMPANIES_JSON):
    """今回処理するコードと、処理前の未取得総数を返す。"""
    master_codes = load_master_codes(companies_path)
    analyzed = load_analyzed_codes(client)
    missing = [code for code in master_codes if code not in analyzed]
    attempts = load_recent_attempts(client)
    return select_targets(missing, attempts, limit), len(missing)
