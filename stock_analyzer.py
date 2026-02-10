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
import requests


# 日本語フォント設定
rcParams['font.sans-serif'] = ['Yu Gothic', 'Meiryo', 'Hiragino Sans', 'MS Gothic']
rcParams['axes.unicode_minus'] = False


class StockAnalyzer:
    """株式データ分析クラス"""
    
    def __init__(self):
        """初期化"""
        self.output_dir = "output"
        self.charts_dir = "charts"
        self._create_directories()
        
    def _create_directories(self):
        """出力ディレクトリの作成"""
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.charts_dir, exist_ok=True)
        
    def analyze(self, symbol: str, period: str = "1y", skip_chart: bool = False, skip_extras: bool = False) -> Dict[str, Any]:
        """
        株式データを分析してJSONとチャートを生成
        
        Args:
            symbol: 銘柄コード（例: "7203.T", "AAPL"）
            period: 期間（例: "1d", "5d", "1mo", "3mo", "1y", "5y"）
            
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
            "dividend_yield": None,
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
            "price_history": [],  # 株価履歴（OHLC）
            "trend": None,
            "chart_png": None,
            "source": "Yahoo Finance (yfinance/yahooquery)",
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            # yfinanceのTickerオブジェクト作成
            ticker = yf.Ticker(symbol)
            
            # 基本的な株価・指標データ取得
            self._get_basic_metrics(ticker, result)

            # kabutanから正確なPBRを取得（日本株のみ、常時実行）
            if symbol.endswith('.T'):
                self._get_kabutan_metrics(symbol, result)

            # 財務データ取得
            self._get_financial_data(ticker, result)
            
            # 5年分の詳細財務データ取得
            self._get_five_year_financial_data(ticker, result)
            
            # ROE/ROA計算（正確な計算）
            self._calculate_roe_roa(ticker, result)
            
            # 業種・セクター情報取得
            self._get_industry_sector(symbol, ticker, result)
            
            # トレンド分析とチャート作成
            if not skip_chart:
                self._analyze_trend_and_create_chart(ticker, symbol, result, period)
            
            # 日本語会社名・業種取得
            self._get_jp_labels(symbol, result)

            # 業績予想データ取得（日本株、バッチでも常に取得）
            if symbol.endswith('.T'):
                self._get_forecast_data(symbol, result)

            if not skip_extras:
                # 主要株主・役員情報取得
                self._get_holders_and_officers(symbol, result)

                # 会社概要・事業説明取得
                self._get_business_summary(symbol, ticker, result)

                # 信用倍率取得（日本株のみ）
                if symbol.endswith('.T'):
                    self._get_margin_trading_data(symbol, result)

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
                
            # PER, PBR, 配当利回り
            if hasattr(fast_info, 'pe_ratio'):
                result["per"] = fast_info.pe_ratio
            if hasattr(fast_info, 'price_to_book'):
                result["pbr"] = fast_info.price_to_book
            if hasattr(fast_info, 'dividend_yield'):
                result["dividend_yield"] = fast_info.dividend_yield
                
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
            if result["dividend_yield"] is None:
                result["dividend_yield"] = info.get('dividendYield')
                
            # 追加情報
            result["current_liabilities"] = info.get('totalCurrentLiabilities')
            result["cash_and_equivalents"] = info.get('totalCash')
            
        except:
            pass
            
        # 配当利回りのフォールバック（TTM計算）
        if result["dividend_yield"] is None and result["last_price"]:
            try:
                dividends = ticker.dividends
                if not dividends.empty:
                    # 直近365日の配当合計
                    one_year_ago = datetime.now() - pd.Timedelta(days=365)
                    recent_div = dividends[dividends.index >= one_year_ago]
                    if not recent_div.empty:
                        ttm_dividend = recent_div.sum()
                        result["dividend_yield"] = ttm_dividend / result["last_price"]
            except:
                pass
                
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
            
    def _get_five_year_financial_data(self, ticker: yf.Ticker, result: Dict[str, Any]):
        """5年分の詳細財務データを取得"""
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
                
        except Exception as e:
            print(f"損益計算書データ取得エラー: {str(e)}")
            
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
            
    def _get_industry_sector(self, symbol: str, ticker: yf.Ticker, result: Dict[str, Any]):
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
        if symbol.endswith('.T') and (not result["industry"] or not result["sector"]):
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                url = f"https://finance.yahoo.co.jp/quote/{symbol}/profile"
                response = requests.get(url, headers=headers, timeout=5)
                
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
            
    def _get_jp_labels(self, symbol: str, result: Dict[str, Any]):
        """日本語会社名・業種取得（.T銘柄のみ）"""
        try:
            from utils.jp_labels import fetch_jp_labels
            from utils.en2ja_taxonomy import SECTOR_JA, INDUSTRY_JA_EXAMPLES
            
            # Yahoo!ファイナンス日本版から取得
            name_jp, industry_jp = fetch_jp_labels(symbol)
            result["name_jp"] = name_jp or None
            result["industry_jp"] = industry_jp or None
            
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
            
            if holders_data.get("error"):
                print(f"主要株主・役員取得エラー: {holders_data['error']}")
                
        except Exception as e:
            print(f"主要株主・役員取得エラー: {str(e)}")
            result["major_holders"] = None
            result["institutional_holders"] = None
            result["company_officers"] = None
            result["holders_source"] = "error"
    
    def _get_kabutan_metrics(self, symbol: str, result: Dict[str, Any]):
        """kabutanから正確なPBR/PERを取得して上書き（日本株のみ）"""
        try:
            from bs4 import BeautifulSoup

            code = symbol.replace('.T', '')
            url = f'https://kabutan.jp/stock/?code={code}'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(url, headers=headers, timeout=15)
            response.encoding = 'utf-8'

            if response.status_code != 200:
                return

            soup = BeautifulSoup(response.text, 'html.parser')

            abbr = soup.find('abbr', title='Price Book-value Ratio')
            if not abbr:
                return
            table = abbr.find_parent('table')
            if not table:
                return
            rows = table.find_all('tr')
            if len(rows) < 2:
                return
            cells = rows[1].find_all('td')
            if len(cells) >= 2:
                # PBR = cells[1]
                pbr_text = cells[1].get_text(strip=True).replace('倍', '')
                try:
                    pbr_val = float(pbr_text)
                    old_pbr = result.get('pbr')
                    result['pbr'] = pbr_val
                    print(f"PBR上書き（kabutan）: {old_pbr} → {pbr_val}")
                except ValueError:
                    pass

        except Exception as e:
            print(f"kabutan PBR取得エラー: {str(e)}")

    def _get_margin_trading_data(self, symbol: str, result: Dict[str, Any]):
        """
        Yahoo!ファイナンス日本版から信用倍率データを取得

        Args:
            symbol: 銘柄コード（例: "7203.T"）
            result: 結果を格納する辞書
        """
        try:
            from bs4 import BeautifulSoup

            # .Tを除去して4桁コードを取得
            code = symbol.replace('.T', '')

            # Yahoo!ファイナンス日本版のURL
            url = f"https://finance.yahoo.co.jp/quote/{code}.T"

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }

            response = requests.get(url, headers=headers, timeout=10)
            response.encoding = 'utf-8'

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')

                # dl/dt/dd構造から信用取引データを取得
                for dl in soup.find_all('dl'):
                    for dt in dl.find_all('dt'):
                        dt_text = dt.get_text(strip=True)
                        dd = dt.find_next_sibling('dd')
                        if dd:
                            dd_text = dd.get_text(strip=True)

                            # 数値を抽出（カンマ区切りの数値）
                            nums = re.findall(r'[\d,]+\.?\d*', dd_text)

                            if '信用買残' in dt_text and nums:
                                try:
                                    result["margin_trading_buy"] = int(nums[0].replace(',', ''))
                                except:
                                    pass
                            elif '信用売残' in dt_text and nums:
                                try:
                                    result["margin_trading_sell"] = int(nums[0].replace(',', ''))
                                except:
                                    pass
                            elif '信用倍率' in dt_text and nums:
                                try:
                                    result["margin_trading_ratio"] = float(nums[0].replace(',', ''))
                                except:
                                    pass

                # 倍率がない場合、買残/売残から計算
                if result["margin_trading_ratio"] is None:
                    if result["margin_trading_buy"] and result["margin_trading_sell"]:
                        if result["margin_trading_sell"] > 0:
                            result["margin_trading_ratio"] = round(
                                result["margin_trading_buy"] / result["margin_trading_sell"], 2
                            )

                print(f"信用倍率データ取得成功: 倍率={result.get('margin_trading_ratio')}, "
                      f"買残={result.get('margin_trading_buy')}, 売残={result.get('margin_trading_sell')}")

        except Exception as e:
            print(f"信用倍率データ取得エラー: {str(e)}")

    def _get_forecast_data(self, symbol: str, result: Dict[str, Any]):
        """
        Yahoo!ファイナンス日本版から業績予想データを取得

        Args:
            symbol: 銘柄コード（例: "7203.T"）
            result: 結果を格納する辞書
        """
        try:
            import json as json_module
            import re

            # .Tを除去して4桁コードを取得
            code = symbol.replace('.T', '')

            # Yahoo!ファイナンス日本版の業績ページURL
            url = f"https://finance.yahoo.co.jp/quote/{code}.T/performance"

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }

            response = requests.get(url, headers=headers, timeout=10)
            response.encoding = 'utf-8'

            if response.status_code == 200:
                html = response.text

                # エスケープ引用符を正規化（HTML内のJSON文字列対応）
                normalized_html = html.replace('\\"', '"')

                # JSONデータを抽出（ReactのpropsやstateとしてHTMLに埋め込まれている）
                # "forecast":{"yearEndDate":"2025-12-31","netSales":35000000000,...}
                forecast_pattern = r'"forecast"\s*:\s*\{[^}]+\}'
                forecast_match = re.search(forecast_pattern, normalized_html)

                if forecast_match:
                    forecast_str = forecast_match.group(0)
                    # "forecast": を除去してJSONパース可能な形に
                    json_str = '{' + forecast_str + '}'

                    try:
                        data = json_module.loads(json_str)
                        forecast = data.get('forecast', {})

                        # 決算期
                        year_end = forecast.get('yearEndDate')
                        if year_end:
                            result["forecast_year"] = year_end

                        # 売上高（円→億円に変換）
                        net_sales = forecast.get('netSales')
                        if net_sales and isinstance(net_sales, (int, float)):
                            result["forecast_revenue"] = net_sales / 1e8

                        # 営業利益（円→億円に変換）
                        op_income = forecast.get('operatingIncome')
                        if op_income and isinstance(op_income, (int, float)):
                            result["forecast_op_income"] = op_income / 1e8

                        # 経常利益（円→億円に変換）
                        ordinary_income = forecast.get('ordinaryIncome')
                        if ordinary_income and isinstance(ordinary_income, (int, float)):
                            result["forecast_ordinary_income"] = ordinary_income / 1e8

                        # 純利益（円→億円に変換）
                        net_profit = forecast.get('netProfit')
                        if net_profit and isinstance(net_profit, (int, float)):
                            result["forecast_net_income"] = net_profit / 1e8

                        print(f"業績予想データ取得成功: 期={result.get('forecast_year')}, "
                              f"売上={result.get('forecast_revenue')}億, "
                              f"営利={result.get('forecast_op_income')}億")

                    except json_module.JSONDecodeError as e:
                        # シンプルな正規表現でフォールバック
                        print(f"JSONパース失敗、正規表現でフォールバック: {e}")

                        # netSales
                        sales_match = re.search(r'"netSales"\s*:\s*(\d+)', normalized_html)
                        if sales_match:
                            result["forecast_revenue"] = int(sales_match.group(1)) / 1e8

                        # operatingIncome
                        op_match = re.search(r'"operatingIncome"\s*:\s*(\d+)', normalized_html)
                        if op_match:
                            result["forecast_op_income"] = int(op_match.group(1)) / 1e8

                        # ordinaryIncome
                        ordinary_match = re.search(r'"ordinaryIncome"\s*:\s*(\d+)', normalized_html)
                        if ordinary_match:
                            result["forecast_ordinary_income"] = int(ordinary_match.group(1)) / 1e8

                        # netProfit
                        profit_match = re.search(r'"netProfit"\s*:\s*(\d+)', normalized_html)
                        if profit_match:
                            result["forecast_net_income"] = int(profit_match.group(1)) / 1e8

                        # yearEndDate
                        year_match = re.search(r'"yearEndDate"\s*:\s*"([^"]+)"', normalized_html)
                        if year_match:
                            result["forecast_year"] = year_match.group(1)

                        print(f"業績予想データ取得成功（フォールバック）: 売上={result.get('forecast_revenue')}億")

                else:
                    print("業績予想データが見つかりませんでした")

        except Exception as e:
            print(f"業績予想データ取得エラー: {str(e)}")

    def _get_business_summary(self, symbol: str, ticker: yf.Ticker, result: Dict[str, Any]):
        """
        会社概要・事業説明を取得
        
        Args:
            symbol: 銘柄コード
            ticker: yfinanceのTickerオブジェクト
            result: 結果を格納する辞書
        """
        try:
            # 1. yahooquery優先で取得
            try:
                yq_ticker = yq.Ticker(symbol, formatted=False)
                asset_profile = yq_ticker.asset_profile
                
                if isinstance(asset_profile, dict) and symbol in asset_profile:
                    profile = asset_profile[symbol]
                    if isinstance(profile, dict):
                        summary = profile.get('longBusinessSummary')
                        if summary:
                            result["business_summary"] = summary
                            print(f"事業概要取得成功 (yahooquery): {len(summary)} 文字")
            except Exception as e:
                print(f"yahooquery事業概要取得エラー: {str(e)}")
            
            # 2. yfinanceでフォールバック
            if not result["business_summary"]:
                try:
                    info = ticker.info
                    summary = info.get('longBusinessSummary')
                    if summary:
                        result["business_summary"] = summary
                        print(f"事業概要取得成功 (yfinance): {len(summary)} 文字")
                except Exception as e:
                    print(f"yfinance事業概要取得エラー: {str(e)}")
            
            # 3. 日本株の場合、各種サイトから日本語データを取得
            if symbol.endswith('.T'):
                try:
                    from jp_company_scraper import get_all_jp_company_data
                    jp_data = get_all_jp_company_data(symbol)

                    if not jp_data.get('error'):
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

            # 4. フォールバック: 日本株の場合、四季報オンラインから日本語説明を取得
            if symbol.endswith('.T') and not result.get("business_summary_jp"):
                try:
                    code = symbol.replace('.T', '')

                    # 四季報オンラインのURL
                    shikiho_url = f"https://shikiho.toyokeizai.net/stocks/{code}"
                    
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    }
                    
                    response = requests.get(shikiho_url, headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        import re
                        import html
                        
                        # class="shimen-articles__table"のテーブルから情報を取得
                        # パターン1: class属性を使った検索
                        table_pattern = r'<table[^>]*class="shimen-articles__table"[^>]*>(.*?)</table>'
                        table_match = re.search(table_pattern, response.text, re.DOTALL)
                        
                        if table_match:
                            table_content = table_match.group(1)
                            
                            # テーブル内のth/tdペアを抽出
                            row_pattern = r'<tr[^>]*>.*?<th[^>]*>(.*?)</th>.*?<td[^>]*>(.*?)</td>.*?</tr>'
                            rows = re.findall(row_pattern, table_content, re.DOTALL)
                            
                            if rows:
                                # 各行の内容を結合
                                summary_parts = []
                                for th, td in rows:
                                    # HTMLタグを除去
                                    th_clean = re.sub(r'<[^>]+>', '', th).strip()
                                    td_clean = re.sub(r'<[^>]+>', '', td).strip()
                                    # HTMLエンティティのデコード
                                    th_clean = html.unescape(th_clean)
                                    td_clean = html.unescape(td_clean)
                                    
                                    if th_clean and td_clean:
                                        summary_parts.append(f"{th_clean}: {td_clean}")
                                
                                if summary_parts:
                                    result["business_summary_jp"] = " ".join(summary_parts)
                                    print(f"四季報事業概要取得成功: {len(result['business_summary_jp'])} 文字")
                        
                        # フォールバック: より一般的なパターンで検索
                        if not result["business_summary_jp"]:
                            # 【伸長】【積極採用】などのパターンを直接検索
                            general_pattern = r'【([^】]+)】</th>.*?<td[^>]*>(.*?)</td>'
                            general_matches = re.findall(general_pattern, response.text, re.DOTALL)
                            
                            if general_matches:
                                summary_parts = []
                                for title, content in general_matches:
                                    content_clean = re.sub(r'<[^>]+>', '', content).strip()
                                    content_clean = html.unescape(content_clean)
                                    if title and content_clean:
                                        summary_parts.append(f"【{title}】{content_clean}")
                                
                                if summary_parts:
                                    result["business_summary_jp"] = " ".join(summary_parts)
                                    print(f"四季報事業概要取得成功（一般パターン）: {len(result['business_summary_jp'])} 文字")
                        
                except Exception as e:
                    print(f"四季報事業概要取得エラー: {str(e)}")
                    
        except Exception as e:
            print(f"事業概要取得エラー: {str(e)}")


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