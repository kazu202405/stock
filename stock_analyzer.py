"""
株式データ取得・分析モジュール
Yahoo Finance APIを使用して株式情報を取得し、JSONとチャート画像を出力
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, Any, Optional, List
import time

import pandas as pd
import numpy as np
import yfinance as yf
import yahooquery as yq
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import rcParams


# 日本語フォント設定
rcParams['font.sans-serif'] = ['Yu Gothic', 'Meiryo', 'Hiragino Sans', 'MS Gothic']
rcParams['axes.unicode_minus'] = False


# ================================
# JPX公式企業リスト（static/companies.json）による会社名解決
# 新規上場コード（例: 156A / 367A）では yfinance / Yahoo!ファイナンス日本版が
# 会社名ではなく代表者名（人名）を返すことがあるため、公式リストを最優先で採用する。
# ================================
_JPX_NAME_MAP: Optional[Dict[str, str]] = None


def _load_jpx_name_map() -> Dict[str, str]:
    """static/companies.json を {証券コード: 会社名} の辞書として一度だけ読み込む"""
    global _JPX_NAME_MAP
    if _JPX_NAME_MAP is None:
        _JPX_NAME_MAP = {}
        try:
            path = os.path.join(os.path.dirname(__file__), "static", "companies.json")
            with open(path, "r", encoding="utf-8") as f:
                for item in json.load(f):
                    code = str(item.get("c", "")).strip()
                    name = (item.get("n") or "").strip()
                    if code and name:
                        _JPX_NAME_MAP[code] = name
        except Exception as e:
            print(f"companies.json 読み込みエラー: {e}")
    return _JPX_NAME_MAP


def _lookup_jpx_name(symbol: str) -> Optional[str]:
    """'367A.T' → 'プリモグローバルホールディングス' を JPX公式リストから解決（無ければ None）"""
    if not symbol:
        return None
    code = symbol[:-2] if symbol.endswith(".T") else symbol
    return _load_jpx_name_map().get(code)


def _classify_source_error(error) -> str:
    """外部取得ライブラリの例外文字列を画面用の共通理由に寄せる。"""
    text = str(error).lower()
    if '429' in text or 'too many requests' in text or 'rate limit' in text:
        return 'rate_limited'
    if 'timeout' in text or 'timed out' in text:
        return 'timeout'
    if '403' in text or 'forbidden' in text:
        return 'rate_limited'
    return 'error'


def _forecast_number(value) -> Optional[float]:
    """Yahoo埋め込みJSONの数値を安全にfloatへ寄せる。0や負数も有効値。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip().replace(',', '')
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def extract_yahoo_forecast_data(page_html: str) -> Dict[str, Any]:
    """Yahoo JapanのHTMLから会社予想を抽出し、非開示も区別する。

    Yahooは会社が予想を開示していない場合でも、forecastオブジェクトに
    決算期と更新日だけを入れる。そのケースを取得失敗として扱わない。
    """
    import html as html_module

    normalized = html_module.unescape(page_html or '').replace('\\"', '"')
    matches = re.finditer(r'"forecast"\s*:\s*\{[^}]*\}', normalized)
    period_hint = None

    field_map = {
        'forecast_revenue': 'netSales',
        'forecast_op_income': 'operatingIncome',
        'forecast_ordinary_income': 'ordinaryIncome',
        'forecast_net_income': 'netProfit',
    }

    for match in matches:
        fragment = match.group(0)
        try:
            forecast = json.loads('{' + fragment + '}').get('forecast', {})
        except json.JSONDecodeError:
            # 構造が少し崩れていてもforecast断片の外（実績値）を誤取得しない。
            forecast = {}
            for source in field_map.values():
                number_match = re.search(
                    rf'"{source}"\s*:\s*"?(-?[\d,]+(?:\.\d+)?)"?', fragment)
                if number_match:
                    forecast[source] = number_match.group(1)
            year_match = re.search(r'"yearEndDate"\s*:\s*"([^"]+)"', fragment)
            if year_match:
                forecast['yearEndDate'] = year_match.group(1)

        period_hint = forecast.get('yearEndDate') or period_hint
        parsed = {}
        for target, source in field_map.items():
            number = _forecast_number(forecast.get(source))
            if number is not None:
                parsed[target] = number / 1e8

        if parsed:
            if forecast.get('yearEndDate'):
                parsed['forecast_year'] = str(forecast['yearEndDate'])
            parsed['_forecast_status'] = 'success'
            return parsed

    if period_hint:
        return {
            '_forecast_status': 'not_disclosed',
            '_forecast_period': str(period_hint),
            '_forecast_reason': '会社が数値予想を開示していません',
        }
    return {
        '_forecast_status': 'no_data',
        '_forecast_reason': '業績予想データがページ内にありません',
    }


# 予想配当が「確定した年度の配当」から離れてよい倍率。
# これを外れたら、増配・減配ではなく単位や分割調整の取り違えを疑う。
# 2026-08-12 の事故（4918: 実際15円のところ150円＝10倍）はこの範囲で落ちる。
FORWARD_DPS_MIN_RATIO = 0.2
FORWARD_DPS_MAX_RATIO = 5.0

# 予想利回りとして採ってよい上限(%)。StockAnalyzer.MAX_DIVIDEND_YIELD と同じ値。
MAX_FORWARD_DIVIDEND_YIELD = 20.0


def forecast_annual_dividend(payments, fiscal_end_month, today=None):
    """支払い実績から予想年間配当（円）を出す。決算年度に分けて数える。

    **「直近1回 × 年間回数」だけでは破綻する。** 直近の支払いが
    〈前期を締めた期末配当〉のとき、その額を年換算しても意味が無い。
    日本企業は中間と期末で額が違うことが多く、実データで大きく外れた。

        7273 イクヨ   中間30円 + 期末3円 = 年33円
                      直近(期末3円)×2 = 6円  → 実態の1/5
        7505 扶桑電通 中間79.5円 + 期末7.5円 = 年87円
                      直近(期末7.5円)×2 = 15円 → 実態の1/6

        全3,127銘柄で確定年度と比べたところ、0.6倍未満が28件、
        1.6倍超が57件あった（2026-08-14 実測）。

    分け方:
      進行中の決算年度に支払いがまだ無い＝直近の支払いは前期を締めたもの。
      このときの最良の見積もりは**前期の年間配当そのもの**。
      支払いがあるなら、残りの回数分を直近の額で補う。

    引数:
        payments: [(日付文字列, 金額), ...]。順不同でよい。**分割調整済みのこと**。
        fiscal_end_month: 決算月（1〜12）。screened_latest.fiscal_month。
                          不明なら3月として扱う（日本企業の最多）。
    """
    if not payments:
        return None

    from datetime import datetime as _dt
    now = _dt.strptime(today, '%Y-%m-%d') if today else _dt.now()

    try:
        end_month = int(fiscal_end_month)
        if not 1 <= end_month <= 12:
            raise ValueError
    except (TypeError, ValueError):
        end_month = 3

    rows = []
    for date_str, value in payments:
        try:
            when = _dt.strptime(str(date_str)[:10], '%Y-%m-%d')
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if amount > 0 and when <= now:
            rows.append((when, amount))
    if not rows:
        return None

    # 決算年度に割り振る。stock_analyzer が配当を集計するときと同じ規則。
    by_fy = {}
    for when, amount in rows:
        fy = when.year if when.month <= end_month else when.year + 1
        by_fy.setdefault(fy, []).append((when, amount))

    current_fy = now.year if now.month <= end_month else now.year + 1
    in_progress = by_fy.get(current_fy, [])

    completed_years = sorted(y for y in by_fy if y < current_fy)
    if not completed_years:
        # 上場直後などで確定した年度がまだ無い。進行中の実績だけで年換算する
        if not in_progress:
            return None
        return round(sum(a for _, a in in_progress), 4)

    last_fy = completed_years[-1]
    last_fy_payments = by_fy[last_fy]
    last_fy_total = sum(a for _, a in last_fy_payments)

    if not in_progress:
        # 直近の支払いは前期を締めたもの。前期並みと見るのが素直
        return round(last_fy_total, 4)

    # 進行中の年度に支払いがある。残りは**前期の対応する回の額**で埋める。
    #
    # 「直近の額 × 残り回数」ではいけない。中間と期末で額が大きく違う
    # 会社を取り違える。
    #     7505 扶桑電通(9月決算): 中間7.5円・期末79.5円 = 年87円
    #     進行中は中間7.5円まで。期末も7.5円と見ると年15円になる
    # 前期の期末79.5円を当てれば 7.5 + 79.5 = 87円 になる。
    #
    # 前期からの増減は、ここまでに払った分の比で補正する
    # （増配していれば残りも増える前提）。
    in_progress.sort(key=lambda r: r[0])
    last_fy_payments.sort(key=lambda r: r[0])
    paid = sum(a for _, a in in_progress)

    same_slots = last_fy_payments[:len(in_progress)]
    remaining_slots = last_fy_payments[len(in_progress):]

    if remaining_slots:
        base = sum(a for _, a in same_slots)
        growth = (paid / base) if base > 0 else 1.0
        return round(paid + sum(a for _, a in remaining_slots) * growth, 4)

    # 前期に「残りの回」が無い＝前期の方が支払い回数が少ない。
    # 上場直後に多い（367A: 前期は期末105円のみ、当期は中間60円まで）。
    # 直近12か月の支払い件数から年間回数を見て、直近の額で埋める。
    from datetime import timedelta as _td
    trailing_count = len([1 for when, _ in rows if when >= now - _td(days=365)])
    expected_count = max(len(last_fy_payments), len(in_progress), trailing_count)
    remaining = expected_count - len(in_progress)
    if remaining <= 0:
        return round(paid, 4)
    latest_amount = in_progress[-1][1]
    return round(paid + latest_amount * remaining, 4)


