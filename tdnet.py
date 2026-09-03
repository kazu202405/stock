# -*- coding: utf-8 -*-
"""TDnet（適時開示）の決算短信から、会社が出した通期予想を取り出す。

なぜ作ったか:
  業績予想（売上・営業益）の取得元が Yahoo!ファイナンス日本版のHTMLだけで、
  いちばん脆い経路に乗っていた（充足率83.6%）。決算短信は会社が自分で出す
  一次情報で、TDnet が**ログイン不要・無料**で配っている。

⚠️ **公開されているのは直近31日ぶんだけ。** 過去に遡っての一括取得はできない
   （そこは有料サービスの領域）。∴ **毎日拾い続ける運用が前提**。
   取りこぼした日は取り返せないので、決算検知の cron に相乗りさせる。

## 短信サマリーの読み方（2026-09-03 に実物27本から確かめた）

zip の中の `XBRLData/Summary/...-ixbrl.htm` が短信サマリー。
inline XBRL なので `<ix:nonFraction name="..." contextRef="...">` を読む。

**通期予想のコンテキストはこの形だけ**:

    CurrentYearDuration_ConsolidatedMember_ForecastMember   四半期短信の今期通期予想
    NextYearDuration_ConsolidatedMember_ForecastMember      本決算短信の来期（＝新しい今期）予想
    （連結が無い会社は NonConsolidatedMember）

⚠️ **似て非なるコンテキストが多い。前方一致で拾うと別の数字が混ざる。**
   - `CurrentAccumulatedQ2Duration_...` … 中間期の予想（通期ではない）
   - `..._FirstQuarterMember_...` `..._YearEndMember_...` `..._AnnualMember_...`
     … **配当予想の期別内訳**。実測で DividendPerShare が最多(65回)を占めるのはこれ
   ∴ コンテキストは**完全一致**で見る。

## 要素名（実物で確認したものだけを入れている）

    売上   NetSales / OperatingRevenues（銀行・鉄道・不動産等の営業収益）/ SalesIFRS
    営業益 OperatingIncome / OperatingIncomeIFRS
    経常益 OrdinaryIncome
    純利益 ProfitAttributableToOwnersOfParent / NetIncome（非連結）/ IFRS版

⚠️ 推測で足さないこと。見たことのない名前を入れると、合っているのか
   分からないまま数字が入る。増やすときは実物で確かめてから。
"""

from __future__ import annotations

import html as html_module
import io
import re
import zipfile
from datetime import date

import requests

BASE = 'https://www.release.tdnet.info/inbs/'
UA = {'User-Agent': 'Mozilla/5.0'}
TIMEOUT = 60

# 通期予想のコンテキスト（完全一致）。連結を優先し、無ければ非連結。
FULL_YEAR_CONTEXTS = (
    'NextYearDuration_ConsolidatedMember_ForecastMember',
    'CurrentYearDuration_ConsolidatedMember_ForecastMember',
    'NextYearDuration_NonConsolidatedMember_ForecastMember',
    'CurrentYearDuration_NonConsolidatedMember_ForecastMember',
)

FIELD_FOR_ELEMENT = {
    'NetSales': 'forecast_revenue',
    'OperatingRevenues': 'forecast_revenue',
    'SalesIFRS': 'forecast_revenue',
    'NetSalesIFRS': 'forecast_revenue',
    'OperatingIncome': 'forecast_op_income',
    'OperatingIncomeIFRS': 'forecast_op_income',
    'OrdinaryIncome': 'forecast_ordinary_income',
    'ProfitAttributableToOwnersOfParent': 'forecast_net_income',
    'ProfitAttributableToOwnersOfParentIFRS': 'forecast_net_income',
    'NetIncome': 'forecast_net_income',
}

_IX = re.compile(r'<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>', re.S | re.I)
_CONTEXT = re.compile(
    r'<xbrli:context\b[^>]*id="([^"]+)"(.*?)</xbrli:context>', re.S | re.I)
_END_DATE = re.compile(r'<xbrli:endDate>([^<]+)</xbrli:endDate>', re.I)

# 一覧の1行。コード・会社名・表題・添付zip。
_ROW = re.compile(r'<tr.*?</tr>', re.S)
_CELL = re.compile(r'<td[^>]*>(.*?)</td>', re.S)


def _text(fragment: str) -> str:
    return html_module.unescape(re.sub('<[^>]+>', '', fragment)).strip()


def _attr(attrs: str, key: str):
    m = re.search(key + r'="([^"]*)"', attrs)
    return m.group(1) if m else None


def list_url(day: date) -> str:
    return BASE + 'I_list_001_%s.html' % day.strftime('%Y%m%d')


