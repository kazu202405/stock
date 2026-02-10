import os
import json
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from flask import jsonify, request
from config import *
# from models.login import *  # ログイン機能無効化
from models.common import *
from models.root import *
from models.model import *
from models.financial_analysis import *
from models.user import *
from models.chatbot import *
from models.business_plan_preparation import *
from stock_analyzer import StockAnalyzer, batch_analyze
from supabase_client import (
    get_supabase_client,
    add_to_watchlist, remove_from_watchlist, get_watchlist,
    is_in_watchlist, get_watchlist_with_details, upsert_screened_data,
    update_screened_data, upsert_screened_data_with_match_rate,
    calculate_match_rate, get_screened_data,
    get_technical_stocks,
    get_signal_gc_stocks, get_signal_dc_stocks, upsert_signal_stocks
)
from gc_scraper import scrape_gc_stocks, scrape_dc_stocks


# ヘルパー関数
def normalize_code(code):
    """銘柄コードを正規化（.Tを除去して統一）"""
    if code and code.endswith('.T'):
        return code[:-2]
    return code


def get_latest_value(val):
    """配列データから最新値を抽出"""
    if val is None:
        return None
    if isinstance(val, list) and len(val) > 0:
        sorted_list = sorted(val, key=lambda x: x.get('date', ''), reverse=True)
        return sorted_list[0].get('value')
    if isinstance(val, (int, float)):
        return val
    return None


def get_yearly_values(data_list, count=4):
    """配列データから直近N年分の値を取得"""
    if not data_list or not isinstance(data_list, list):
        return [None] * count
    sorted_list = sorted(data_list, key=lambda x: x.get('date', ''), reverse=True)
    values = [item.get('value') for item in sorted_list[:count]]
    while len(values) < count:
        values.append(None)
    return values


def to_oku(val):
    """億円単位に変換"""
    if val is None:
        return None
    return val / 1e8