def annualized_dividend_from_payments(payments, today=None):
    """支払い実績から予想年間配当（円）を出す。＝直近の1回 × 年間の支払い回数。

    **決算年度を見ないため、直近が期末配当だと大きく外れる。**
    `forecast_annual_dividend()` を使うこと。決算月が取れない場合の
    保険としてのみ残している。

    **Yahoo の `dividendRate` を使わない理由**（2026-08-14 に判明）:
      `info` の要約値は株式併合に追随していないことがある。`ticker.dividends`
      （支払い実績）は調整済みなので、両者が食い違う。

        5706 三井金属  dividendRate=28   実際の支払い 90 / 100 / 145円
        8377 ほくほく  dividendRate=15   実際の支払い 22.5 / 27.5 / 45 / 65円

      いずれも `lastDividendValue`（14 / 7.5）を2倍しただけの値で、
      併合前の額のまま止まっていた。比率で弾けば大きな取り違えは防げるが、
      1:2 の併合なら比率0.5で検証を通り抜ける。**調整済みのデータだけで
      計算すれば、この問題自体が起きない。**

    引数:
        payments: [(日付文字列, 金額), ...]。順不同でよい。分割調整済みのこと。

    回数を「直近12か月の支払い件数」で数えるのは、日本企業が中間・期末の
    年2回（一部は年1回）で払うため。直近1回を年換算するのは、増配後の
    水準を反映させるため（前期の合計だと増配前の数字になる）。
    """
    if not payments:
        return None

    from datetime import datetime as _dt, timedelta as _td
    now = _dt.strptime(today, '%Y-%m-%d') if today else _dt.now()
    one_year_ago = now - _td(days=365)

    rows = []
    for date_str, value in payments:
        try:
            when = _dt.strptime(str(date_str)[:10], '%Y-%m-%d')
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if amount > 0 and when <= now:
            rows.append((when, amount))

    recent = [r for r in rows if r[0] >= one_year_ago]
    if not recent:
        # 直近12か月に支払いが無い＝無配。0.0 ではなく None
        # （「配当を出していない」と「利回りが0%」は別物）
        return None

    recent.sort(key=lambda r: r[0])
    frequency = len(recent)
    latest_amount = recent[-1][1]
    return round(latest_amount * frequency, 4)


def trailing_dividend_yield_from_payments(payments, price, today=None):
    """実績配当利回り(%)＝直近12か月に実際に支払われた配当 ÷ 株価。

    `StockAnalyzer._trailing_dividend_yield` と同じ計算を、支払い実績の
    リストから行う版。バックフィルがバッチ取得した配当をそのまま渡せる
    ようにするために切り出した。**判定は1か所にまとめる**（分析側と
    バックフィルで違う値が出ると、どちらが正か分からなくなる）。

    無配（履歴はあるが直近12か月の支払いがゼロ）は 0.0 ではなく None。
    """
    if not payments or not price:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    from datetime import datetime as _dt, timedelta as _td
    now = _dt.strptime(today, '%Y-%m-%d') if today else _dt.now()
    one_year_ago = now - _td(days=365)

    total = 0.0
    for date_str, value in payments:
        try:
            when = _dt.strptime(str(date_str)[:10], '%Y-%m-%d')
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if amount > 0 and one_year_ago <= when <= now:
            total += amount

    if total <= 0:
        return None

    value = (total / price) * 100
    # 上場企業の年間利回りがこれを超えるのは、ほぼ分割・単位の取り違え。
    if value > MAX_FORWARD_DIVIDEND_YIELD:
        return None
    return round(value, 4)


def forward_dividend_yield(dps_forecast, price, confirmed_dps=None):
    """予想配当利回り(%)を出す。信用できない値は None を返す。

    **利回りは Yahoo の利回り値を使わず、必ずここで計算する。**
      - Yahoo の利回りは提供元によって小数だったり%だったりする
        （yfinance は 4.24、yahooquery は 0.0424）。単位を推測する分岐が
        2026-08-12 の「利回り47%」事故の原因だった。配当額（円）には
        この曖昧さが無いので、額から自分で割れば推測が要らない
      - 画面に出る株価・予想配当・利回りが互いに検算できる状態になる
        （Yahooの利回りはYahoo側の株価基準なので、当社表示の株価と合わない）

    検証:
      1. 利回りが現実的な範囲か（0% 超 MAX 以下）
      2. 確定した年度の配当と桁が合っているか。増配・減配では説明の
         つかない乖離は、分割調整漏れ等を疑って捨てる

    無配（予想配当が0）は 0.0 ではなく None。「配当を出していない」と
    「利回りが0%」は別物で、0.0 だと配当実施企業と見分けがつかない
    （実績利回り _trailing_dividend_yield と同じ扱い）。
    """
    try:
        if dps_forecast is None or price is None:
            return None
        dps_forecast = float(dps_forecast)
        price = float(price)
    except (TypeError, ValueError):
        return None

    if price <= 0 or dps_forecast <= 0:
        return None

    if confirmed_dps:
        try:
            confirmed = float(confirmed_dps)
        except (TypeError, ValueError):
            confirmed = 0
        if confirmed > 0:
            ratio = dps_forecast / confirmed
            if not (FORWARD_DPS_MIN_RATIO <= ratio <= FORWARD_DPS_MAX_RATIO):
                return None

    value = (dps_forecast / price) * 100
    if value > MAX_FORWARD_DIVIDEND_YIELD:
        return None
    return round(value, 4)


