"""値が無い項目について、その理由を分類する。

なぜ必要か:
    画面はこれまで、値が無ければ一律に `---` か「取得できていません」と出していた。
    しかし実データを数えると、欠損の大半は取得の失敗ではない。

        PER欠損 244件 → 赤字213件 / ETF等21件 / 本当に取得元に無い数件
        財務履歴なし 75件 → ほぼETF・投信・コモディティ連動型

    赤字企業のPERは「取得できなかった」のではなく、計算式として存在しない。
    ETFに決算が無いのも取得元のせいではない。ここを一律に
    「Yahooにデータなし」と書くと、事実と違ううえに誤解を広げる。

    Company Note は「賢くなった」が見えるアプリなので、
    ここは正しく「その指標は存在しない」と伝える場面にする。

使う側:
    - report_builder … レポートの data_quality.omitted_items
    - app            … 銘柄詳細へ返す omissions

    分類の真実をここ1箇所に置き、両方から参照する。
"""

import json

# 取得そのものに失敗した状態（source_status の status 値）
FETCH_FAILED_STATUSES = {
    'rate_limited', 'timeout', 'error', 'source_error',
    'network_error', 'parse_error', 'circuit_open',
}

# 画面に出す文言。取得元の名前を出すのは、本当に取得元の問題のときだけ。
MESSAGES = {
    'loss_making': '赤字のため算出できません',
    'negative_equity': '純資産がマイナスのため算出できません',
    'no_financials': 'ETF・投資信託などのため財務指標はありません',
    'not_a_share': '株式ではないため、一株あたりの指標はありません',
    'not_applicable': '制度上、対象外です',
    'not_disclosed': '会社が公表していません',
    'no_data': '取得元に収録されていません',
    'fetch_failed': '取得に失敗しました',
    'skipped': 'まだ取得していません',
    'not_attempted': 'まだ取得していません',
    'unknown': '値がありません',
}

# DBの列名と、この分類で使う項目名の対応。
# PERの列名は per_forward だが中身は trailing 優先で、名前と実体がずれている。
# ここでは per として扱い、値を読むときだけ列名に戻す。
COLUMN_FOR_FIELD = {'per': 'per_forward'}

# 項目 → source_status のどのキーを見るか
ITEM_SOURCE_KEYS = {
    'per': 'financials', 'pbr': 'financials', 'eps': 'financials',
    'dps': 'financials', 'dividend_yield': 'financials',
    'equity_ratio': 'financials', 'operating_margin': 'financials',
    'cash': 'financials', 'current_liabilities': 'financials',
    'current_ratio': 'financials', 'operating_cf': 'financials',
    'payout_ratio': 'financials', 'market_cap': 'financials',
    'stock_price': 'financials',
    'margin_trading_ratio': 'margin_trading',
    'major_shareholders_jp': 'holders_officers',
    'company_officers': 'holders_officers',
    'business_summary': 'business_summary',
    'established': 'company_profile_dates',
    'listing_date': 'company_profile_dates',
    'industry_jp': 'yahoo_jp_profile',
}


