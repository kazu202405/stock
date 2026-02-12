import os
import json
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import uuid
from functools import wraps
from flask import jsonify, request, session
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
    get_signal_gc_stocks, get_signal_dc_stocks, upsert_signal_stocks,
    get_dividend_stocks, set_dividend_flag, remove_dividend_flag,
    add_favorite_stock, remove_favorite_stock, get_favorite_stocks, is_favorite_stock,
    create_note, get_user_notes, get_public_notes,
    get_notes_by_company, update_note, delete_note,
    create_user as create_app_user, authenticate_user, get_user_by_id,
    get_user_by_referral_code, get_direct_referrals, get_referral_tree,
    get_referral_chain, get_all_users, update_user_role, update_display_name,
    migrate_guest_notes, update_user_email, update_user_password,
    create_question, get_public_questions, get_questions_by_company,
    get_question_by_id, delete_question,
    create_answer, get_answers_for_question, delete_answer, set_best_answer,
    toggle_like, get_user_likes
)
from gc_scraper import scrape_gc_stocks, scrape_dc_stocks


# =============================================
# 認証ヘルパー関数
# =============================================

def get_current_user():
    """sessionからログインユーザーを取得。未ログインならNone"""
    user_id = session.get('user_id')
    if not user_id:
        return None
    return get_user_by_id(user_id)


def _resolve_display_name(item, user_map):
    """投稿アイテムの表示名を解決。poster_name > display_name > name の優先順"""
    if item.get('is_anonymous'):
        item['user_display_name'] = '匿名ユーザー'
    elif item.get('poster_name'):
        item['user_display_name'] = item['poster_name']
    else:
        user = user_map.get(item.get('user_id'))
        if user:
            item['user_display_name'] = user.get('display_name') or user.get('name', 'ユーザー')
        else:
            item['user_display_name'] = 'ユーザー'


def _build_user_map(user_ids):
    """ユーザーIDリストからID→ユーザー情報のマップを構築"""
    user_map = {}
    for uid in user_ids:
        try:
            user = get_user_by_id(uid)
            if user:
                user_map[uid] = user
        except Exception:
            pass
    return user_map


def _get_poster_name_for_session():
    """現在のセッションユーザーのデフォルト投稿名を取得"""
    user_id = session.get('user_id')
    if not user_id:
        return None
    try:
        user = get_user_by_id(user_id)
        if user:
            return user.get('display_name') or user.get('name')
    except Exception:
        pass
    return session.get('user_name')


