"""PER・PBRの推移を、手元のデータだけで組み立てる。

背景:
    screened_latest が持つ PER / PBR は現在値の1点だけで、履歴が無かった。
    外部サイトから取ってきても各社が載せているのは同じく現在値なので、
    履歴は「株価の履歴 × 決算期ごとのEPS/BPS」で自分で作る。

        PER = その日の株価 ÷ その時点で公表済みの EPS
        PBR = その日の株価 ÷ その時点で公表済みの BPS

    材料はすべてDB内にあるため、外部取得は発生しない。

    - 株価:   stock_price_history.daily_1y / weekly_10y / monthly_10y
    - EPS:    screened_latest.financial_history.eps
    - BPS:    screened_latest.financial_history.bps

決算発表ラグ:
    決算期末の翌日に市場がその数字を知っているわけではない。東証は決算短信を
    決算期末後45日以内に開示するよう要請しているため、ここでは決算期末 + 45日
    を「その数字が使えるようになる日」として扱う。これをしないと、実際には
    公表前だった利益で過去のPERを計算してしまう。
"""

from datetime import datetime, timedelta, timezone

# 東証が要請する決算短信の開示期限（決算期末後45日以内）
DISCLOSURE_LAG_DAYS = 45

# 指標として意味を成さない値は出さない。株価が数十円で分母がほぼゼロの銘柄で
# PERが数万倍になり、グラフが読めなくなるのを防ぐ。
MAX_REASONABLE_PER = 300.0
MAX_REASONABLE_PBR = 50.0


def _to_date(value):
    """'2026-03-31' や UNIX秒を date にそろえる"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).date()
    text = str(value)[:10]
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


def _effective_periods(series):
    """[{date, value}] を「使えるようになった日」順に並べ替える。

    決算期末ではなく、決算期末+45日を有効日とする。
    """
    periods = []
    for row in series or []:
        if not isinstance(row, dict) or row.get('value') in (None, ''):
            continue
        fiscal_end = _to_date(row.get('date'))
        if fiscal_end is None:
            continue
        try:
            value = float(row['value'])
        except (TypeError, ValueError):
            continue
        periods.append({
            'fiscal_end': fiscal_end,
            'available_from': fiscal_end + timedelta(days=DISCLOSURE_LAG_DAYS),
            'value': value,
        })
    periods.sort(key=lambda p: p['available_from'])
    return periods


def _value_known_at(periods, on_date):
    """その日時点で公表済みの、最も新しい決算期の値を返す"""
    known = None
    for period in periods:
        if period['available_from'] <= on_date:
            known = period
        else:
            break
    return known


def build_valuation_history(price_points, eps_series=None, bps_series=None):
    """株価履歴とEPS/BPSからPER・PBRの推移を組み立てる。

    Args:
        price_points: [{time: UNIX秒, close: 終値}, ...]（stock_price_historyの形）
        eps_series:   [{date, value}]（financial_history.eps）
        bps_series:   [{date, value}]（financial_history.bps）

    Returns:
        {'points': [{date, price, per, pbr, eps, bps, fiscal_end}],
         'has_per': bool, 'has_pbr': bool, 'disclosure_lag_days': int}

    分母が無い日・分母が0以下の日は per/pbr を None にする（推測しない）。
    赤字（EPSがマイナス）の期間もPERは None。指標として存在しないため。
    """
    eps_periods = _effective_periods(eps_series)
    bps_periods = _effective_periods(bps_series)

    points = []
    has_per = has_pbr = False

    for raw in price_points or []:
        if not isinstance(raw, dict):
            continue
        on_date = _to_date(raw.get('time') if raw.get('time') is not None
                           else raw.get('date'))
        close = raw.get('close', raw.get('c'))
        if on_date is None or close in (None, ''):
            continue
        try:
            price = float(close)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        eps_period = _value_known_at(eps_periods, on_date)
        bps_period = _value_known_at(bps_periods, on_date)

        per = None
        if eps_period and eps_period['value'] > 0:
            candidate = price / eps_period['value']
            if candidate <= MAX_REASONABLE_PER:
                per = round(candidate, 2)
                has_per = True

        pbr = None
        if bps_period and bps_period['value'] > 0:
            candidate = price / bps_period['value']
            if candidate <= MAX_REASONABLE_PBR:
                pbr = round(candidate, 3)
                has_pbr = True

        points.append({
            'date': on_date.isoformat(),
            'price': price,
            'per': per,
            'pbr': pbr,
            'eps': eps_period['value'] if eps_period else None,
            'bps': bps_period['value'] if bps_period else None,
            'fiscal_end': (eps_period or bps_period or {}).get('fiscal_end').isoformat()
            if (eps_period or bps_period) else None,
        })

    return {
        'points': points,
        'has_per': has_per,
        'has_pbr': has_pbr,
        'disclosure_lag_days': DISCLOSURE_LAG_DAYS,
    }


def summarize(history):
    """レンジと現在値だけを取り出す。「今は割高か割安か」を過去比で見るため。"""
    def _stats(key):
        values = [p[key] for p in history['points'] if p[key] is not None]
        if not values:
            return None
        return {
            'min': min(values),
            'max': max(values),
            'avg': round(sum(values) / len(values), 2),
            'latest': values[-1],
            'count': len(values),
        }

    return {'per': _stats('per'), 'pbr': _stats('pbr')}