def _as_obj(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value or {}


def _series(history, key):
    rows = (history or {}).get(key) or []
    return [r for r in rows
            if isinstance(r, dict) and r.get('value') is not None]


def _latest(history, key):
    rows = _series(history, key)
    return max(rows, key=lambda r: r['date']) if rows else None


def has_no_financials(row, financial_history=None):
    """財務諸表が丸ごと無いか。ETF・投信・コモディティ連動型がこれに当たる。"""
    history = financial_history if financial_history is not None else _as_obj(
        row.get('financial_history'))
    return not (_series(history, 'net_income') or _series(history, 'revenue')
                or _series(history, 'eps'))


def is_pro_market(row):
    """TOKYO PRO Market銘柄か。信用取引の対象外。"""
    market = (row.get('market') or '') + (row.get('market_segment') or '')
    return 'PRO' in market.upper()


def classify(field, row, financial_history=None, source_status=None):
    """値が無い理由を返す。

    Returns:
        {'status': 分類, 'message': 画面に出す文言,
         'source': 取得元（取得元の問題のときだけ）, 'detail': 補足}

    判定の順番が大事。指標として存在しないものを、取得の問題として扱わない。
    """
    history = financial_history if financial_history is not None else _as_obj(
        row.get('financial_history'))
    status_root = source_status if source_status is not None else _as_obj(
        row.get('source_status'))

    def _result(status, source=None, detail=None):
        return {'status': status, 'message': MESSAGES[status],
                'source': source, 'detail': detail}

    # 1. 制度上そもそも存在しないもの
    if field == 'margin_trading_ratio' and is_pro_market(row):
        return _result('not_applicable',
                       detail='TOKYO PRO Marketは信用取引の対象外')

    # 2. 商品の性質上、決算が無いもの（ETF・投信など）
    if has_no_financials(row, history):
        if field in ('per', 'pbr', 'eps', 'dps', 'payout_ratio',
                     'equity_ratio', 'operating_margin', 'current_ratio',
                     'cash', 'current_liabilities', 'operating_cf'):
            return _result('no_financials')

    # 3. 指標の定義上、計算できないもの
    if field in ('per', 'eps'):
        eps = _latest(history, 'eps')
        net_income = _latest(history, 'net_income')
        if eps and eps['value'] <= 0:
            return _result('loss_making')
        if field == 'per' and not eps and net_income and net_income['value'] <= 0:
            return _result('loss_making')
        if field == 'per' and not eps and net_income:
            # 純利益はあるのにEPSが作れない＝株数が取れていない
            return _result('no_data', source='Yahoo Finance (yfinance)',
                           detail='一株益の分母になる株数が取得元に無い')

    if field == 'pbr':
        bps = _latest(history, 'bps')
        if bps and bps['value'] <= 0:
            return _result('negative_equity')
        if not bps:
            return _result('no_data', source='Yahoo Finance (yfinance)',
                           detail='一株純資産の算出に必要な純資産または株数が取得元に無い')

    # 4. 取得元の状態から判断する
    entry = (status_root or {}).get(ITEM_SOURCE_KEYS.get(field) or '') or {}
    status = entry.get('status')
    source = entry.get('source')

    if not entry:
        return _result('not_attempted')
    if status == 'not_disclosed':
        return _result('not_disclosed', source=source)
    if status in ('skipped', 'disabled'):
        return _result('skipped', source=source,
                       detail=entry.get('reason'))
    if status in FETCH_FAILED_STATUSES:
        return _result('fetch_failed', source=source,
                       detail=entry.get('reason') or entry.get('error'))
    # success なのに値が無い＝その取得元にこの項目だけ無かった
    return _result('no_data', source=source)


# スカラー列が空でも履歴側に値があり、画面には表示されている項目。
# ここを見ないと「表示されているのに『未取得』と出る」矛盾が起きる。
_HISTORY_BACKED = {
    'cash': ('cf', 'cash'),
    'current_liabilities': ('cf', 'current_liabilities'),
    'current_assets': ('cf', 'current_assets'),
    'operating_cf': ('cf', 'operating_cf'),
    'equity_ratio': ('cf', 'equity_ratio'),
    'payout_ratio': ('fin', 'payout_ratio'),
    'eps': ('fin', 'eps'),
    'dps': ('fin', 'dps'),
}


def _shown_from_history(field, financial_history, cf_history):
    """履歴から値を出せる項目か。流動比率は流動資産と流動負債から算出する。"""
    if field == 'current_ratio':
        return bool(_series(cf_history, 'current_assets')
                    and _series(cf_history, 'current_liabilities'))
    source = _HISTORY_BACKED.get(field)
    if not source:
        return False
    history = cf_history if source[0] == 'cf' else financial_history
    return bool(_series(history, source[1]))


def classify_missing_fields(row, fields):
    """値が無い項目だけをまとめて分類する。値がある項目は返さない。"""
    history = _as_obj(row.get('financial_history'))
    cf_history = _as_obj(row.get('cf_history'))
    status_root = _as_obj(row.get('source_status'))

    result = {}
    for field in fields:
        value = row.get(COLUMN_FOR_FIELD.get(field, field))
        if isinstance(value, str):
            value = value.strip()
            if value in ('[]', 'null'):
                value = None
        if value not in (None, '', [], {}):
            continue
        # スカラー列は空でも履歴から表示できているなら欠損ではない
        if _shown_from_history(field, history, cf_history):
            continue
        result[field] = classify(field, row, history, status_root)
    return result
