"""分析結果の品質判定に使う小さな共通関数。

yfinance は一時的に財務諸表を空で返すことがある。空の履歴を正常データとして
保存すると、以前取得できていた履歴まで消えてしまうため、保存前にここで判定する。
"""

import json
import re


def history_has_values(history) -> bool:
    """履歴dictに、値を持つ行が1件以上ある場合だけTrueを返す。"""
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except (TypeError, ValueError):
            return False
    if not isinstance(history, dict):
        return False

    for rows in history.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get('value') is not None:
                return True
    return False


def history_json_or_none(history, converter=None):
    """有効な履歴だけJSON化する。空ならNoneを返し、呼び出し側で更新対象から外す。"""
    if not history_has_values(history):
        return None
    value = converter(history) if converter else history
    return json.dumps(value, ensure_ascii=False)


def build_cf_history(stock_data) -> dict:
    """保存する cf_history を組み立てる。**3つの保存パスで同じものを使う。**

    以前はこの辞書が app.py の3箇所に同じ形で書かれていた。項目を足すときに
    どれか1つを直し忘れると、通った保存パスによって銘柄ごとに項目の有無が
    変わる（実際に cash / current_liabilities がそうなっていた）。

    名前が「CF履歴」だが、中身は貸借対照表の年度推移も含む。
    """
    return {
        'operating_cf': stock_data.get('operating_cf', []),
        'investing_cf': stock_data.get('investing_cf', []),
        'financing_cf': stock_data.get('financing_cf', []),
        'cash': stock_data.get('cash', []),
        'current_liabilities': stock_data.get('current_liabilities_list', []),
        'current_assets': stock_data.get('current_assets_list', []),
        # 有利子負債・利益剰余金は流動負債とも現預金とも別の行の数字
        'interest_bearing_debt': stock_data.get('interest_bearing_debt', []),
        'retained_earnings': stock_data.get('retained_earnings', []),
        'equity_ratio': stock_data.get('equity_ratio_list', []),
        'roe': stock_data.get('roe', []),
        'roa': stock_data.get('roa', []),
    }


# 決算期の世代の呼び方。**列の名前とスコアの言い方がずれているので注意。**
#
#   cy … 直近の確定決算       1y … その1期前       2y … 2期前
#   ny … 今期の会社予想（forecast_revenue / forecast_op_income）
#
# スコアの12項目は「2期前→前期」「前期→今期予」と呼んでいるが、
# そこでいう「前期」は cy（直近の確定決算）のこと。対応は:
#   スコア「2期前→前期」  = revenue_growth_1y_cy
#   スコア「前期→今期予」  = revenue_growth_cy_ny
# 2026-08-25 に5銘柄で突き合わせ、スコアの算出値と一致することを確認済み。
GROWTH_COLUMNS = (
    'revenue_2y', 'revenue_1y', 'revenue_cy', 'revenue_ny',
    'op_2y', 'op_1y', 'op_cy', 'op_ny',
    'revenue_growth_2y_1y', 'revenue_growth_1y_cy', 'revenue_growth_cy_ny',
    'op_growth_2y_1y', 'op_growth_1y_cy', 'op_growth_cy_ny',
    'current_ratio',
)


def _series(history, key):
    """[{date, value}] を新しい順に。値の無い行は捨てる。"""
    rows = [r for r in (history.get(key) or [])
            if isinstance(r, dict) and r.get('value') is not None]
    return sorted(rows, key=lambda r: str(r.get('date') or ''), reverse=True)


def _growth(current, previous):
    """増減率(%)。分母が正でなければ判定不能。

    赤字や債務超過で分母がマイナスだと、増減率の符号が意味を失う
    （-10億→-5億 は「改善」だが式は -50% を返す）。スコアの
    evaluate_score_criteria と同じ扱いにそろえてある。
    """
    if current is None or previous is None or previous <= 0:
        return None
    return round((current - previous) / previous * 100, 2)


