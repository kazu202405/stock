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


def analysis_data_status(financial_history, cf_history) -> str:
    """主要な財務履歴とCF履歴が両方取れたときだけfreshとする。"""
    return ('fresh' if history_has_values(financial_history)
            and history_has_values(cf_history) else 'stale')


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
