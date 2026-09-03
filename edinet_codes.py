# -*- coding: utf-8 -*-
"""金融庁EDINETの提出者一覧（EdinetcodeDlInfo.csv）を取り込む。

なぜ要るか:
  1. **証券コード → EDINETコード の対応表**。有報を引くのにこれが要る。
  2. 登記上の本店所在地・資本金・法人番号・決算日が、**1リクエストで全件**。
     いま Yahoo日本版のHTMLを1晩60件ずつ叩いて集めているぶんの一部が、
     ここで一度に揃う（しかも公式）。

⚠️ **APIキーは要らない。** 公式APIの Subscription-Key が必要なのは書類取得の
   ほうで、この一覧は誰でもダウンロードできる。

⚠️ **所在地を screened_latest.headquarters に流し込まないこと。**
   あちらは Yahoo日本版の「本社」、こちらは有報の「登記上の本店」で別物。
   6498キッツは 本社=東京都港区東新橋 / 登記上の本店=千葉市美浜区中瀬。
   混ぜると1つの列に2つの意味が入り、どちらなのか誰にも分からなくなる。

⚠️ **決算月も上書きしない。** yfinance由来の値と44件食い違う。どちらが
   正しいかは有報を見るまで決められないので、ここでは数えるだけにする。
"""

from __future__ import annotations

import csv
import io
import zipfile

CODE_LIST_URL = (
    'https://disclosure2dl.edinet-fsa.go.jp'
    '/searchdocument/codelist/Edinetcode.zip'
)

# ダウンロードに許す秒数。画面から呼ぶ口ではないが、
# 定期実行が外部で詰まると他のジョブとかち合う。
DOWNLOAD_TIMEOUT = 60

# CSVの列名（EDINETの見出しそのまま。全角の「ＥＤＩＮＥＴ」に注意）
COL = {
    'edinet_code': 'ＥＤＩＮＥＴコード',
    'submitter_type': '提出者種別',
    'listed': '上場区分',
    'consolidated': '連結の有無',
    'capital': '資本金',
    'fiscal_day': '決算日',
    'submitter_name': '提出者名',
    'submitter_name_en': '提出者名（英字）',
    'submitter_kana': '提出者名（ヨミ）',
    'registered_address': '所在地',
    'industry': '提出者業種',
    'securities_code': '証券コード',
    'corporate_number': '提出者法人番号',
}


def to_company_code(securities_code):
    """EDINETの5桁証券コードを、うちの4桁コードに直す。

    EDINETは必ず5桁・末尾0で持っている（実測3,821件すべて）。
    72030 → 7203 / 409A0 → 409A
    """
    code = (securities_code or '').strip()
    if len(code) != 5 or not code.endswith('0'):
        return None
    return code[:-1]


def to_fiscal_month(fiscal_day):
    """「3月31日」→ 3。読めなければ None。"""
    text = (fiscal_day or '').strip()
    if '月' not in text:
        return None
    try:
        month = int(text.split('月')[0])
    except (ValueError, TypeError):
        return None
    return month if 1 <= month <= 12 else None


def _to_int(value):
    text = (value or '').strip().replace(',', '')
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def download(url=CODE_LIST_URL, timeout=DOWNLOAD_TIMEOUT):
    """一覧を取ってきて、CSVの行（dict）のリストを返す。"""
    import requests

    res = requests.get(url, timeout=timeout)
    res.raise_for_status()
    return parse(res.content)


def parse(zip_bytes):
    """ZIPのバイト列からCSVの行を取り出す。

    ⚠️ 文字コードは cp932。1行目は「ダウンロード実行日,…,件数,11388件」という
       見出しではない行なので、読み飛ばしてから DictReader に渡す。
    """
    archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = [n for n in archive.namelist() if n.lower().endswith('.csv')]
    if not names:
        raise ValueError('CSVが入っていません: %s' % archive.namelist())
    raw = archive.read(names[0])
    text = raw.decode('cp932', errors='replace')
    lines = text.splitlines()
    if len(lines) < 2:
        raise ValueError('中身が空です')
    return list(csv.DictReader(io.StringIO('\n'.join(lines[1:]))))


def to_rows(records):
    """CSVの行を、edinet_codes テーブルの行に直す。

    証券コードを持たない提出者（非上場のファンド等）は落とす。
    """
    out = {}
    for r in records:
        code = to_company_code(r.get(COL['securities_code']))
        if not code:
            continue
        out[code] = {
            'company_code': code,
            'edinet_code': (r.get(COL['edinet_code']) or '').strip(),
            'submitter_name': (r.get(COL['submitter_name']) or '').strip() or None,
            'submitter_name_en': (r.get(COL['submitter_name_en']) or '').strip() or None,
            'submitter_kana': (r.get(COL['submitter_kana']) or '').strip() or None,
            'registered_address': (r.get(COL['registered_address']) or '').strip() or None,
            'industry': (r.get(COL['industry']) or '').strip() or None,
            'fiscal_day': (r.get(COL['fiscal_day']) or '').strip() or None,
            'fiscal_month': to_fiscal_month(r.get(COL['fiscal_day'])),
            'capital': _to_int(r.get(COL['capital'])),
            'corporate_number': (r.get(COL['corporate_number']) or '').strip() or None,
            'listed': (r.get(COL['listed']) or '').strip() or None,
            'consolidated': (r.get(COL['consolidated']) or '').strip() or None,
            'submitter_type': (r.get(COL['submitter_type']) or '').strip() or None,
        }
    return list(out.values())