def parse_list(html: str) -> list:
    """一覧HTMLを [{code, name, title, zip}] にする。"""
    out = []
    for tr in _ROW.findall(html or ''):
        cells = _CELL.findall(tr)
        if len(cells) < 4:
            continue
        zips = re.findall(r'href="([^"]+\.zip)"', tr)
        code = _text(cells[1])
        if not code:
            continue
        out.append({
            'time': _text(cells[0]),
            'code': code,
            'name': _text(cells[2]),
            'title': _text(cells[3]),
            'zip': zips[0] if zips else None,
        })
    return out


def fetch_list(day: date, session=None) -> list:
    """その日の開示一覧。取れなければ例外。"""
    get = (session or requests).get
    res = get(list_url(day), timeout=TIMEOUT, headers=UA)
    res.raise_for_status()
    res.encoding = res.apparent_encoding or 'utf-8'
    return parse_list(res.text)


def is_earnings_report(title: str) -> bool:
    """決算短信の行か。

    ⚠️ REITは除く。営業収益の体系が違い、アプリの分析対象でもない。
    ⚠️ 訂正版も対象にする。数値が直っていることがあるので、後から来た方を採る。
    """
    t = title or ''
    if '決算短信' not in t:
        return False
    return 'REIT' not in t and 'ＲＥＩＴ' not in t


def _four_digit(code: str):
    """一覧の5桁コード（末尾は種類）を4桁の銘柄コードにする。

    ⚠️ 5桁のまま使わない。screened_latest の company_code は4桁。
    """
    code = (code or '').strip()
    return code[:-1] if len(code) == 5 else code


def summary_html(zip_bytes: bytes):
    """zip から短信サマリーのinline XBRLを取り出す。無ければ None。"""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = [n for n in zf.namelist()
             if '/Summary/' in n and n.endswith('-ixbrl.htm')]
    if not names:
        return None
    return zf.read(names[0]).decode('utf-8', 'replace')


def _context_end_dates(html: str) -> dict:
    out = {}
    for cid, body in _CONTEXT.findall(html or ''):
        m = _END_DATE.search(body)
        if m:
            out[cid] = m.group(1).strip()
    return out


def extract_forecast(html: str) -> dict:
    """短信サマリーから通期予想を返す。単位は**億円**（既存の列に合わせる）。

    Returns:
        {} … 通期予想が載っていない（四半期短信で予想を再掲しない会社がある）
        {'forecast_revenue': 億円, ..., 'forecast_year': '2027-03-31'}
    """
    if not html:
        return {}
    ends = _context_end_dates(html)

    # 連結（前2つ）を先に見て、無ければ非連結
    for context in FULL_YEAR_CONTEXTS:
        got = {}
        for m in _IX.finditer(html):
            attrs = m.group(1)
            if _attr(attrs, 'contextRef') != context:
                continue
            element = (_attr(attrs, 'name') or '').split(':')[-1]
            field = FIELD_FOR_ELEMENT.get(element)
            if not field or field in got:
                continue
            body = _text(m.group(2)).replace(',', '')
            # 「－」「-」だけの欄は未定。0 として入れない。
            try:
                value = float(body)
            except ValueError:
                continue
            scale = _attr(attrs, 'scale')
            try:
                value *= 10 ** int(scale) if scale else 1
            except ValueError:
                pass
            if _attr(attrs, 'sign') == '-':
                value = -value
            got[field] = value / 1e8          # 円 → 億円
        if got:
            if ends.get(context):
                got['forecast_year'] = ends[context]
            return got
    return {}


def fetch_forecast(zip_name: str, session=None) -> dict:
    """一覧のzip名から通期予想を取る。取れなければ {}。"""
    get = (session or requests).get
    url = zip_name if zip_name.startswith('http') else BASE + zip_name
    res = get(url, timeout=TIMEOUT, headers=UA)
    res.raise_for_status()
    return extract_forecast(summary_html(res.content))


def collect(day: date, session=None, sleep=0.4, log=print) -> list:
    """その日の決算短信から通期予想を集める。

    Returns: [{company_code, company_name, title, forecast:{...}}]
    """
    import time

    out = []
    for row in fetch_list(day, session):
        if not is_earnings_report(row['title']) or not row['zip']:
            continue
        try:
            forecast = fetch_forecast(row['zip'], session)
        except Exception as e:
            log('  %s %s 取得失敗: %s' % (row['code'], row['name'], str(e)[:80]))
            continue
        out.append({
            'company_code': _four_digit(row['code']),
            'company_name': row['name'],
            'title': row['title'],
            'forecast': forecast,
        })
        if sleep:
            time.sleep(sleep)
    return out
