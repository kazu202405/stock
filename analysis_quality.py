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