# 一覧が壊れて返ってきたときに、既存の対応表を消さないための下限。
# 実測3,821件なので、半分を切ったら取得がおかしいと見なす。
MIN_EXPECTED_ROWS = 1500


def sync(client=None, rows=None):
    """一覧を取り込む。{'fetched': n, 'saved': n} を返す。

    ⚠️ **件数が極端に少ないときは書き込まない。** 一覧が一時的に空や
       エラーページで返ってきたときに、対応表を壊さないため
       （空データで正常値を消さない、はこのリポジトリで何度も踏んでいる）。
    """
    from datetime import datetime, timezone

    if client is None:
        from supabase_client import get_supabase_client
        client = get_supabase_client()

    if rows is None:
        rows = to_rows(download())
    if len(rows) < MIN_EXPECTED_ROWS:
        raise ValueError(
            'EDINETコード一覧が%d件しかありません（通常3,800件前後）。'
            '取得がおかしいので取り込みません。' % len(rows))

    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    for i in range(0, len(rows), 500):
        chunk = [dict(r, updated_at=now) for r in rows[i:i + 500]]
        client.table('edinet_codes').upsert(
            chunk, on_conflict='company_code').execute()
        saved += len(chunk)
    return {'fetched': len(rows), 'saved': saved}


_FISCAL_CACHE = {'at': None, 'map': {}}

# 引き当て表を作り直す間隔（秒）。有報は年1回しか変わらないので長くてよい。
FISCAL_CACHE_SECONDS = 6 * 3600


def authoritative_fiscal_months(client=None, force=False):
    """{company_code: 決算月} を返す。有報の対象決算期がいちばん強い証拠。

    ⚠️ 銘柄ごとに引くと一括分析で200回問い合わせることになる。
       全件を一度読んでプロセス内に持つ（有報は年1回しか変わらない）。
    """
    import time

    now = time.time()
    if (not force and _FISCAL_CACHE['at']
            and now - _FISCAL_CACHE['at'] < FISCAL_CACHE_SECONDS):
        return _FISCAL_CACHE['map']

    if client is None:
        from supabase_client import get_supabase_client
        client = get_supabase_client()

    out, start = {}, 0
    try:
        while True:
            page = (client.table('edinet_codes')
                    .select('company_code, fiscal_month, report_period_end')
                    .range(start, start + 999).execute().data or [])
            for r in page:
                period = str(r.get('report_period_end') or '')
                month = None
                if len(period) >= 7 and period[4] == '-':
                    try:
                        month = int(period[5:7])
                    except ValueError:
                        month = None
                # 有報が無い会社は提出者一覧の決算日で代用する
                if not month:
                    month = r.get('fiscal_month')
                if month and 1 <= int(month) <= 12:
                    out[r['company_code']] = int(month)
            if len(page) < 1000:
                break
            start += 1000
    except Exception as e:
        # ⚠️ 引けなくても分析は止めない。最頻値に落ちるだけ。
        print('決算月の引き当て表を作れませんでした: %s' % str(e)[:120])
        return _FISCAL_CACHE['map']

    _FISCAL_CACHE['at'] = now
    _FISCAL_CACHE['map'] = out
    return out


def authoritative_fiscal_month(company_code, client=None):
    """1銘柄ぶん。無ければ None（呼び出し側は最頻値に落とす）。"""
    return authoritative_fiscal_months(client).get(str(company_code))


def fiscal_month_mismatches(client=None):
    """決算月の食い違いのうち、**まだ決着がつかないもの**を返す（上書きはしない）。

    三者を突き合わせる:
        ours   … screened_latest（yfinanceの決算日の最頻値）
        edinet … edinet_codes（EDINETの提出者一覧の決算日）
        report … 有報の対象決算期（**会社が自分で出した一次情報。いちばん強い**）

    ⚠️ **有報と一致しているものを数えない。** 決着がついているのに数え続けると、
       何をしても減らない件数になり、やがて誰も見なくなる。
       実測（2026-09-03）では44件のうち37件が「提出者一覧が正しく、こちらが
       決算期変更を追えていなかった」、7件が「こちらが正しく、提出者一覧が古い」で、
       **判断が要るものは0件**だった。

    返す各行: company_code / company_name / ours / edinet / report
    """
    if client is None:
        from supabase_client import get_supabase_client
        client = get_supabase_client()

    report = authoritative_fiscal_months(client, force=True)

    edi = {}
    start = 0
    while True:
        page = (client.table('edinet_codes')
                .select('company_code, fiscal_month')
                .range(start, start + 999).execute().data or [])
        # dictの update は使わない。**DBへの書き込みと見分けがつかなくなる**
        # （「上書きしていないこと」を確かめるテストが、これを拾って落ちた）
        for r in page:
            edi[r['company_code']] = r['fiscal_month']
        if len(page) < 1000:
            break
        start += 1000

    out, start = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, company_name, fiscal_month, delisted_at')
                .range(start, start + 999).execute().data or [])
        for r in page:
            if r.get('delisted_at'):
                continue
            code = r['company_code']
            mine, theirs = r.get('fiscal_month'), edi.get(code)
            if not (mine and theirs) or int(mine) == int(theirs):
                continue
            truth = report.get(code)
            # 有報がこちらを支持しているなら、提出者一覧が古いだけ。決着済み。
            if truth and int(truth) == int(mine):
                continue
            out.append({'company_code': code,
                        'company_name': r.get('company_name'),
                        'ours': int(mine), 'edinet': int(theirs),
                        'report': int(truth) if truth else None})
        if len(page) < 1000:
            break
        start += 1000
    return out
