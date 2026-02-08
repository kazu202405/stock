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
    add_to_watchlist, remove_from_watchlist, get_watchlist,
    is_in_watchlist, get_watchlist_with_details, upsert_screened_data,
    update_screened_data, upsert_screened_data_with_match_rate,
    calculate_match_rate,
    upsert_gc_stocks, get_gc_stocks,
    upsert_dc_stocks, get_dc_stocks
)
from gc_scraper import scrape_gc_stocks, scrape_dc_stocks


# ウォッチリストAPI
@app.route('/api/watchlist', methods=['GET'])
def api_get_watchlist():
    """登録銘柄一覧を取得"""
    try:
        data = get_watchlist_with_details()
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

        company_code = data['company_code']

        # ウォッチリストに追加
        add_to_watchlist(company_code)

        # screened_latestにも基本情報を保存（分析データがあれば）
        if 'stock_data' in data:
            stock_data = data['stock_data']

            # 配列データから最新値を抽出するヘルパー関数
            def get_latest_value(val):
                if val is None:
                    return None
                if isinstance(val, list) and len(val) > 0:
                    # 日付でソートして最新を取得
                    sorted_list = sorted(val, key=lambda x: x.get('date', ''), reverse=True)
                    return sorted_list[0].get('value')
                if isinstance(val, (int, float)):
                    return val
                return None

            # 時価総額を億円単位に変換
            market_cap_raw = stock_data.get('market_cap')
            market_cap_oku = market_cap_raw / 1e8 if market_cap_raw else None

            # 配列データから年度別の値を抽出（最新から順に取得）
            def get_yearly_values(data_list, count=4):
                """配列データから直近N年分の値を取得"""
                if not data_list or not isinstance(data_list, list):
                    return [None] * count
                sorted_list = sorted(data_list, key=lambda x: x.get('date', ''), reverse=True)
                values = [item.get('value') for item in sorted_list[:count]]
                # 足りない分はNoneで埋める
                while len(values) < count:
                    values.append(None)
                return values

            # 億円単位に変換する関数
            def to_oku(val):
                if val is None:
                    return None
                return val / 1e8

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

            screened_data = {
                'company_code': company_code,
                'company_name': stock_data.get('name_jp') or stock_data.get('name', ''),
                'sector': stock_data.get('sector_jp') or stock_data.get('sector', ''),
                'market_cap': market_cap_oku,
                'stock_price': stock_data.get('last_price'),

                # 売上高（億円単位）- 配列の順序: [最新, 1年前, 2年前, 3年前]
                'revenue_cy': to_oku(revenue_vals[0]),   # 今期（最新）
                'revenue_1y': to_oku(revenue_vals[1]),   # 前期
                'revenue_2y': to_oku(revenue_vals[2]),   # 2期前

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
            # 合致度を自動計算して保存
            upsert_screened_data_with_match_rate(screened_data)

        return jsonify({"success": True, "company_code": company_code}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/remove/<company_code>', methods=['DELETE'])
def api_remove_from_watchlist(company_code):
    """銘柄をウォッチリストから削除"""
    try:
        remove_from_watchlist(company_code)
        return jsonify({"success": True, "company_code": company_code}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/check/<company_code>', methods=['GET'])
def api_check_watchlist(company_code):
    """銘柄がウォッチリストに登録されているか確認"""
    try:
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

        company_code = data['company_code']
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
            
        # 最大10銘柄まで
        if len(symbols) > 10:
            return jsonify({"error": "一度に分析できるのは10銘柄までです"}), 400
            
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
    """保存済みGC銘柄一覧を取得"""
    try:
        data = get_gc_stocks()
        return jsonify({"gc_stocks": data}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/gc-stocks/scrape', methods=['POST'])
def api_scrape_gc_stocks():
    """kabutan.jpからGC銘柄をスクレイピングして保存"""
    try:
        from datetime import datetime, timezone
        stocks = scrape_gc_stocks()

        now = datetime.now(timezone.utc).isoformat()
        for s in stocks:
            s['scraped_at'] = now

        upsert_gc_stocks(stocks)

        return jsonify({
            "success": True,
            "count": len(stocks),
            "gc_stocks": stocks
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# DC銘柄API
@app.route('/api/dc-stocks', methods=['GET'])
def api_get_dc_stocks():
    """保存済みDC銘柄一覧を取得"""
    try:
        data = get_dc_stocks()
        return jsonify({"dc_stocks": data}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/dc-stocks/scrape', methods=['POST'])
def api_scrape_dc_stocks():
    """kabutan.jpからDC銘柄をスクレイピングして保存"""
    try:
        from datetime import datetime, timezone
        stocks = scrape_dc_stocks()

        now = datetime.now(timezone.utc).isoformat()
        for s in stocks:
            s['scraped_at'] = now

        upsert_dc_stocks(stocks)

        return jsonify({
            "success": True,
            "count": len(stocks),
            "dc_stocks": stocks
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True)