def login_required_api(f):
    """API用ログイン必須デコレータ"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({"error": "ログインが必要です"}), 401
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    """指定ロール必須デコレータ（例: @role_required('agent', 'admin')）"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            try:
                user = get_current_user()
            except Exception as e:
                print(f"[role_required] get_current_user エラー: {e}")
                return jsonify({"error": f"ユーザー情報取得エラー: {e}"}), 500
            if not user:
                return jsonify({"error": "ログインが必要です"}), 401
            if user.get('role') not in roles:
                return jsonify({"error": f"権限がありません（現在: {user.get('role')}, 必要: {roles}）"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# =============================================
# 認証API
# =============================================

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    """ユーザー登録"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが指定されていません"}), 400

        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip()
        password = data.get('password') or ''
        referral_code = (data.get('referral_code') or '').strip()

        # バリデーション
        if not name:
            return jsonify({"error": "名前を入力してください"}), 400
        if not email:
            return jsonify({"error": "メールアドレスを入力してください"}), 400
        if len(password) < 6:
            return jsonify({"error": "パスワードは6文字以上で入力してください"}), 400

        user = create_app_user(
            name=name,
            email=email,
            password=password,
            referred_by_code=referral_code if referral_code else None
        )

        # ゲストノートの引き継ぎ
        guest_id = session.get('guest_user_id')
        migrated = 0
        if guest_id:
            migrated = migrate_guest_notes(guest_id, user['id'])
            session.pop('guest_user_id', None)

        # セッションにログイン状態を保存
        session['user_id'] = user['id']
        session['user_name'] = user['name']
        session['user_role'] = user['role']

        return jsonify({
            "success": True,
            "user": {
                "id": user['id'],
                "name": user['name'],
                "email": user['email'],
                "role": user['role'],
                "referral_code": user['referral_code'],
            },
            "migrated_notes": migrated
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """ログイン"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが指定されていません"}), 400

        email = (data.get('email') or '').strip()
        password = data.get('password') or ''

        if not email or not password:
            return jsonify({"error": "メールアドレスとパスワードを入力してください"}), 400

        user = authenticate_user(email, password)
        if not user:
            return jsonify({"error": "メールアドレスまたはパスワードが正しくありません"}), 401

        # ゲストノートの引き継ぎ
        guest_id = session.get('guest_user_id')
        migrated = 0
        if guest_id:
            migrated = migrate_guest_notes(guest_id, user['id'])
            session.pop('guest_user_id', None)

        # セッションにログイン状態を保存
        session['user_id'] = user['id']
        session['user_name'] = user['name']
        session['user_role'] = user['role']
        session.permanent = True

        return jsonify({
            "success": True,
            "user": {
                "id": user['id'],
                "name": user['name'],
                "email": user['email'],
                "role": user['role'],
                "referral_code": user['referral_code'],
            },
            "migrated_notes": migrated
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    """ログアウト"""
    session.pop('user_id', None)
    session.pop('user_name', None)
    session.pop('user_role', None)
    return jsonify({"success": True}), 200


@app.route('/api/auth/me', methods=['GET'])
def api_auth_me():
    """現在のユーザー情報取得"""
    user = get_current_user()
    if not user:
        return jsonify({"logged_in": False}), 200
    return jsonify({
        "logged_in": True,
        "user": {
            "id": user['id'],
            "name": user['name'],
            "email": user['email'],
            "role": user['role'],
            "referral_code": user['referral_code'],
            "display_name": user.get('display_name') or '',
        }
    }), 200


@app.route('/api/auth/display-name', methods=['PUT'])
@login_required_api
def api_update_display_name():
    """投稿名（display_name）を更新"""
    try:
        user_id = session['user_id']
        data = request.get_json()
        display_name = (data.get('display_name') or '').strip() if data else ''
        if display_name and len(display_name) > 30:
            return jsonify({"error": "投稿名は30文字以内にしてください"}), 400
        result = update_display_name(user_id, display_name if display_name else None)
        if result:
            return jsonify({"success": True, "display_name": result.get('display_name') or ''}), 200
        return jsonify({"error": "更新に失敗しました"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/auth/email', methods=['PUT'])
@login_required_api
def api_update_email():
    """メールアドレスを変更"""
    try:
        user_id = session['user_id']
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが指定されていません"}), 400

        new_email = (data.get('new_email') or '').strip()
        current_password = data.get('current_password') or ''

        if not new_email:
            return jsonify({"error": "新しいメールアドレスを入力してください"}), 400
        if not current_password:
            return jsonify({"error": "現在のパスワードを入力してください"}), 400

        result = update_user_email(user_id, new_email, current_password)
        if result:
            return jsonify({"success": True, "email": result.get('email', '')}), 200
        return jsonify({"error": "更新に失敗しました"}), 500
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/auth/password', methods=['PUT'])
@login_required_api
def api_update_password():
    """パスワードを変更"""
    try:
        user_id = session['user_id']
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが指定されていません"}), 400

        current_password = data.get('current_password') or ''
        new_password = data.get('new_password') or ''
        new_password_confirm = data.get('new_password_confirm') or ''

        if not current_password:
            return jsonify({"error": "現在のパスワードを入力してください"}), 400
        if not new_password:
            return jsonify({"error": "新しいパスワードを入力してください"}), 400
        if new_password != new_password_confirm:
            return jsonify({"error": "新しいパスワードが一致しません"}), 400

        result = update_user_password(user_id, current_password, new_password)
        if result:
            return jsonify({"success": True}), 200
        return jsonify({"error": "更新に失敗しました"}), 500
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================
# 紹介API
# =============================================

@app.route('/api/referrals/my', methods=['GET'])
@login_required_api
def api_my_referrals():
    """自分の直接紹介一覧"""
    try:
        user_id = session['user_id']
        referrals = get_direct_referrals(user_id)
        return jsonify({"referrals": referrals}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/referrals/tree', methods=['GET'])
@login_required_api
def api_referral_tree():
    """紹介ツリー取得"""
    try:
        user_id = session['user_id']
        tree = get_referral_tree(user_id)
        return jsonify({"tree": tree}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/referrals/code', methods=['GET'])
@login_required_api
def api_referral_code():
    """自分の紹介コード＋紹介リンク取得"""
    try:
        user = get_current_user()
        code = user['referral_code']
        link = f"{request.host_url}register?ref={code}"
        return jsonify({
            "referral_code": code,
            "referral_link": link,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/referrals/check/<code>', methods=['GET'])
def api_check_referral_code(code):
    """紹介コードの有効性確認（紹介者名を返す）"""
    try:
        user = get_user_by_referral_code(code)
        if user:
            return jsonify({"valid": True, "referrer_name": user['name']}), 200
        return jsonify({"valid": False}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================
# 管理者API
# =============================================

@app.route('/api/admin/users', methods=['GET'])
@role_required('admin')
def api_admin_users():
    """ユーザー一覧"""
    try:
        role_filter = request.args.get('role')
        users = get_all_users(role=role_filter)
        return jsonify({"users": users}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/users/<user_id>/role', methods=['PUT'])
@role_required('admin')
def api_admin_update_role(user_id):
    """ロール変更"""
    try:
        data = request.get_json()
        new_role = data.get('role')
        if not new_role:
            return jsonify({"error": "ロールを指定してください"}), 400
        user = update_user_role(user_id, new_role)
        if not user:
            return jsonify({"error": "ユーザーが見つかりません"}), 404
        return jsonify({"success": True, "user": user}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


def _fetch_and_save_gc_stocks():
    """GC銘柄をスクレイピングしてsignal_stocks+screened_latestに保存"""
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

    return stocks


def _fetch_and_save_dc_stocks():
    """DC銘柄をスクレイピングしてsignal_stocks+screened_latestに保存"""
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

    return stocks


@app.route('/api/gc-stocks/scrape', methods=['POST'])
def api_scrape_gc_stocks():
    """kabutan.jpからGC銘柄をスクレイピングしてsignal_stocksに保存"""
    try:
        stocks = _fetch_and_save_gc_stocks()
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

    # 財務履歴をJSON形式で保存（合致度計算に必要）
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
        'eps': get_latest_value(stock_data.get('eps')),
        'dps': get_latest_value(stock_data.get('dps')),
        'payout_ratio': get_latest_value(stock_data.get('payout_ratio')),
        'roe': get_latest_value(stock_data.get('roe')),
        'analyzed_at': now,
        'forecast_revenue': stock_data.get('forecast_revenue'),
        'forecast_op_income': stock_data.get('forecast_op_income'),
        'forecast_ordinary_income': stock_data.get('forecast_ordinary_income'),
        'forecast_net_income': stock_data.get('forecast_net_income'),
        'forecast_year': stock_data.get('forecast_year'),
        'financial_history': json.dumps(financial_history, ensure_ascii=False),
        'cf_history': json.dumps(cf_history, ensure_ascii=False),
        'data_source': 'yfinance',
        'data_status': 'fresh'
    }

    # デバッグログ: 配当性向データの確認
    pr_raw = stock_data.get('payout_ratio')
    pr_val = get_latest_value(pr_raw)
    print(f"[DEBUG] {code} payout_ratio raw={pr_raw}, latest={pr_val}, eps={get_latest_value(stock_data.get('eps'))}, dps={get_latest_value(stock_data.get('dps'))}")

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

        # forceパラメータで今日分析済みも再実行可能
        data = request.get_json(silent=True) or {}
        force = data.get('force', False)

        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if force:
            codes = [s['company_code'] for s in watchlist]
        else:
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


@app.route('/api/watchlist/recalculate', methods=['POST'])
def api_recalculate_match_rates():
    """ウォッチリスト全銘柄の合致度を既存データから再計算"""
    try:
        watchlist = get_watchlist_with_details()
        if not watchlist:
            return jsonify({"error": "ウォッチリストが空です"}), 400

        updated = 0
        for item in watchlist:
            code = item.get('company_code')
            if not code:
                continue
            existing = get_screened_data(code)
            if not existing:
                continue
            new_rate = calculate_match_rate(existing)
            old_rate = existing.get('match_rate')
            if new_rate != old_rate:
                update_screened_data(code, {'match_rate': new_rate})
                updated += 1

        return jsonify({
            "success": True,
            "message": f"{len(watchlist)}件中 {updated}件の合致度を更新しました"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        stocks = _fetch_and_save_dc_stocks()
        return jsonify({
            "success": True,
            "count": len(stocks),
            "dc_stocks": stocks
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/dividend-stocks', methods=['GET'])
def api_get_dividend_stocks():
    """高配当フラグが立っている銘柄を一覧取得"""
    try:
        stocks = get_dividend_stocks()
        return jsonify({"dividend_stocks": stocks}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/dividend-stocks/add', methods=['POST'])
def api_add_dividend_stock():
    """銘柄に高配当フラグを設定"""
    try:
        data = request.get_json()
        if not data or 'company_code' not in data:
            return jsonify({"error": "銘柄コードが指定されていません"}), 400

        company_code = normalize_code(data['company_code'])
        set_dividend_flag(company_code, True)
        return jsonify({"message": f"{company_code}を高配当企業に登録しました"}), 200
    except Exception as e:
        print(f"[高配当登録エラー] {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dividend-stocks/remove/<company_code>', methods=['DELETE'])
def api_remove_dividend_stock(company_code):
    """高配当フラグを解除"""
    try:
        company_code = normalize_code(company_code)
        remove_dividend_flag(company_code)
        return jsonify({"message": f"{company_code}の高配当フラグを解除しました"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


div_analyze_status = {"running": False, "done": 0, "total": 0, "errors": 0, "stop_requested": False}


def _analyze_div_background(codes):
    """高配当銘柄をバックグラウンドで1銘柄ずつ分析"""
    global div_analyze_status
    analyzer = StockAnalyzer()

    for i, code in enumerate(codes):
        if div_analyze_status["stop_requested"]:
            break

        try:
            result = _analyze_stock_and_save(analyzer, code)
            if not result:
                div_analyze_status["errors"] += 1
        except Exception as e:
            print(f"高配当分析エラー ({code}): {e}")
            div_analyze_status["errors"] += 1

        div_analyze_status["done"] += 1
        if i < len(codes) - 1:
            import time
            time.sleep(0.35)

    div_analyze_status["running"] = False
    div_analyze_status["stop_requested"] = False


@app.route('/api/dividend-stocks/analyze', methods=['POST'])
def api_analyze_dividend_stocks():
    """高配当銘柄の詳細分析をバックグラウンドで開始（未分析のみ）"""
    global div_analyze_status
    try:
        if div_analyze_status["running"]:
            return jsonify({
                "error": "分析が既に実行中です",
                "status": div_analyze_status
            }), 409

        stocks = get_dividend_stocks()
        if not stocks:
            return jsonify({"error": "高配当企業がありません"}), 400

        # forceパラメータで今日分析済みも再実行可能
        data = request.get_json(silent=True) or {}
        force = data.get('force', False)

        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if force:
            codes = [s['company_code'] for s in stocks]
        else:
            codes = [s['company_code'] for s in stocks
                     if not (s.get('analyzed_at') or '').startswith(today)]

        if not codes:
            return jsonify({
                "success": True,
                "message": "本日の分析は全銘柄完了済みです",
                "status": {"running": False, "done": 0, "total": 0, "errors": 0, "stop_requested": False}
            }), 200

        div_analyze_status = {
            "running": True, "done": 0, "total": len(codes),
            "errors": 0, "stop_requested": False
        }

        thread = threading.Thread(target=_analyze_div_background, args=(codes,), daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": f"未分析 {len(codes)}件の分析を開始しました",
            "status": div_analyze_status
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/dividend-stocks/analyze/stop', methods=['POST'])
def api_div_analyze_stop():
    """高配当分析を停止"""
    global div_analyze_status
    if div_analyze_status["running"]:
        div_analyze_status["stop_requested"] = True
        return jsonify({"success": True, "message": "停止リクエストを送信しました"}), 200
    return jsonify({"success": True, "message": "分析は実行されていません"}), 200


@app.route('/api/dividend-stocks/analyze/status', methods=['GET'])
def api_div_analyze_status():
    """高配当分析の進捗状況を取得"""
    return jsonify(div_analyze_status), 200


# =============================================
# お気に入り銘柄API
# =============================================

@app.route('/api/favorite-stocks', methods=['GET'])
def api_get_favorite_stocks():
    """お気に入り銘柄一覧を取得"""
    try:
        user_id = get_or_create_guest_user_id()
        stocks = get_favorite_stocks(user_id)
        return jsonify({"favorite_stocks": stocks}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/favorite-stocks/add', methods=['POST'])
def api_add_favorite_stock():
    """お気に入り銘柄を追加"""
    try:
        user_id = get_or_create_guest_user_id()
        data = request.get_json()
        if not data or 'company_code' not in data:
            return jsonify({"error": "銘柄コードが指定されていません"}), 400

        company_code = normalize_code(data['company_code'])
        add_favorite_stock(user_id, company_code)
        return jsonify({"message": f"{company_code}をお気に入りに追加しました"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/favorite-stocks/remove/<company_code>', methods=['DELETE'])
def api_remove_favorite_stock(company_code):
    """お気に入り銘柄を削除"""
    try:
        user_id = get_or_create_guest_user_id()
        company_code = normalize_code(company_code)
        remove_favorite_stock(user_id, company_code)
        return jsonify({"message": f"{company_code}をお気に入りから削除しました"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/favorite-stocks/check/<company_code>', methods=['GET'])
def api_check_favorite_stock(company_code):
    """お気に入り登録状態を確認"""
    try:
        user_id = get_or_create_guest_user_id()
        company_code = normalize_code(company_code)
        is_fav = is_favorite_stock(user_id, company_code)
        return jsonify({"is_favorite": is_fav}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


tech_analyze_status = {"running": False, "done": 0, "total": 0, "errors": 0, "stop_requested": False}


def _analyze_tech_background(codes):
    """テクニカル銘柄をバックグラウンドで1銘柄ずつ分析"""
    global tech_analyze_status
    analyzer = StockAnalyzer()

    for i, code in enumerate(codes):
        if tech_analyze_status["stop_requested"]:
            break

        try:
            result = _analyze_stock_and_save(analyzer, code)
            if result:
                # signal_stocksも更新
                try:
                    client = get_supabase_client()
                    client.table('signal_stocks').update({
                        'sector': result.get('sector'),
                        'market_cap': result.get('market_cap'),
                        'stock_price': result.get('raw', {}).get('last_price'),
                        'per': result.get('raw', {}).get('per'),
                        'pbr': result.get('raw', {}).get('pbr'),
                        'dividend_yield': result.get('raw', {}).get('dividend_yield'),
                        'match_rate': result.get('match_rate'),
                        'analyzed_at': result.get('analyzed_at'),
                    }).eq('company_code', code).execute()
                except Exception:
                    pass
            else:
                tech_analyze_status["errors"] += 1
        except Exception as e:
            print(f"テクニカル分析エラー ({code}): {e}")
            tech_analyze_status["errors"] += 1

        tech_analyze_status["done"] += 1
        if i < len(codes) - 1:
            import time
            time.sleep(0.35)

    tech_analyze_status["running"] = False
    tech_analyze_status["stop_requested"] = False


@app.route('/api/technical-stocks/analyze', methods=['POST'])
def api_analyze_technical_stocks():
    """テクニカル銘柄の詳細分析をバックグラウンドで開始（未分析のみ）"""
    global tech_analyze_status
    try:
        if tech_analyze_status["running"]:
            return jsonify({
                "error": "分析が既に実行中です",
                "status": tech_analyze_status
            }), 409

        # テクニカル銘柄一覧を取得
        client = get_supabase_client()
        signals = client.table('signal_stocks').select('company_code,analyzed_at').or_(
            'gc_date.not.is.null,dc_date.not.is.null'
        ).execute()
        stocks = signals.data or []

        if not stocks:
            return jsonify({"error": "テクニカル銘柄がありません"}), 400

        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        codes = [s['company_code'] for s in stocks
                 if not (s.get('analyzed_at') or '').startswith(today)]

        if not codes:
            return jsonify({
                "success": True,
                "message": "本日の分析は全銘柄完了済みです",
                "status": {"running": False, "done": 0, "total": 0, "errors": 0, "stop_requested": False}
            }), 200

        tech_analyze_status = {
            "running": True, "done": 0, "total": len(codes),
            "errors": 0, "stop_requested": False
        }

        thread = threading.Thread(target=_analyze_tech_background, args=(codes,), daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": f"未分析 {len(codes)}件の分析を開始しました",
            "status": tech_analyze_status
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/technical-stocks/analyze/stop', methods=['POST'])
def api_tech_analyze_stop():
    """テクニカル分析を停止"""
    global tech_analyze_status
    if tech_analyze_status["running"]:
        tech_analyze_status["stop_requested"] = True
        return jsonify({"success": True, "message": "停止リクエストを送信しました"}), 200
    return jsonify({"success": True, "message": "分析は実行されていません"}), 200


@app.route('/api/technical-stocks/analyze/status', methods=['GET'])
def api_tech_analyze_status():
    """テクニカル分析の進捗状況を取得"""
    return jsonify(tech_analyze_status), 200


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
            # screened_latestに保存
            try:
                update_screened_data(code, {'business_summary_jp': summary_jp})
                print(f"日本語事業概要を保存しました: {code}")
            except Exception as e:
                print(f"日本語事業概要の保存エラー: {e}")
            return jsonify({"business_summary_jp": summary_jp}), 200
        else:
            return jsonify({"error": "日本語の事業概要を取得できませんでした"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _fetch_live_price(company_code):
    """yfinanceから現在の株価を取得（内部ヘルパー）。成功時はfloat、失敗時はNoneを返す"""
    try:
        import yfinance as yf
        symbol = company_code if company_code.endswith('.T') else company_code + '.T'
        ticker = yf.Ticker(symbol)
        price = None
        try:
            fast_info = ticker.fast_info
            if hasattr(fast_info, 'last_price') and fast_info.last_price:
                price = float(fast_info.last_price)
        except Exception:
            pass
        if price is None:
            try:
                info = ticker.info
                raw = info.get('regularMarketPrice') or info.get('currentPrice')
                if raw is not None:
                    price = float(raw)
            except Exception:
                pass
        return price
    except Exception as e:
        print(f"[LivePrice] {company_code} 取得エラー: {e}")
        return None


def _fetch_live_price_with_fallback(company_code):
    """ライブ株価を取得し、成功時はscreened_latestも更新。失敗時はscreened_latestにフォールバック"""
    code = normalize_code(company_code)
    # まずライブ取得を試みる
    live_price = _fetch_live_price(code)
    if live_price is not None:
        # 成功: screened_latestにも書き戻す
        try:
            update_screened_data(code, {'stock_price': live_price})
        except Exception as e:
            print(f"[LivePrice] {code} screened_latest更新エラー: {e}")
        return live_price, True  # (価格, ライブかどうか)
    # フォールバック: screened_latestのキャッシュ価格
    stock = get_screened_data(code)
    if stock and stock.get('stock_price'):
        return float(stock['stock_price']), False
    return None, False


@app.route('/api/stock/current-price/<company_code>', methods=['GET'])
def api_get_current_price(company_code):
    """yfinanceから現在の株価のみを軽量取得"""
    try:
        code = normalize_code(company_code)
        price, is_live = _fetch_live_price_with_fallback(code)
        if price is not None:
            return jsonify({"company_code": code, "price": price, "is_live": is_live}), 200
        return jsonify({"error": "株価を取得できませんでした"}), 404
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


# =============================================
# 仮ユーザーID管理
# =============================================

def get_or_create_guest_user_id():
    """ログイン済みならそのユーザーID、未ログインならゲストIDを返す"""
    if session.get('user_id'):
        return session['user_id']
    if 'guest_user_id' not in session:
        session['guest_user_id'] = f"guest_{uuid.uuid4().hex[:8]}"
    return session['guest_user_id']


# =============================================
# ノートAPI
# =============================================

@app.route('/api/notes/my', methods=['GET'])
def api_get_my_notes():
    """自分のノート一覧を取得"""
    try:
        user_id = get_or_create_guest_user_id()
        notes = get_user_notes(user_id)
        return jsonify({"notes": notes, "user_id": user_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/notes', methods=['GET'])
def api_get_notes():
    """ノート一覧を取得（クエリパラメータで絞り込み）"""
    try:
        company_code = request.args.get('company_code')
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))

        if company_code:
            notes = get_notes_by_company(company_code)
        else:
            notes = get_public_notes(limit=limit, offset=offset)

        # ユーザー名を解決（poster_name > display_name > name）
        user_ids = list(set(
            n['user_id'] for n in notes
            if n.get('user_id') and not n.get('is_anonymous') and not n.get('poster_name')
        ))
        user_map = _build_user_map(user_ids)
        for note in notes:
            _resolve_display_name(note, user_map)

        return jsonify({"notes": notes}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/notes', methods=['POST'])
def api_create_note():
    """ノートを作成"""
    try:
        user_id = get_or_create_guest_user_id()
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが指定されていません"}), 400
        if not data.get('title') or not data.get('content'):
            return jsonify({"error": "タイトルと本文は必須です"}), 400

        note = create_note(user_id, data)
        return jsonify({"note": note}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/notes/<note_id>', methods=['PUT'])
def api_update_note(note_id):
    """ノートを更新"""
    try:
        user_id = get_or_create_guest_user_id()
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが指定されていません"}), 400

        note = update_note(note_id, user_id, data)
        if not note:
            return jsonify({"error": "ノートが見つかりません（または権限がありません）"}), 404
        return jsonify({"note": note}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/notes/<note_id>', methods=['DELETE'])
def api_delete_note(note_id):
    """ノートを削除"""
    try:
        user_id = get_or_create_guest_user_id()
        success = delete_note(note_id, user_id)
        if not success:
            return jsonify({"error": "ノートが見つかりません（または権限がありません）"}), 404
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/notes/tags', methods=['GET'])
def api_get_note_tags():
    """公開ノートから使われているタグ一覧を取得"""
    try:
        notes = get_public_notes(limit=200, offset=0)
        tag_count = {}
        for note in notes:
            for tag in (note.get('tags') or []):
                tag_count[tag] = tag_count.get(tag, 0) + 1
        # 使用回数の多い順にソート
        sorted_tags = sorted(tag_count.items(), key=lambda x: x[1], reverse=True)
        return jsonify({"tags": [{"name": t[0], "count": t[1]} for t in sorted_tags]}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================
# コミュニティQ&A API
# =============================================

@app.route('/api/community/questions', methods=['GET'])
def api_get_questions():
    """質問一覧を取得"""
    try:
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        filter_resolved = request.args.get('filter', 'all')
        company_code = request.args.get('company_code')

        if company_code:
            questions = get_questions_by_company(company_code)
        else:
            questions = get_public_questions(limit=limit, offset=offset, filter_resolved=filter_resolved)

        # ユーザー名を解決（poster_name > display_name > name）
        user_ids = list(set(
            q['user_id'] for q in questions
            if q.get('user_id') and not q.get('is_anonymous') and not q.get('poster_name')
        ))
        user_map = _build_user_map(user_ids)
        for q in questions:
            _resolve_display_name(q, user_map)

        # ログインユーザーのいいね状態を取得
        user_likes = {}
        current_user_id = session.get('user_id')
        if current_user_id and questions:
            q_ids = [q['id'] for q in questions]
            liked_set = get_user_likes(current_user_id, 'question', q_ids)
            user_likes = {qid: True for qid in liked_set}

        return jsonify({"questions": questions, "user_likes": user_likes}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/community/questions', methods=['POST'])
@login_required_api
def api_create_question():
    """質問を作成"""
    try:
        user_id = session['user_id']
        data = request.get_json()
        if not data or not data.get('title') or not data.get('content'):
            return jsonify({"error": "タイトルと本文は必須です"}), 400
        question = create_question(user_id, data)
        return jsonify({"question": question}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/community/questions/<question_id>', methods=['GET'])
def api_get_question_detail(question_id):
    """質問詳細＋回答一覧を取得"""
    try:
        question = get_question_by_id(question_id)
        if not question:
            return jsonify({"error": "質問が見つかりません"}), 404

        # 質問者名を解決（poster_name > display_name > name）
        q_user_ids = [] if question.get('is_anonymous') or question.get('poster_name') else [question['user_id']]
        answers = get_answers_for_question(question_id)
        ans_user_ids = [a['user_id'] for a in answers if a.get('user_id') and not a.get('is_anonymous') and not a.get('poster_name')]
        all_ids = list(set(q_user_ids + ans_user_ids))
        user_map = _build_user_map(all_ids)
        _resolve_display_name(question, user_map)
        for a in answers:
            _resolve_display_name(a, user_map)

        # ログインユーザーのいいね状態
        user_likes = {}
        current_user_id = session.get('user_id')
        if current_user_id:
            # 質問へのいいね
            q_liked = get_user_likes(current_user_id, 'question', [question_id])
            if question_id in q_liked:
                user_likes[question_id] = True
            # 回答へのいいね
            if answers:
                a_ids = [a['id'] for a in answers]
                a_liked = get_user_likes(current_user_id, 'answer', a_ids)
                for aid in a_liked:
                    user_likes[aid] = True

        return jsonify({
            "question": question,
            "answers": answers,
            "user_likes": user_likes,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/community/questions/<question_id>', methods=['DELETE'])
@login_required_api
def api_delete_question(question_id):
    """質問を削除（所有者のみ）"""
    try:
        user_id = session['user_id']
        success = delete_question(question_id, user_id)
        if not success:
            return jsonify({"error": "質問が見つかりません（または権限がありません）"}), 404
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/community/questions/<question_id>/answers', methods=['POST'])
@login_required_api
def api_create_answer(question_id):
    """回答を作成"""
    try:
        user_id = session['user_id']
        data = request.get_json()
        if not data or not data.get('content'):
            return jsonify({"error": "回答内容は必須です"}), 400
        # poster_nameが未指定ならデフォルト投稿名を設定
        if not data.get('poster_name') and not data.get('is_anonymous'):
            data['poster_name'] = _get_poster_name_for_session()
        answer = create_answer(question_id, user_id, data)
        # 回答者名を付与
        if answer.get('is_anonymous'):
            answer['user_display_name'] = '匿名ユーザー'
        elif answer.get('poster_name'):
            answer['user_display_name'] = answer['poster_name']
        else:
            answer['user_display_name'] = session.get('user_name', 'ユーザー')
        return jsonify({"answer": answer}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/community/questions/<question_id>/best-answer', methods=['PUT'])
@login_required_api
def api_set_best_answer(question_id):
    """ベストアンサーを設定（質問者のみ）"""
    try:
        user_id = session['user_id']
        data = request.get_json()
        answer_id = data.get('answer_id') if data else None
        if not answer_id:
            return jsonify({"error": "answer_idは必須です"}), 400
        success = set_best_answer(question_id, answer_id, user_id)
        if not success:
            return jsonify({"error": "権限がありません（質問者のみ設定可能です）"}), 403
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/community/answers/<answer_id>', methods=['DELETE'])
@login_required_api
def api_delete_answer(answer_id):
    """回答を削除（所有者のみ）"""
    try:
        user_id = session['user_id']
        success = delete_answer(answer_id, user_id)
        if not success:
            return jsonify({"error": "回答が見つかりません（または権限がありません）"}), 404
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/community/likes', methods=['POST'])
@login_required_api
def api_toggle_like():
    """いいねをトグル"""
    try:
        user_id = session['user_id']
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが指定されていません"}), 400
        target_type = data.get('target_type')
        target_id = data.get('target_id')
        if target_type not in ('question', 'answer') or not target_id:
            return jsonify({"error": "target_typeとtarget_idは必須です"}), 400
        result = toggle_like(user_id, target_type, target_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/community/questions/tags', methods=['GET'])
def api_get_question_tags():
    """質問から使われているタグ一覧を取得"""
    try:
        questions = get_public_questions(limit=200, offset=0)
        tag_count = {}
        for q in questions:
            for tag in (q.get('tags') or []):
                tag_count[tag] = tag_count.get(tag, 0) + 1
        sorted_tags = sorted(tag_count.items(), key=lambda x: x[1], reverse=True)
        return jsonify({"tags": [{"name": t[0], "count": t[1]} for t in sorted_tags]}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================
# スケジューラ: GC/DC銘柄の自動定期取得
# =============================================

from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import atexit

def scheduled_fetch_gc_dc():
    """定期実行: GC/DC銘柄を自動取得"""
    from datetime import datetime
    print(f"[Scheduler] GC/DC自動取得開始: {datetime.now()}")
    try:
        gc = _fetch_and_save_gc_stocks()
        print(f"[Scheduler] GC: {len(gc)}件取得")
    except Exception as e:
        print(f"[Scheduler] GCエラー: {e}")
    try:
        dc = _fetch_and_save_dc_stocks()
        print(f"[Scheduler] DC: {len(dc)}件取得")
    except Exception as e:
        print(f"[Scheduler] DCエラー: {e}")

def scheduled_update_stock_prices():
    """定期実行: screened_latest全銘柄の株価をyfinanceから更新"""
    from datetime import datetime
    import time
    print(f"[Scheduler] 株価バッチ更新開始: {datetime.now()}")
    try:
        client = get_supabase_client()
        # screened_latestの全銘柄コードを取得
        result = client.table('screened_latest').select('company_code').execute()
        codes = [r['company_code'] for r in result.data] if result.data else []
        print(f"[Scheduler] 対象銘柄数: {len(codes)}件")
        success_count = 0
        fail_count = 0
        for code in codes:
            try:
                price = _fetch_live_price(code)
                if price is not None:
                    client.table('screened_latest').update(
                        {'stock_price': price}
                    ).eq('company_code', code).execute()
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                print(f"[Scheduler] {code} 更新エラー: {e}")
                fail_count += 1
            time.sleep(0.35)  # レート制限回避
        print(f"[Scheduler] 株価バッチ更新完了: 成功{success_count}件, 失敗{fail_count}件")
    except Exception as e:
        print(f"[Scheduler] 株価バッチ更新エラー: {e}")


scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Tokyo'))
scheduler.add_job(scheduled_fetch_gc_dc, 'cron', hour=9, minute=15, id='gc_dc_morning')
scheduler.add_job(scheduled_fetch_gc_dc, 'cron', hour=17, minute=15, id='gc_dc_evening')
# 株価バッチ更新（9:25 / 11:45 / 15:20 JST）
scheduler.add_job(scheduled_update_stock_prices, 'cron', hour=9, minute=25, id='price_update_morning')
scheduler.add_job(scheduled_update_stock_prices, 'cron', hour=11, minute=45, id='price_update_midday')
scheduler.add_job(scheduled_update_stock_prices, 'cron', hour=15, minute=20, id='price_update_closing')
scheduler.start()
print("[Scheduler] スケジューラ起動（GC/DC: 9:15/17:15, 株価更新: 9:25/11:45/15:20 JST）")

# アプリ終了時にスケジューラも停止
atexit.register(lambda: scheduler.shutdown(wait=False))


@app.route('/api/scheduler/status', methods=['GET'])
def api_scheduler_status():
    """スケジューラの状態と次回実行時刻を取得"""
    try:
        jobs = []
        for job in scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                'trigger': str(job.trigger),
            })
        return jsonify({
            "running": scheduler.running,
            "jobs": jobs,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/scheduler/trigger', methods=['POST'])
def api_scheduler_trigger():
    """GC/DC取得を今すぐ手動実行（テスト用）"""
    try:
        thread = threading.Thread(target=scheduled_fetch_gc_dc, daemon=True)
        thread.start()
        return jsonify({
            "success": True,
            "message": "GC/DC自動取得をバックグラウンドで開始しました"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/scheduler/trigger-price-update', methods=['POST'])
def api_scheduler_trigger_price_update():
    """株価バッチ更新を今すぐ手動実行（テスト用）"""
    try:
        thread = threading.Thread(target=scheduled_update_stock_prices, daemon=True)
        thread.start()
        return jsonify({
            "success": True,
            "message": "株価バッチ更新をバックグラウンドで開始しました"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================
# 企業比較API
# =============================================

@app.route('/api/compare', methods=['GET'])
def api_compare():
    """2〜3社の財務指標を比較用に返却"""
    try:
        codes_param = request.args.get('codes', '')
        if not codes_param:
            return jsonify({"error": "銘柄コードを指定してください"}), 400

        codes = [c.strip() for c in codes_param.split(',') if c.strip()]
        if len(codes) < 2 or len(codes) > 3:
            return jsonify({"error": "2〜3銘柄を指定してください"}), 400

        results = []
        for code in codes:
            data = get_screened_data(normalize_code(code))
            if data:
                results.append(data)
            else:
                return jsonify({"error": f"{code} のデータがありません。先に分析してください。"}), 404

        return jsonify({"companies": results}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================
# セクター分析API
# =============================================

def _safe_avg(values):
    """None/非数値を除外して平均値を計算"""
    nums = [v for v in values if v is not None and isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 2) if nums else None


@app.route('/api/sector/summary', methods=['GET'])
def api_sector_summary():
    """セクター別集計データを返却"""
    try:
        client = get_supabase_client()
        result = client.table('screened_latest').select(
            'company_code,company_name,sector,market_cap,per_forward,pbr,'
            'dividend_yield,roe,operating_margin,equity_ratio,match_rate,'
            'payout_ratio,stock_price,roa'
        ).not_.is_('sector', 'null').execute()

        sector_map = {}
        for item in result.data:
            sector = item.get('sector')
            if not sector:
                continue
            if sector not in sector_map:
                sector_map[sector] = []
            sector_map[sector].append(item)

        summary = []
        for sector, companies in sorted(sector_map.items(), key=lambda x: len(x[1]), reverse=True):
            summary.append({
                'sector': sector,
                'count': len(companies),
                'avg_per': _safe_avg([c.get('per_forward') for c in companies]),
                'avg_pbr': _safe_avg([c.get('pbr') for c in companies]),
                'avg_dividend_yield': _safe_avg([c.get('dividend_yield') for c in companies]),
                'avg_roe': _safe_avg([c.get('roe') for c in companies]),
                'avg_operating_margin': _safe_avg([c.get('operating_margin') for c in companies]),
                'avg_equity_ratio': _safe_avg([c.get('equity_ratio') for c in companies]),
                'companies': companies
            })

        return jsonify({"sectors": summary}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================
# デモ売買API
# =============================================

def _get_demo_user_id():
    """デモ売買用のユーザーIDを取得（セッションベース）"""
    user_id = session.get('user_id')
    if not user_id:
        # ゲストユーザーの場合はセッションIDを使用
        if not session.get('demo_user_id'):
            session['demo_user_id'] = str(uuid.uuid4())
        user_id = session['demo_user_id']
    return user_id


def _get_or_create_demo_account(user_id):
    """デモ口座を取得（なければ作成）"""
    client = get_supabase_client()
    result = client.table('demo_account').select('*').eq('user_id', user_id).execute()
    if result.data:
        return result.data[0]
    # 新規作成（初期100万円）
    new_account = {'user_id': user_id, 'cash_balance': 1000000}
    client.table('demo_account').insert(new_account).execute()
    return new_account


@app.route('/api/demo/account', methods=['GET'])
def api_demo_account():
    """デモ口座情報（残高 + ポートフォリオ）を取得"""
    try:
        user_id = _get_demo_user_id()
        account = _get_or_create_demo_account(user_id)
        client = get_supabase_client()

        # ポートフォリオ取得
        portfolio = client.table('demo_portfolio').select('*').eq(
            'user_id', user_id
        ).order('created_at', desc=True).execute()

        # 各銘柄の現在価格をscreened_latestから取得
        holdings = []
        total_value = 0
        for p in portfolio.data:
            screened = get_screened_data(p['company_code'])
            current_price = screened.get('stock_price', 0) if screened else 0
            market_value = (current_price or 0) * p['shares']
            cost_value = p['avg_cost'] * p['shares']
            pnl = market_value - cost_value
            total_value += market_value
            holdings.append({
                **p,
                'current_price': current_price,
                'market_value': market_value,
                'pnl': pnl,
            })

        return jsonify({
            "cash_balance": float(account['cash_balance']),
            "total_value": total_value,
            "total_assets": float(account['cash_balance']) + total_value,
            "holdings": holdings,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/demo/buy', methods=['POST'])
def api_demo_buy():
    """デモ買い注文"""
    try:
        from datetime import datetime, timezone
        user_id = _get_demo_user_id()
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが指定されていません"}), 400

        code = normalize_code(data.get('company_code', ''))
        shares = int(data.get('shares', 0))
        reason = data.get('reason', '')

        if not code or shares <= 0:
            return jsonify({"error": "銘柄コードと株数を正しく指定してください"}), 400

        # 現在価格を取得（ライブ優先、失敗時はキャッシュにフォールバック）
        price, is_live = _fetch_live_price_with_fallback(code)
        stock = get_screened_data(code)
        if price is None:
            return jsonify({"error": f"{code} の価格データがありません。先に分析してください。"}), 404

        total = price * shares

        # 残高チェック
        account = _get_or_create_demo_account(user_id)
        cash = float(account['cash_balance'])
        if cash < total:
            return jsonify({"error": f"残高不足です（残高: ¥{cash:,.0f}、必要: ¥{total:,.0f}）"}), 400

        client = get_supabase_client()
        now = datetime.now(timezone.utc).isoformat()

        # 残高を減算
        client.table('demo_account').update({
            'cash_balance': cash - total
        }).eq('user_id', user_id).execute()

        # ポートフォリオを更新（既存保有があれば平均取得単価を再計算）
        existing = client.table('demo_portfolio').select('*').eq(
            'user_id', user_id
        ).eq('company_code', code).execute()

        if existing.data:
            old = existing.data[0]
            new_shares = old['shares'] + shares
            new_avg_cost = (float(old['avg_cost']) * old['shares'] + total) / new_shares
            client.table('demo_portfolio').update({
                'shares': new_shares,
                'avg_cost': new_avg_cost,
                'buy_reason': reason if reason else old.get('buy_reason', ''),
                'updated_at': now,
            }).eq('id', old['id']).execute()
        else:
            client.table('demo_portfolio').insert({
                'user_id': user_id,
                'company_code': code,
                'company_name': stock.get('company_name', ''),
                'shares': shares,
                'avg_cost': price,
                'buy_reason': reason,
            }).execute()

        # 売買履歴を記録
        client.table('demo_trades').insert({
            'user_id': user_id,
            'company_code': code,
            'company_name': stock.get('company_name', ''),
            'trade_type': 'buy',
            'shares': shares,
            'price': price,
            'total_amount': total,
            'reason': reason,
        }).execute()

        return jsonify({
            "success": True,
            "message": f"{stock.get('company_name', code)} を {shares}株 購入しました（¥{total:,.0f}）",
            "new_balance": cash - total,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/demo/sell', methods=['POST'])
def api_demo_sell():
    """デモ売り注文"""
    try:
        from datetime import datetime, timezone
        user_id = _get_demo_user_id()
        data = request.get_json()
        if not data:
            return jsonify({"error": "データが指定されていません"}), 400

        code = normalize_code(data.get('company_code', ''))
        shares = int(data.get('shares', 0))
        reason = data.get('reason', '')

        if not code or shares <= 0:
            return jsonify({"error": "銘柄コードと株数を正しく指定してください"}), 400

        client = get_supabase_client()

        # 保有チェック
        existing = client.table('demo_portfolio').select('*').eq(
            'user_id', user_id
        ).eq('company_code', code).execute()

        if not existing.data:
            return jsonify({"error": f"{code} を保有していません"}), 400

        holding = existing.data[0]
        if holding['shares'] < shares:
            return jsonify({"error": f"保有数（{holding['shares']}株）を超える売却はできません"}), 400

        # 現在価格を取得（ライブ優先、失敗時はキャッシュにフォールバック）
        price, is_live = _fetch_live_price_with_fallback(code)
        stock = get_screened_data(code)
        if price is None:
            return jsonify({"error": f"{code} の価格データがありません"}), 404

        total = price * shares

        # 残高を加算
        account = _get_or_create_demo_account(user_id)
        new_cash = float(account['cash_balance']) + total
        client.table('demo_account').update({
            'cash_balance': new_cash
        }).eq('user_id', user_id).execute()

        # ポートフォリオを更新
        remaining = holding['shares'] - shares
        if remaining == 0:
            client.table('demo_portfolio').delete().eq('id', holding['id']).execute()
        else:
            client.table('demo_portfolio').update({
                'shares': remaining,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }).eq('id', holding['id']).execute()

        # 売買履歴を記録
        client.table('demo_trades').insert({
            'user_id': user_id,
            'company_code': code,
            'company_name': stock.get('company_name', ''),
            'trade_type': 'sell',
            'shares': shares,
            'price': price,
            'total_amount': total,
            'reason': reason,
        }).execute()

        return jsonify({
            "success": True,
            "message": f"{stock.get('company_name', code)} を {shares}株 売却しました（¥{total:,.0f}）",
            "new_balance": new_cash,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/demo/history', methods=['GET'])
def api_demo_history():
    """デモ売買履歴を取得"""
    try:
        user_id = _get_demo_user_id()
        client = get_supabase_client()
        result = client.table('demo_trades').select('*').eq(
            'user_id', user_id
        ).order('traded_at', desc=True).limit(100).execute()
        return jsonify({"trades": result.data}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/demo/reset', methods=['POST'])
def api_demo_reset():
    """デモ口座をリセット（残高100万円、ポートフォリオ・履歴クリア）"""
    try:
        user_id = _get_demo_user_id()
        client = get_supabase_client()

        # ポートフォリオ削除
        client.table('demo_portfolio').delete().eq('user_id', user_id).execute()
        # 履歴削除
        client.table('demo_trades').delete().eq('user_id', user_id).execute()
        # 残高リセット
        client.table('demo_account').upsert({
            'user_id': user_id,
            'cash_balance': 1000000,
        }).execute()

        return jsonify({"success": True, "message": "デモ口座をリセットしました（残高: ¥1,000,000）"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True)