class StockAnalyzer:
    """株式データ分析クラス"""

    # 外部から来た指標を「そのまま信じてよい上限」。これを超えたら採らない。
    # 欠損だけを見て異常値を素通りさせると、画面に出てから気づくことになる
    # （2026-08-12: 配当利回り47%・PBR48倍が本番に出ていた）。
    MAX_DIVIDEND_YIELD = 20.0   # %。上場企業の年間利回りの現実的な上限
    MAX_PER = 300.0             # 倍。これ以上は分母がほぼゼロで指標にならない
    MAX_PBR = 50.0              # 倍

    def __init__(self):
        """初期化"""
        self.output_dir = "output"
        self.charts_dir = "charts"
        self._create_directories()
        
    def _create_directories(self):
        """出力ディレクトリの作成"""
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.charts_dir, exist_ok=True)
        
    def analyze(self, symbol: str, period: str = "1y", skip_chart: bool = False,
                skip_extras: bool = False, safe_sources_only: bool = False) -> Dict[str, Any]:
        """
        株式データを分析してJSONとチャートを生成
        
        Args:
            symbol: 銘柄コード（例: "7203.T", "AAPL"）
            period: 期間（例: "1d", "5d", "1mo", "3mo", "1y", "5y"）
            safe_sources_only: 管理画面の無料・低負荷更新用。Yahoo日本版HTML、
                EDINET DB、GPT、外部HTMLスクレイピングを呼ばない
            
        Returns:
            分析結果の辞書
        """
        result = {
            "symbol": symbol,
            "name": None,
            "currency": None,
            "market_cap": None,
            "last_price": None,
            "per": None,
            "pbr": None,
            "dividend_yield": None,      # 実績（直近12か月に支払われた配当÷株価）
            "dps_forecast": None,        # 予想1株配当（円・年換算）
            "dividend_yield_forward": None,  # 予想利回り(%)
            "equity_ratio_pct": None,
            "op_margin_pct": None,
            "operating_cash_flow": None,
            "current_liabilities": None,
            "cash_and_equivalents": None,
            "industry": None,
            "sector": None,
            "revenue": [],
            "op_income": [],
            "ordinary_income": [],  # 経常利益
            "net_income": [],  # 純利益
            "eps": [],  # 1株益
            "bps": [],  # 1株純資産（PBRの推移を自前計算するのに使う）
            "dps": [],  # 1株配
            "operating_cf": [],  # 営業CF
            "investing_cf": [],  # 投資CF
            "financing_cf": [],  # 財務CF
            "cash": [],  # 現金等
            "current_assets_list": [],  # 流動資産（5年分）
            "current_liabilities_list": [],  # 流動負債（5年分）
            "equity_ratio_list": [],  # 自己資本比率（5年分）
            "roe": [],  # ROE
            "roa": [],  # ROA
            "payout_ratio": [],  # 配当性向
            "margin_trading_ratio": None,  # 信用倍率
            "margin_trading_buy": None,  # 信用買残
            "margin_trading_sell": None,  # 信用売残
            "forecast_revenue": None,  # 今期予想売上高
            "forecast_op_income": None,  # 今期予想営業利益
            "forecast_ordinary_income": None,  # 今期予想経常利益
            "forecast_net_income": None,  # 今期予想純利益
            "forecast_year": None,  # 今期予想の決算期
            "business_summary": None,  # 事業概要（英語）
            "business_summary_jp": None,  # 事業概要（日本語）
            "major_shareholders_jp": [],  # 大株主（日本語）
            "established": None,
            "listing_date": None,
            "headquarters_jp": None,
            "ceo_name_jp": None,
            "market_jp": None,
            "price_history": [],  # 株価履歴（OHLC）
            "trend": None,
            "chart_png": None,
            "source": "Yahoo Finance (yfinance/yahooquery)",
            "source_status": {},
            "timestamp": datetime.now().isoformat()
        }

        # safe_sources_only は呼び出し側が skip_extras を付け忘れても、
        # 株主・役員・概要・GPT・EDINET DBへ進まないことを保証する。
        if safe_sources_only:
            skip_extras = True
            result["source_status"]["acquisition_mode"] = {
                "status": "success",
                "source": "無料・低負荷更新",
                "included": ["Yahoo Financeグローバル (yfinance/yahooquery)", "JPXローカル企業一覧", "確認済み公式キャッシュ"],
                "excluded": ["Yahoo日本版HTML", "EDINET DB", "OpenAI", "株探", "Strainer", "J-LiC"],
            }
        
        try:
            # yfinanceのTickerオブジェクト作成
            ticker = yf.Ticker(symbol)
            
            # 基本的な株価・指標データ取得
            self._get_basic_metrics(ticker, result)

            # 財務データ取得
            self._get_financial_data(ticker, result)
            
            # 5年分の詳細財務データ取得
            self._get_five_year_financial_data(ticker, result)

            # EPS・BPSが揃った後にPER/PBRを補う。
            # Yahooのinfoが返さなかった銘柄でも、割り算で出せる場合がある。
            self._fill_missing_multiples(result)

            # 予想配当利回り。5年分の配当が揃ってから計算する
            # （確定した決算年度の配当と桁が合うかを検証に使うため）。
            self._fill_forward_dividend(ticker, result)

            # ROE/ROA計算（正確な計算）
            self._calculate_roe_roa(ticker, result)
            
            # 業種・セクター情報取得
            self._get_industry_sector(
                symbol, ticker, result, allow_yahoo_jp=not safe_sources_only)
            
            # トレンド分析とチャート作成
            if not skip_chart:
                self._analyze_trend_and_create_chart(ticker, symbol, result, period)
            
            # 日本語会社名・業種取得
            self._get_jp_labels(
                symbol, result, allow_yahoo_jp=not safe_sources_only)

            # 業績予想データ取得（日本株、バッチでも常に取得）
            if symbol.endswith('.T'):
                if safe_sources_only:
                    result['source_status']['forecast'] = {
                        'status': 'skipped',
                        'source': '業績予想取得',
                        'reason': '無料・低負荷更新ではYahoo日本版HTMLとEDINET DBを使用しません',
                    }
                else:
                    self._get_forecast_data(symbol, result)

            if not skip_extras:
                # 主要株主・役員情報取得
                self._get_holders_and_officers(symbol, result)

                # 会社概要・事業説明取得
                self._get_business_summary(symbol, ticker, result)

                # Yahoo Japanで日本語概要が取れず、英語概要だけ取れた場合は
                # EDINET DBを消費する前にGPTで日本語化する。
                if (symbol.endswith('.T') and not result.get('business_summary_jp')
                        and result.get('business_summary')):
                    from summary_translation import translate_summary_to_jp
                    translated = translate_summary_to_jp(result['business_summary'])
                    if translated:
                        result['business_summary_jp'] = translated
                        result.setdefault('source_status', {})['business_summary'] = {
                            'status': 'success',
                            'source': 'Yahoo Finance英語概要 + OpenAI日本語要約',
                            'language': 'ja',
                            'translated_from': 'en',
                        }

                # 信用倍率取得（日本株のみ）
                if symbol.endswith('.T'):
                    self._get_margin_trading_data(symbol, result)

            # 外部サイトが未収録・遮断中でも、確認済みの公式開示キャッシュは
            # ローカル参照なのでEDINET DBより先に補完する。バッチのskip_extras時にも適用する。
            if symbol.endswith('.T'):
                from official_company_profiles import apply_official_profile_fallback
                official_filled = apply_official_profile_fallback(symbol, result)
                if 'business_summary_jp' in official_filled:
                    result['source_status']['business_summary'] = {
                        'status': 'success',
                        'source': 'JPX/会社公式開示（確認済みキャッシュ）',
                        'language': 'ja',
                    }
                if ('major_shareholders_jp' in official_filled
                        or 'company_officers' in official_filled):
                    result['source_status']['holders_officers'] = {
                        'status': 'success',
                        'source': '会社公式開示（確認済みキャッシュ）',
                    }

            # Yahoo系・無料補助ソース・確認済み公式キャッシュを試した後も
            # 欠けている項目だけをEDINET DBで補完する。
            # Free枠は100回/日のため、高速バッチ(skip_extras=True)では呼ばない。
            if symbol.endswith('.T') and not skip_extras:
                try:
                    from edinet_db_client import apply_edinet_db_fallback
                    edinet_filled = apply_edinet_db_fallback(symbol, result)
                    if edinet_filled:
                        print(f"EDINET DB補完成功: {', '.join(edinet_filled)}")
                except Exception as e:
                    print(f"EDINET DB補完エラー: {e}")
                    result.setdefault('source_status', {})['edinet_db'] = {
                        'status': _classify_source_error(e),
                        'source': 'EDINET DB API',
                        'error': str(e),
                    }

            has_financials = bool(result.get('revenue') or result.get('op_income'))
            result['source_status'].setdefault('financials', {
                'status': 'success' if has_financials else 'no_data',
                'source': 'Yahoo Finance (yfinance)',
            })
            if skip_extras:
                result['source_status'].setdefault('business_summary', {
                    'status': ('success' if result.get('business_summary_jp')
                               or result.get('business_summary') else 'skipped'),
                    'source': '事業概要取得',
                    'reason': '高速バッチのskip_extras',
                })
                result['source_status'].setdefault('holders_officers', {
                    'status': ('success' if result.get('major_shareholders_jp')
                               or result.get('company_officers') else 'skipped'),
                    'source': '主要株主・役員取得',
                    'reason': '高速バッチのskip_extras',
                })

            # JSON保存
            output_file = os.path.join(self.output_dir, f"snapshot_{symbol.replace('.', '_')}.json")
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=str)
            
            print(f"分析完了: {output_file}")
            
        except Exception as e:
            print(f"エラー: {symbol}の分析中にエラーが発生しました: {str(e)}")
            result["error"] = str(e)
            
        return result
    
    def _get_basic_metrics(self, ticker: yf.Ticker, result: Dict[str, Any]):
        """基本的な株価・指標データを取得"""
        try:
            # fast_infoを優先
            fast_info = ticker.fast_info
            
            if hasattr(fast_info, 'last_price'):
                result["last_price"] = fast_info.last_price
            if hasattr(fast_info, 'market_cap'):
                result["market_cap"] = fast_info.market_cap
            if hasattr(fast_info, 'currency'):
                result["currency"] = fast_info.currency
                
            # PER, PBR
            if hasattr(fast_info, 'pe_ratio'):
                result["per"] = fast_info.pe_ratio
            if hasattr(fast_info, 'price_to_book'):
                result["pbr"] = fast_info.price_to_book

        except:
            pass

        # infoで補完
        try:
            info = ticker.info

            # 名前
            result["name"] = info.get('longName') or info.get('shortName')

            # fast_infoで取得できなかった値を補完
            if result["last_price"] is None:
                result["last_price"] = info.get('regularMarketPrice') or info.get('currentPrice')
            if result["market_cap"] is None:
                result["market_cap"] = info.get('marketCap')
            if result["currency"] is None:
                result["currency"] = info.get('currency')
            if result["per"] is None:
                result["per"] = info.get('trailingPE') or info.get('forwardPE')
            if result["pbr"] is None:
                result["pbr"] = info.get('priceToBook')

            # 配当利回り＝実際に支払われた配当（直近12か月）÷株価。
            #
            # 2026-08-12: 以前は Yahoo の trailingAnnualDividendRate を第一にし、
            # 取れなければ dividendYield を「0.5超なら%、以下なら小数」と推測して
            # 100倍していた。この2つがどちらも壊れており、22銘柄で 20〜47% という
            # あり得ない利回りが出ていた。
            #   - dividendYield は実際には常に%（0.4 は 0.4%）。0.5未満を小数と
            #     見なす分岐が、利回り0.5%未満の銘柄を軒並み100倍していた
            #     （9720: 0.4% → 40%）
            #   - trailingAnnualDividendRate は分割調整されないことがある
            #     （4918: 実際15円のところ150円 → 47.5%）
            # 支払い履歴 ticker.dividends は分割調整済みで検証もできるため、
            # 推測が要らない。これを正とする。
            result["dividend_yield"] = self._trailing_dividend_yield(
                ticker, result.get("last_price"))

            if result["dividend_yield"] is None:
                # 支払い履歴が無い銘柄だけ Yahoo の値を使う。%として扱う（換算しない）。
                raw_yield = info.get('dividendYield')
                if raw_yield is not None and 0 <= raw_yield <= self.MAX_DIVIDEND_YIELD:
                    result["dividend_yield"] = raw_yield

            # 予想配当（年換算）。実績とは別の列に持ち、画面でも別物として出す。
            # 実績は決算期をまたぐため、期末配当と翌期の中間配当が重なった年は
            # 実態より高く出る（367A: 実績6.18% / 予想4.24%）。
            #
            # dividendRate は「円」で来るので単位の曖昧さが無い。利回り自体は
            # 5年分の配当が揃ってから _fill_forward_dividend() で計算する
            # （確定年度の配当と突き合わせて検証するため）。
            result["dps_forecast"] = info.get('dividendRate')

            # 追加情報
            result["current_liabilities"] = info.get('totalCurrentLiabilities')
            result["cash_and_equivalents"] = info.get('totalCash')

            # firstTradeDateEpochUtc は会社の設立日ではなく取引開始日。
            # JPX由来の上場日が無いときのフォールバックとしてのみ使う。
            first_trade = info.get('firstTradeDateEpochUtc')
            if first_trade and not result.get('listing_date'):
                try:
                    result['listing_date'] = datetime.fromtimestamp(
                        int(first_trade)).date().isoformat()
                except (TypeError, ValueError, OSError):
                    pass

        except:
            pass

        # info の取得自体が落ちた場合の保険（上の try の外で、支払い履歴だけで出す）
        if result["dividend_yield"] is None:
            result["dividend_yield"] = self._trailing_dividend_yield(
                ticker, result.get("last_price"))

    def _fiscal_end_month(self, result: Dict[str, Any]):
        """決算月を EPS の期末日から求める。取れなければ None。

        `_get_five_year_financial_data` が配当を決算年度に集計するときと
        同じ求め方にそろえる。ここがズレると、同じ支払いが別の年度に
        割り振られて予想配当が狂う。
        """
        eps = result.get('eps') or []
        dated = [x for x in eps if x.get('date')]
        if not dated:
            return None
        latest = max(dated, key=lambda x: str(x['date']))
        try:
            return int(str(latest['date'])[5:7])
        except (ValueError, IndexError):
            return None

    def _fill_forward_dividend(self, ticker, result: Dict[str, Any]):
        """予想配当と予想利回りを入れる。信用できなければ None のままにする。

        予想配当は**支払い実績から自分で年換算する**。Yahoo の
        `dividendRate` は株式併合に追随しないことがあり、実際に
        4銘柄で併合前の額のまま止まっていた（annualized_dividend_from_payments
        のコメント参照）。`ticker.dividends` は調整済みで、実績利回りの計算に
        すでに取得しているため、追加の通信は発生しない。

        検証に「確定した決算年度の年間配当」を使うので、5年分の配当
        （result["dps"]）が揃ってから呼ぶこと。dps は決算年度ごとの合計で、
        進行中の年度は中間配当までしか入っていないため対象から外す。
        """
        try:
            dividends = ticker.dividends
            if dividends is not None and not dividends.empty:
                payments = [(idx.strftime('%Y-%m-%d'), float(v))
                            for idx, v in dividends.items()]
                # 決算月は result にまだ入っていない（後の工程で決まる）。
                # 配当を決算年度に集計するときと同じく、EPSの期末日から採る。
                own = forecast_annual_dividend(
                    payments, self._fiscal_end_month(result))
                if own is not None:
                    result["dps_forecast"] = own
        except Exception as e:
            # 取れなければ _get_basic_metrics が入れた Yahoo の値のまま進む
            print(f'  予想配当の自前計算に失敗（Yahooの値を使います）: {e}')

        confirmed_dps = None
        today = datetime.now().strftime('%Y-%m-%d')
        for row in sorted(result.get("dps") or [],
                          key=lambda x: str(x.get('date', '')), reverse=True):
            if str(row.get('date', '')) <= today:
                confirmed_dps = row.get('value')
                break

        result["dividend_yield_forward"] = forward_dividend_yield(
            result.get("dps_forecast"), result.get("last_price"), confirmed_dps)

        if result.get("dps_forecast") and result["dividend_yield_forward"] is None:
            # 捨てたことを記録に残す。黙って None にすると、後から
            # 「取れなかった」のか「弾いた」のか分からなくなる。
            print(f"  予想配当を採用しませんでした: rate={result.get('dps_forecast')}, "
                  f"price={result.get('last_price')}, 確定年度の配当={confirmed_dps}")

    def _trailing_dividend_yield(self, ticker: yf.Ticker, price):
        """直近12か月に実際に支払われた配当 ÷ 株価 × 100。

        `ticker.dividends` は分割調整済みの支払い実績なので、Yahooの要約値
        （trailingAnnualDividendRate / dividendYield）と違って単位を推測しなくてよい。

        無配（履歴はあるが直近12か月の支払いがゼロ）は 0.0 ではなく None を返す。
        「配当を出していない」と「利回りが0%」は同じではなく、0.0 を入れると
        画面で「0.00%」と表示されて配当実施企業と見分けがつかなくなるため。
        """
        if not price or price <= 0:
            return None
        try:
            dividends = ticker.dividends
            if dividends is None or dividends.empty:
                return None
            one_year_ago = pd.Timestamp.now(tz='Asia/Tokyo') - pd.Timedelta(days=365)
            recent = dividends[dividends.index >= one_year_ago]
            if recent.empty:
                return None
            total = float(recent.sum())
            if total <= 0:
                return None
            value = (total / price) * 100
            # 上場企業の年間利回りがこれを超えるのは、ほぼ分割・単位の取り違え。
            # 誤った数字を出すくらいなら「不明」にする。
            if value > self.MAX_DIVIDEND_YIELD:
                return None
            return round(value, 4)
        except Exception:
            return None

    def _get_financial_data(self, ticker: yf.Ticker, result: Dict[str, Any]):
        """財務データを取得"""
        try:
            # 貸借対照表（年次）
            balance_sheet = ticker.balance_sheet
            if not balance_sheet.empty:
                latest = balance_sheet.iloc[:, 0]  # 最新年度
                
                # 自己資本比率（強化版）
                def _get_balance_sheet_value(row_names, data_frame, col):
                    """貸借対照表から値を取得するヘルパー関数"""
                    for name in row_names:
                        # 複数のバリエーションを試す
                        variations = [
                            name,
                            name.replace("Stockholder", "Stockholders"),
                            name.replace("Stockholders", "Stockholder"),
                            name.replace(" ", ""),
                            name.replace("Total ", "")
                        ]
                        for variant in variations:
                            if variant in data_frame.index:
                                value = data_frame.loc[variant, col]
                                if pd.notna(value):
                                    return float(value)
                    return None
                
                equity_keys = ['Total Stockholder Equity', 'Total Stockholders Equity', 
                              'Total Equity', "Total shareholders' equity", 'Stockholder Equity']
                assets_keys = ['Total Assets', 'Total Asset']
                liab_keys = ['Total Liabilities', 'Total Liab', 'Total Debt']
                
                total_equity = _get_balance_sheet_value(equity_keys, balance_sheet, balance_sheet.columns[0])
                total_assets = _get_balance_sheet_value(assets_keys, balance_sheet, balance_sheet.columns[0])
                total_liabilities = _get_balance_sheet_value(liab_keys, balance_sheet, balance_sheet.columns[0])
                
                # フォールバック：Total Assets = Total Equity + Total Liabilities
                if total_assets is None and total_equity is not None and total_liabilities is not None:
                    total_assets = total_equity + total_liabilities
                    print(f"フォールバック: Total Assets = {total_assets} (Equity: {total_equity} + Liab: {total_liabilities})")
                
                if total_equity and total_assets and total_assets != 0:
                    equity_ratio = round((total_equity / total_assets) * 100, 2)
                    result["equity_ratio_pct"] = equity_ratio
                    print(f"自己資本比率計算: {equity_ratio}% (Equity: {total_equity}, Assets: {total_assets})")
                else:
                    result["equity_ratio_pct"] = None
                    print(f"自己資本比率計算失敗: Equity={total_equity}, Assets={total_assets}, Liab={total_liabilities}")
                    
        except:
            pass
            
        try:
            # 損益計算書（年次）
            financials = ticker.financials
            if not financials.empty:
                # 売上高と営業利益の推移
                revenue_keys = ['Total Revenue', 'Revenue']
                op_income_keys = ['Operating Income', 'EBIT']
                
                for col in financials.columns[:3]:  # 直近3年分
                    date_str = col.strftime('%Y-%m-%d')
                    
                    # 売上高
                    for key in revenue_keys:
                        if key in financials.index:
                            value = financials.loc[key, col]
                            if pd.notna(value):
                                result["revenue"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                                
                    # 営業利益
                    for key in op_income_keys:
                        if key in financials.index:
                            value = financials.loc[key, col]
                            if pd.notna(value):
                                result["op_income"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                                
                # 営業利益率（最新）
                if result["revenue"] and result["op_income"]:
                    latest_revenue = result["revenue"][0]["value"]
                    latest_op_income = result["op_income"][0]["value"]
                    if latest_revenue != 0:
                        result["op_margin_pct"] = (latest_op_income / latest_revenue) * 100
                        
        except:
            pass
            
        try:
            # キャッシュフロー計算書（年次）
            cashflow = ticker.cashflow
            if not cashflow.empty:
                cf_keys = ['Operating Cash Flow', 'Total Cash From Operating Activities']
                
                for key in cf_keys:
                    if key in cashflow.index:
                        latest_cf = cashflow.loc[key].iloc[0]  # 最新年度
                        if pd.notna(latest_cf):
                            result["operating_cash_flow"] = float(latest_cf)
                            break
                            
        except:
            pass
            
    def _fill_missing_eps(self, ticker: yf.Ticker, financials, result: Dict[str, Any]):
        """EPSが欠けている決算期を、純利益÷株数で補完する。

        Yahoo（yfinance）は最新決算期のBasic EPS / Diluted EPSをNaNで返すことがある。
        売上・純利益は入っているためテーブルの他の列は埋まり、一株益だけが --- になる。
        400銘柄のサンプルでは133銘柄（約35%）が該当した。

        補完に使う株数は「純利益と同じ決算期」のものだけ。期ズレした株数で割ると
        別物の数字になるため、期が一致しない場合は補完しない（推測しない）。
        取得値ではなく算出値であることを、各値の derived と source_status に残す。
        """
        eps_dates = {d['date'] for d in result.get('eps', [])}
        missing = [d for d in result.get('net_income', []) if d['date'] not in eps_dates]
        if not missing:
            return

        # 株数の取得元。いずれも「その決算期の列」から取る。
        balance_sheet = None
        try:
            balance_sheet = ticker.balance_sheet
        except Exception as e:
            print(f"EPS補完用の貸借対照表取得エラー: {e}")

        def _shares_for(date_str):
            # 1) 損益計算書の期中平均株数（EPSの本来の分母に最も近い）
            for key in ['Basic Average Shares', 'Diluted Average Shares']:
                if financials is not None and key in financials.index:
                    for col in financials.columns:
                        if col.strftime('%Y-%m-%d') == date_str:
                            value = financials.loc[key, col]
                            if pd.notna(value) and float(value) > 0:
                                return float(value), key
            # 2) 貸借対照表の期末株数（自己株式控除後）
            if balance_sheet is not None and not balance_sheet.empty:
                for key in ['Ordinary Shares Number']:
                    if key in balance_sheet.index:
                        for col in balance_sheet.columns:
                            if col.strftime('%Y-%m-%d') == date_str:
                                value = balance_sheet.loc[key, col]
                                if pd.notna(value) and float(value) > 0:
                                    return float(value), key
            return None, None

        filled = []
        for item in missing:
            shares, shares_key = _shares_for(item['date'])
            if not shares:
                continue
            eps_value = float(item['value']) / shares
            result['eps'].append({
                'date': item['date'],
                'value': round(eps_value, 2),
                'derived': True,
            })
            filled.append({'date': item['date'], 'shares_source': shares_key})

        if not filled:
            # 株数が取れず補完できなかった場合も、黙って欠損にしない。
            result.setdefault('source_status', {})['eps'] = {
                'status': 'no_data',
                'source': 'Yahoo Finance (yfinance)',
                'reason': 'EPSが欠けているが同一決算期の株数も取得できず補完不可',
                'missing_periods': [d['date'] for d in missing],
            }
            return

        result['eps'].sort(key=lambda x: x['date'])
        result.setdefault('source_status', {})['eps'] = {
            'status': 'derived',
            'source': '純利益÷株数（アプリ側で算出）',
            'reason': 'Yahooが該当決算期のBasic/Diluted EPSを返さなかったため',
            'derived_periods': filled,
        }

    # 2つの計算が食い違ったとみなす倍率。
    MULTIPLE_DISAGREEMENT = 1.5

    def _fill_missing_multiples(self, result: Dict[str, Any]):
        """PER・PBRを、独立した2つの計算で突き合わせてから確定する。

        取れる値は2系統ある。

          A. Yahooの `trailingPE` / `priceToBook`（要約値。TTMで新しい）
          B. 株価 ÷ 最新決算期のEPS / BPS（開示数値からの割り算）

        2026-08-12 の調査で、**どちらも壊れることがある**と分かった。

          3939: Aが誤り。Yahooの bookValue が 10.319（貸借対照表からは 97.97）で
                PBR 48.65倍と表示。正しくは 5.26倍
          1773: Bが誤り。EPS・BPSの系列が同じ倍率で小さく、PBRが 48.7倍 になる。
                Yahooの 1.20倍 の方が ROE と整合する

        Bの検算にROEを使う案は成り立たない。**EPSとBPSが同じ倍率で狂うと
        ROE（＝EPS÷BPS）は変わらない**ので、スケール誤りを検出できない。
        時価総額÷純資産という株数を経由しない基準も、`equity` 列が全銘柄で
        空のため今は使えない。

        したがって「どちらが正しいか」を機械的に決められない。
        **決められないものを、決めたふりをして出さない。**
        2つが1.5倍以上食い違ったら値を持たせず「判定不能」にする。
        スコアは判定できた項目数を分母にするので（§10 の修正）、
        判定不能の項目は減点ではなく除外として正しく扱われる。

        赤字（EPSがマイナス）ならPERは存在しないので作らない。
        """
        price = result.get('last_price')
        if not price or price <= 0:
            return

        def _latest(series):
            rows = [r for r in (result.get(series) or [])
                    if isinstance(r, dict) and r.get('value') is not None]
            return max(rows, key=lambda r: r['date']) if rows else None

        derived, conflicts = {}, {}
        for key, series, limit in (('per', 'eps', self.MAX_PER),
                                   ('pbr', 'bps', self.MAX_PBR)):
            external = result.get(key)
            if external is not None and (external <= 0 or external > limit):
                # 桁が明らかにおかしい外部値は、この時点で捨てる
                external = None
                result[key] = None

            row = _latest(series)
            computed = None
            if row and row['value'] > 0:
                candidate = price / row['value']
                if candidate <= limit:
                    computed = round(candidate, 4)

            if computed is None:
                # 割り算で出せない銘柄。外部値があればそれを使う（従来どおり）
                continue

            if external is None:
                result[key] = computed
                derived[key] = {'from': series, 'fiscal_end': row['date']}
                continue

            gap = max(external / computed, computed / external)
            if gap < self.MULTIPLE_DISAGREEMENT:
                # 一致。要約値の方がTTMで新しいのでそちらを残す
                continue

            # 食い違った。どちらが正しいか決められないので値を持たせない。
            result[key] = None
            conflicts[key] = {
                'external': round(external, 4),
                'computed': computed,
                'from': series,
                'fiscal_end': row['date'],
                'gap': round(gap, 2),
            }

        if conflicts:
            result.setdefault('source_status', {})['multiples_conflict'] = {
                'status': 'conflict',
                'source': 'Yahoo要約値 と 株価÷EPS/BPS',
                'reason': ('2つの計算が1.5倍以上食い違ったため判定不能にしました'
                           '（どちらが正しいか機械的に決められないため）'),
                'items': conflicts,
            }

        if derived:
            result.setdefault('source_status', {})['multiples'] = {
                'status': 'derived',
                'source': '株価÷EPS / 株価÷BPS（アプリ側で算出）',
                'reason': 'Yahooのinfoが該当指標を返さなかったため',
                'derived': derived,
            }

    def _build_bps_series(self, ticker: yf.Ticker, result: Dict[str, Any]):
        """決算期ごとの1株純資産(BPS)を貸借対照表から作る。

        PBR = 株価 ÷ BPS。BPSを決算期ごとに持っておけば、株価履歴と突き合わせて
        PBRの推移を外部取得なしで再現できる。

        純資産と株数は同じ列（＝同じ決算期）から取る。少数株主持分を含まない
        Stockholders Equity を使い、株数は自己株式控除後の Ordinary Shares Number
        を使う。どちらかが欠ける決算期は作らない（推測しない）。
        """
        try:
            balance_sheet = ticker.balance_sheet
        except Exception as e:
            print(f"BPS算出用の貸借対照表取得エラー: {e}")
            return
        if balance_sheet is None or balance_sheet.empty:
            return

        equity_keys = ['Stockholders Equity', 'Total Stockholder Equity',
                       'Total Stockholders Equity', 'Total Equity']
        shares_keys = ['Ordinary Shares Number', 'Share Issued']

        def _value(keys, col):
            for key in keys:
                if key in balance_sheet.index:
                    value = balance_sheet.loc[key, col]
                    if pd.notna(value):
                        return float(value)
            return None

        for col in balance_sheet.columns:
            equity = _value(equity_keys, col)
            shares = _value(shares_keys, col)
            if not equity or not shares or shares <= 0:
                continue
            result['bps'].append({
                'date': col.strftime('%Y-%m-%d'),
                'value': round(equity / shares, 2),
            })

        result['bps'].sort(key=lambda x: x['date'])
        if result['bps']:
            result.setdefault('source_status', {})['bps'] = {
                'status': 'derived',
                'source': '純資産÷株数（アプリ側で算出）',
                'periods': len(result['bps']),
            }

    def _get_five_year_financial_data(self, ticker: yf.Ticker, result: Dict[str, Any]):
        """5年分の詳細財務データを取得"""
        errors = []
        try:
            # 損益計算書（年次）- 最大5年分
            financials = ticker.financials
            if not financials.empty:
                # 最大5年分取得
                years_to_get = min(5, len(financials.columns))
                
                for i in range(years_to_get):
                    col = financials.columns[i]
                    date_str = col.strftime('%Y-%m-%d')
                    
                    # 売上高（既存）
                    revenue_keys = ['Total Revenue', 'Revenue']
                    for key in revenue_keys:
                        if key in financials.index:
                            value = financials.loc[key, col]
                            if pd.notna(value) and not any(d['date'] == date_str for d in result["revenue"]):
                                result["revenue"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                    
                    # 営業利益（既存）
                    op_income_keys = ['Operating Income', 'EBIT']
                    for key in op_income_keys:
                        if key in financials.index:
                            value = financials.loc[key, col]
                            if pd.notna(value) and not any(d['date'] == date_str for d in result["op_income"]):
                                result["op_income"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                    
                    # 経常利益（Income Before Tax が近い）
                    ordinary_keys = ['Income Before Tax', 'Pretax Income']
                    for key in ordinary_keys:
                        if key in financials.index:
                            value = financials.loc[key, col]
                            if pd.notna(value):
                                result["ordinary_income"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                    
                    # 純利益
                    net_income_keys = ['Net Income', 'Net Income Common Stockholders']
                    for key in net_income_keys:
                        if key in financials.index:
                            value = financials.loc[key, col]
                            if pd.notna(value):
                                result["net_income"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                    
                    # EPS（Basic EPS）
                    eps_keys = ['Basic EPS', 'Diluted EPS']
                    for key in eps_keys:
                        if key in financials.index:
                            value = financials.loc[key, col]
                            if pd.notna(value):
                                result["eps"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break

                # Yahooは最新決算期のBasic/Diluted EPSを空で返すことがある。
                # 売上・純利益は入っているのにEPSだけ欠ける形なので、画面では
                # 「最新年度の一株益だけ ---」になる。純利益と同じ決算期の株数が
                # 取れる場合に限り、純利益÷株数で補完する。
                self._fill_missing_eps(ticker, financials, result)

                # PBRの推移を自前計算するために、決算期ごとの1株純資産を持つ。
                # PBRは点の値しか保存しておらず、履歴を出す材料が無かった。
                self._build_bps_series(ticker, result)

        except Exception as e:
            print(f"損益計算書データ取得エラー: {str(e)}")
            errors.append(e)
            
        try:
            # キャッシュフロー計算書（年次）
            cashflow = ticker.cashflow
            if not cashflow.empty:
                years_to_get = min(5, len(cashflow.columns))
                
                for i in range(years_to_get):
                    col = cashflow.columns[i]
                    date_str = col.strftime('%Y-%m-%d')
                    
                    # 営業CF
                    cf_keys = ['Operating Cash Flow', 'Total Cash From Operating Activities']
                    for key in cf_keys:
                        if key in cashflow.index:
                            value = cashflow.loc[key, col]
                            if pd.notna(value):
                                result["operating_cf"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                    
                    # 投資CF
                    invest_keys = ['Investing Cash Flow', 'Total Cash From Investing Activities']
                    for key in invest_keys:
                        if key in cashflow.index:
                            value = cashflow.loc[key, col]
                            if pd.notna(value):
                                result["investing_cf"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                    
                    # 財務CF
                    finance_keys = ['Financing Cash Flow', 'Total Cash From Financing Activities']
                    for key in finance_keys:
                        if key in cashflow.index:
                            value = cashflow.loc[key, col]
                            if pd.notna(value):
                                result["financing_cf"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                                
        except Exception as e:
            print(f"キャッシュフローデータ取得エラー: {str(e)}")
            errors.append(e)
            
        try:
            # 貸借対照表（年次）
            balance_sheet = ticker.balance_sheet
            if not balance_sheet.empty:
                years_to_get = min(5, len(balance_sheet.columns))
                
                for i in range(years_to_get):
                    col = balance_sheet.columns[i]
                    date_str = col.strftime('%Y-%m-%d')
                    
                    # 現金及び現金同等物
                    cash_keys = ['Cash And Cash Equivalents', 'Cash', 'Cash And Short Term Investments']
                    for key in cash_keys:
                        if key in balance_sheet.index:
                            value = balance_sheet.loc[key, col]
                            if pd.notna(value):
                                result["cash"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                    
                    # 流動資産
                    current_assets_keys = ['Total Current Assets', 'Current Assets']
                    for key in current_assets_keys:
                        if key in balance_sheet.index:
                            value = balance_sheet.loc[key, col]
                            if pd.notna(value):
                                result["current_assets_list"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                    
                    # 流動負債
                    current_liab_keys = ['Total Current Liabilities', 'Current Liabilities']
                    for key in current_liab_keys:
                        if key in balance_sheet.index:
                            value = balance_sheet.loc[key, col]
                            if pd.notna(value):
                                result["current_liabilities_list"].append({
                                    "date": date_str,
                                    "value": float(value)
                                })
                                break
                    
                    # ROE, ROA, 自己資本比率計算用のデータ
                    equity_keys = ['Total Equity', 'Total Stockholder Equity', 
                                  'Total Stockholders Equity', "Total shareholders' equity"]
                    assets_keys = ['Total Assets']
                    
                    total_equity = None
                    total_assets = None
                    
                    for key in equity_keys:
                        if key in balance_sheet.index:
                            total_equity = balance_sheet.loc[key, col]
                            break
                    
                    for key in assets_keys:
                        if key in balance_sheet.index:
                            total_assets = balance_sheet.loc[key, col]
                            break
                    
                    # 自己資本比率（自己資本 / 総資産 * 100）- 強化版
                    def _get_bs_value(row_names, data_frame, col):
                        """貸借対照表から値を取得するヘルパー関数（5年分用）"""
                        for name in row_names:
                            variations = [
                                name,
                                name.replace("Stockholder", "Stockholders"),
                                name.replace("Stockholders", "Stockholder"),
                                name.replace(" ", ""),
                                name.replace("Total ", "")
                            ]
                            for variant in variations:
                                if variant in data_frame.index:
                                    value = data_frame.loc[variant, col]
                                    if pd.notna(value):
                                        return float(value)
                        return None
                    
                    equity_keys_5y = ['Total Stockholder Equity', 'Total Stockholders Equity', 
                                     'Total Equity', "Total shareholders' equity", 'Stockholder Equity']
                    assets_keys_5y = ['Total Assets', 'Total Asset']
                    liab_keys_5y = ['Total Liabilities', 'Total Liab', 'Total Debt']
                    
                    equity_5y = _get_bs_value(equity_keys_5y, balance_sheet, col)
                    assets_5y = _get_bs_value(assets_keys_5y, balance_sheet, col)
                    liab_5y = _get_bs_value(liab_keys_5y, balance_sheet, col)
                    
                    # フォールバック：Total Assets = Total Equity + Total Liabilities
                    if assets_5y is None and equity_5y is not None and liab_5y is not None:
                        assets_5y = equity_5y + liab_5y
                    
                    if equity_5y and assets_5y and assets_5y != 0:
                        equity_ratio = round((equity_5y / assets_5y) * 100, 2)
                        result["equity_ratio_list"].append({
                            "date": date_str,
                            "value": equity_ratio
                        })
                    
                    # 対応する純利益を探す
                    net_income_for_date = None
                    for ni in result["net_income"]:
                        if ni["date"] == date_str:
                            net_income_for_date = ni["value"]
                            break
                    
                    # ROE/ROA計算は別途専用関数で処理（より正確な計算のため）
                    # ここでは個別年度の処理のみ継続
                        
        except Exception as e:
            print(f"貸借対照表データ取得エラー: {str(e)}")
            errors.append(e)
            
        try:
            # 配当データ（DPS）と配当性向計算
            dividends = ticker.dividends
            if not dividends.empty:
                # EPSの決算日から決算月を推定（決算年度ベースで集計するため）
                eps_sorted = sorted(result["eps"], key=lambda x: x["date"])
                fiscal_end_month = 3  # デフォルト3月決算
                if eps_sorted:
                    latest_eps_date = pd.to_datetime(eps_sorted[-1]["date"])
                    fiscal_end_month = latest_eps_date.month

                # 決算年度ごとに配当を集計
                # 例: 3月決算 → 前年4月〜当年3月の配当を同一年度とする
                fiscal_year_divs = {}
                for div_date, div_value in dividends.items():
                    if div_date.month <= fiscal_end_month:
                        fy_year = div_date.year
                    else:
                        fy_year = div_date.year + 1
                    if fy_year not in fiscal_year_divs:
                        fiscal_year_divs[fy_year] = 0.0
                    fiscal_year_divs[fy_year] += div_value

                # 最新5年分のDPSと配当性向を計算
                sorted_fys = sorted(fiscal_year_divs.keys(), reverse=True)[:5]
                for fy_year in sorted_fys:
                    total_dps = fiscal_year_divs[fy_year]
                    date_str = f"{fy_year}-{fiscal_end_month:02d}-28"

                    result["dps"].append({
                        "date": date_str,
                        "value": float(total_dps)
                    })

                    # 同じ決算年度のEPSを探して配当性向計算
                    eps_for_year = None
                    for eps_item in eps_sorted:
                        eps_date = pd.to_datetime(eps_item["date"])
                        if eps_date.year == fy_year:
                            eps_for_year = eps_item["value"]
                            break

                    if eps_for_year and eps_for_year > 0:
                        payout_ratio = (total_dps / eps_for_year) * 100
                        result["payout_ratio"].append({
                            "date": date_str,
                            "value": float(payout_ratio)
                        })

        except Exception as e:
            print(f"配当データ取得エラー: {str(e)}")
            errors.append(e)

        has_financials = bool(result.get('revenue') or result.get('op_income'))
        if has_financials:
            status = 'success'
        elif errors:
            statuses = [_classify_source_error(e) for e in errors]
            status = ('rate_limited' if 'rate_limited' in statuses else
                      'timeout' if 'timeout' in statuses else 'error')
        else:
            status = 'no_data'
        result.setdefault('source_status', {})['financials'] = {
            'status': status,
            'source': 'Yahoo Finance (yfinance)',
            'errors': [str(e) for e in errors[:3]],
        }
            
    def _calculate_roe_roa(self, ticker: yf.Ticker, result: Dict[str, Any]):
        """ROE/ROA計算（平均自己資本・総資産使用）"""
        try:
            financials = ticker.financials      # 年次損益計算書
            balance_sheet = ticker.balance_sheet  # 年次貸借対照表
            
            if financials.empty or balance_sheet.empty:
                print("ROE/ROA計算: 財務データが不足")
                return
            
            # 列名のバリエーション定義
            NI_KEYS = ["Net Income", "Net Income Common Stockholders", "Net Income Applicable To Common Shares"]
            EQ_KEYS = ["Total Stockholder Equity", "Total Stockholders Equity", "Total Equity Gross Minority Interest", "Total Equity"]
            ASSETS_KEYS = ["Total Assets", "Total Asset"]
            
            def _pick_row(df, keys):
                """指定キーの中から存在する行を取得"""
                for key in keys:
                    if key in df.index:
                        return df.loc[key]
                return None
            
            net_income_series = _pick_row(financials, NI_KEYS)
            equity_series = _pick_row(balance_sheet, EQ_KEYS)  
            assets_series = _pick_row(balance_sheet, ASSETS_KEYS)
            
            if net_income_series is None:
                print("ROE/ROA計算: Net Incomeが見つかりません")
                return
            
            if equity_series is None:
                print("ROE/ROA計算: Total Equityが見つかりません")
                return
                
            # 共通の決算期のみ処理
            common_dates = net_income_series.index.intersection(equity_series.index)
            if assets_series is not None:
                common_dates = common_dates.intersection(assets_series.index)
            
            common_dates = sorted(common_dates)[-5:]  # 直近5年
            
            print(f"ROE/ROA計算: {len(common_dates)}年分のデータを処理")
            
            # 結果配列をクリア（重複を防ぐため）
            result["roe"] = []
            result["roa"] = []
            
            for i, date in enumerate(common_dates):
                net_income = net_income_series.get(date)
                equity_current = equity_series.get(date)
                
                # NaN チェック
                if pd.isna(net_income) or pd.isna(equity_current) or equity_current == 0:
                    continue
                
                net_income = float(net_income)
                equity_current = float(equity_current)
                
                # 平均自己資本の計算
                if i > 0:
                    prev_date = common_dates[i-1]
                    equity_prev = equity_series.get(prev_date)
                    if pd.notna(equity_prev) and equity_prev != 0:
                        avg_equity = (equity_current + float(equity_prev)) / 2.0
                    else:
                        avg_equity = equity_current
                else:
                    avg_equity = equity_current  # 初年度は期末値のみ
                
                # ROE計算
                if avg_equity != 0:
                    roe_pct = round((net_income / avg_equity) * 100.0, 1)
                    result["roe"].append({
                        "date": date.strftime('%Y-%m-%d'),
                        "value": roe_pct
                    })
                    print(f"ROE {date.strftime('%Y')}: {roe_pct}% (NI: {net_income:,.0f}, AvgEq: {avg_equity:,.0f})")
                
                # ROA計算
                if assets_series is not None:
                    assets_current = assets_series.get(date)
                    if pd.notna(assets_current) and assets_current != 0:
                        assets_current = float(assets_current)
                        
                        # 平均総資産の計算
                        if i > 0:
                            prev_date = common_dates[i-1]
                            assets_prev = assets_series.get(prev_date)
                            if pd.notna(assets_prev) and assets_prev != 0:
                                avg_assets = (assets_current + float(assets_prev)) / 2.0
                            else:
                                avg_assets = assets_current
                        else:
                            avg_assets = assets_current
                        
                        roa_pct = round((net_income / avg_assets) * 100.0, 1)
                        result["roa"].append({
                            "date": date.strftime('%Y-%m-%d'),
                            "value": roa_pct
                        })
                        print(f"ROA {date.strftime('%Y')}: {roa_pct}% (NI: {net_income:,.0f}, AvgAssets: {avg_assets:,.0f})")
            
            print(f"ROE計算完了: {len(result['roe'])}件")
            print(f"ROA計算完了: {len(result['roa'])}件")
            
        except Exception as e:
            print(f"ROE/ROA計算エラー: {str(e)}")
            result["roe"] = []
            result["roa"] = []
            
    def _get_industry_sector(self, symbol: str, ticker: yf.Ticker,
                             result: Dict[str, Any], allow_yahoo_jp: bool = True):
        """業種・セクター情報を取得（3段階フォールバック）"""
        
        # 1. yahooquery優先
        try:
            yq_ticker = yq.Ticker(symbol, formatted=False)
            asset_profile = yq_ticker.asset_profile
            
            if isinstance(asset_profile, dict) and symbol in asset_profile:
                profile = asset_profile[symbol]
                if isinstance(profile, dict):
                    result["industry"] = profile.get('industry')
                    result["sector"] = profile.get('sector')
                    
            if result["industry"] and result["sector"]:
                return
                
        except:
            pass
            
        # 2. yfinance.infoでフォールバック
        try:
            info = ticker.info
            if not result["industry"]:
                result["industry"] = info.get('industry')
            if not result["sector"]:
                result["sector"] = info.get('sector')
                
            if result["industry"] and result["sector"]:
                return
                
        except:
            pass
            
        # 3. 最終手段：Yahoo Finance JPからスクレイピング（日本株のみ）
        if (allow_yahoo_jp and symbol.endswith('.T')
                and (not result["industry"] or not result["sector"])):
            try:
                from yahoo_jp_guard import fetch as yahoo_fetch
                url = f"https://finance.yahoo.co.jp/quote/{symbol}/profile"
                _html = yahoo_fetch(url, timeout=5)
                response = type('R', (), {'status_code': 200 if _html else 503, 'text': _html or ''})()

                if response.status_code == 200:
                    # 簡易的なパース（実際のHTML構造に応じて調整が必要）
                    import re
                    pattern = r'業種[：:]\s*([^<\n]+)'
                    match = re.search(pattern, response.text)
                    if match:
                        result["industry"] = match.group(1).strip()
                        
            except:
                pass
                
    def _analyze_trend_and_create_chart(self, ticker: yf.Ticker, symbol: str, result: Dict[str, Any], period: str = "1y"):
        """トレンド分析とチャート作成"""
        try:
            # 1D/1Wは取得が不安定なことがあるのでフォールバック込み
            try_periods = [period, "1y"] if period in ("1d", "5d") else [period]
            hist = None
            for p in try_periods:
                # 最大2回試行（初回失敗時に2秒待ってリトライ）
                for attempt in range(2):
                    try:
                        print(f"データ取得試行: {symbol}, 期間: {p}, 試行{attempt+1}/2")
                        hist = ticker.history(period=p, timeout=10)
                        if not hist.empty:
                            print(f"データ取得成功: {len(hist)} 行")
                            break
                        else:
                            print(f"データが空: 期間 {p}, 試行{attempt+1}")
                    except Exception as e:
                        print(f"データ取得失敗 ({p}, 試行{attempt+1}): {str(e)}")
                    if attempt == 0:
                        time.sleep(2)
                if hist is not None and not hist.empty:
                    break

            if hist is None or hist.empty:
                return
                
            # 終値データ
            close_prices = hist['Close']
            
            # トレンド分析（最小二乗回帰）
            x = np.arange(len(close_prices))
            y = close_prices.values
            
            # NaN除去
            mask = ~np.isnan(y)
            x = x[mask]
            y = y[mask]
            
            if len(x) > 2:  # 最低3点は必要
                try:
                    # 線形回帰
                    z = np.polyfit(x, y, 1)
                    slope = z[0]
                    
                    # R²計算
                    p = np.poly1d(z)
                    yhat = p(x)
                    ybar = np.mean(y)
                    ssreg = np.sum((yhat - ybar) ** 2)
                    sstot = np.sum((y - ybar) ** 2)
                    r2 = ssreg / sstot if sstot != 0 else 0
                    
                    # トレンドラベル判定
                    if slope < -0.1:
                        label = "Down"
                    elif slope > 0.1:
                        label = "Up"
                    else:
                        label = "Flat"
                        
                    result["trend"] = {
                        "slope": float(slope),
                        "r2": float(r2),
                        "label": label
                    }
                except Exception as e:
                    print(f"トレンド計算エラー: {str(e)}")
                    result["trend"] = None
            else:
                print(f"トレンド計算: データ点不足 ({len(x)}点)")
                result["trend"] = None

            # OHLCデータを結果に格納（Lightweight Charts用）
            price_history = []
            for idx, row in hist.iterrows():
                # Lightweight Chartsはtime（UNIX秒）を期待
                timestamp = int(idx.timestamp())
                price_history.append({
                    "time": timestamp,
                    "open": float(row['Open']) if pd.notna(row['Open']) else None,
                    "high": float(row['High']) if pd.notna(row['High']) else None,
                    "low": float(row['Low']) if pd.notna(row['Low']) else None,
                    "close": float(row['Close']) if pd.notna(row['Close']) else None,
                    "volume": int(row['Volume']) if pd.notna(row.get('Volume', None)) else None
                })
            result["price_history"] = price_history
            print(f"株価履歴データ: {len(price_history)}件")

            # チャート作成
            fig, ax = plt.subplots(figsize=(12, 6))
            
            # 株価プロット
            ax.plot(hist.index, close_prices, label='終値', color='#1f77b4', linewidth=1.5)
            
            # トレンドライン
            if result.get("trend"):
                dates_numeric = mdates.date2num(hist.index[mask])
                z_dates = np.polyfit(dates_numeric, y, 1)
                p_dates = np.poly1d(z_dates)
                ax.plot(hist.index[mask], p_dates(dates_numeric), 
                       label=f'トレンド ({result["trend"]["label"]})', 
                       color='red', linestyle='--', alpha=0.6)
                
            # グラフ装飾
            ax.set_title(f'{symbol} - {period}の株価推移', fontsize=14, fontweight='bold')
            ax.set_xlabel('日付', fontsize=11)
            ax.set_ylabel('株価', fontsize=11)
            ax.grid(True, alpha=0.3)
            ax.legend(loc='best')
            
            # X軸の日付フォーマット
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            plt.xticks(rotation=45)
            
            plt.tight_layout()
            
            # チャート保存
            chart_file = os.path.join(self.charts_dir, f"chart_{symbol.replace('.', '_')}_{period}.png")
            try:
                plt.savefig(chart_file, dpi=100, bbox_inches='tight', facecolor='white')
                plt.close()
                print(f"チャート保存成功: {chart_file}")
            except Exception as e:
                print(f"チャート保存エラー: {str(e)}")
                plt.close()
                return
            
            result["chart_png"] = chart_file
            print(f"チャート作成: {chart_file}")
            
        except Exception as e:
            print(f"チャート作成エラー: {str(e)}")
            
    def _get_jp_labels(self, symbol: str, result: Dict[str, Any],
                       allow_yahoo_jp: bool = True):
        """日本語会社名・業種取得（.T銘柄のみ）"""
        try:
            from utils.jp_labels import fetch_jp_labels
            from utils.en2ja_taxonomy import SECTOR_JA, INDUSTRY_JA_EXAMPLES
            
            # 無料・低負荷更新ではYahoo日本版HTMLを読まず、下のJPXローカル一覧と
            # 英語分類の変換だけを使う。
            name_jp, industry_jp = ((None, None) if not allow_yahoo_jp
                                    else fetch_jp_labels(symbol))
            result["name_jp"] = name_jp or None
            result["industry_jp"] = industry_jp or None

            # JPX公式リストを最優先で会社名に採用
            # （新規上場コードで yfinance / Yahoo日本版が代表者名を返す問題への対策）
            jpx_name = _lookup_jpx_name(symbol)
            if jpx_name:
                result["name_jp"] = jpx_name
            
            # 英語→日本語フォールバック
            result["sector_jp"] = SECTOR_JA.get(result.get("sector") or "", None)
            if not result.get("industry_jp"):
                en = (result.get("industry") or "").strip()
                result["industry_jp"] = INDUSTRY_JA_EXAMPLES.get(en, None)
                
        except Exception as e:
            print(f"日本語ラベル取得エラー: {str(e)}")
            result["name_jp"] = None
            result["industry_jp"] = None
            result["sector_jp"] = None

    def _get_holders_and_officers(self, symbol: str, result: Dict[str, Any]):
        """主要株主・役員情報取得"""
        try:
            from utils.holders_officers import get_holders_and_officers
            
            holders_data = get_holders_and_officers(symbol)
            
            # 主要株主情報
            result["major_holders"] = holders_data.get("major_holders")
            result["institutional_holders"] = holders_data.get("institutional_holders") 
            result["institution_ownership"] = holders_data.get("institution_ownership")
            result["fund_ownership"] = holders_data.get("fund_ownership")
            result["major_holders_breakdown"] = holders_data.get("major_holders_breakdown")
            
            # 役員情報
            result["company_officers"] = holders_data.get("company_officers")
            
            # メタデータ
            result["holders_source"] = holders_data.get("source", "yfinance/yahooquery")
            result["holders_fallback_needed"] = holders_data.get("fallback_needed", False)
            result.setdefault('source_status', {}).update(
                holders_data.get('source_status') or {})
            
            if holders_data.get("error"):
                print(f"主要株主・役員取得エラー: {holders_data['error']}")
                
        except Exception as e:
            print(f"主要株主・役員取得エラー: {str(e)}")
            result["major_holders"] = None
            result["institutional_holders"] = None
            result["company_officers"] = None
            result["holders_source"] = "error"
    
    def _get_margin_trading_data(self, symbol: str, result: Dict[str, Any]):
        """
        Yahoo!ファイナンス日本版、次にJPX週次残高から信用倍率を取得

        Args:
            symbol: 銘柄コード（例: "7203.T"）
            result: 結果を格納する辞書
        """
        from bs4 import BeautifulSoup
        from yahoo_jp_guard import fetch_result as yahoo_fetch_result

        status_root = result.setdefault('source_status', {})
        code = symbol.replace('.T', '')
        url = f"https://finance.yahoo.co.jp/quote/{code}.T"
        yahoo_status = yahoo_fetch_result(url, timeout=10)
        html = yahoo_status.get('html')

        if html:
            try:
                soup = BeautifulSoup(html, 'html.parser')
                for dl in soup.find_all('dl'):
                    for dt in dl.find_all('dt'):
                        dt_text = dt.get_text(strip=True)
                        dd = dt.find_next_sibling('dd')
                        if not dd:
                            continue
                        nums = re.findall(r'[\d,]+\.?\d*', dd.get_text(strip=True))
                        if not nums:
                            continue
                        value = nums[0].replace(',', '')
                        try:
                            if '信用買残' in dt_text:
                                result['margin_trading_buy'] = int(value)
                            elif '信用売残' in dt_text:
                                result['margin_trading_sell'] = int(value)
                            elif '信用倍率' in dt_text:
                                result['margin_trading_ratio'] = float(value)
                        except (TypeError, ValueError):
                            continue
            except Exception as exc:
                yahoo_status.update({'status': 'parse_error', 'error': str(exc)})

        if result.get('margin_trading_ratio') is None:
            buy = result.get('margin_trading_buy')
            sell = result.get('margin_trading_sell')
            if buy is not None and sell and sell > 0:
                result['margin_trading_ratio'] = round(buy / sell, 2)

        if any(result.get(key) is not None for key in (
                'margin_trading_buy', 'margin_trading_sell', 'margin_trading_ratio')):
            status_root['margin_trading'] = {
                'status': 'success',
                'source': 'Yahoo!ファイナンス日本版',
                'fetched_at': datetime.now().astimezone().isoformat(),
                'url': url,
            }
            print(f"信用倍率データ取得成功 (Yahoo): 倍率={result.get('margin_trading_ratio')}, "
                  f"買残={result.get('margin_trading_buy')}, 売残={result.get('margin_trading_sell')}")
            return

        # Yahooが遮断・未収録・構造変更の場合は、JPX公式の週次残高で補完する。
        try:
            from jpx_margin import get_margin_balance
            jpx_data, jpx_status = get_margin_balance(code)
            jpx_status['yahoo_attempt'] = {
                key: yahoo_status.get(key)
                for key in ('status', 'http_status', 'error', 'url')
            }
            status_root['margin_trading'] = jpx_status
            if jpx_data:
                for key in ('margin_trading_buy', 'margin_trading_sell',
                            'margin_trading_ratio'):
                    if result.get(key) is None:
                        result[key] = jpx_data.get(key)
                result['margin_trading_as_of'] = jpx_data.get('as_of')
                print(f"信用倍率データ取得成功 (JPX週次): "
                      f"倍率={result.get('margin_trading_ratio')}, "
                      f"基準日={result.get('margin_trading_as_of')}")
        except Exception as exc:
            status_root['margin_trading'] = {
                'status': 'error',
                'source': 'JPX 銘柄別信用取引週末残高',
                'error': str(exc),
                'yahoo_attempt': {
                    key: yahoo_status.get(key)
                    for key in ('status', 'http_status', 'error', 'url')
                },
            }
            print(f"信用倍率データ取得エラー: {exc}")

    def _get_forecast_data(self, symbol: str, result: Dict[str, Any]):
        """
        Yahoo!ファイナンス日本版から業績予想データを取得

        Args:
            symbol: 銘柄コード（例: "7203.T"）
            result: 結果を格納する辞書
        """
        try:
            # .Tを除去して4桁コードを取得
            code = symbol.replace('.T', '')

            # Yahoo!ファイナンス日本版の業績ページURL
            url = f"https://finance.yahoo.co.jp/quote/{code}.T/performance"

            from yahoo_jp_guard import fetch_result as yahoo_fetch_result
            yahoo_status = yahoo_fetch_result(url, timeout=10)
            _html = yahoo_status.get('html')
            if not _html:
                result.setdefault('source_status', {})['forecast'] = {
                    'status': yahoo_status.get('status') or 'no_data',
                    'source': 'Yahoo!ファイナンス日本版 /performance',
                    'http_status': yahoo_status.get('http_status'),
                    'error': yahoo_status.get('error'),
                    'url': url,
                }
                return result
            parsed = extract_yahoo_forecast_data(_html)
            for key in ('forecast_revenue', 'forecast_op_income',
                        'forecast_ordinary_income', 'forecast_net_income',
                        'forecast_year'):
                if key in parsed:
                    result[key] = parsed[key]

            status = parsed.get('_forecast_status', 'no_data')
            source_status = {
                'status': status,
                'source': 'Yahoo!ファイナンス日本版 /performance',
                'http_status': 200,
                'url': url,
            }
            if parsed.get('_forecast_reason'):
                source_status['reason'] = parsed['_forecast_reason']
            if parsed.get('_forecast_period'):
                source_status['forecast_period'] = parsed['_forecast_period']
            result.setdefault('source_status', {})['forecast'] = source_status

            if status == 'success':
                print(f"業績予想データ取得成功: 期={result.get('forecast_year')}, "
                      f"売上={result.get('forecast_revenue')}億, "
                      f"営利={result.get('forecast_op_income')}億")
            elif status == 'not_disclosed':
                print(f"業績予想は会社非開示: 期={parsed.get('_forecast_period')}")
            else:
                print("業績予想データが見つかりませんでした")

        except Exception as e:
            print(f"業績予想データ取得エラー: {str(e)}")
            result.setdefault('source_status', {})['forecast'] = {
                'status': 'parse_error',
                'source': 'Yahoo!ファイナンス日本版 /performance',
                'error': str(e),
            }

    def _get_business_summary(self, symbol: str, ticker: yf.Ticker, result: Dict[str, Any]):
        """
        会社概要・事業説明を取得
        
        Args:
            symbol: 銘柄コード
            ticker: yfinanceのTickerオブジェクト
            result: 結果を格納する辞書
        """
        try:
            # 1. 日本株はYahoo Japanの日本語概要を最初に試す。
            if symbol.endswith('.T'):
                try:
                    from jp_company_scraper import get_all_jp_company_data
                    jp_data = get_all_jp_company_data(symbol)

                    result.setdefault('source_status', {}).update(
                        jp_data.get('source_status') or {})

                    # 取得先ごとに独立して扱う。どれか1つが失敗していても、
                    # J-LiC・Strainerなど他の成功データは取り込む。
                    if jp_data:
                        # 事業概要（特色）
                        if jp_data.get('business_summary_jp'):
                            result["business_summary_jp"] = jp_data['business_summary_jp']
                            print(f"Yahoo Japan 事業概要取得成功: {len(result['business_summary_jp'])} 文字")

                        # 連結事業も追加情報として格納
                        if jp_data.get('business_segments'):
                            if result.get("business_summary_jp"):
                                result["business_summary_jp"] += f"<br>【連結事業】{jp_data['business_segments']}"
                            else:
                                result["business_summary_jp"] = f"【連結事業】{jp_data['business_segments']}"

                        # 追加情報を格納
                        if jp_data.get('headquarters_jp'):
                            result['headquarters_jp'] = jp_data['headquarters_jp']
                        if jp_data.get('established'):
                            result['established'] = jp_data['established']
                        if jp_data.get('employees_jp'):
                            result['employees_jp'] = jp_data['employees_jp']
                        if jp_data.get('average_salary_jp'):
                            result['average_salary_jp'] = jp_data['average_salary_jp']
                        # 代表者名・業種分類・市場名も取得できているので取りこぼさない
                        if jp_data.get('ceo_name_jp'):
                            result['ceo_name_jp'] = jp_data['ceo_name_jp']
                        if jp_data.get('industry_jp'):
                            result['industry_jp'] = jp_data['industry_jp']
                        if jp_data.get('market_jp'):
                            result['market_jp'] = jp_data['market_jp']

                        # 日本語の役員情報（j-lic.comから）- 日本語データを優先使用
                        if jp_data.get('officers_jp'):
                            jp_officers = jp_data['officers_jp']
                            # 日本語データを主データとして使用（より詳細な情報を持つ）
                            result['company_officers'] = [
                                {
                                    'name': o.get('name'),
                                    'title': o.get('title'),
                                    'name_jp': o.get('name'),
                                    'title_jp': o.get('title'),
                                    'bio': o.get('bio'),
                                    'shares': o.get('shares')
                                }
                                for o in jp_officers
                            ]
                            print(f"日本語役員データ: {len(jp_officers)}名")

                        # 日本語の大株主情報（strainer.jpから）
                        if jp_data.get('major_shareholders_jp'):
                            result['major_shareholders_jp'] = jp_data['major_shareholders_jp']
                            print(f"日本語大株主データ: {len(jp_data['major_shareholders_jp'])}社")

                except Exception as e:
                    print(f"日本語データ取得エラー: {str(e)}")

            # 2. 日本語概要が無いときだけ、Yahooグローバル側の英語概要を取得する。
            if not result.get('business_summary_jp'):
                self._get_english_business_summary(symbol, ticker, result)

            result.setdefault('source_status', {})['business_summary'] = {
                'status': ('success' if result.get('business_summary_jp')
                           or result.get('business_summary') else 'no_data'),
                'source': ('日本語プロフィール/公式フォールバック'
                           if result.get('business_summary_jp') else
                           'Yahoo Finance (yfinance/yahooquery)'),
                'language': 'ja' if result.get('business_summary_jp') else 'en',
            }
                    
        except Exception as e:
            print(f"事業概要取得エラー: {str(e)}")
            result.setdefault('source_status', {})['business_summary'] = {
                'status': _classify_source_error(e),
                'source': '事業概要取得', 'error': str(e),
            }

    def _get_english_business_summary(self, symbol: str, ticker: yf.Ticker,
                                      result: Dict[str, Any]):
        """yahooquery、次にyfinanceから英語の事業概要を取得する。"""
        try:
            yq_ticker = yq.Ticker(symbol, formatted=False)
            asset_profile = yq_ticker.asset_profile
            if isinstance(asset_profile, dict) and symbol in asset_profile:
                profile = asset_profile[symbol]
                if isinstance(profile, dict):
                    summary = profile.get('longBusinessSummary')
                    if summary:
                        result['business_summary'] = summary
                        print(f"事業概要取得成功 (yahooquery): {len(summary)} 文字")
        except Exception as e:
            print(f"yahooquery事業概要取得エラー: {e}")

        if not result.get('business_summary'):
            try:
                info = ticker.info
                summary = info.get('longBusinessSummary')
                if summary:
                    result['business_summary'] = summary
                    print(f"事業概要取得成功 (yfinance): {len(summary)} 文字")
            except Exception as e:
                print(f"yfinance事業概要取得エラー: {e}")


def batch_analyze(symbols: List[str], sleep_time: float = 0.35, skip_chart: bool = False, skip_extras: bool = False):
    """
    複数銘柄をバッチ分析

    Args:
        symbols: 銘柄コードのリスト
        sleep_time: リクエスト間のスリープ時間（秒）
        skip_chart: チャート生成をスキップするか
        skip_extras: 株主・事業概要・信用倍率・業績予想をスキップするか
    """
    analyzer = StockAnalyzer()
    results = []

    for i, symbol in enumerate(symbols):
        print(f"\n[{i+1}/{len(symbols)}] {symbol}を分析中...")
        result = analyzer.analyze(symbol, skip_chart=skip_chart, skip_extras=skip_extras)
        results.append(result)
        
        # 最後の銘柄以外はスリープ
        if i < len(symbols) - 1:
            time.sleep(sleep_time)
            
    return results


if __name__ == "__main__":
    # テスト実行
    test_symbols = ["7203.T", "1928.T"]  # トヨタ、積水ハウス
    
    print("株式データ分析を開始します...")
    analyzer = StockAnalyzer()
    
    for symbol in test_symbols:
        print(f"\n{symbol}を分析中...")
        result = analyzer.analyze(symbol)
        
        # 主要な結果を表示
        if not result.get("error"):
            print(f"  会社名: {result.get('name')}")
            print(f"  株価: {result.get('last_price')}")
            print(f"  PER: {result.get('per')}")
            print(f"  PBR: {result.get('pbr')}")
            print(f"  配当利回り: {result.get('dividend_yield')}")
            print(f"  業種: {result.get('industry')}")
            print(f"  セクター: {result.get('sector')}")
            if result.get('trend'):
                print(f"  トレンド: {result['trend']['label']}")
        else:
            print(f"  エラー: {result['error']}")
            
        time.sleep(0.35)  # 礼儀正しく待つ