# ウォッチリストAPI
@app.route('/api/watchlist', methods=['GET'])
def api_get_watchlist():
    """登録銘柄一覧を取得（GC/DC形成日付き）"""
    try:
        data = get_watchlist_with_details()

        # screened_latestの永続日付を優先、signal_stocksで補完
        signal_stocks = get_signal_gc_stocks() + get_signal_dc_stocks()
        signal_map = {}
        for s in signal_stocks:
            code = s['company_code']
            if code not in signal_map:
                signal_map[code] = {}
            if s.get('gc_date'):
                signal_map[code]['gc_date'] = s['gc_date']
            if s.get('dc_date'):
                signal_map[code]['dc_date'] = s['dc_date']

        for item in data:
            code = item.get('company_code', '').replace('.T', '')
            sig = signal_map.get(code, {})
            item['gc_date'] = item.get('gc_date') or sig.get('gc_date')
            item['dc_date'] = item.get('dc_date') or sig.get('dc_date')

        return jsonify({"watchlist": data}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/add', methods=['POST'])
def api_add_to_watchlist():
    """銘柄をウォッチリストに登録"""
    try:
        data = request.get_json()
        if not data or 'company_code' not in data:
            return jsonify({"error": "銘柄コードが指定されていません"}), 400

        company_code = normalize_code(data['company_code'])

        # ウォッチリストに追加
        add_to_watchlist(company_code)

        # screened_latestにも基本情報を保存（分析データがあれば）
        if 'stock_data' in data:
            stock_data = data['stock_data']

            # 時価総額を億円単位に変換
            market_cap_raw = stock_data.get('market_cap')
            market_cap_oku = market_cap_raw / 1e8 if market_cap_raw else None

            # 売上高（直近4年分：今期予、前期、2期前、3期前）
            revenue_vals = get_yearly_values(stock_data.get('revenue'), 4)

            # 営業利益（直近4年分）
            op_vals = get_yearly_values(stock_data.get('op_income'), 4)

            # キャッシュフロー（直近値）
            operating_cf = get_latest_value(stock_data.get('operating_cf'))
            investing_cf = get_latest_value(stock_data.get('investing_cf'))
            financing_cf = get_latest_value(stock_data.get('financing_cf'))

            # 純利益
            net_income = get_latest_value(stock_data.get('net_income'))

            # 現預金
            cash = get_latest_value(stock_data.get('cash'))

            # 流動負債・流動資産
            current_liabilities = get_latest_value(stock_data.get('current_liabilities_list'))
            current_assets = get_latest_value(stock_data.get('current_assets_list'))

            # 流動比率計算
            current_ratio = None
            if current_assets and current_liabilities and current_liabilities > 0:
                current_ratio = (current_assets / current_liabilities) * 100

            # EPS/DPS（最新値）
            eps = get_latest_value(stock_data.get('eps'))
            dps = get_latest_value(stock_data.get('dps'))
            payout_ratio = get_latest_value(stock_data.get('payout_ratio'))

            # ROE（最新値）
            roe_list = stock_data.get('roe')
            roe = get_latest_value(roe_list) if roe_list else None

            # 財務履歴をJSON形式で保存
            financial_history = {
                'revenue': stock_data.get('revenue', []),
                'op_income': stock_data.get('op_income', []),
                'ordinary_income': stock_data.get('ordinary_income', []),
                'net_income': stock_data.get('net_income', []),
                'eps': stock_data.get('eps', []),
                'dps': stock_data.get('dps', []),
                'payout_ratio': stock_data.get('payout_ratio', [])
            }

            # CF履歴をJSON形式で保存
            cf_history = {
                'operating_cf': stock_data.get('operating_cf', []),
                'investing_cf': stock_data.get('investing_cf', []),
                'financing_cf': stock_data.get('financing_cf', []),
                'cash': stock_data.get('cash', []),
                'current_liabilities': stock_data.get('current_liabilities_list', []),
                'current_assets': stock_data.get('current_assets_list', []),
                'equity_ratio': stock_data.get('equity_ratio_list', []),
                'roe': stock_data.get('roe', []),
                'roa': stock_data.get('roa', [])
            }

            # Noneのフィールドを除外して構築（既存データをnullで上書きしない）
            screened_data_full = {
                'company_code': company_code,
                'company_name': stock_data.get('name_jp') or stock_data.get('name', ''),
                'sector': stock_data.get('sector_jp') or stock_data.get('sector', ''),
                'market_cap': market_cap_oku,
                'stock_price': stock_data.get('last_price'),

                # 売上高（億円単位）
                'revenue_cy': to_oku(revenue_vals[0]),
                'revenue_1y': to_oku(revenue_vals[1]),
                'revenue_2y': to_oku(revenue_vals[2]),

                # 営業利益（億円単位）
                'op_cy': to_oku(op_vals[0]),
                'op_1y': to_oku(op_vals[1]),
                'op_2y': to_oku(op_vals[2]),

                # キャッシュフロー（億円単位）
                'operating_cf': to_oku(operating_cf),
                'investing_cf': to_oku(investing_cf),
                'free_cf': to_oku(operating_cf + investing_cf) if operating_cf and investing_cf else None,

                # その他財務
                'net_income': to_oku(net_income),
                'cash': to_oku(cash),
                'current_liabilities': to_oku(current_liabilities),
                'current_assets': to_oku(current_assets),
                'current_ratio': current_ratio,

                # 指標
                'equity_ratio': get_latest_value(stock_data.get('equity_ratio_pct')),
                'operating_margin': get_latest_value(stock_data.get('op_margin_pct')),
                'roe': roe,
                'roa': get_latest_value(stock_data.get('roa')),
                'per_forward': stock_data.get('per'),
                'pbr': stock_data.get('pbr'),
                'dividend_yield': stock_data.get('dividend_yield'),
                'eps': eps,
                'dps': dps,
                'payout_ratio': payout_ratio,

                # 信用取引
                'margin_trading_ratio': stock_data.get('margin_trading_ratio'),
                'margin_trading_buy': stock_data.get('margin_trading_buy'),
                'margin_trading_sell': stock_data.get('margin_trading_sell'),

                # 業績予想（Yahoo Finance Japan）
                'forecast_revenue': stock_data.get('forecast_revenue'),
                'forecast_op_income': stock_data.get('forecast_op_income'),
                'forecast_ordinary_income': stock_data.get('forecast_ordinary_income'),
                'forecast_net_income': stock_data.get('forecast_net_income'),
                'forecast_year': stock_data.get('forecast_year'),

                # 事業概要
                'business_summary': stock_data.get('business_summary'),
                'business_summary_jp': stock_data.get('business_summary_jp'),

                # 株主・役員情報（JSON）
                'major_holders': json.dumps(stock_data.get('major_holders', []), ensure_ascii=False) if stock_data.get('major_holders') else None,
                'institutional_holders': json.dumps(stock_data.get('institutional_holders', []), ensure_ascii=False) if stock_data.get('institutional_holders') else None,
                'company_officers': json.dumps(stock_data.get('company_officers', []), ensure_ascii=False) if stock_data.get('company_officers') else None,
                'major_shareholders_jp': json.dumps(stock_data.get('major_shareholders_jp', []), ensure_ascii=False) if stock_data.get('major_shareholders_jp') else None,

                # 財務履歴（JSON）
                'financial_history': json.dumps(financial_history, ensure_ascii=False),
                'cf_history': json.dumps(cf_history, ensure_ascii=False),

                'data_source': 'yfinance',
                'data_status': 'fresh'
            }

            # Noneのフィールドを除外（既存データを保護）、ただしcompany_codeは必須
            screened_data = {k: v for k, v in screened_data_full.items() if v is not None or k == 'company_code'}

            # 合致度を自動計算して保存
            upsert_screened_data_with_match_rate(screened_data)

        return jsonify({"success": True, "company_code": company_code}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/remove/<company_code>', methods=['DELETE'])
def api_remove_from_watchlist(company_code):
    """銘柄をウォッチリストから削除"""
    try:
        company_code = normalize_code(company_code)
        remove_from_watchlist(company_code)
        return jsonify({"success": True, "company_code": company_code}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/remove-all', methods=['DELETE'])
def api_remove_all_from_watchlist():
    """ウォッチリストを全件削除"""
    try:
        client = get_supabase_client()
        client.table('watched_tickers').delete().neq('company_code', '').execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/check/<company_code>', methods=['GET'])
def api_check_watchlist(company_code):
    """銘柄がウォッチリストに登録されているか確認"""
    try:
        company_code = normalize_code(company_code)
        is_registered = is_in_watchlist(company_code)
        return jsonify({"is_registered": is_registered, "company_code": company_code}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/update', methods=['POST'])
def api_update_watchlist():
    """screened_latestのデータを更新（編集機能用）"""
    try:
        data = request.get_json()
        if not data or 'company_code' not in data:
            return jsonify({"error": "銘柄コードが指定されていません"}), 400

        company_code = normalize_code(data['company_code'])
        edited_data = data.get('edited_data', {})

        if not edited_data:
            return jsonify({"error": "更新データがありません"}), 400

        # 更新用データを構築
        update_data = {}

        # 主要指標
        if 'equity_ratio' in edited_data:
            update_data['equity_ratio'] = edited_data['equity_ratio']
        if 'operating_margin' in edited_data:
            update_data['operating_margin'] = edited_data['operating_margin']
        if 'per_forward' in edited_data:
            update_data['per_forward'] = edited_data['per_forward']
        if 'pbr' in edited_data:
            update_data['pbr'] = edited_data['pbr']
        if 'dividend_yield' in edited_data:
            update_data['dividend_yield'] = edited_data['dividend_yield']
        if 'market_cap' in edited_data:
            # 億円単位に変換
            update_data['market_cap'] = edited_data['market_cap'] / 1e8 if edited_data['market_cap'] else None

        # 財務履歴をJSON形式で更新
        if 'financial_history' in edited_data:
            update_data['financial_history'] = json.dumps(edited_data['financial_history'], ensure_ascii=False)

        # CF履歴をJSON形式で更新
        if 'cf_history' in edited_data:
            update_data['cf_history'] = json.dumps(edited_data['cf_history'], ensure_ascii=False)

        if update_data:
            # 合致度を再計算するため、既存データを取得してマージ
            from supabase_client import get_screened_data
            existing_data = get_screened_data(company_code) or {}
            merged_data = {**existing_data, **update_data}
            update_data['match_rate'] = calculate_match_rate(merged_data)

            update_screened_data(company_code, update_data)
            return jsonify({"success": True, "company_code": company_code, "updated_fields": list(update_data.keys())}), 200
        else:
            return jsonify({"error": "有効な更新データがありません"}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 株式データAPI エンドポイント
# タイムアウト設定（秒）
ANALYZE_TIMEOUT = 60

@app.route('/api/stock/analyze', methods=['POST'])
def analyze_stock():
    """
    株式データを分析してJSON形式で返す
    タイムアウト処理付き（60秒）
    """
    try:
        # リクエストデータ取得
        data = request.get_json()
        if not data or 'symbol' not in data:
            return jsonify({"error": "銘柄コードが指定されていません"}), 400

        symbol = data['symbol']
        period = data.get('period', '1y')

        # 銘柄コードの簡易バリデーション
        if not symbol or len(symbol) < 1:
            return jsonify({"error": "無効な銘柄コードです"}), 400

        # タイムアウト付きで分析実行
        def run_analysis():
            analyzer = StockAnalyzer()
            return analyzer.analyze(symbol, period=period)

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_analysis)
                result = future.result(timeout=ANALYZE_TIMEOUT)
        except FuturesTimeoutError:
            print(f"タイムアウト: {symbol}の分析が{ANALYZE_TIMEOUT}秒を超えました")
            return jsonify({
                "error": f"データ取得がタイムアウトしました（{ANALYZE_TIMEOUT}秒）。時間をおいて再度お試しください。",
                "symbol": symbol,
                "timeout": True
            }), 504

        # エラーチェック
        if result.get("error"):
            return jsonify({"error": result["error"]}), 500

        # チャート画像をBase64エンコード（存在する場合）
        if result.get("chart_png") and os.path.exists(result["chart_png"]):
            try:
                with open(result["chart_png"], "rb") as img_file:
                    chart_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                    result["chart_base64"] = f"data:image/png;base64,{chart_base64}"
            except:
                pass

        # 分析結果をscreened_latestに自動保存（サーバー側）
        try:
            _save_analysis_to_screened(symbol, result)
        except Exception as save_err:
            print(f"分析結果の自動保存エラー: {save_err}")

        # GC/DC日付を付与（screened_latest永続日付を優先、signal_stocksで補完）
        try:
            code = normalize_code(symbol)
            screened = get_screened_data(code)
            saved_gc = screened.get('gc_date') if screened else None
            saved_dc = screened.get('dc_date') if screened else None
            if not saved_gc or not saved_dc:
                client = get_supabase_client()
                sig = client.table('signal_stocks').select('gc_date,dc_date').eq(
                    'company_code', code
                ).execute()
                if sig.data:
                    s = sig.data[0]
                    saved_gc = saved_gc or s.get('gc_date')
                    saved_dc = saved_dc or s.get('dc_date')
            result['gc_date'] = saved_gc
            result['dc_date'] = saved_dc
        except:
            pass

        return jsonify(result), 200

    except Exception as e:
        print(f"分析エラー: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/stock/batch', methods=['POST'])
def analyze_stocks_batch():
    """
    複数銘柄を一括分析
    """
    try:
        # リクエストデータ取得
        data = request.get_json()
        if not data or 'symbols' not in data:
            return jsonify({"error": "銘柄コードリストが指定されていません"}), 400
            
        symbols = data['symbols']
        
        if not isinstance(symbols, list) or len(symbols) == 0:
            return jsonify({"error": "無効な銘柄コードリストです"}), 400
            
        # 最大200銘柄まで
        if len(symbols) > 200:
            return jsonify({"error": "一度に分析できるのは200銘柄までです"}), 400
            
        # バッチ分析実行
        results = batch_analyze(symbols)
        
        # チャート画像をBase64エンコード
        for result in results:
            if result.get("chart_png") and os.path.exists(result["chart_png"]):
                try:
                    with open(result["chart_png"], "rb") as img_file:
                        chart_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                        result["chart_base64"] = f"data:image/png;base64,{chart_base64}"
                except:
                    pass
                    
        return jsonify({"results": results}), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/stock/cache/<symbol>', methods=['GET'])
def get_cached_analysis(symbol):
    """
    キャッシュされた分析結果を取得
    """
    try:
        # ファイル名のサニタイズ
        safe_symbol = symbol.replace('.', '_')
        json_file = f"output/snapshot_{safe_symbol}.json"
        
        if not os.path.exists(json_file):
            return jsonify({"error": "データが見つかりません"}), 404
            
        with open(json_file, 'r', encoding='utf-8') as f:
            result = json.load(f)
            
        # チャート画像をBase64エンコード（存在する場合）
        if result.get("chart_png") and os.path.exists(result["chart_png"]):
            try:
                with open(result["chart_png"], "rb") as img_file:
                    chart_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                    result["chart_base64"] = f"data:image/png;base64,{chart_base64}"
            except:
                pass
                
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# GC銘柄API
@app.route('/api/gc-stocks', methods=['GET'])
def api_get_gc_stocks():
    """保存済みGC銘柄一覧を取得（signal_stocks統合テーブル、表示用フィルタ適用）"""
    try:
        data = get_signal_gc_stocks()

        display_data = []
        for item in data:
            # 表示用フィルタ: PER/PBR両方なし(ETF等)、PER>=40、PBR>=10 は非表示
            per = item.get('per')
            pbr = item.get('pbr')
            if per is None and pbr is None:
                continue
            if per is not None and per >= 40:
                continue
            if pbr is not None and pbr >= 10:
                continue
            display_data.append(item)

        return jsonify({"gc_stocks": display_data}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/gc-stocks/scrape', methods=['POST'])
def api_scrape_gc_stocks():
    """kabutan.jpからGC銘柄をスクレイピングしてsignal_stocksに保存"""
    try:
        from datetime import datetime, timezone
        stocks = scrape_gc_stocks()

        now = datetime.now(timezone.utc).isoformat()
        for s in stocks:
            s['gc_date'] = now

        # signal_stocksにupsert（既存のdc_dateは保持される）
        upsert_signal_stocks(stocks)

        # screened_latestにもGC形成日を永続保存
        try:
            client = get_supabase_client()
            codes = [s['company_code'] for s in stocks]
            for code in codes:
                client.table('screened_latest').update(
                    {'gc_date': now}
                ).eq('company_code', code).execute()
        except Exception as e:
            print(f"GC日付の永続保存エラー: {e}")

        return jsonify({
            "success": True,
            "count": len(stocks),
            "gc_stocks": stocks
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _convert_timestamps(obj):
    """Pandas Timestamp/numpy型を再帰的にJSON化可能な型に変換"""
    import pandas as pd
    import numpy as np
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _convert_timestamps(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_timestamps(item) for item in obj]
    return obj


def _save_analysis_to_screened(symbol, stock_data):
    """フル分析結果をscreened_latestに保存（サーバー側で確実に保存）"""
    company_code = normalize_code(symbol)

    market_cap_raw = stock_data.get('market_cap')
    market_cap_oku = market_cap_raw / 1e8 if market_cap_raw else None

    revenue_vals = get_yearly_values(stock_data.get('revenue'), 4)
    op_vals = get_yearly_values(stock_data.get('op_income'), 4)

    operating_cf = get_latest_value(stock_data.get('operating_cf'))
    investing_cf = get_latest_value(stock_data.get('investing_cf'))
    financing_cf = get_latest_value(stock_data.get('financing_cf'))
    net_income = get_latest_value(stock_data.get('net_income'))
    cash = get_latest_value(stock_data.get('cash'))
    current_liabilities = get_latest_value(stock_data.get('current_liabilities_list'))
    current_assets = get_latest_value(stock_data.get('current_assets_list'))

    current_ratio = None
    if current_assets and current_liabilities and current_liabilities > 0:
        current_ratio = (current_assets / current_liabilities) * 100

    eps = get_latest_value(stock_data.get('eps'))
    dps = get_latest_value(stock_data.get('dps'))
    payout_ratio = get_latest_value(stock_data.get('payout_ratio'))
    roe = get_latest_value(stock_data.get('roe'))

    financial_history = {
        'revenue': stock_data.get('revenue', []),
        'op_income': stock_data.get('op_income', []),
        'ordinary_income': stock_data.get('ordinary_income', []),
        'net_income': stock_data.get('net_income', []),
        'eps': stock_data.get('eps', []),
        'dps': stock_data.get('dps', []),
        'payout_ratio': stock_data.get('payout_ratio', [])
    }

    cf_history = {
        'operating_cf': stock_data.get('operating_cf', []),
        'investing_cf': stock_data.get('investing_cf', []),
        'financing_cf': stock_data.get('financing_cf', []),
        'cash': stock_data.get('cash', []),
        'current_liabilities': stock_data.get('current_liabilities_list', []),
        'current_assets': stock_data.get('current_assets_list', []),
        'equity_ratio': stock_data.get('equity_ratio_list', []),
        'roe': stock_data.get('roe', []),
        'roa': stock_data.get('roa', [])
    }

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    screened_data_full = {
        'company_code': company_code,
        'company_name': stock_data.get('name_jp') or stock_data.get('name', ''),
        'sector': stock_data.get('sector_jp') or stock_data.get('sector', ''),
        'market_cap': market_cap_oku,
        'stock_price': stock_data.get('last_price'),
        'revenue_cy': to_oku(revenue_vals[0]),
        'revenue_1y': to_oku(revenue_vals[1]),
        'revenue_2y': to_oku(revenue_vals[2]),
        'op_cy': to_oku(op_vals[0]),
        'op_1y': to_oku(op_vals[1]),
        'op_2y': to_oku(op_vals[2]),
        'operating_cf': to_oku(operating_cf),
        'investing_cf': to_oku(investing_cf),
        'free_cf': to_oku(operating_cf + investing_cf) if operating_cf and investing_cf else None,
        'net_income': to_oku(net_income),
        'cash': to_oku(cash),
        'current_liabilities': to_oku(current_liabilities),
        'current_assets': to_oku(current_assets),
        'current_ratio': current_ratio,
        'equity_ratio': get_latest_value(stock_data.get('equity_ratio_pct')),
        'operating_margin': get_latest_value(stock_data.get('op_margin_pct')),
        'roe': roe,
        'roa': get_latest_value(stock_data.get('roa')),
        'per_forward': stock_data.get('per'),
        'pbr': stock_data.get('pbr'),
        'dividend_yield': stock_data.get('dividend_yield'),
        'eps': eps,
        'dps': dps,
        'payout_ratio': payout_ratio,
        'margin_trading_ratio': stock_data.get('margin_trading_ratio'),
        'margin_trading_buy': stock_data.get('margin_trading_buy'),
        'margin_trading_sell': stock_data.get('margin_trading_sell'),
        'forecast_revenue': stock_data.get('forecast_revenue'),
        'forecast_op_income': stock_data.get('forecast_op_income'),
        'forecast_ordinary_income': stock_data.get('forecast_ordinary_income'),
        'forecast_net_income': stock_data.get('forecast_net_income'),
        'forecast_year': _convert_timestamps(stock_data.get('forecast_year')),
        'business_summary': stock_data.get('business_summary'),
        'business_summary_jp': stock_data.get('business_summary_jp'),
        'major_holders': json.dumps(_convert_timestamps(stock_data.get('major_holders', [])), ensure_ascii=False) if stock_data.get('major_holders') else None,
        'institutional_holders': json.dumps(_convert_timestamps(stock_data.get('institutional_holders', [])), ensure_ascii=False) if stock_data.get('institutional_holders') else None,
        'company_officers': json.dumps(_convert_timestamps(stock_data.get('company_officers', [])), ensure_ascii=False) if stock_data.get('company_officers') else None,
        'major_shareholders_jp': json.dumps(_convert_timestamps(stock_data.get('major_shareholders_jp', [])), ensure_ascii=False) if stock_data.get('major_shareholders_jp') else None,
        'financial_history': json.dumps(_convert_timestamps(financial_history), ensure_ascii=False),
        'cf_history': json.dumps(_convert_timestamps(cf_history), ensure_ascii=False),
        'analyzed_at': now,
        'data_source': 'yfinance',
        'data_status': 'fresh'
    }

    # Noneのフィールドを除外（既存データを保護）
    screened_data = {k: v for k, v in screened_data_full.items() if v is not None or k == 'company_code'}

    upsert_screened_data_with_match_rate(screened_data)
    print(f"分析結果をscreened_latestに保存しました: {company_code} ({len(screened_data)}フィールド)")

    # signal_stocksにも反映（テクニカル分析タブ用）
    try:
        signal_update = {k: v for k, v in {
            'company_name': screened_data.get('company_name'),
            'sector': screened_data.get('sector'),
            'market_cap': market_cap_oku,
            'stock_price': stock_data.get('last_price'),
            'per': stock_data.get('per'),
            'pbr': stock_data.get('pbr'),
            'dividend_yield': stock_data.get('dividend_yield'),
            'match_rate': screened_data.get('match_rate'),
            'analyzed_at': now,
        }.items() if v is not None}
        if signal_update:
            client = get_supabase_client()
            client.table('signal_stocks').update(signal_update).eq(
                'company_code', company_code
            ).execute()
    except Exception as e:
        print(f"signal_stocks更新エラー: {e}")


# バックグラウンド分析の進捗管理
import threading
gc_analyze_status = {"running": False, "done": 0, "total": 0, "errors": 0, "stop_requested": False}
wl_analyze_status = {"running": False, "done": 0, "total": 0, "errors": 0, "stop_requested": False}

def _analyze_stock_and_save(analyzer, company_code):
    """1銘柄を分析してscreened_latestに保存。成功時にscreened_dataを返す。
    company_codeは '7203.T' でも '7203' でもOK。"""
    symbol = company_code if company_code.endswith('.T') else f"{company_code}.T"
    code = normalize_code(company_code)  # DB保存用（.Tなしで統一）
    stock_data = analyzer.analyze(symbol, skip_chart=True, skip_extras=True)

    if not stock_data.get('name'):
        return None

    market_cap_oku = None
    if stock_data.get('market_cap'):
        market_cap_oku = round(stock_data['market_cap'] / 1e8, 1)

    operating_cf = get_latest_value(stock_data.get('operating_cf'))
    investing_cf = get_latest_value(stock_data.get('investing_cf'))

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    screened_data_full = {
        'company_code': code,
        'company_name': stock_data.get('name_jp') or stock_data.get('name', ''),
        'sector': stock_data.get('sector_jp') or stock_data.get('sector', ''),
        'market_cap': market_cap_oku,
        'stock_price': stock_data.get('last_price'),
        'equity_ratio': get_latest_value(stock_data.get('equity_ratio_pct')),
        'operating_margin': get_latest_value(stock_data.get('op_margin_pct')),
        'operating_cf': to_oku(operating_cf) if operating_cf else None,
        'free_cf': to_oku(operating_cf + investing_cf) if operating_cf and investing_cf else None,
        'roa': get_latest_value(stock_data.get('roa')),
        'per_forward': stock_data.get('per'),
        'pbr': stock_data.get('pbr'),
        'dividend_yield': stock_data.get('dividend_yield'),
        'analyzed_at': now,
        'forecast_revenue': stock_data.get('forecast_revenue'),
        'forecast_op_income': stock_data.get('forecast_op_income'),
        'forecast_ordinary_income': stock_data.get('forecast_ordinary_income'),
        'forecast_net_income': stock_data.get('forecast_net_income'),
        'forecast_year': stock_data.get('forecast_year'),
        'data_source': 'yfinance',
        'data_status': 'fresh'
    }

    # Noneのフィールドを除外（フル分析で保存済みのデータを上書きしない）
    screened_data = {k: v for k, v in screened_data_full.items() if v is not None or k == 'company_code'}

    upsert_screened_data_with_match_rate(screened_data)
    return {**screened_data, 'raw': stock_data}


def _analyze_gc_background(codes):
    """GC銘柄をバックグラウンドで1銘柄ずつ分析"""
    global gc_analyze_status
    analyzer = StockAnalyzer()

    for i, code in enumerate(codes):
        if gc_analyze_status["stop_requested"]:
            break

        try:
            result = _analyze_stock_and_save(analyzer, code)
            if result:
                client = get_supabase_client()
                client.table('signal_stocks').update({
                    'sector': result.get('sector'),
                    'market_cap': result.get('market_cap'),
                    'dividend_yield': result['raw'].get('dividend_yield'),
                    'match_rate': result.get('match_rate'),
                    'analyzed_at': result.get('analyzed_at'),
                }).eq('company_code', code).execute()
            else:
                gc_analyze_status["errors"] += 1
        except Exception as e:
            print(f"GC分析エラー ({code}): {e}")
            gc_analyze_status["errors"] += 1

        gc_analyze_status["done"] += 1
        if i < len(codes) - 1:
            import time
            time.sleep(0.35)

    gc_analyze_status["running"] = False
    gc_analyze_status["stop_requested"] = False


def _analyze_wl_background(codes):
    """ウォッチリスト銘柄をバックグラウンドで1銘柄ずつ分析"""
    global wl_analyze_status
    analyzer = StockAnalyzer()

    for i, code in enumerate(codes):
        if wl_analyze_status["stop_requested"]:
            break

        try:
            result = _analyze_stock_and_save(analyzer, code)
            if not result:
                wl_analyze_status["errors"] += 1
        except Exception as e:
            print(f"WL分析エラー ({code}): {e}")
            wl_analyze_status["errors"] += 1

        wl_analyze_status["done"] += 1
        if i < len(codes) - 1:
            import time
            time.sleep(0.35)

    wl_analyze_status["running"] = False
    wl_analyze_status["stop_requested"] = False


@app.route('/api/gc-stocks/analyze', methods=['POST'])
def api_analyze_gc_stocks():
    """GC銘柄の詳細分析をバックグラウンドで開始（未分析のみ）"""
    global gc_analyze_status
    try:
        if gc_analyze_status["running"]:
            return jsonify({
                "error": "分析が既に実行中です",
                "status": gc_analyze_status
            }), 409

        gc_stocks = get_signal_gc_stocks()
        if not gc_stocks:
            return jsonify({"error": "GC銘柄がありません。先に取得してください"}), 400

        # 今日未分析の銘柄のみ対象
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        codes = [s['company_code'] for s in gc_stocks
                 if not (s.get('analyzed_at') or '').startswith(today)]

        if not codes:
            return jsonify({
                "success": True,
                "message": "本日の分析は全銘柄完了済みです",
                "status": {"running": False, "done": 0, "total": 0, "errors": 0, "stop_requested": False}
            }), 200

        gc_analyze_status = {
            "running": True, "done": 0, "total": len(codes),
            "errors": 0, "stop_requested": False
        }

        thread = threading.Thread(target=_analyze_gc_background, args=(codes,), daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": f"未分析 {len(codes)}件の分析を開始しました",
            "status": gc_analyze_status
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/gc-stocks/analyze/stop', methods=['POST'])
def api_gc_analyze_stop():
    """GC分析を停止"""
    global gc_analyze_status
    if gc_analyze_status["running"]:
        gc_analyze_status["stop_requested"] = True
        return jsonify({"success": True, "message": "停止リクエストを送信しました"}), 200
    return jsonify({"success": True, "message": "分析は実行されていません"}), 200


@app.route('/api/gc-stocks/analyze/status', methods=['GET'])
def api_gc_analyze_status():
    """GC分析の進捗状況を取得"""
    return jsonify(gc_analyze_status), 200


# ウォッチリスト一括分析API
@app.route('/api/watchlist/analyze', methods=['POST'])
def api_analyze_watchlist():
    """ウォッチリスト銘柄の詳細分析をバックグラウンドで開始（未分析のみ）"""
    global wl_analyze_status
    try:
        if wl_analyze_status["running"]:
            return jsonify({
                "error": "分析が既に実行中です",
                "status": wl_analyze_status
            }), 409

        watchlist = get_watchlist_with_details()
        if not watchlist:
            return jsonify({"error": "ウォッチリストが空です"}), 400

        # 今日未分析の銘柄のみ対象（.T付きのまま渡す）
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        codes = [s['company_code'] for s in watchlist
                 if not (s.get('analyzed_at') or '').startswith(today)]

        if not codes:
            return jsonify({
                "success": True,
                "message": "本日の分析は全銘柄完了済みです",
                "status": {"running": False, "done": 0, "total": 0, "errors": 0, "stop_requested": False}
            }), 200

        wl_analyze_status = {
            "running": True, "done": 0, "total": len(codes),
            "errors": 0, "stop_requested": False
        }

        thread = threading.Thread(target=_analyze_wl_background, args=(codes,), daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": f"未分析 {len(codes)}件の分析を開始しました",
            "status": wl_analyze_status
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/analyze/stop', methods=['POST'])
def api_wl_analyze_stop():
    """ウォッチリスト分析を停止"""
    global wl_analyze_status
    if wl_analyze_status["running"]:
        wl_analyze_status["stop_requested"] = True
        return jsonify({"success": True, "message": "停止リクエストを送信しました"}), 200
    return jsonify({"success": True, "message": "分析は実行されていません"}), 200


@app.route('/api/watchlist/analyze/status', methods=['GET'])
def api_wl_analyze_status():
    """ウォッチリスト分析の進捗状況を取得"""
    return jsonify(wl_analyze_status), 200


# DC銘柄API
@app.route('/api/dc-stocks', methods=['GET'])
def api_get_dc_stocks():
    """保存済みDC銘柄一覧を取得（signal_stocks統合テーブル）"""
    try:
        data = get_signal_dc_stocks()
        return jsonify({"dc_stocks": data}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/dc-stocks/scrape', methods=['POST'])
def api_scrape_dc_stocks():
    """kabutan.jpからDC銘柄をスクレイピングしてsignal_stocksに保存"""
    try:
        from datetime import datetime, timezone
        stocks = scrape_dc_stocks()

        now = datetime.now(timezone.utc).isoformat()
        for s in stocks:
            s['dc_date'] = now

        # signal_stocksにupsert（既存のgc_dateは保持される）
        upsert_signal_stocks(stocks)

        # screened_latestにもDC形成日を永続保存
        try:
            client = get_supabase_client()
            codes = [s['company_code'] for s in stocks]
            for code in codes:
                client.table('screened_latest').update(
                    {'dc_date': now}
                ).eq('company_code', code).execute()
        except Exception as e:
            print(f"DC日付の永続保存エラー: {e}")

        return jsonify({
            "success": True,
            "count": len(stocks),
            "dc_stocks": stocks
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/technical-stocks', methods=['GET'])
def api_get_technical_stocks():
    """GC/DC形成日を持つ銘柄を一覧取得（signal_stocks + screened_latestマージ）"""
    try:
        client = get_supabase_client()

        # signal_stocksからGC/DC日付を持つ銘柄を取得
        signals = client.table('signal_stocks').select('*').or_(
            'gc_date.not.is.null,dc_date.not.is.null'
        ).order('company_code').execute()

        codes = [s['company_code'] for s in signals.data]

        # screened_latestから最新の財務データを取得
        screened_map = {}
        if codes:
            screened = client.table('screened_latest').select(
                'company_code,company_name,sector,market_cap,stock_price,'
                'per_forward,pbr,dividend_yield,match_rate,analyzed_at'
            ).in_('company_code', codes).execute()
            screened_map = {s['company_code']: s for s in screened.data}

        # マージ: screened_latestの財務データで補完
        result = []
        for sig in signals.data:
            code = sig['company_code']
            sc = screened_map.get(code, {})
            result.append({
                'company_code': code,
                'company_name': sc.get('company_name') or sig.get('company_name'),
                'sector': sc.get('sector') or sig.get('sector'),
                'market_cap': sc.get('market_cap') or sig.get('market_cap'),
                'stock_price': sc.get('stock_price') or sig.get('stock_price'),
                'per': sc.get('per_forward') or sig.get('per'),
                'pbr': sc.get('pbr') or sig.get('pbr'),
                'dividend_yield': sc.get('dividend_yield') or sig.get('dividend_yield'),
                'match_rate': sc.get('match_rate') or sig.get('match_rate'),
                'gc_date': sig.get('gc_date'),
                'dc_date': sig.get('dc_date'),
                'analyzed_at': sc.get('analyzed_at') or sig.get('analyzed_at'),
            })

        return jsonify({"technical_stocks": result}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/stock/summary-jp/<company_code>', methods=['POST'])
def api_retry_summary_jp(company_code):
    """日本語事業概要を再取得"""
    try:
        from jp_company_scraper import get_yahoo_japan_profile
        code = company_code.replace('.T', '').strip()
        yahoo_data = get_yahoo_japan_profile(code)
        summary_jp = yahoo_data.get('business_summary_jp')
        segments = yahoo_data.get('business_segments')
        if summary_jp and segments:
            summary_jp += f"<br>【連結事業】{segments}"
        elif segments:
            summary_jp = f"【連結事業】{segments}"
        if summary_jp:
            return jsonify({"business_summary_jp": summary_jp}), 200
        else:
            return jsonify({"error": "日本語の事業概要を取得できませんでした"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/stock/screened/<company_code>', methods=['GET'])
def api_get_screened_stock(company_code):
    """screened_latestから単一銘柄のキャッシュデータ取得（GC/DC日付付き）"""
    try:
        company_code = normalize_code(company_code)
        data = get_screened_data(company_code)
        if data:
            # screened_latestの永続日付を優先、signal_stocksで補完
            if not data.get('gc_date') or not data.get('dc_date'):
                client = get_supabase_client()
                sig = client.table('signal_stocks').select('gc_date,dc_date').eq(
                    'company_code', company_code
                ).execute()
                if sig.data:
                    s = sig.data[0]
                    data['gc_date'] = data.get('gc_date') or s.get('gc_date')
                    data['dc_date'] = data.get('dc_date') or s.get('dc_date')
            return jsonify(data), 200
        return jsonify({"error": "not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True)
