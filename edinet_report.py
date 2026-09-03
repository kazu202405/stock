# -*- coding: utf-8 -*-
"""有価証券報告書（EDINET公式API・CSV形式）から役員・大株主を取り出す。

なぜ要るか:
  役員は52.1%、英語の大株主は0.6%しか埋まっていない。取得元が
  yahooquery / J-LiC / Strainer で、日本の中小型株をそもそも収録していない。
  有報には**構造化されて**入っているので、公式から取れば埋まる。

⚠️ **「（議案）」の行を混ぜないこと。**
   有報には「現在の役員」と「株主総会に諮る予定の役員（議案）」の両方が載る。
   しかも**同じコンテキストID**を共有するので、コンテキストで分けられない。
   項目名の末尾で判定するしかない。混ぜると全員が二重になり、さらに
   まだ就任していない人が現任として出る（トヨタは議案側にだけ次期社長が居た）。

⚠️ **設立日は取らない。** 沿革のテキストには子会社の設立が大量に混ざっていて
   （「1940年３月豊田製鋼㈱…設立」）、自社の創立と区別できない。
   専用の要素も無いので、ここでは扱わない。

⚠️ **CSVの文字コードは UTF-16・タブ区切り。** cp932 で読むと全滅する。
"""

from __future__ import annotations

import csv
import io
import re
import zipfile

# 有価証券報告書の書類種別コード
DOC_TYPE_ANNUAL_REPORT = '120'

# 大株主の割合は小数（0.1280）で入っている。画面は%表示なので100倍する。
RATIO_TO_PERCENT = 100

PROPOSAL_MARK = '（議案）'


def _clean_name(value):
    """氏名の間に入る全角スペースを詰める。

    有報は「豊  田  章  男」のように字間を空けて書くことが多い。
    そのまま出すと検索にも表示にも噛み合わない。
    """
    text = (value or '').strip()
    text = text.replace('　', ' ')
    text = re.sub(r'\s+', ' ', text)
    # 1文字ずつ空けているだけの並び（「豊 田 章 男」）は詰める
    parts = text.split(' ')
    if len(parts) >= 3 and all(len(p) == 1 for p in parts):
        return ''.join(parts)
    return text


def _to_number(value):
    text = (value or '').strip().replace(',', '')
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_csv(zip_bytes):
    """有報ZIPの中の本表CSVを行（dict）にして返す。

    ⚠️ 監査報告書（jpaud-）ではなく本表（jpcrp-）を読むこと。
    """
    archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = [n for n in archive.namelist()
             if n.lower().endswith('.csv') and 'jpcrp' in n]
    if not names:
        raise ValueError('本表CSVが入っていません: %s' % archive.namelist())
    text = archive.read(names[0]).decode('utf-16')
    return list(csv.DictReader(io.StringIO(text), delimiter='\t'))


def _is_current(item_name):
    """現任の行か（議案の行でないか）。"""
    return PROPOSAL_MARK not in (item_name or '')


def _has_current_officers(rows):
    """現任（議案でない）の氏名の行があるか。"""
    for r in rows:
        name = r.get('項目名') or ''
        if name.startswith('氏名、役員の状況') and _is_current(name):
            return True
    return False


def extract_officers(rows):
    """役員を [{name_jp, title_jp, yearBorn, age}] で返す。

    画面（updateCompanyOfficers）は name_jp / title_jp / age を読む。
    既存の英語データ（yahooquery由来）と同じ列に入るので、キーを合わせる。
    """
    from datetime import date

    # ⚠️ **現任の行が1つも無く、（議案）の行しか無い会社がある。**
    #    トヨタは現任と議案の両方を持つが、福山通運・カバーなどは議案だけ。
    #    議案を一律に落とすと、そういう会社は**役員0人**になる（実測で6社）。
    #    ∴ 現任があればそれだけを使い、無いときに限って議案を使う。
    #    混ぜないこと。混ぜると、まだ就任していない人が現任として並ぶ。
    use_proposal = not _has_current_officers(rows)

    by_ctx = {}
    order = []
    for r in rows:
        name = r.get('項目名') or ''
        if not name.startswith(('氏名、役員の状況', '役職名、役員の状況',
                                '生年月日、役員の状況')):
            continue
        if not use_proposal and not _is_current(name):
            continue                      # ⚠️ 議案は現任ではない
        ctx = r.get('コンテキストID') or ''
        if ctx not in by_ctx:
            by_ctx[ctx] = {}
            order.append(ctx)
        value = (r.get('値') or '').strip()
        if name.startswith('氏名、'):
            by_ctx[ctx]['name_jp'] = _clean_name(value)
        elif name.startswith('役職名、'):
            by_ctx[ctx]['title_jp'] = _clean_name(value)
        elif name.startswith('生年月日、'):
            by_ctx[ctx]['born'] = value

    out = []
    today = date.today()
    for ctx in order:
        o = by_ctx[ctx]
        if not o.get('name_jp'):
            continue                      # 議案にしか居ない人はここで落ちる
        row = {'name_jp': o['name_jp'], 'title_jp': o.get('title_jp') or ''}
        born = o.get('born') or ''
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})', born)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            row['yearBorn'] = y
            row['age'] = today.year - y - ((today.month, today.day) < (mo, d))
        out.append(row)
    return out


def extract_major_holders(rows):
    """大株主を [{name, shares, ratio}] で返す（既存の列と同じ形）。

    ratio は%（有報は 0.1280 のような小数で持っているので100倍する）。
    """
    by_ctx = {}
    order = []
    for r in rows:
        ctx = r.get('コンテキストID') or ''
        if 'Major' not in ctx:
            continue
        name = r.get('項目名') or ''
        if ctx not in by_ctx:
            by_ctx[ctx] = {}
            order.append(ctx)
        value = (r.get('値') or '').strip()
        if name.startswith('氏名又は名称、大株主'):
            by_ctx[ctx]['name'] = _clean_name(value)
        elif name.startswith('所有株式数'):
            by_ctx[ctx]['shares'] = _to_number(value)
        elif '所有株式数の割合' in name or name.startswith('発行済株式'):
            ratio = _to_number(value)
            if ratio is not None:
                by_ctx[ctx]['ratio'] = round(ratio * RATIO_TO_PERCENT, 2)

    out = []
    for ctx in order:
        h = by_ctx[ctx]
        if not h.get('name'):
            continue
        row = {'name': h['name']}
        if h.get('shares') is not None:
            row['shares'] = int(h['shares'])
        if h.get('ratio') is not None:
            row['ratio'] = h['ratio']
        out.append(row)
    return out


def extract_employees(rows):
    """従業員数（連結）。取れなければ None。"""
    for r in rows:
        if (r.get('項目名') or '').strip() == '従業員数':
            n = _to_number(r.get('値'))
            if n is not None:
                return int(n)
    return None


def extract(zip_bytes):
    """有報ZIPから、保存したいものをまとめて取り出す。"""
    rows = read_csv(zip_bytes)
    return {
        'company_officers': extract_officers(rows),
        'major_shareholders_jp': extract_major_holders(rows),
        'employees': extract_employees(rows),
    }