def derive_growth_columns(row) -> dict:
    """financial_history / cf_history から、絞り込みに使う派生列を作る。

    なぜ列に落とすか:
      スコアの判定は financial_history から都度計算しているので列が要らない。
      だがスクリーナーはDB側で絞るため、列が空だと「増収率10%以上」で
      探せない。2026-08-25 時点で成長率の列は**全銘柄で空**だった。

    ⚠️ ここで作るのは派生値なので、**元の値と一緒に動かすこと**。
       財務履歴を取り直したらこの列も作り直す。片方だけ更新すると、
       画面には正しい売上高と古い増減率が並ぶ（片方が正しいので壊れて見えない）。
    """
    financial = _json_obj(row.get('financial_history'))
    cf = _json_obj(row.get('cf_history'))

    out = {}
    for name, key in (('revenue', 'revenue'), ('op', 'op_income')):
        rows = _series(financial, key)
        values = [r['value'] for r in rows[:3]]          # 円
        cy, y1, y2 = (values + [None, None, None])[:3]
        forecast = row.get('forecast_revenue' if name == 'revenue'
                           else 'forecast_op_income')     # 億円
        ny = forecast * 1e8 if forecast is not None else None

        oku = lambda v: None if v is None else round(v / 1e8, 2)
        out['%s_cy' % name] = oku(cy)
        out['%s_1y' % name] = oku(y1)
        out['%s_2y' % name] = oku(y2)
        out['%s_ny' % name] = oku(ny)
        out['%s_growth_2y_1y' % name] = _growth(y1, y2)
        out['%s_growth_1y_cy' % name] = _growth(cy, y1)
        out['%s_growth_cy_ny' % name] = _growth(ny, cy)

    # 流動比率。**同じ決算期の値どうしでしか割らない。**
    # 期がずれた流動資産と流動負債を割ると、増資や大型返済のあった年に
    # 実態と違う比率が出る。
    assets = _series(cf, 'current_assets')
    liabilities = _series(cf, 'current_liabilities')
    current_ratio = None
    if assets and liabilities and assets[0]['date'] == liabilities[0]['date']             and liabilities[0]['value'] > 0:
        current_ratio = round(assets[0]['value'] / liabilities[0]['value'] * 100, 2)
    out['current_ratio'] = current_ratio

    return out


def _json_obj(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value or {}


def analysis_data_status(financial_history, cf_history) -> str:
    """主要な財務履歴とCF履歴が両方取れたときだけfreshとする。"""
    return ('fresh' if history_has_values(financial_history)
            and history_has_values(cf_history) else 'stale')


def derive_fiscal_month(financial_history, cf_history=None):
    """決算期の月(1-12)を財務履歴の決算日から求める。取れなければNone。

    決算"発表予定日"ではなく決算"期"の月であることに注意。発表予定日は無料で
    全銘柄を取れる取得元が未整理のため、ここでは扱わない。

    yfinanceの決算日は3月期なら 2026-03-31 のように期末日で入る。ただし配当
    (dps)の日付は権利確定日で決算期末とズレるため、損益・BS項目だけを見る。
    最新の決算日ではなく最頻値を採るのは、1年だけ決算期変更や不規則な日付が
    混ざっても引きずられないようにするため。
    """
    from collections import Counter

    def _rows(history):
        if isinstance(history, str):
            try:
                history = json.loads(history)
            except (TypeError, ValueError):
                return {}
        return history if isinstance(history, dict) else {}

    # dps / payout_ratio は権利確定日ベースなので決算期の判定には使わない
    usable_keys = ('revenue', 'op_income', 'ordinary_income', 'net_income', 'eps',
                   'operating_cf', 'investing_cf', 'financing_cf', 'cash',
                   'current_liabilities', 'current_assets')

    months = Counter()
    for history in (financial_history, cf_history):
        for key, rows in _rows(history).items():
            if key not in usable_keys or not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or row.get('value') is None:
                    continue
                date = str(row.get('date') or '')
                if len(date) >= 7 and date[4] == '-':
                    try:
                        month = int(date[5:7])
                    except ValueError:
                        continue
                    if 1 <= month <= 12:
                        months[month] += 1

    if not months:
        return None
    # 同数のときは小さい月に倒す（結果を実行ごとにブレさせないため）
    return min(months.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def normalize_analysis_symbol(symbol):
    """英数字の新証券コードを含む日本株コードに .T を付ける。

    AAPLのような4文字の米国ティッカーを誤判定しないよう、4文字中に数字を
    1文字以上含む場合だけ日本株とみなす。
    """
    value = (symbol or '').strip().upper()
    if value.endswith('.T'):
        return value
    if re.fullmatch(r'[0-9A-Z]{4}', value) and any(c.isdigit() for c in value):
        return f'{value}.T'
    return value


def serialize_data_source(source_status=None, primary='yfinance'):
    """既存TEXT列に、後方互換を保った取得元の診断情報を保存する。"""
    payload = {'primary': primary, 'sources': source_status or {}}
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def parse_data_source(value):
    """旧形式の 'yfinance' と新しいJSON形式の両方を読む。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            return {'primary': value, 'sources': {}}
    return {'primary': None, 'sources': {}}
