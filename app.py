import os
import re
import json
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import uuid
from functools import wraps
from flask import jsonify, request, session, redirect
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
from html_safe import sanitize_rich_text

# 事業概要は改行のために <br> を持つのでHTMLとして出すが、中身は取得元の
# ページとLLMの出力で、こちらが決められない。テンプレートでは |safe ではなく
# こちらを使う（<br> だけ残して他は文字にする）。
def _safe_summary_filter(value):
    from markupsafe import Markup
    return Markup(sanitize_rich_text(value) or '')


app.jinja_env.filters['safe_summary'] = _safe_summary_filter
from analysis_quality import (
    analysis_data_status, build_cf_history, derive_fiscal_month,
    history_json_or_none, normalize_analysis_symbol,
)
from supabase_client import (
    get_supabase_client,
    add_to_watchlist, remove_from_watchlist, get_watchlist,
    is_in_watchlist, get_watchlist_with_details, upsert_screened_data,
    update_screened_data, upsert_screened_data_with_match_rate,
    calculate_match_rate, attach_score_quality, get_screened_data,
    get_technical_stocks, merge_source_status,
    get_learning_progress, mark_learning_understood, unmark_learning_understood,
    LearningProgressUnavailable,
    get_signal_gc_stocks, get_signal_dc_stocks, upsert_signal_stocks,
    get_dividend_stocks, set_dividend_flag, remove_dividend_flag,
    add_favorite_stock, remove_favorite_stock, get_favorite_stocks, is_favorite_stock,
    add_favorite_stocks, add_to_favorite_folder, remove_from_favorite_folder,
    set_favorite_folders, list_favorite_folders, count_unfiled_favorites,
    create_favorite_folder, rename_favorite_folder, delete_favorite_folder,
    create_note, get_user_notes, get_public_notes,
    get_notes_by_company, update_note, delete_note,
    get_user_by_id,
    get_user_by_referral_code, get_direct_referrals, get_referral_tree,
    get_referral_chain, get_all_users, update_user_role, update_display_name,
    migrate_guest_notes, update_gia_credential, ensure_app_user,
    create_question, get_public_questions, get_questions_by_company,
    get_question_by_id, delete_question,
    create_answer, get_answers_for_question, delete_answer, set_best_answer,
    toggle_like, get_user_likes
)
from gc_scraper import scrape_gc_stocks, scrape_dc_stocks


# =============================================
# APIレスポンスのキャッシュ抑止
# 一覧系API（/api/watchlist 等）はブラウザのHTTPキャッシュで古い結果が返り、
# 「登録したのに手動更新しないと表に出ない」症状の原因になり得るため、
# 動的APIには no-store を付与して常に最新を取得させる。
# =============================================

@app.after_request
def add_no_cache_headers(response):
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


# =============================================
# 正式ドメインへの集約
#
# 独自ドメインに移したあと、旧URL（*.onrender.com）に来た人と検索エンジンを
# 新ドメインへ301で送る。301は「恒久的な移転」の意味なので、検索側の評価も
# 引き継がれる。放置して両方のURLで同じ中身が見える状態にすると、評価が
# 二手に分かれるうえ重複コンテンツとして扱われる。
#
# CANONICAL_HOST が未設定のときは何もしない。DNSが通る前に有効化すると
# 存在しないドメインへ飛ばして全滅するため、切り替えは環境変数で行う。
# =============================================

CANONICAL_HOST = (os.getenv('CANONICAL_HOST') or '').strip().lower()


@app.before_request
def redirect_to_canonical_host():
    if not CANONICAL_HOST:
        return None

    host = (request.host or '').lower()
    if host == CANONICAL_HOST or host.startswith('127.0.0.1') or host.startswith('localhost'):
        return None

    # 死活監視はリダイレクトさせない。外部cronが旧URLを叩いている場合に、
    # 監視が落ちたと誤検知されるのを避ける
    if request.path.startswith('/health'):
        return None

    # full_path はクエリが無くても末尾に '?' を付けるので、有無で使い分ける
    path = request.full_path if request.query_string else request.path
    return redirect(f'https://{CANONICAL_HOST}{path}', code=301)


# =============================================
# ヘルスチェック（Supabase自動停止の防止用キープアライブ）
# =============================================

def _jobs_health():
    """定期実行が生きているかを (ok, problem) で返す。/health/* が共有する。"""
    import data_freshness
    try:
        # ⚠️ getattr で読むこと。**start() していないジョブには next_run_time が
        #    無く**、属性で直接読むと例外になる。そこで jobs=None に倒れると
        #    「読めなかった」に化けて、起動していないことが分からなくなる。
        #    None を並べて渡せば scheduler_item が「起動していません」を出す。
        jobs = [{'id': j.id,
                 'next_run_time': (getattr(j, 'next_run_time', None).isoformat()
                                   if getattr(j, 'next_run_time', None) else None)}
                for j in scheduler.get_jobs()]
    except Exception as e:
        print(f'[health] スケジューラの状態を取得できません: {str(e)[:120]}')
        jobs = None                  # 正常には倒さない
    return data_freshness.health(jobs)


@app.route('/health/db', methods=['GET'])
def health_db():
    """軽量DBクエリでSupabaseに触り、無料枠の自動一時停止を防ぐ。
    外部監視（UptimeRobot）が5分おきに叩いている。

    ⚠️ **定期実行の判定もここに相乗りさせている。** 外形監視の枠が1つしか
       無いため。DBに触る目的（無料枠の自動停止よけ）は先に果たしてあり、
       そのあとで503を返しても、監視は落ちている間も叩き続けるので
       キープアライブは効き続ける。

       ∴ このURLが赤いとき、原因は「DBに届かない」だけでなく
       「定期実行が止まっている」こともある。本文の problem で見分ける
       （db / scheduler / price）。
    """
    try:
        client = get_supabase_client()
        # 最小コストの読み取り（1行だけ）でDBアクティビティを発生させる
        client.table('watched_tickers').select('company_code').limit(1).execute()
    except Exception as e:
        # ⚠️ **例外文をそのまま返さない。** ここは未ログインで叩ける口なので、
        #    接続エラーの本文から接続先ホスト・ライブラリ・内部構成が読める。
        #    原因はサーバー側のログに残す。外へ出すのは「届かない」だけでよい
        #    （監視は status と problem しか見ていない）。
        print(f'[health/db] DBに届きません: {str(e)[:300]}')
        return jsonify({"status": "error", "db": "unreachable",
                        "problem": "db"}), 503

    try:
        ok, problem = _jobs_health()
    except Exception as e:
        # ⚠️ 判定できないことを正常として返すと、監視が黙って無効になる。
        print(f'[health/db] 定期実行の判定に失敗: {str(e)[:200]}')
        return jsonify({"status": "error", "db": "reachable"}), 503
    if not ok:
        print(f'[health/db] 定期実行の異常を検出: {problem}')
        return jsonify({"status": "stale", "db": "reachable",
                        "problem": problem}), 503
    return jsonify({"status": "ok", "db": "reachable"}), 200


@app.route('/health/jobs', methods=['GET'])
def health_jobs():
    """定期実行が止まっていたら503を返す。外形監視から叩かせる口。

    なぜ要るか:
      鮮度パネルは管理画面を**開かないと見えない**。2026-08-31、株価の定期実行が
      3回とも空振りしてスクリーナーが丸1日 前営業日の終値を出し、
      さらに夕方からスケジューラ自体が発火を止めていたが、
      どちらも誰かが管理画面を開くまで気づけなかった。

      UptimeRobot が5分おきに `/health/db` を叩いているので、そこに相乗りする。
      **新しい通知の仕組みを作らずに済む**のが狙い
      （監視先を増やすより、既にある監視に載せるほうが忘れられない）。

    ⚠️ **本文に件数を出さないこと。** ここは未ログインで叩ける。
       別アプリで `/api/health/db` が誰でも会員数を返していた例がある。
       返すのは status と、決まった語彙の problem だけ。

    ⚠️ 判定できないときは 503（fail-closed）。読めないことを ok として返すと、
       監視そのものが黙って無効になる。
    """
    try:
        ok, problem = _jobs_health()
    except Exception as e:
        print(f'[health/jobs] 判定できません: {str(e)[:200]}')
        return jsonify({"status": "error"}), 503
    if ok:
        return jsonify({"status": "ok"}), 200
    print(f'[health/jobs] 異常を検出: {problem}')
    return jsonify({"status": "stale", "problem": problem}), 503


# =============================================
# 認証ヘルパー関数
# =============================================

def get_current_user():
    """sessionからログインユーザーを取得。未ログインならNone。

    ⚠️ 「セッションはあるが app_users に居ない」状態が実際に起きる
    （認証統一の前後で発行されたID、動作確認用に手で入れたセッション、
    ユーザー行を消したあとの残りなど）。

    この状態を放置すると**ゾンビセッション**になる:
      - ヘッダーには session['user_name'] があるので名前が出る＝ログイン中に見える
      - しかし get_current_user() は None なので API は 401 を返す
      - デモ売買は session['user_id'] をそのまま使うため、実在しないIDで
        新しい口座（100万円・保有0件）を勝手に作ってしまい、
        「売買したのにデータが無い」ように見える
    名前だけ出て何も動かないので、利用者からは原因が全く分からない。

    そこで、解決できないセッションはここで捨てる。次のリクエストで
    ログイン画面に戻り、入り直せば正しいIDが入る。
    """
    user_id = session.get('user_id')
    if not user_id:
        return None

    user = get_user_by_id(user_id)
    if user:
        return user

    print(f'[session] app_usersに存在しないIDのセッションを破棄します: {user_id}')
    for key in ('user_id', 'user_name', 'user_email', 'user_role'):
        session.pop(key, None)
    return None


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


def login_required_api(f):
    """API用ログイン必須デコレータ"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({"error": "ログインが必要です"}), 401
        return f(*args, **kwargs)
    return decorated


# 非会員に見せるコミュニティの質問数。
# 全部隠すと「何も無い場所」に見えて二度と来なくなる。数件見せて
# 「他にもある」と分かる状態にする。
FREE_COMMUNITY_QUESTIONS = 3

# 段の表示名。内部キーをそのまま画面に出さないための対応表。
# 表示名は後から変えられるが、内部キー（online/real/...）は決済と紐づくので変えない。
MEMBERSHIP_LABELS = {
    'online': 'オンライン会員',
    'real': 'リアル会員',
    'invite': 'ご招待会員',
    'premium': 'プレミアム会員',
    'terakoya': 'テラこや会員（旧）',
    'salon': 'サロン会員（旧）',
    'pro': '本会員（旧）',
}


def is_member_session():
    """いまのセッションが有料会員か。

    判定は gia_identity.is_paid_member() に集約（gia-next の isActiveMember と
    同じ定義）。admin は運営が会員向け表示を確認できるよう常に会員扱い。
    """
    if session.get('user_role') == 'admin':
        return True
    import gia_identity
    return gia_identity.is_paid_member(session.get('user_id'))


def member_required_api(f):
    """API用 会員必須デコレータ。

    ページ側にガードを置いても、APIが素通しだと curl で中身が取れる。
    会員限定のデータを返すAPIは必ずこちらを使う。
    未ログイン(401)と、ログイン済みだが非会員(403)を分けて返す。
    画面側が「ログインして」と「会員になって」を出し分けられるようにするため。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({"error": "ログインが必要です"}), 401
        if not is_member_session():
            return jsonify({
                "error": "この機能は会員限定です",
                "upgrade_url": "https://gia2018.com/upgrade",
            }), 403
        return f(*args, **kwargs)
    return decorated


def admin_required_api(f):
    """API用 管理者必須デコレータ。

    運用系（全銘柄の再取得・スクレイピング・再計算）と、利用者ごとに
    分かれていない共有データ（ウォッチリスト・高配当フラグ）を守る。

    2026-08-25 の点検で、これらが**認証なしで叩ける**状態だと分かった。
    漏洩ではなく「外から起動される」ことが実害で、
      - 全3,880銘柄の再取得が走り、Yahooから遮断される
      - EDINET DBの無料枠（100回/日）を使い切られる
    という形で出る。実際この日、別件でYahoo!JPから3時間遮断されている。

    ⚠️ 判定は session['user_role'] を使う。_require_admin()（ページ側）と
    /api/admin/stock/safe-refresh が同じものを見ており、これは
    GIA_ADMIN_EMAILS でも app_users.role でも admin になる。
    role_required('admin') は app_users.role しか見ないため、メール基準の
    管理者だと「管理画面は開けるのにボタンだけ403」になる。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({"error": "ログインが必要です"}), 401
        if session.get('user_role') != 'admin':
            return jsonify({"error": "管理者権限が必要です"}), 403
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    """指定ロール必須デコレータ（例: @role_required('agent', 'admin')）

    ⚠️ role は app_users.role。メール基準（GIA_ADMIN_EMAILS）の管理者は
    ここを通らないので、管理者向けAPIには admin_required_api を使うこと。
    """
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

        # 画面側のログインと同じ経路（GIAのSupabase Auth）を通す。
        # ここだけ旧認証を残すと、片方でしかログインできない状態になる。
        import gia_identity
        try:
            account = gia_identity.sign_in(email, password)
        except gia_identity.GiaIdentityUnavailable as e:
            print(f'GIA接続の設定不備: {e}')
            return jsonify({"error": "ログイン機能の設定が完了していません"}), 503

        if not account:
            return jsonify({"error": "メールアドレスまたはパスワードが正しくありません"}), 401

        user = ensure_app_user(account['id'], account['email'])

        # ゲストノートの引き継ぎ
        guest_id = session.get('guest_user_id')
        migrated = 0
        if guest_id:
            migrated = migrate_guest_notes(guest_id, user['id'])
            session.pop('guest_user_id', None)

        role = ('admin' if gia_identity.is_admin_email(account['email'])
                else (user.get('role') or 'user'))
        session['user_id'] = user['id']
        session['user_name'] = user.get('name') or ''
        session['user_email'] = account['email']
        session['user_role'] = role
        session.permanent = True

        return jsonify({
            "success": True,
            "user": {
                "id": user['id'],
                "name": user.get('name'),
                "email": account['email'],
                "role": role,
                "referral_code": user.get('referral_code'),
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
    """現在のユーザー情報取得。

    会員の段（plan）も返す。本人が自分の契約状態を確認できないと、
    「会員限定です」と言われたときに何が足りないのか分からない。
    段の正本は GIA(applicants) 側なので、ここは読むだけ。
    """
    user = get_current_user()
    if not user:
        return jsonify({"logged_in": False}), 200

    # 会員情報の取得に失敗しても、アカウント設定自体は開けるようにする
    # （ここで落ちると画面が「読み込み中...」のまま止まる）。
    plan = None
    member = False
    try:
        import gia_identity
        m = gia_identity.get_membership(user['id'])
        plan = m.get('plan')
        member = is_member_session()
    except Exception as e:
        print(f'会員情報の取得に失敗（表示は続行） {user["id"]}: {e}')

    return jsonify({
        "logged_in": True,
        "user": {
            "id": user['id'],
            "name": user['name'],
            "email": user['email'],
            "role": user['role'],
            "referral_code": user['referral_code'],
            "display_name": user.get('display_name') or '',
            "plan": plan,
            "plan_label": MEMBERSHIP_LABELS.get(plan, '無料会員'),
            "is_member": member,
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

        result = update_gia_credential(user_id, current_password, new_email=new_email)
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

        result = update_gia_credential(user_id, current_password, new_password=new_password)
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
        # @login_required_api を通っていても、セッションのIDが app_users に
        # 無いことがある（ゲスト・動作確認用セッション・移行前の古いID）。
        # None を素通りさせると TypeError で 500 になり、マイページの
        # 初期化がそこで止まってデモ売買まで読み込まれない。
        if not user:
            return jsonify({"error": "ログインし直してください"}), 401
        code = user.get('referral_code')
        if not code:
            return jsonify({"error": "紹介コードがまだ発行されていません"}), 404
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


def get_latest_completed_value(val):
    """配列データから「まだ終わっていない年度」を除いた最新値を抽出。

    配当(dps)・配当性向は決算年度ごとに合計している。進行中の年度は
    中間配当までしか入っていないため、そのまま最新値として拾うと
    年間配当が半分に見える（8月決算の367Aで、確定した2025年度105円では
    なく、中間だけの2026年度60円が「1株配当」として出ていた）。

    決算年度の行には期末の日付が入っているので、それが未来なら
    その年度はまだ終わっていない。日付だけで判定でき、銘柄ごとの
    例外を持たなくてよい。

    売上や利益は実績しか入らない（＝日付が未来にならない）ので、
    この関数を通すのは配当まわりだけでよい。
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return val
    if not isinstance(val, list) or not val:
        return None

    from datetime import datetime as _dt
    today = _dt.now().strftime('%Y-%m-%d')
    completed = [x for x in val if str(x.get('date', '')) <= today]
    # 全部が未来＝確定した年度がまだ1つも無い。半端な値を出すより
    # 「無い」を返す（画面は欠損として扱い、理由を出す）。
    if not completed:
        return None
    return sorted(completed, key=lambda x: x.get('date', ''), reverse=True)[0].get('value')


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


def analysis_data_source_name(stock_data):
    """実際に値を補完した取得元をDBの短い識別子にも残す。"""
    edinet = (stock_data.get('source_status') or {}).get('edinet_db') or {}
    if edinet.get('status') == 'success' and edinet.get('filled'):
        return 'yfinance+edinet_db'
    return 'yfinance'


# ウォッチリストAPI
@app.route('/api/watchlist', methods=['GET'])
@member_required_api
def api_get_watchlist():
    """登録銘柄一覧を取得（GC/DC形成日付き）。会員限定。

    2026-08-11: デコレータが無く誰でも取れる状態だった。ホームの「好調企業」
    タブの中身そのものなので、ページを会員限定にしてもここが開いていれば
    curl で取れてしまう。
    """
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
            attach_score_quality(item)

        return jsonify({"watchlist": data}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/add', methods=['POST'])
@admin_required_api
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
            # 配当は進行中の年度を拾わない（中間配当だけで年間配当に見えるため）
            eps = get_latest_value(stock_data.get('eps'))
            dps = get_latest_completed_value(stock_data.get('dps'))
            payout_ratio = get_latest_completed_value(stock_data.get('payout_ratio'))

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
            cf_history = build_cf_history(stock_data)

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
                # 実績とは別物。予想＝直近配当の年換算（migration_forward_dividend.sql）
                'dps_forecast': stock_data.get('dps_forecast'),
                'dividend_yield_forward': stock_data.get('dividend_yield_forward'),
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
                'business_summary_jp': sanitize_rich_text(stock_data.get('business_summary_jp')),
                'established': stock_data.get('established'),
                'listing_date': stock_data.get('listing_date'),
                'ceo_name': stock_data.get('ceo_name_jp'),
                'headquarters': stock_data.get('headquarters_jp'),
                'industry_jp': stock_data.get('industry_jp'),
                'market': stock_data.get('market_jp'),

                # 株主・役員情報（JSON）
                'major_holders': json.dumps(stock_data.get('major_holders', []), ensure_ascii=False) if stock_data.get('major_holders') else None,
                'institutional_holders': json.dumps(stock_data.get('institutional_holders', []), ensure_ascii=False) if stock_data.get('institutional_holders') else None,
                'company_officers': json.dumps(stock_data.get('company_officers', []), ensure_ascii=False) if stock_data.get('company_officers') else None,
                'major_shareholders_jp': json.dumps(stock_data.get('major_shareholders_jp', []), ensure_ascii=False) if stock_data.get('major_shareholders_jp') else None,

                # 財務履歴（JSON）
                # yfinanceが一時的に空を返した場合はNoneにして更新対象から外す。
                # 既存の正常な履歴を空JSONで消さないことが最優先。
                'financial_history': history_json_or_none(financial_history),
                'cf_history': history_json_or_none(cf_history),

                'data_source': analysis_data_source_name(stock_data),
                'source_status': stock_data.get('source_status'),
                'data_status': analysis_data_status(financial_history, cf_history)
            }

            # Noneのフィールドを除外（既存データを保護）、ただしcompany_codeは必須
            screened_data = {k: v for k, v in screened_data_full.items() if v is not None or k == 'company_code'}

            # 合致度を自動計算して保存
            upsert_screened_data_with_match_rate(screened_data)

        return jsonify({"success": True, "company_code": company_code}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/remove/<company_code>', methods=['DELETE'])
@admin_required_api
def api_remove_from_watchlist(company_code):
    """銘柄をウォッチリストから削除"""
    try:
        company_code = normalize_code(company_code)
        remove_from_watchlist(company_code)
        return jsonify({"success": True, "company_code": company_code}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/watchlist/remove-all', methods=['DELETE'])
@admin_required_api
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
    """screened_latestのデータを手で書き換える（管理者専用）。

    ⚠️ 2026-08-25 まで認証が無く、未ログインでも POST するだけで
    任意の銘柄の自己資本比率・PER・PBR・配当利回り・時価総額・
    財務履歴を上書きできた。書き換えると match_rate も再計算されるので、
    スコアごと汚染される。

    判定は /api/admin/stock/safe-refresh と同じ session['user_role'] を使う。
    """
    if not session.get('user_id') or session.get('user_role') != 'admin':
        return jsonify({"error": "管理者権限が必要です"}), 403

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

        # 今期の会社予想。取得元に無くても決算短信を見れば分かるので手で入れられる。
        # 単位は億円（列の単位に合わせる。ここで換算しない）。
        for key in ('forecast_revenue', 'forecast_op_income',
                    'forecast_ordinary_income', 'forecast_net_income'):
            if key in edited_data:
                update_data[key] = edited_data[key]

        # 事業概要（日本語）。**一行紹介の器であって、長文の置き場ではない。**
        # この列は3か所に出る:
        #   スクリーナーのカード … 2行でクランプ（長文は切られる）
        #   テーマ・業種ページ   … 全文が出る（長文だと一覧が崩れる）
        #   銘柄ページの meta description … 全文を striptags（SEOの説明文になる）
        # 実データは中央値85文字・最長184文字。長い分析は銘柄ノートへ。
        if 'business_summary_jp' in edited_data:
            summary = (edited_data['business_summary_jp'] or '').strip() or None
            if summary and len(summary) > BUSINESS_SUMMARY_MAX:
                return jsonify({
                    "error": f"事業概要は{BUSINESS_SUMMARY_MAX}文字までです"
                             "（一覧やSEOの説明文にも使われます）。"
                             "長い分析は銘柄ノートに書いてください。"
                }), 400
            # 管理者でも、貼り付けた文章にタグが混じることがある。
            # 画面は innerHTML で出すので、入口でエスケープしておく。
            update_data['business_summary_jp'] = sanitize_rich_text(summary)

        # 決算期は日付の文字列（'2027-03-31'）。数値の列と混ぜない。
        if 'forecast_year' in edited_data:
            value = (edited_data['forecast_year'] or '').strip() or None
            if value and not re.match(r'^\d{4}-\d{2}-\d{2}$', value):
                return jsonify({"error": "決算期は 2027-03-31 の形式で入れてください"}), 400
            update_data['forecast_year'] = value

        # 履歴は**キー単位でマージする。丸ごと置き換えない。**
        #
        # 編集画面の表に出ているのは revenue / op_income / ordinary_income /
        # net_income / eps / dps / payout_ratio の7つだけ。丸ごと置き換えると、
        # 表に無いキー（financial_history なら bps、cf_history なら
        # interest_bearing_debt・retained_earnings・current_assets など）が
        # **保存のたびに黙って消える**。消えてもエラーは出ず、画面も
        # 「保存しました」と出るので気づけない。
        from supabase_client import get_screened_data as _get_screened

        def _merge_history(column, incoming):
            current = _get_screened(company_code) or {}
            existing = current.get(column)
            if isinstance(existing, str):
                try:
                    existing = json.loads(existing)
                except (TypeError, ValueError):
                    existing = {}
            merged = dict(existing or {})
            merged.update(incoming or {})
            return json.dumps(merged, ensure_ascii=False)

        if 'financial_history' in edited_data:
            update_data['financial_history'] = _merge_history(
                'financial_history', edited_data['financial_history'])

        if 'cf_history' in edited_data:
            update_data['cf_history'] = _merge_history(
                'cf_history', edited_data['cf_history'])

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
@member_required_api
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

        symbol = normalize_analysis_symbol(data['symbol'])
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

            # スコアと12項目の判定は、保存後のDBの値からサーバーで作って返す。
            # ブラウザ側で計算し直さないための唯一の入口
            # （画面表示用の再計算をJSに持たせると、片方を直すたびにズレる）。
            if screened and session.get('user_id'):
                from supabase_client import score_breakdown
                result['score_breakdown'] = score_breakdown(screened)
            if screened:
                result['data_status'] = screened.get('data_status')
        except:
            pass

        return jsonify(result), 200

    except Exception as e:
        print(f"分析エラー: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/stock/safe-refresh', methods=['POST'])
def admin_safe_refresh_stock():
    """有料枠・日本版Yahoo HTML・外部HTMLスクレイピングを使わず1銘柄を更新する。"""
    if not session.get('user_id') or session.get('user_role') != 'admin':
        return jsonify({"error": "管理者権限が必要です"}), 403

    data = request.get_json(silent=True) or {}
    symbol = normalize_analysis_symbol(data.get('symbol'))
    if not symbol:
        return jsonify({"error": "銘柄コードが指定されていません"}), 400

    try:
        analyzer = StockAnalyzer()
        result = analyzer.analyze(
            symbol,
            period=data.get('period', '1y'),
            skip_chart=True,
            skip_extras=True,
            safe_sources_only=True,
        )
        if result.get('error'):
            return jsonify({"error": result['error']}), 502

        _save_analysis_to_screened(symbol, result)
        return jsonify({
            "success": True,
            "company_code": normalize_code(symbol),
            "message": "無料・低負荷更新が完了しました。業績予想・概要・株主・役員は既存値を保持しています。",
            "source_status": result.get('source_status', {}),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/stock/batch', methods=['POST'])
@member_required_api
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
@member_required_api
def get_cached_analysis(symbol):
    """
    キャッシュされた分析結果を取得（会員限定）。

    output/snapshot_*.json を返す旧分析フローのファイルキャッシュ。全項目を
    含むため、公開ページからは使っていないが素通しにすると会員限定データの
    抜け道になる。ログイン必須にする。
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
@member_required_api
def api_get_gc_stocks():
    """保存済みGC銘柄一覧を取得（signal_stocks統合テーブル、表示用フィルタ適用）。会員限定。"""
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
    """GC銘柄をスクレイピングしてsignal_stocksに保存"""
    from datetime import datetime, timezone
    stocks = scrape_gc_stocks()

    now = datetime.now(timezone.utc).isoformat()
    for s in stocks:
        s['gc_date'] = now

    # signal_stocksにupsert（既存のdc_dateは保持される）
    upsert_signal_stocks(stocks)

    # screened_latest.gc_date には書かない。
    # 以前はここに「取得時刻」を入れていたが、それは実際のクロス日ではない。
    # screened_latest.gc_date は日足から計算した本当のGC発生日を持つ列にし、
    # 更新は ma_cross.sync_gc_to_screened に一本化する。ここで上書きすると
    # スクリーナーのGC日が取得時刻で汚れる。

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
@admin_required_api
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
    # 配当は進行中の年度を拾わない（中間配当だけで年間配当に見えるため）
    dps = get_latest_completed_value(stock_data.get('dps'))
    payout_ratio = get_latest_completed_value(stock_data.get('payout_ratio'))
    roe = get_latest_value(stock_data.get('roe'))

    financial_history = {
        'revenue': stock_data.get('revenue', []),
        'op_income': stock_data.get('op_income', []),
        'ordinary_income': stock_data.get('ordinary_income', []),
        'net_income': stock_data.get('net_income', []),
        'eps': stock_data.get('eps', []),
        'bps': stock_data.get('bps', []),
        'dps': stock_data.get('dps', []),
        'payout_ratio': stock_data.get('payout_ratio', [])
    }

    cf_history = build_cf_history(stock_data)

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
        # 実績とは別物。予想＝直近配当の年換算（migration_forward_dividend.sql）
        'dps_forecast': stock_data.get('dps_forecast'),
        'dividend_yield_forward': stock_data.get('dividend_yield_forward'),
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
        'business_summary_jp': sanitize_rich_text(stock_data.get('business_summary_jp')),
        'established': stock_data.get('established'),
        'listing_date': stock_data.get('listing_date'),
        'ceo_name': stock_data.get('ceo_name_jp'),
        'headquarters': stock_data.get('headquarters_jp'),
        'industry_jp': stock_data.get('industry_jp'),
        'market': stock_data.get('market_jp'),
        'major_holders': json.dumps(_convert_timestamps(stock_data.get('major_holders', [])), ensure_ascii=False) if stock_data.get('major_holders') else None,
        'institutional_holders': json.dumps(_convert_timestamps(stock_data.get('institutional_holders', [])), ensure_ascii=False) if stock_data.get('institutional_holders') else None,
        'company_officers': json.dumps(_convert_timestamps(stock_data.get('company_officers', [])), ensure_ascii=False) if stock_data.get('company_officers') else None,
        'major_shareholders_jp': json.dumps(_convert_timestamps(stock_data.get('major_shareholders_jp', [])), ensure_ascii=False) if stock_data.get('major_shareholders_jp') else None,
        'financial_history': history_json_or_none(financial_history, _convert_timestamps),
        'cf_history': history_json_or_none(cf_history, _convert_timestamps),
        'analyzed_at': now,
        # 株価と倍率を同じ snapshot から書くので、この時点で揃っている。
        # multiples.py の不変条件（派生値は同じ行の stock_price と同時点）の印。
        'price_updated_at': now,
        'data_source': analysis_data_source_name(stock_data),
        'source_status': stock_data.get('source_status'),
        'data_status': analysis_data_status(financial_history, cf_history)
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
        'bps': stock_data.get('bps', []),
        'dps': stock_data.get('dps', []),
        'payout_ratio': stock_data.get('payout_ratio', [])
    }

    cf_history = build_cf_history(stock_data)

    # 財務健全性カード用のスカラー列。
    # 以前はcf_historyにしか入れていなかったため、この保存パスだけを通った銘柄
    # （＝全銘柄バックフィル対象のほぼ全部）でcash / current_liabilities /
    # current_ratio / total_assets / equity が永久に空のままだった。
    latest_cash = get_latest_value(stock_data.get('cash'))
    latest_current_liab = get_latest_value(stock_data.get('current_liabilities_list'))
    latest_current_assets = get_latest_value(stock_data.get('current_assets_list'))
    current_ratio = None
    if latest_current_assets and latest_current_liab:
        current_ratio = round((latest_current_assets / latest_current_liab) * 100, 4)

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
        'cash': to_oku(latest_cash) if latest_cash else None,
        'current_liabilities': to_oku(latest_current_liab) if latest_current_liab else None,
        'current_assets': to_oku(latest_current_assets) if latest_current_assets else None,
        'current_ratio': current_ratio,
        'net_income': to_oku(get_latest_value(stock_data.get('net_income'))) if get_latest_value(stock_data.get('net_income')) else None,
        'roa': get_latest_value(stock_data.get('roa')),
        'per_forward': stock_data.get('per'),
        'pbr': stock_data.get('pbr'),
        'dividend_yield': stock_data.get('dividend_yield'),
        # 実績とは別物。予想＝直近配当の年換算（migration_forward_dividend.sql）
        'dps_forecast': stock_data.get('dps_forecast'),
        'dividend_yield_forward': stock_data.get('dividend_yield_forward'),
        'eps': get_latest_value(stock_data.get('eps')),
        # 配当は進行中の年度を拾わない（中間配当だけで年間配当に見えるため）
        'dps': get_latest_completed_value(stock_data.get('dps')),
        'payout_ratio': get_latest_completed_value(stock_data.get('payout_ratio')),
        'roe': get_latest_value(stock_data.get('roe')),
        'analyzed_at': now,
        # 株価と倍率を同じ snapshot から書くので、この時点で揃っている。
        # multiples.py の不変条件（派生値は同じ行の stock_price と同時点）の印。
        'price_updated_at': now,
        'forecast_revenue': stock_data.get('forecast_revenue'),
        'forecast_op_income': stock_data.get('forecast_op_income'),
        'forecast_ordinary_income': stock_data.get('forecast_ordinary_income'),
        'forecast_net_income': stock_data.get('forecast_net_income'),
        'forecast_year': stock_data.get('forecast_year'),
        'business_summary': stock_data.get('business_summary'),
        'business_summary_jp': sanitize_rich_text(stock_data.get('business_summary_jp')),
        'established': stock_data.get('established'),
        'listing_date': stock_data.get('listing_date'),
        'ceo_name': stock_data.get('ceo_name_jp'),
        'headquarters': stock_data.get('headquarters_jp'),
        'industry_jp': stock_data.get('industry_jp'),
        'market': stock_data.get('market_jp'),
        'company_officers': json.dumps(
            _convert_timestamps(stock_data.get('company_officers', [])),
            ensure_ascii=False) if stock_data.get('company_officers') else None,
        'major_shareholders_jp': json.dumps(
            _convert_timestamps(stock_data.get('major_shareholders_jp', [])),
            ensure_ascii=False) if stock_data.get('major_shareholders_jp') else None,
        'financial_history': history_json_or_none(financial_history),
        'cf_history': history_json_or_none(cf_history),
        # 有報の対象決算期があればそれを使う（決算期変更を追えるのはこちらだけ）
        'fiscal_month': derive_fiscal_month(
            financial_history, cf_history,
            authoritative=_authoritative_fiscal_month(company_code)),
        'data_source': analysis_data_source_name(stock_data),
        'source_status': stock_data.get('source_status'),
        'data_status': analysis_data_status(financial_history, cf_history)
    }

    # デバッグログ: 配当性向データの確認
    pr_raw = stock_data.get('payout_ratio')
    pr_val = get_latest_value(pr_raw)
    print(f"[DEBUG] {code} payout_ratio raw={pr_raw}, latest={pr_val}, eps={get_latest_value(stock_data.get('eps'))}, dps={get_latest_value(stock_data.get('dps'))}")

    # Noneのフィールドを除外（フル分析で保存済みのデータを上書きしない）
    screened_data = {k: v for k, v in screened_data_full.items() if v is not None or k == 'company_code'}

    _save_screened_tolerating_new_columns(screened_data)
    return {**screened_data, 'raw': stock_data}


# migrationを適用する前でも分析・保存が止まらないようにするための、
# 「まだ無い列」の一覧。列が来たら自動でまた書き始める。
def _authoritative_fiscal_month(company_code):
    """有報の対象決算期から分かる決算月。引けなければ None。

    ⚠️ ここで例外を出して分析を止めない。引けなければ最頻値に落ちるだけ。
    """
    try:
        from edinet_codes import authoritative_fiscal_month
        return authoritative_fiscal_month(company_code)
    except Exception as e:
        print(f'決算月の引き当てに失敗（最頻値を使います）: {str(e)[:120]}')
        return None


_MIGRATION_PENDING_COLUMNS = {'fiscal_month', 'dps_forecast', 'dividend_yield_forward'}

# 株主・役員のオンデマンド取得に許す最大秒数。
# チャート（price_history.FETCH_TIMEOUT_SECONDS）より長めにしてよい。
# 無料ソース → 公式キャッシュ → EDINET DB と3段構えで、正常でも数秒かかる。
# ただし「画面が待つ」ことに変わりはないので上限は必ず置く。
HOLDERS_FETCH_TIMEOUT_SECONDS = 25
_missing_columns = set()


def _save_screened_tolerating_new_columns(screened_data):
    """新しい列がまだ本番に無くても保存を落とさない。

    migrationは運用側で手で適用するため、コードが先行する期間がある。
    その間、新列のせいで全銘柄の分析結果が保存できなくなる事故を避ける。
    """
    payload = {k: v for k, v in screened_data.items() if k not in _missing_columns}
    try:
        upsert_screened_data_with_match_rate(payload)
        return
    except Exception as e:
        message = str(e)
        dropped = {c for c in _MIGRATION_PENDING_COLUMNS
                   if c in payload and c in message}
        if not dropped:
            raise
        _missing_columns.update(dropped)
        print(f'[migration未適用] {", ".join(sorted(dropped))} 列が無いため'
              f'除外して保存します。supabase/ の該当migrationを適用してください'
              f'（fiscal_month → migration_fiscal_month.sql ／'
              f' dps_forecast・dividend_yield_forward → migration_forward_dividend.sql）。')

    upsert_screened_data_with_match_rate(
        {k: v for k, v in payload.items() if k not in _missing_columns})


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
@admin_required_api
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
@admin_required_api
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
@admin_required_api
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
@admin_required_api
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
@admin_required_api
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
            "message": f"{len(watchlist)}件中 {updated}件のスコアを更新しました"
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
@admin_required_api
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
@member_required_api
def api_get_dividend_stocks():
    """高配当フラグが立っている銘柄を一覧取得。会員限定。"""
    try:
        stocks = get_dividend_stocks()
        # 一覧の色分けに充足度が要る。付けないと「点数は高いがデータが欠けている」
        # 銘柄が緑で出てしまう（score-color.js は充足度が無いと点数だけで判定する）
        for row in stocks:
            attach_score_quality(row)
        return jsonify({"dividend_stocks": stocks}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/dividend-stocks/add', methods=['POST'])
@admin_required_api
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
@admin_required_api
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
@admin_required_api
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
@admin_required_api
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


# 一度に登録できる件数と、作れるフォルダの数。
# 上限が無いと、一覧を全選択して数千件を1リクエストで投げられる。
BULK_FAVORITE_MAX = 200
FAVORITE_FOLDER_MAX = 50


# =============================================
# お気に入り銘柄API
# =============================================

@app.route('/api/favorite-stocks', methods=['GET'])
def api_get_favorite_stocks():
    """お気に入り銘柄一覧を取得"""
    try:
        user_id = get_or_create_guest_user_id()
        stocks = get_favorite_stocks(user_id)
        for row in stocks:
            attach_score_quality(row)
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


@app.route('/api/favorite-stocks/bulk', methods=['POST'])
def api_add_favorite_stocks_bulk():
    """選んだ銘柄をまとめてお気に入りに入れる。

    好調企業・高配当企業・テクニカル分析の一覧から、チェックした銘柄を
    1回で登録するための口。1件ずつ叩くと数十リクエストになる。
    """
    try:
        user_id = get_or_create_guest_user_id()
        data = request.get_json() or {}
        codes = [normalize_code(c) for c in (data.get('company_codes') or []) if c]
        if not codes:
            return jsonify({"error": "銘柄が選ばれていません"}), 400
        if len(codes) > BULK_FAVORITE_MAX:
            return jsonify({"error": f"一度に登録できるのは{BULK_FAVORITE_MAX}件までです"}), 400
        result = add_favorite_stocks(user_id, codes, data.get('folder_id') or None)
        return jsonify(result), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f'お気に入り一括登録エラー: {e}')
        return jsonify({"error": "登録できませんでした"}), 500


@app.route('/api/favorite-stocks/folder', methods=['POST'])
def api_add_to_favorite_folder():
    """選んだお気に入りにフォルダの札を付ける。

    ⚠️ 1銘柄が複数のフォルダに入る。ここは「移動」ではなく「追加」なので、
       すでに付いている他の札は外さない。
    """
    try:
        user_id = get_or_create_guest_user_id()
        data = request.get_json() or {}
        codes = [normalize_code(c) for c in (data.get('company_codes') or []) if c]
        folder_id = data.get('folder_id')
        if not codes or not folder_id:
            return jsonify({"error": "銘柄とフォルダを選んでください"}), 400
        if len(codes) > BULK_FAVORITE_MAX:
            return jsonify({"error": f"一度に扱えるのは{BULK_FAVORITE_MAX}件までです"}), 400
        return jsonify({"filed": add_to_favorite_folder(user_id, codes, folder_id)}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f'フォルダ追加エラー: {e}')
        return jsonify({"error": "追加できませんでした"}), 500


@app.route('/api/favorite-stocks/folder', methods=['DELETE'])
def api_remove_from_favorite_folder():
    """フォルダの札を外す。**お気に入りからは消えない。**"""
    try:
        user_id = get_or_create_guest_user_id()
        data = request.get_json() or {}
        codes = [normalize_code(c) for c in (data.get('company_codes') or []) if c]
        folder_id = data.get('folder_id')
        if not codes or not folder_id:
            return jsonify({"error": "銘柄とフォルダを選んでください"}), 400
        return jsonify({"removed": remove_from_favorite_folder(
            user_id, codes, folder_id)}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f'フォルダ解除エラー: {e}')
        return jsonify({"error": "外せませんでした"}), 500


@app.route('/api/favorite-stocks/folders', methods=['PUT'])
def api_set_favorite_folders():
    """1銘柄に付ける札を、渡された集合そのものに置き換える。

    銘柄ごとにチェックを付け外しする画面用。まとめて送るので、
    付け外しのたびにリクエストが飛ばない。
    """
    try:
        user_id = get_or_create_guest_user_id()
        data = request.get_json() or {}
        code = normalize_code(data.get('company_code') or '')
        if not code:
            return jsonify({"error": "銘柄が指定されていません"}), 400
        ids = set_favorite_folders(user_id, code, data.get('folder_ids') or [])
        return jsonify({"folder_ids": ids}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f'フォルダ設定エラー: {e}')
        return jsonify({"error": "変更できませんでした"}), 500


# =============================================
# お気に入りのフォルダAPI
#
# ⚠️ アプリは service_role で接続する＝RLSがバイパスされる。
#    どの口も user_id で絞ること。folder_id はクライアントから来るので、
#    本人のものかを確かめずに使うと他人のフォルダを触れてしまう
#    （確認は supabase_client 側の owns_favorite_folder が担う）。
# =============================================

@app.route('/api/favorite-folders', methods=['GET'])
def api_list_favorite_folders():
    """フォルダ一覧（件数つき）と、未分類の件数を返す"""
    try:
        user_id = get_or_create_guest_user_id()
        return jsonify({"folders": list_favorite_folders(user_id),
                        "unfiled": count_unfiled_favorites(user_id)}), 200
    except Exception as e:
        print(f'フォルダ一覧エラー: {e}')
        return jsonify({"error": "取得できませんでした"}), 500


@app.route('/api/favorite-folders', methods=['POST'])
def api_create_favorite_folder():
    """フォルダを作る"""
    try:
        user_id = get_or_create_guest_user_id()
        data = request.get_json() or {}
        folders = list_favorite_folders(user_id)
        if len(folders) >= FAVORITE_FOLDER_MAX:
            return jsonify({"error": f"フォルダは{FAVORITE_FOLDER_MAX}個までです"}), 400
        return jsonify(create_favorite_folder(user_id, data.get('name'))), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f'フォルダ作成エラー: {e}')
        return jsonify({"error": "作成できませんでした"}), 500


@app.route('/api/favorite-folders/<folder_id>', methods=['PATCH'])
def api_rename_favorite_folder(folder_id):
    """フォルダ名を変える"""
    try:
        user_id = get_or_create_guest_user_id()
        data = request.get_json() or {}
        return jsonify(rename_favorite_folder(user_id, folder_id, data.get('name'))), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f'フォルダ改名エラー: {e}')
        return jsonify({"error": "変更できませんでした"}), 500


@app.route('/api/favorite-folders/<folder_id>', methods=['DELETE'])
def api_delete_favorite_folder(folder_id):
    """フォルダを消す。**中のお気に入りは消さず未分類に戻る。**"""
    try:
        user_id = get_or_create_guest_user_id()
        delete_favorite_folder(user_id, folder_id)
        return jsonify({"message": "フォルダを削除しました（銘柄は未分類に戻りました）"}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f'フォルダ削除エラー: {e}')
        return jsonify({"error": "削除できませんでした"}), 500


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
@admin_required_api
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

        # 1回あたりの上限。全件だと数時間かかりWebの応答に影響するため区切る。
        # 画面側だけの制限だと、リクエストを直接叩かれた場合に効かない。
        limit = (request.get_json(silent=True) or {}).get('limit') or 200
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit = 200
        codes = codes[:limit]

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
@admin_required_api
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
@member_required_api
def api_get_technical_stocks():
    """GC/DC発生日を持つ銘柄を一覧取得。会員限定。

    日付は ma_crosses（保存済みの日足から自前計算したもの）を使う。
    signal_stocks.gc_date はスクレイピングした時刻が全銘柄一律で入っており、
    「いつGCしたか」を表していないため。
    ma_crosses が未作成・空の場合は従来通り signal_stocks にフォールバックする。
    """
    try:
        client = get_supabase_client()

        signals = []
        source = 'ma_crosses'
        try:
            page = 0
            while page < 10:
                res = (client.table('ma_crosses')
                       .select('company_code, latest_gc_date, latest_dc_date, cross_count')
                       .or_('latest_gc_date.not.is.null,latest_dc_date.not.is.null')
                       .range(page * 1000, page * 1000 + 999)
                       .execute())
                chunk = res.data or []
                if not chunk:
                    break
                for r in chunk:
                    signals.append({
                        'company_code': r['company_code'],
                        'gc_date': r.get('latest_gc_date'),
                        'dc_date': r.get('latest_dc_date'),
                        'cross_count': r.get('cross_count'),
                    })
                if len(chunk) < 1000:
                    break
                page += 1
        except Exception as e:
            print(f'ma_crosses 参照エラー（signal_stocksにフォールバック）: {e}')
            signals = []

        if not signals:
            source = 'signal_stocks'
            res = client.table('signal_stocks').select('*').or_(
                'gc_date.not.is.null,dc_date.not.is.null'
            ).order('company_code').execute()
            signals = res.data or []

        codes = [s['company_code'] for s in signals]

        # screened_latestから最新の財務データを取得
        # in_ に数千件を一度に渡すとURLが長くなりすぎるため分割して取得する
        screened_map = {}
        for i in range(0, len(codes), 100):
            chunk = codes[i:i + 100]
            # 色分けに要るのは「全項目を判定できたか」だけなので、保存済みの
            # score_complete を1列取るに留める。
            # attach_score_quality() を使うには financial_history / cf_history が要り、
            # この一覧は3,700件あるため応答が 1.5MB / 2.6秒 に膨らんだ（実測）。
            # worker1本なので、一覧APIをそこまで重くする価値は無い。
            screened = client.table('screened_latest').select(
                'company_code,company_name,sector,market_cap,stock_price,'
                'per_forward,pbr,dividend_yield,match_rate,analyzed_at,score_complete'
            ).in_('company_code', chunk).execute()
            for x in (screened.data or []):
                screened_map[x['company_code']] = x

        # マージ: screened_latestの財務データで補完
        result = []
        for sig in signals:
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
                'cross_count': sig.get('cross_count'),
                'analyzed_at': sc.get('analyzed_at') or sig.get('analyzed_at'),
                # 充足度そのものではなく「全項目そろっているか」だけを渡す。
                # score-color.js はこれだけで暫定かどうかを判定できる。
                'score_complete': sc.get('score_complete'),
            })

        # 直近でGCした順を既定にする（何もしなくても「今どれがGCしたか」が分かる）
        result.sort(key=lambda r: (r.get('gc_date') or '', r.get('dc_date') or ''), reverse=True)

        return jsonify({"technical_stocks": result, "source": source}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _translate_summary_to_jp(english_text):
    """旧呼び出し箇所との互換用。実装は共通モジュールに置く。"""
    from summary_translation import translate_summary_to_jp
    return translate_summary_to_jp(english_text)


@app.route('/api/stock/summary-jp/<company_code>', methods=['POST'])
@member_required_api
def api_retry_summary_jp(company_code):
    """日本語事業概要を無料ソース優先で再取得し、最後にEDINET DBで補完する。"""
    try:
        from jp_company_scraper import get_yahoo_japan_profile
        from supabase_client import merge_source_status
        code = company_code.replace('.T', '').strip()
        existing = get_screened_data(code) or {}
        source_updates = {}
        yahoo_data = get_yahoo_japan_profile(code)
        if yahoo_data.get('_source_status'):
            source_updates['yahoo_jp_profile'] = yahoo_data['_source_status']
        # ⚠️ 取得元のページの中身をそのまま持つ。画面は innerHTML で出すので、
        # ここでエスケープしてから <br> を足す（足したあとに通すと、
        # こちらが意図して入れた <br> まで文字になる）。
        summary_jp = sanitize_rich_text(yahoo_data.get('business_summary_jp'))
        segments = sanitize_rich_text(yahoo_data.get('business_segments'))
        if summary_jp and segments:
            summary_jp += f"<br>【連結事業】{segments}"
        elif segments:
            summary_jp = f"【連結事業】{segments}"
        if summary_jp:
            source_updates['business_summary'] = {
                'status': 'success', 'source': 'Yahoo Finance Japan', 'language': 'ja',
            }

        # Yahoo Japanで取れない場合、保存済みの英語概要を先に日本語化する。
        # EDINET DB Free枠（100回/日）は英語もGPTも使えない銘柄へ温存する。
        translated = False
        if not summary_jp:
            english = existing.get('business_summary')
            summary_jp = _translate_summary_to_jp(english)
            translated = bool(summary_jp)
            if translated:
                source_updates['business_summary'] = {
                    'status': 'success',
                    'source': 'Yahoo Finance英語概要 + OpenAI日本語要約',
                    'language': 'ja', 'translated_from': 'en',
                }

        # IPO/TOKYO PRO Market等の確認済み公式キャッシュはローカル参照なので、
        # EDINET DBの無料枠を消費する前に使う。
        if not summary_jp:
            from official_company_profiles import apply_official_profile_fallback
            official_result = {'business_summary_jp': None, 'source_status': {}}
            apply_official_profile_fallback(f'{code}.T', official_result)
            summary_jp = official_result.get('business_summary_jp')
            source_updates.update(official_result.get('source_status') or {})
            if summary_jp:
                source_updates['business_summary'] = {
                    'status': 'success',
                    'source': 'JPX/会社公式開示（確認済みキャッシュ）',
                    'language': 'ja',
                }

        # 無料ソースと確認済み公式キャッシュでも空の場合だけEDINET DBを使う。
        if not summary_jp:
            from edinet_db_client import fetch_edinet_db_business_summary
            summary_jp, edinet_status = fetch_edinet_db_business_summary(f'{code}.T')
            source_updates['edinet_db'] = edinet_status
            if summary_jp:
                source_updates['business_summary'] = {
                    'status': 'success', 'source': 'EDINET DB API (gBizINFO/EDINET)',
                    'language': 'ja',
                }

        merged_status = merge_source_status(existing.get('source_status'), source_updates)

        if summary_jp:
            # 既存行があればUPDATE、無ければINSERT相当のupsert。
            # upsertは INSERT ... ON CONFLICT として実行されるため、部分的な項目だけを
            # 渡すとNOT NULL制約に引っかかる（23502）。まずUPDATEを試すのが安全。
            try:
                if get_screened_data(code):
                    update_screened_data(code, {
                        'business_summary_jp': summary_jp,
                        'source_status': merged_status,
                    })
                else:
                    upsert_screened_data({
                        'company_code': code,
                        'business_summary_jp': summary_jp,
                        'source_status': merged_status,
                    })
                print(f"日本語事業概要を保存しました: {code}（LLM翻訳: {translated}）")
            except Exception as e:
                print(f"日本語事業概要の保存エラー: {e}")
            return jsonify({"business_summary_jp": summary_jp, "translated": translated}), 200
        else:
            if existing and source_updates:
                update_screened_data(code, {'source_status': merged_status})
            return jsonify({"error": "日本語の事業概要を取得できませんでした"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================
# 移動平均クロス（GC/DC）の再計算
#
# 従来 signal_stocks.gc_date には「スクレイピングした時刻」が全銘柄一律で
# 入っており、いつGCしたかは分からなかった。保存済みの日足から自前で計算する。
# ネットワークを使わないので外部サイトのレート制限とは無関係に実行できる。
# =============================================

ma_cross_status = {"running": False, "done": 0, "total": 0, "saved": 0,
                   "stop_requested": False, "finished_at": None, "error": None}


def _recalc_ma_crosses_background():
    global ma_cross_status
    import ma_cross
    try:
        def progress(done, total, saved):
            ma_cross_status["done"] = done
            ma_cross_status["total"] = total
            ma_cross_status["saved"] = saved

        result = ma_cross.calculate_for_all(
            progress=progress,
            should_stop=lambda: ma_cross_status["stop_requested"],
        )
        ma_cross_status["saved"] = result["saved"]
        ma_cross_status["total"] = result["total"]
        ma_cross_status["done"] = result["total"]
    except Exception as e:
        print(f'GC/DC再計算エラー: {e}')
        ma_cross_status["error"] = str(e)[:200]
    finally:
        from datetime import datetime, timezone
        ma_cross_status["running"] = False
        ma_cross_status["finished_at"] = datetime.now(timezone.utc).isoformat()


earnings_status = {"running": False, "done": 0, "total": 0, "errors": 0,
                   "codes": [], "stop_requested": False, "finished_at": None, "error": None}


def _update_earnings_background(codes, deadline_at=None):
    """決算発表があった銘柄だけを再分析する。

    財務データは1銘柄10回のAPI呼び出しが必要で全件やり直すと約4.5時間かかるが、
    決算を出した銘柄だけなら平常日は数件〜数十件で済む。

    deadline_at を渡すと、その時刻を過ぎたところで**次の銘柄に進まない**。
    夜間の自動実行で使う。決算期は1日1,000件を超えることがあり、
    上限が無いと朝まで走り続けて他の定期実行とかち合う。
    処理済みの印は1件ごとに付けるので、途中で切り上げても翌晩に続きから進む。
    """
    global earnings_status
    analyzer = StockAnalyzer()
    import time as _time

    for i, code in enumerate(codes):
        if earnings_status["stop_requested"]:
            break
        if deadline_at is not None and _time.monotonic() >= deadline_at:
            print(f'決算更新: 時間切れのため{i}件で切り上げます（残り{len(codes) - i}件）')
            break
        try:
            result = _analyze_stock_and_save(analyzer, code)
            if not result:
                earnings_status["errors"] += 1
        except Exception as e:
            print(f'決算更新エラー ({code}): {e}')
            earnings_status["errors"] += 1
        # 1件ずつ処理済みにする。途中で止まっても、成功した分は再処理されない
        try:
            from datetime import datetime, timezone
            get_supabase_client().table('earnings_queue').update({
                'processed': True,
                'processed_at': datetime.now(timezone.utc).isoformat(),
            }).eq('company_code', code).execute()
        except Exception as e:
            print(f'決算キューの更新エラー ({code}): {e}')

        earnings_status["done"] = i + 1
        if i < len(codes) - 1:
            _time.sleep(0.35)   # yfinanceのレート制限対策

    from datetime import datetime, timezone
    earnings_status["running"] = False
    earnings_status["finished_at"] = datetime.now(timezone.utc).isoformat()


def _enqueue_announced():
    """決算発表のあった銘柄をキューに記録し、未処理の全件を返す。

    その日に発表された分だけを直接処理する作りだと、ボタンを押し忘れた日の分が
    消えてしまう。検知した時点でキューに積み、処理済みになるまで残す。

    ⚠️ **同じ日に2回検知しても、処理済みを未処理に戻さない。**
    kabutanが返すのは「その日の発表」なので、1日に2回ここを通ると同じ銘柄が
    並ぶ。以前は毎回 processed=False で上書きしていたため、
    「更新ボタンを押す → 21時の検知cronが同じ銘柄を未処理に戻す →
    次に押すと何も変わっていないのに全部取り直す」が毎日起きていた
    （2026-08-19に実データで確認。1銘柄あたり約10リクエストなので決算日に効く）。

    そのかわり、同じ日のうちに決算→業績修正と2回出した銘柄は拾い直せない。
    翌日の検知で announced_date が変わるので、そこで開き直る。
    無駄な全件再取得を毎日払うより軽いと判断した。
    """
    import earnings_scraper
    from datetime import datetime, timezone, timedelta
    client = get_supabase_client()

    data = earnings_scraper.fetch_announced_stocks()
    # 市場の日付で見る。スケジューラは Asia/Tokyo なのにここだけUTCだと、
    # JSTの朝9時より前に走った検知が「前日の発表」として記録される。
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).date().isoformat()
    now = datetime.now(timezone.utc).isoformat()

    codes = [s['company_code'] for s in data['stocks']]

    # 今日の日付で既に積んである銘柄は触らない（processed を巻き戻さないため）
    already_today = set()
    if codes:
        try:
            for i in range(0, len(codes), 500):
                rows = (client.table('earnings_queue')
                        .select('company_code')
                        .in_('company_code', codes[i:i + 500])
                        .eq('announced_date', today)
                        .execute().data or [])
                already_today.update(r['company_code'] for r in rows)
        except Exception as e:
            # 読めなかったら「触らない」側に倒す。取りこぼしても翌日の検知で拾えるが、
            # 巻き戻すと全件再取得が走ってレート制限に当たる
            print(f'決算キューの照会エラー（今回は既存行を触りません）: {e}')
            already_today = set(codes)

    payload = [{
        'company_code': s['company_code'],
        'company_name': s.get('company_name'),
        'announced_date': today,
        'source': s.get('source'),
        'processed': False,
        'updated_at': now,
    } for s in data['stocks'] if s['company_code'] not in already_today]

    if payload:
        try:
            client.table('earnings_queue').upsert(payload).execute()
        except Exception as e:
            print(f'決算キューの記録エラー: {e}')

    # 未処理を古い順に取り出す（押し忘れた過去分もここで拾える）
    res = (client.table('earnings_queue')
           .select('company_code, company_name, announced_date')
           .eq('processed', False)
           .order('announced_date')
           .limit(1000)
           .execute())
    return res.data or [], data['by_source']


@app.route('/api/earnings/announced', methods=['GET'])
def api_earnings_announced():
    """決算発表銘柄を確認する（キューに記録するが更新はしない）"""
    try:
        pending, by_source = _enqueue_announced()
        return jsonify({'pending': pending, 'total': len(pending),
                        'by_source': by_source}), 200
    except Exception as e:
        print(f'決算発表銘柄の取得エラー: {e}')
        return jsonify({'error': '取得できませんでした', 'pending': []}), 500


@app.route('/api/earnings/update', methods=['POST'])
@admin_required_api
def api_update_earnings():
    """決算発表のあった銘柄（未処理分すべて）の財務データを更新する"""
    global earnings_status
    if earnings_status["running"]:
        return jsonify({"error": "すでに実行中です"}), 409

    try:
        pending, _ = _enqueue_announced()
        codes = [s['company_code'] for s in pending]
    except Exception as e:
        print(f'決算発表銘柄の取得エラー: {e}')
        return jsonify({"error": "決算発表銘柄を取得できませんでした"}), 500

    if not codes:
        return jsonify({"started": False, "total": 0,
                        "message": "更新が必要な銘柄はありません"}), 200

    earnings_status = {"running": True, "done": 0, "total": len(codes), "errors": 0,
                       "codes": codes, "stop_requested": False,
                       "finished_at": None, "error": None}
    threading.Thread(target=_update_earnings_background, args=(codes,), daemon=True).start()
    return jsonify({"started": True, "total": len(codes)}), 202


@app.route('/api/earnings/update/status', methods=['GET'])
def api_earnings_status():
    return jsonify(earnings_status), 200


@app.route('/api/earnings/update/stop', methods=['POST'])
@admin_required_api
def api_stop_earnings():
    earnings_status["stop_requested"] = True
    return jsonify({"stopping": True}), 200


daily_update_status = {"running": False, "phase": "", "done": 0, "total": 0,
                       "saved": 0, "stop_requested": False, "finished_at": None, "error": None}


def _update_daily_and_recalc_background():
    """日足を更新し、続けてGC/DCを再計算する。

    日足を更新してもGC/DCを計算し直さないと結果が変わらないため、
    2つを別々に押させず1本の処理として通す。
    """
    global daily_update_status, ma_cross_status
    import price_history as ph
    from datetime import datetime, timezone

    try:
        client = get_supabase_client()

        # 対象銘柄（screened_latest にある＝分析対象の銘柄）
        codes = []
        page = 0
        while page < 20:
            res = (client.table('screened_latest')
                   .select('company_code')
                   .range(page * 1000, page * 1000 + 999)
                   .execute())
            rows = res.data or []
            codes.extend(r['company_code'] for r in rows)
            if len(rows) < 1000:
                break
            page += 1

        daily_update_status.update({"phase": "日足を取得中", "total": len(codes), "done": 0})

        # まとめて取得し、まとめて保存する
        CHUNK = 100
        saved = 0
        for i in range(0, len(codes), CHUNK):
            if daily_update_status["stop_requested"]:
                break
            chunk = codes[i:i + CHUNK]
            fetched = ph.fetch_ohlc_batch(chunk, period='1y', chunk_size=CHUNK)
            now = datetime.now(timezone.utc).isoformat()
            payload = [{'company_code': c, 'daily_1y': rows,
                        'daily_updated_at': now, 'updated_at': now}
                       for c, rows in fetched.items()]
            if payload:
                try:
                    client.table('stock_price_history').upsert(payload).execute()
                    saved += len(payload)
                except Exception as e:
                    print(f'日足の保存エラー: {e}')
            daily_update_status["done"] = min(i + CHUNK, len(codes))
            daily_update_status["saved"] = saved

        if saved == 0 and codes:
            raise RuntimeError('日足を1件も保存できませんでした')

        # 続けてGC/DCを再計算する
        daily_update_status["phase"] = "GC/DCを再計算中"
        ma_cross_status.update({"running": True, "done": 0, "total": 0, "saved": 0,
                                "stop_requested": False, "error": None})
        _recalc_ma_crosses_background()

        # かぶたんとのすり合わせ。
        # 日付の一致は求めない（かぶたんの日付は発生日ではなく取得日）。
        # 「かぶたんは検知しているのに自前が古いまま」＝日足が壊れている銘柄を洗い出す。
        try:
            import ma_cross as _mc
            agreement = _mc.compare_with_signals()
            daily_update_status["agreement"] = agreement
            print(f"[すり合わせ] 比較{agreement['compared']}件 / "
                  f"かぶたんが検知済みで自前が古い: {agreement['stale_count']}件")
            for s in agreement["samples"]:
                print(f"   {s['company_code']} {s['kind']} かぶたん{s['kabutan']} / 自前{s['mine']}")
        except Exception as e:
            print(f'すり合わせエラー: {e}')

        daily_update_status["phase"] = "完了"

    except Exception as e:
        print(f'日足更新エラー: {e}')
        daily_update_status["error"] = str(e)[:200]
        daily_update_status["phase"] = "エラー"
    finally:
        from datetime import datetime, timezone
        daily_update_status["running"] = False
        daily_update_status["finished_at"] = datetime.now(timezone.utc).isoformat()


@app.route('/api/price-history/update', methods=['POST'])
@admin_required_api
def api_update_daily_prices():
    """日足を更新し、続けてGC/DCを再計算する（バックグラウンド）"""
    global daily_update_status
    if daily_update_status["running"] or ma_cross_status["running"]:
        return jsonify({"error": "すでに実行中です"}), 409

    daily_update_status = {"running": True, "phase": "準備中", "done": 0, "total": 0,
                           "saved": 0, "stop_requested": False, "finished_at": None, "error": None}
    threading.Thread(target=_update_daily_and_recalc_background, daemon=True).start()
    return jsonify({"started": True}), 202


@app.route('/api/price-history/update/status', methods=['GET'])
def api_update_daily_status():
    return jsonify({**daily_update_status, "ma": ma_cross_status}), 200


@app.route('/api/price-history/update/stop', methods=['POST'])
@admin_required_api
def api_stop_daily_update():
    daily_update_status["stop_requested"] = True
    return jsonify({"stopping": True}), 200


@app.route('/api/ma-crosses/recalculate', methods=['POST'])
@admin_required_api
def api_recalc_ma_crosses():
    """保存済みの日足からGC/DC発生日を再計算する（バックグラウンド）"""
    global ma_cross_status
    if ma_cross_status["running"]:
        return jsonify({"error": "すでに実行中です", "status": ma_cross_status}), 409

    ma_cross_status = {"running": True, "done": 0, "total": 0, "saved": 0,
                       "stop_requested": False, "finished_at": None, "error": None}
    threading.Thread(target=_recalc_ma_crosses_background, daemon=True).start()
    return jsonify({"started": True}), 202


@app.route('/api/ma-crosses/status', methods=['GET'])
def api_ma_crosses_status():
    return jsonify(ma_cross_status), 200


@app.route('/api/ma-crosses/stop', methods=['POST'])
@admin_required_api
def api_stop_ma_crosses():
    ma_cross_status["stop_requested"] = True
    return jsonify({"stopping": True}), 200


@app.route('/api/ma-crosses', methods=['GET'])
def api_list_ma_crosses():
    """GC（またはDC）発生日の新しい順に銘柄を返す。

    type=gc|dc、days で「直近N日以内に発生したもの」に絞れる。
    """
    try:
        from datetime import date, timedelta
        client = get_supabase_client()

        cross_type = (request.args.get('type') or 'gc').lower()
        column = 'latest_dc_date' if cross_type == 'dc' else 'latest_gc_date'
        limit = min(max(request.args.get('limit', 100, type=int) or 100, 1), 500)

        query = (client.table('ma_crosses')
                 .select('company_code, latest_gc_date, latest_dc_date, cross_count')
                 .not_.is_(column, 'null'))

        days = request.args.get('days', type=int)
        if days:
            query = query.gte(column, (date.today() - timedelta(days=days)).isoformat())

        res = query.order(column, desc=True).limit(limit).execute()
        rows = res.data or []

        # 銘柄名などを screened_latest から補う
        codes = [r['company_code'] for r in rows]
        detail = {}
        for i in range(0, len(codes), 100):
            chunk = codes[i:i + 100]
            d = (client.table('screened_latest')
                 .select('company_code, company_name, sector, stock_price, market_cap, per_forward, pbr, match_rate')
                 .in_('company_code', chunk).execute())
            for x in (d.data or []):
                detail[x['company_code']] = x

        merged = [{**r, **detail.get(r['company_code'], {})} for r in rows]
        return jsonify({'type': cross_type, 'rows': merged, 'total': len(merged)}), 200
    except Exception as e:
        print(f'GC/DC一覧の取得エラー: {e}')
        return jsonify({'error': '取得できませんでした', 'rows': []}), 500


@app.route('/api/report/<source>/<key>', methods=['GET'])
@member_required_api
def api_report(source, key):
    """企業分析レポートのデータを返す。会員限定（/reportページと同じ基準）。
    source は将来 'own'（経営者が自社の数字で作る）を足せるようURLに含めている。
    """
    try:
        import report_builder
        if source != 'listed':
            return jsonify({'error': 'このデータ源にはまだ対応していません'}), 400

        code = normalize_code(key)
        regenerate = request.args.get('regenerate') == '1'
        report = report_builder.build_report('listed', code, regenerate=regenerate)
        if not report:
            return jsonify({'error': 'この銘柄のデータがまだありません'}), 404
        return jsonify(report), 200
    except Exception as e:
        print(f'レポート生成エラー {source}/{key}: {e}')
        return jsonify({'error': 'レポートを作成できませんでした'}), 500


# =============================================
# 全銘柄スクリーニング
#
# 従来 /screener はウォッチリスト（自分で登録した数十件）しか見られず、
# screened_latest に溜まった全銘柄を横断して探す手段が無かった。
# =============================================

# 並べ替えに使ってよいカラム（任意の文字列を order に渡さないためのホワイトリスト）
# 事業概要の上限。実データは中央値85文字・最長184文字なので、
# 手で書き足す余地を見て少し広めに取る。長い分析は銘柄ノートへ。
BUSINESS_SUMMARY_MAX = 300

SCREEN_SORTABLE = {
    'match_rate', 'market_cap', 'stock_price', 'per_forward', 'pbr',
    'roe', 'roa', 'equity_ratio', 'operating_margin',
    'payout_ratio', 'operating_cf', 'free_cf',
    'revenue_growth_1y_cy', 'op_growth_1y_cy', 'current_ratio',
    # 配当利回りは予想が主（2026-08-25）。実績も並べ替えられるようには
    # しておくが、画面のヘッダーからは予想だけを押せるようにしてある。
    'dividend_yield_forward', 'dividend_yield',
    'company_code', 'analyzed_at', 'gc_date',
}

# クエリパラメータ名 -> (カラム, 比較方向)
SCREEN_FILTERS = {
    # ⚠️ 足すときは、その列が実際に埋まっているか確かめること。
    # 列があることと使えることは別。2026-08-25 時点で total_assets / equity /
    # margin_trading_ratio は**ほぼ空**なので絞り込みに出していない。
    #
    # 増減率と流動比率は同日に backfill_growth_columns.py で埋めた
    # （売上の増減率97.9% / 営業利益の増減率86.0% / 流動比率95.7%）。
    # 派生値なので毎晩 _recalculate_growth_columns() で作り直している。
    'per_min': ('per_forward', 'gte'),
    'per_max': ('per_forward', 'lte'),
    'pbr_min': ('pbr', 'gte'),
    'pbr_max': ('pbr', 'lte'),
    'roe_min': ('roe', 'gte'),
    'roe_max': ('roe', 'lte'),
    'roa_min': ('roa', 'gte'),
    'roa_max': ('roa', 'lte'),
    'equity_ratio_min': ('equity_ratio', 'gte'),
    'equity_ratio_max': ('equity_ratio', 'lte'),
    'operating_margin_min': ('operating_margin', 'gte'),
    'operating_margin_max': ('operating_margin', 'lte'),
    'payout_ratio_min': ('payout_ratio', 'gte'),
    'payout_ratio_max': ('payout_ratio', 'lte'),
    'operating_cf_min': ('operating_cf', 'gte'),
    'free_cf_min': ('free_cf', 'gte'),
    # 増減率。列の名前は決算期の世代で付いていて、スコアの言い方とずれる。
    #   revenue_growth_1y_cy = スコアの「売上高増減率(2期前→前期)」
    #   revenue_growth_cy_ny = スコアの「売上高増減率(前期→今期予)」
    # 対応は analysis_quality.GROWTH_COLUMNS の注記が正。
    'revenue_growth_min': ('revenue_growth_1y_cy', 'gte'),
    'revenue_growth_max': ('revenue_growth_1y_cy', 'lte'),
    'revenue_forecast_growth_min': ('revenue_growth_cy_ny', 'gte'),
    'op_growth_min': ('op_growth_1y_cy', 'gte'),
    'op_growth_max': ('op_growth_1y_cy', 'lte'),
    'op_forecast_growth_min': ('op_growth_cy_ny', 'gte'),
    'current_ratio_min': ('current_ratio', 'gte'),
    'match_rate_max': ('match_rate', 'lte'),
    'dividend_yield_max': ('dividend_yield_forward', 'lte'),
    # 「高配当を探す」で期待されているのは予想利回り。実績で絞ると、
    # 決算期をまたいで期末＋翌期中間が同じ12か月に入った銘柄が
    # 実力以上の利回りで並ぶ（367A: 実績5.93% / 予想4.08%）。
    # ⚠️ 予想を持たない銘柄はこの絞り込みから外れる。2026-08-25 時点で
    #    実績はあるが予想が無いのは38件（全体の1%）。
    'dividend_yield_min': ('dividend_yield_forward', 'gte'),
    'market_cap_min': ('market_cap', 'gte'),
    'market_cap_max': ('market_cap', 'lte'),
    'match_rate_min': ('match_rate', 'gte'),
}

SCREEN_COLUMNS = (
    'company_code, company_name, sector, industry_jp, market_segment, '
    'business_summary_jp, market_cap, stock_price, '
    'per_forward, pbr, roe, roa, equity_ratio, operating_margin, '
    'dividend_yield, dividend_yield_forward, payout_ratio, '
    'match_rate, score_complete, operating_cf, free_cf, '
    'forecast_revenue, forecast_op_income, financial_history, cf_history, '
    'analyzed_at, gc_date, dc_date'
)

# ROEランキングで採用する自己資本比率の下限(%)。
# これを下回るとROEの分母が小さすぎて数値が不安定になるため順位付けに使わない。
ROE_MIN_EQUITY_RATIO = 5

# 東証の市場区分。プルダウンの並びは規模の大きい順に固定する
MARKET_SEGMENTS = ['プライム', 'スタンダード', 'グロース']

_sector_cache = {'values': None, 'fetched_at': 0}


@app.route('/api/stocks/sectors', methods=['GET'])
def api_stock_sectors():
    """業種の一覧（絞り込みプルダウン用）。実データから作り10分キャッシュする。

    業種はJPXの33業種区分（industry_jp）を使う。
    従来使っていた sector は英語の分類を訳したもので粒度が粗く
    （「資本財」に機械も電機も建設も入る）、絞り込みの軸にならなかった。
    """
    import time as _time
    try:
        cached = _sector_cache['values']
        if cached and _time.time() - _sector_cache['fetched_at'] < 600:
            return jsonify({'sectors': cached, 'industries': cached,
                            'markets': MARKET_SEGMENTS}), 200

        client = get_supabase_client()
        industries = set()
        page = 0
        while page < 10:  # 上限を設けて暴走を防ぐ
            res = (client.table('screened_latest')
                   .select('industry_jp')
                   .range(page * 1000, page * 1000 + 999)
                   .execute())
            rows = res.data or []
            for r in rows:
                s = (r.get('industry_jp') or '').strip()
                if s:
                    industries.add(s)
            if len(rows) < 1000:
                break
            page += 1

        values = sorted(industries)
        _sector_cache['values'] = values
        _sector_cache['fetched_at'] = _time.time()
        return jsonify({'sectors': values, 'industries': values,
                        'markets': MARKET_SEGMENTS}), 200
    except Exception as e:
        print(f'業種一覧の取得エラー: {e}')
        return jsonify({'sectors': [], 'industries': [],
                        'markets': MARKET_SEGMENTS}), 200


_tag_count_cache = {'values': None, 'fetched_at': 0}


def _tag_counts(client):
    """テーマごとの銘柄数。10分キャッシュする。

    件数はテーブル全体を読まないと出せない。タグは1銘柄あたり数件付くため
    全銘柄で1万行を超える。毎回読むと画面の表示が目に見えて遅くなる。
    """
    import time as _time
    if (_tag_count_cache['values'] is not None
            and _time.time() - _tag_count_cache['fetched_at'] < 600):
        return _tag_count_cache['values']

    counts = {}
    page = 0
    while page < 50:
        res = (client.table('stock_tag_map')
               .select('tag_name')
               .range(page * 1000, page * 1000 + 999)
               .execute())
        rows = res.data or []
        for r in rows:
            counts[r['tag_name']] = counts.get(r['tag_name'], 0) + 1
        if len(rows) < 1000:
            break
        page += 1

    _tag_count_cache['values'] = counts
    _tag_count_cache['fetched_at'] = _time.time()
    return counts


@app.route('/api/stocks/tags', methods=['GET'])
def api_stock_tags():
    """テーマ・属性の一覧をカテゴリごとに返す。

    銘柄数も返すので、画面側で「該当0件のテーマ」を出さずに済む。
    """
    try:
        client = get_supabase_client()
        kind = request.args.get('kind')          # theme / attribute
        include_hidden = request.args.get('all') == '1'

        query = client.table('stock_tags').select(
            'name, kind, category, description, display_active, sort_order')
        if kind:
            query = query.eq('kind', kind)
        if not include_hidden:
            query = query.eq('display_active', True)
        tags = (query.order('sort_order').execute().data or [])

        counts = _tag_counts(client)

        categories = {}
        for t in tags:
            cat = t.get('category') or 'その他'
            categories.setdefault(cat, {'category': cat, 'kind': t.get('kind'),
                                        'count': 0, 'tags': []})
            entry = {**t, 'count': counts.get(t['name'], 0)}
            categories[cat]['tags'].append(entry)
            categories[cat]['count'] += entry['count']

        return jsonify({'categories': list(categories.values())}), 200
    except Exception as e:
        print(f'タグ一覧の取得エラー: {e}')
        return jsonify({'error': '取得できませんでした', 'categories': []}), 500


# ---- テーマ管理（admin専用） ----

def _bump_tag_cache():
    """テーマを編集したら、一覧・件数のキャッシュを捨てて次回作り直させる"""
    _tag_count_cache['values'] = None
    _tag_count_cache['fetched_at'] = 0


@app.route('/api/admin/themes', methods=['GET'])
@role_required('admin')
def api_admin_themes():
    """テーマ・業種の全件（非表示・未使用も含む）をカテゴリ別に返す。

    運用画面用なので display_active=false のものも隠さない。
    件数は0のテーマも「未使用として把握したい」ため一覧に残す。
    """
    try:
        client = get_supabase_client()
        tags = (client.table('stock_tags')
                .select('name, kind, category, description, tagging_enabled, '
                        'display_active, sort_order')
                .order('sort_order').execute().data or [])
        counts = _tag_counts(client)

        cats = {}
        for t in tags:
            cat = t.get('category') or 'その他'
            cats.setdefault(cat, {'category': cat, 'tags': []})
            cats[cat]['tags'].append({**t, 'count': counts.get(t['name'], 0)})
        return jsonify({'categories': list(cats.values())}), 200
    except Exception as e:
        print(f'テーマ管理一覧の取得エラー: {e}')
        return jsonify({'error': '取得できませんでした', 'categories': []}), 500


@app.route('/api/admin/themes', methods=['POST'])
@role_required('admin')
def api_admin_theme_create():
    """テーマを新規追加する"""
    try:
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'テーマ名を入力してください'}), 400

        client = get_supabase_client()
        exists = (client.table('stock_tags').select('name')
                  .eq('name', name).limit(1).execute().data)
        if exists:
            return jsonify({'error': f'「{name}」は既にあります'}), 409

        row = {
            'name': name,
            'kind': data.get('kind') or 'theme',
            'category': (data.get('category') or 'その他').strip(),
            'description': (data.get('description') or '').strip() or None,
            'tagging_enabled': bool(data.get('tagging_enabled', True)),
            'display_active': bool(data.get('display_active', True)),
            'sort_order': int(data.get('sort_order') or 900),
        }
        client.table('stock_tags').insert(row).execute()
        _bump_tag_cache()
        return jsonify({'success': True, 'tag': row}), 200
    except Exception as e:
        print(f'テーマ追加エラー: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/themes/<path:name>', methods=['PATCH'])
@role_required('admin')
def api_admin_theme_update(name):
    """テーマの属性（説明・表示ON/OFF・LLM候補ON/OFF・カテゴリ等）を更新する。

    主キーの name 自体は変更しない。改名すると stock_tag_map との整合を
    取り直す必要があり、事故のもとになるため別operationにしてある。
    """
    try:
        data = request.get_json() or {}
        patch = {}
        for key in ('category', 'description'):
            if key in data:
                patch[key] = (data.get(key) or '').strip() or None
        for key in ('tagging_enabled', 'display_active'):
            if key in data:
                patch[key] = bool(data.get(key))
        if 'sort_order' in data:
            patch['sort_order'] = int(data.get('sort_order') or 900)
        if not patch:
            return jsonify({'error': '更新内容がありません'}), 400

        client = get_supabase_client()
        res = (client.table('stock_tags').update(patch)
               .eq('name', name).execute())
        if not res.data:
            return jsonify({'error': 'テーマが見つかりません'}), 404
        _bump_tag_cache()
        return jsonify({'success': True, 'tag': res.data[0]}), 200
    except Exception as e:
        print(f'テーマ更新エラー: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/themes/<path:name>', methods=['DELETE'])
@role_required('admin')
def api_admin_theme_delete(name):
    """テーマを削除する。付いている銘柄の紐付けも一緒に消す。

    紐付けを残すと、マスタに無いタグが宙に浮いて画面で拾えなくなる。
    """
    try:
        client = get_supabase_client()
        client.table('stock_tag_map').delete().eq('tag_name', name).execute()
        client.table('stock_tags').delete().eq('name', name).execute()
        _bump_tag_cache()
        return jsonify({'success': True}), 200
    except Exception as e:
        print(f'テーマ削除エラー: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/themes/<path:name>/stocks', methods=['GET'])
@role_required('admin')
def api_admin_theme_stocks(name):
    """そのテーマが付いている銘柄の一覧（手動編集画面用）"""
    try:
        client = get_supabase_client()
        codes = []
        page = 0
        while page < 20:
            res = (client.table('stock_tag_map')
                   .select('company_code, source')
                   .eq('tag_name', name)
                   .range(page * 1000, page * 1000 + 999).execute())
            rows = res.data or []
            codes.extend(rows)
            if len(rows) < 1000:
                break
            page += 1

        src = {r['company_code']: r.get('source') for r in codes}
        names = list(src.keys())
        detail = {}
        for i in range(0, len(names), 100):
            chunk = names[i:i + 100]
            res = (client.table('screened_latest')
                   .select('company_code, company_name, industry_jp')
                   .in_('company_code', chunk).execute())
            for r in (res.data or []):
                detail[r['company_code']] = r

        stocks = []
        for code in names:
            d = detail.get(code, {})
            stocks.append({
                'company_code': code,
                'company_name': d.get('company_name'),
                'industry_jp': d.get('industry_jp'),
                'source': src.get(code),
            })
        stocks.sort(key=lambda s: s['company_code'])
        return jsonify({'name': name, 'total': len(stocks), 'stocks': stocks}), 200
    except Exception as e:
        print(f'テーマ別銘柄の取得エラー: {e}')
        return jsonify({'error': str(e), 'stocks': []}), 500


@app.route('/api/admin/themes/<path:name>/stocks', methods=['POST'])
@role_required('admin')
def api_admin_theme_add_stock(name):
    """銘柄にテーマを手動で付ける。

    source='manual' で入れる。次にLLMで付け直しても、manualは
    上書き対象外なので消えない（backfillはsource='llm'だけ入れ替える）。
    """
    try:
        data = request.get_json() or {}
        code = normalize_code((data.get('company_code') or '').strip())
        if not code:
            return jsonify({'error': '銘柄コードを指定してください'}), 400

        client = get_supabase_client()
        tag = (client.table('stock_tags').select('name')
               .eq('name', name).limit(1).execute().data)
        if not tag:
            return jsonify({'error': 'テーマが見つかりません'}), 404
        stock = (client.table('screened_latest').select('company_code, company_name, industry_jp')
                 .eq('company_code', code).limit(1).execute().data)
        if not stock:
            return jsonify({'error': f'銘柄 {code} が見つかりません'}), 404

        client.table('stock_tag_map').upsert(
            {'company_code': code, 'tag_name': name, 'source': 'manual'}).execute()
        _bump_tag_cache()
        s = stock[0]
        return jsonify({'success': True, 'stock': {
            'company_code': code, 'company_name': s.get('company_name'),
            'industry_jp': s.get('industry_jp'), 'source': 'manual'}}), 200
    except Exception as e:
        print(f'テーマへの銘柄追加エラー: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/themes/<path:name>/stocks/<code>', methods=['DELETE'])
@role_required('admin')
def api_admin_theme_remove_stock(name, code):
    """銘柄からテーマを外す"""
    try:
        client = get_supabase_client()
        client.table('stock_tag_map').delete().eq(
            'tag_name', name).eq('company_code', normalize_code(code)).execute()
        _bump_tag_cache()
        return jsonify({'success': True}), 200
    except Exception as e:
        print(f'テーマからの銘柄削除エラー: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/themes/<path:name>/suggest', methods=['POST'])
@role_required('admin')
def api_admin_theme_suggest(name):
    """このテーマに該当しそうな銘柄をLLMに提案させる。

    未使用テーマ（HBM・パワー半導体など）は事業説明文だけからは判定しづらく、
    全銘柄を再走査するのは費用が高い。そこで2段構えにする。

      1. キーワードで候補を粗く絞る（説明文の部分一致。費用ゼロ）
      2. 候補だけをLLMに渡し、本当に該当するかを判定させる

    こうすると、全3,600件でなく数十件の判定で済む。
    提案するだけで、実際に付けるかは人が確認して決める。
    """
    try:
        data = request.get_json() or {}
        keywords = [k.strip() for k in (data.get('keywords') or '').split(',') if k.strip()]

        client = get_supabase_client()
        tag = (client.table('stock_tags').select('name, description')
               .eq('name', name).limit(1).execute().data)
        if not tag:
            return jsonify({'error': 'テーマが見つかりません'}), 404
        desc = tag[0].get('description') or ''

        # キーワード未指定ならテーマ名自体を使う
        if not keywords:
            keywords = [name]

        # 既に付いている銘柄は候補から除く
        already = set()
        page = 0
        while page < 20:
            res = (client.table('stock_tag_map').select('company_code')
                   .eq('tag_name', name)
                   .range(page * 1000, page * 1000 + 999).execute())
            rows = res.data or []
            already.update(r['company_code'] for r in rows)
            if len(rows) < 1000:
                break
            page += 1

        # キーワードで候補を集める（説明文の部分一致）
        candidates = {}
        for kw in keywords[:8]:
            res = (client.table('screened_latest')
                   .select('company_code, company_name, industry_jp, business_summary_jp')
                   .ilike('business_summary_jp', f'*{kw}*')
                   .limit(60).execute())
            for r in (res.data or []):
                if r['company_code'] not in already:
                    candidates[r['company_code']] = r
        cand = list(candidates.values())[:50]

        if not cand:
            return jsonify({'name': name, 'suggestions': [],
                            'note': '候補が見つかりませんでした。キーワードを変えてみてください。'}), 200

        # LLMに該当判定させる
        import llm
        if not llm.is_available():
            # LLMが無ければキーワード一致をそのまま候補として返す
            return jsonify({'name': name, 'suggestions': [
                {'company_code': c['company_code'], 'company_name': c.get('company_name'),
                 'industry_jp': c.get('industry_jp'), 'reason': 'キーワード一致'}
                for c in cand[:20]]}), 200

        listing = '\n'.join(
            f"{c['company_code']} {c.get('company_name') or ''}: "
            f"{(c.get('business_summary_jp') or '')[:80]}" for c in cand)
        prompt = (
            f'次のテーマに、本当に該当する企業だけを選んでください。\n'
            f'テーマ: {name}\n'
            f'定義: {desc or "（定義なし）"}\n\n'
            '判定は事業内容にもとづいて厳しめに。関連しそう・将来性がある程度では選ばない。\n'
            '各企業について、該当するかと一言の理由を返してください。\n\n'
            '候補:\n' + listing + '\n\n'
            '次のJSONのみ:\n'
            '{"picks": [{"code": "銘柄コード", "reason": "該当する理由"}, ...]}')
        result = llm.chat_json(prompt, model='gpt-4o-mini', temperature=0.2, timeout=60)

        picks = (result or {}).get('picks') or []
        by_code = {c['company_code']: c for c in cand}
        suggestions = []
        for p in picks:
            code = str(p.get('code') or '').strip()
            if code in by_code:
                c = by_code[code]
                suggestions.append({
                    'company_code': code, 'company_name': c.get('company_name'),
                    'industry_jp': c.get('industry_jp'),
                    'reason': (p.get('reason') or '')[:60]})

        return jsonify({'name': name, 'suggestions': suggestions,
                        'scanned': len(cand)}), 200
    except Exception as e:
        print(f'テーマ提案エラー: {e}')
        return jsonify({'error': str(e), 'suggestions': []}), 500


def _codes_for_tags(client, tag_names=None, category=None):
    """指定のテーマ／カテゴリに該当する銘柄コードの集合を返す。

    カテゴリ指定では、そのカテゴリに属するテーマを1つでも持つ銘柄を拾う。
    銘柄に「金融」という大枠タグを別途付ける必要はない。
    """
    names = list(tag_names or [])

    if category:
        res = (client.table('stock_tags')
               .select('name')
               .eq('category', category)
               .execute())
        names.extend(r['name'] for r in (res.data or []))

    if not names:
        return None   # 絞り込み指定なし

    codes = set()
    # in_ に多数を渡すとURLが長くなりすぎるため分割する
    for i in range(0, len(names), 50):
        chunk = names[i:i + 50]
        page = 0
        while page < 30:
            res = (client.table('stock_tag_map')
                   .select('company_code')
                   .in_('tag_name', chunk)
                   .range(page * 1000, page * 1000 + 999)
                   .execute())
            rows = res.data or []
            codes.update(r['company_code'] for r in rows)
            if len(rows) < 1000:
                break
            page += 1
    return codes


def _attach_gc(rows):
    """各行に gc_active（今ゴールデン状態か）を付ける。

    gc_date / dc_date は screened_latest に持たせているので追加の問い合わせは不要。
    直近のクロスがGCなら（GCの後にDCが来ていなければ）True。
    単なる状態の色分け用で、売買を促す表現は付けない。
    """
    for r in rows:
        gc = r.get('gc_date')
        dc = r.get('dc_date')
        r['gc_active'] = bool(gc and (not dc or gc >= dc))


@app.route('/api/stocks/screen', methods=['GET'])
@member_required_api
def api_screen_stocks():
    """全銘柄を横断して絞り込み・並べ替え・ページングする。

    会員限定。match_rate（合致度スコア）や横断的な絞り込みは会員価値であり、
    スクリーナー画面自体がログイン必須。APIを素通しにするとスコアを直叩きで
    収集できてしまうため、ページと同じ基準でゲートする。
    """
    try:
        client = get_supabase_client()

        page = max(1, request.args.get('page', 1, type=int) or 1)
        per_page = request.args.get('per_page', 50, type=int) or 50
        per_page = min(max(per_page, 1), 200)

        sort = request.args.get('sort') or 'match_rate'
        if sort not in SCREEN_SORTABLE:
            sort = 'match_rate'
        desc = (request.args.get('order') or 'desc').lower() != 'asc'

        query = client.table('screened_latest').select(SCREEN_COLUMNS, count='exact')

        # 社名が無い行は分析が成立していないので除外する
        query = query.not_.is_('company_name', 'null')

        # ETF・REIT等は事業会社でないのでスクリーナーに出さない。
        # DBの行は消していない（security_filter.EXCLUDED_CODES から外せば戻る）
        from security_filter import exclude_delisted, exclude_non_operating
        query = exclude_non_operating(query)

        # 上場廃止も出さない。株価が最終売買日で凍結されており、
        # スコアや割安さを他社と並べても意味がない
        query = exclude_delisted(query)

        # 数値の絞り込み
        for param, (column, op) in SCREEN_FILTERS.items():
            raw = request.args.get(param)
            if raw is None or raw == '':
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            query = query.gte(column, value) if op == 'gte' else query.lte(column, value)

        # 決算月とデータ充足度は数値の大小ではないので、上の一括処理と分ける。
        # fiscal_month は 1〜12、score_complete は真偽値。
        fiscal_month = (request.args.get('fiscal_month') or '').strip()
        if fiscal_month.isdigit() and 1 <= int(fiscal_month) <= 12:
            query = query.eq('fiscal_month', int(fiscal_month))

        # 「12項目すべて判定できた銘柄だけ」。判定できていない項目があると
        # スコアは分母が小さくなるので、点数の比較が同じ土俵にならない。
        if (request.args.get('score_complete') or '').lower() in ('1', 'true', 'yes'):
            query = query.eq('score_complete', True)

        # 業種はJPXの33業種（industry_jp）で絞る。
        # sector は旧来の粗い分類。保存済みURLが壊れないよう受け付けは残すが、
        # どちらの列を見るかは推測せずパラメータ名で決める
        industry = (request.args.get('industry') or '').strip()
        if industry:
            query = query.eq('industry_jp', industry)

        sector = (request.args.get('sector') or '').strip()
        if sector and not industry:
            query = query.eq('sector', sector)

        market = (request.args.get('market') or '').strip()
        if market in MARKET_SEGMENTS:
            query = query.eq('market_segment', market)

        # テーマ／カテゴリでの絞り込み。
        # カテゴリ指定では、そのカテゴリのテーマを1つでも持つ銘柄を拾う
        # （銘柄に「金融」のような大枠タグを別途付ける必要はない）。
        tag_names = [t for t in request.args.getlist('tag') if t.strip()]
        tag_category = (request.args.get('tag_category') or '').strip()
        if tag_names or tag_category:
            codes = _codes_for_tags(client, tag_names, tag_category or None)
            if not codes:
                # 該当銘柄が無い場合は空を返す。フィルタ自体を無視すると
                # 「絞り込んだのに全件出る」という誤解を招く
                return jsonify({'rows': [], 'total': 0, 'page': page,
                                'per_page': per_page, 'total_pages': 1,
                                'sort': sort, 'order': 'desc' if desc else 'asc'}), 200
            # in_ の上限を超えないよう、多すぎる場合は絞り込みを分割せず件数で制限する
            query = query.in_('company_code', list(codes)[:2000])

        # 事業内容のフリーワード検索。
        # テーマで拾いきれない長い裾（「厨房」「金型」など）を拾うため。
        # 日本語は単語区切りが無いので部分一致で扱う
        business = (request.args.get('business') or '').strip()
        if business:
            safe = business.replace(',', ' ').replace('(', ' ').replace(')', ' ').strip()
            if safe:
                query = query.ilike('business_summary_jp', f'*{safe}*')

        # 銘柄コード・社名の部分一致
        keyword = (request.args.get('q') or '').strip()
        if keyword:
            safe = keyword.replace(',', ' ').replace('(', ' ').replace(')', ' ').strip()
            if safe:
                query = query.or_(f'company_code.ilike.*{safe}*,company_name.ilike.*{safe}*')

        # 並べ替えるカラムが空の行は順位付けできないため除外する
        # （例: ROE順で見たいのにROEが無い銘柄が混ざると一覧が読みにくい）
        if sort != 'company_code':
            query = query.not_.is_(sort, 'null')

        # ROE順のときは自己資本が極端に薄い企業を除外する。
        # ROE = 純利益 ÷ 自己資本 のため、分母が小さいと数値が爆発する。
        # 実データでは自己資本比率3.49%の銘柄がROE10,404%として1位に来ており、
        # そのままではランキングとして成立しない。
        # include_anomalies=1 で除外を解除できる。
        if sort == 'roe' and request.args.get('include_anomalies') != '1':
            query = query.gte('equity_ratio', ROE_MIN_EQUITY_RATIO)

        offset = (page - 1) * per_page
        query = query.order(sort, desc=desc)

        # スコア順のときは「同じスコアなら全項目を判定できた方（緑）を上」。
        #
        # 画面のスコアは緑（12項目すべて判定）と橙（判定できた分だけの暫定値）が
        # あり、同じ100でも中身の確かさが違う。並べ替えの第2キーにすることで、
        # 確かな方が先に出る。
        #
        # **ブラウザ側で並べ替えてはいけない。** 一覧は50件ずつサーバーで
        # 区切っているので、取得後に並べると同じスコアの塊がページをまたいだ
        # ときに順序が崩れる（1ページ目に緑と橙が混ざり、2ページ目にまた緑が出る）。
        if sort == 'match_rate':
            try:
                query = query.order('score_complete', desc=True, nullsfirst=False)
            except TypeError:
                # nullsfirst を受け取らない版のための保険
                query = query.order('score_complete', desc=True)

        res = query.range(offset, offset + per_page - 1).execute()

        rows = res.data or []
        _attach_gc(rows)
        for row in rows:
            attach_score_quality(row)
            # 判定にだけ使う履歴はレスポンスから外し、一覧APIを軽く保つ。
            row.pop('financial_history', None)
            row.pop('cf_history', None)

        total = res.count or 0
        return jsonify({
            'rows': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page if per_page else 1,
            'sort': sort,
            'order': 'desc' if desc else 'asc',
        }), 200
    except Exception as e:
        print(f'スクリーニングのエラー: {e}')
        return jsonify({'error': '銘柄の絞り込みに失敗しました', 'rows': [], 'total': 0}), 500


@app.route('/api/stock/price-history/<company_code>', methods=['GET'])
def api_price_history(company_code):
    """チャート用の株価履歴を返す。
    range=1m|3m|6m|1y は日足、2y|3y|5y は週足、10y は月足。
    期間に応じて足を間引くことで、長期でもローソクが潰れないようにしている。
    """
    try:
        import price_history as ph
        code = normalize_code(company_code)
        range_key = (request.args.get('range') or '1y').lower()
        granularity = ph.granularity_for_range(range_key)

        crosses = None
        if granularity == 'daily':
            rows = ph.get_daily(code)
            # 日足はここで最新化されるので、同じデータからGC/DCも計算し直す。
            # これをやらないと、チャートには出ているクロスがトレンド表示に
            # 反映されない（一括再計算を手で押すまでズレ続ける）。
            try:
                import ma_cross
                crosses = ma_cross.calculate_for_code(code)
            except Exception as e:
                print(f'GC/DCの再計算エラー {code}: {e}')
        else:
            rows = ph.get_long_term(code, granularity)

        # 流動性は日足からしか出さない。週足・月足に集約したあとの出来高は
        # 1本が数週間分の合計なので、「1日あたり」の意味にならない。
        # ここで返すのは、すでに読み込んだ日足の使い回しなので追加の取得はない。
        liquidity = ph.liquidity_summary(rows) if granularity == 'daily' else None

        return jsonify({
            'company_code': code,
            'range': range_key,
            'granularity': granularity,
            'rows': rows or [],
            'liquidity': liquidity,
            'gc_date': (crosses or {}).get('latest_gc_date'),
            'dc_date': (crosses or {}).get('latest_dc_date'),
        }), 200
    except Exception as e:
        print(f"株価履歴の取得エラー {company_code}: {e}")
        return jsonify({"error": "株価履歴を取得できませんでした"}), 500


@app.route('/api/market/indices', methods=['GET'])
def api_market_indices():
    """マーケットページの一覧カード用。全指数の最新値と前日比を返す。"""
    try:
        import market_data as md
        return jsonify({'items': md.get_summaries()}), 200
    except Exception as e:
        print(f'指数一覧の取得エラー: {e}')
        return jsonify({'error': '指数を取得できませんでした', 'items': []}), 500


@app.route('/api/market/index/<key>', methods=['GET'])
def api_market_index(key):
    """指数のチャート用データ。range=1m|3m|6m|1y は日足、2y〜5y は週足、10y は月足。

    キー（n225 等）でしか引けないようにしているのは、任意のティッカーを
    外部から投げ込ませないため。
    """
    try:
        import market_data as md
        idx = md.INDEX_BY_KEY.get(key)
        if not idx:
            return jsonify({'error': '不明な指数です'}), 404

        range_key = (request.args.get('range') or '1y').lower()
        rows, granularity = md.get_rows(key, range_key)
        return jsonify({
            'key': key,
            'name': idx['name'],
            'currency': idx['currency'],
            'prefix': idx['prefix'],
            'decimals': idx['decimals'],
            'range': range_key,
            'granularity': granularity,
            'rows': rows or [],
        }), 200
    except Exception as e:
        print(f'指数チャートの取得エラー {key}: {e}')
        return jsonify({'error': '指数を取得できませんでした'}), 500


@app.route('/api/market/valuation', methods=['GET'])
def api_market_valuation():
    """市場全体のPER・PBRの月次系列。

    指数のPERではなく市場全体の数値（日本=東証プライム / 米国=S&P500）。
    定義が違うので、出所と注記も一緒に返して画面に必ず出させる。
    """
    try:
        import market_valuation as mval
        series = mval.get_series()
        return jsonify({
            'markets': mval.MARKETS,
            'series': series,
        }), 200
    except Exception as e:
        print(f'市場バリュエーションの取得エラー: {e}')
        return jsonify({'error': 'PERを取得できませんでした', 'series': {}}), 500


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


# 未ログインでも返してよいフィールド（許可リスト方式＝既定は返さない）。
# 会員限定の中身（5年財務・CF・財務健全性・会社予想・成長率・ROA）を
# APIから外すための土台。ここに無い列は非会員には一切返らない。
#
# 重要: 以前は screened_latest の全カラムを誰にでも返し、画面ではCSSで
# ぼかしていただけだった。ぼかしは見た目だけで、DevTools・ソース表示・
# APIのNetworkタブ・curl で中身が丸見えだった。会員限定は必ずサーバー側で
# 落とす。合致度の判定はブラウザでこれらの値から計算しているため、
# 入力値を渡さなければ非会員は判定を再現できない。
# 欠損理由を添えて返す項目。値がある項目は omissions に載らない。
OMISSION_FIELDS = (
    'per', 'pbr', 'dividend_yield', 'equity_ratio', 'operating_margin',
    'market_cap', 'cash', 'current_liabilities', 'current_ratio',
    'payout_ratio', 'margin_trading_ratio',
    'major_shareholders_jp', 'company_officers',
    'established', 'listing_date',
)

FREE_SCREENED_FIELDS = {
    'company_code', 'company_name', 'sector', 'industry_jp', 'market_segment',
    'stock_price', 'market_cap', 'per_forward', 'pbr', 'equity_ratio',
    'operating_margin', 'dividend_yield',
    # 予想利回りも無料側に置く。実績だけを返すと、非会員には決算期をまたいで
    # 高く出た数字（367A: 6.18%）しか見えず、一番誤解を招く値だけが公開される。
    # 深さとしては実績利回りと同じ「銘柄の基本情報」であり、会社予想の
    # 売上・利益（会員限定）とは別物。
    'dps_forecast', 'dividend_yield_forward',
    'business_summary', 'business_summary_jp',
    'established', 'listing_date', 'ceo_name', 'headquarters', 'market',
    'data_status', 'data_source', 'source_status',
    'gc_date', 'dc_date', 'analyzed_at',
    # 主要株主・役員は公開情報の整理なので無料側
    'major_holders', 'institutional_holders', 'company_officers',
    'major_shareholders_jp',
}


# 同じ銘柄で取得に失敗し続けても、閲覧のたびに外部を叩かないための間隔。
# 株主・役員は年1回しか変わらないので、短くする理由がない。
HOLDERS_RETRY_HOURS = 24 * 7
# 未収録（そもそも取得元にデータが無い）銘柄はさらに間隔を空ける。
HOLDERS_NO_DATA_RETRY_HOURS = 24 * 30


def _holders_retry_allowed(source_status):
    """前回の取得結果を見て、いま再取得してよいかを判定する"""
    from datetime import datetime, timezone, timedelta

    if isinstance(source_status, str):
        try:
            source_status = json.loads(source_status)
        except (TypeError, ValueError):
            source_status = {}
    entry = (source_status or {}).get('holders_officers') or {}

    # 高速バッチでスキップされただけなら、待たずに取りに行ってよい
    if entry.get('status') == 'skipped':
        return True

    fetched_at = entry.get('fetched_at')
    if not fetched_at:
        return True
    try:
        last = datetime.fromisoformat(str(fetched_at).replace('Z', '+00:00'))
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    hours = (HOLDERS_NO_DATA_RETRY_HOURS if entry.get('status') == 'no_data'
             else HOLDERS_RETRY_HOURS)
    return datetime.now(timezone.utc) - last >= timedelta(hours=hours)


def _officers_are_useful(officers):
    """役員データが「経営陣が株主か」を見るのに使えるか。

    使えると言えるのは、持株数が入っているか、少なくとも日本語の氏名が
    あって大株主リストと突き合わせられる場合。yfinance が返す
    「Mr. Hideo Misawa」だけの行はどちらも満たさない。
    """
    if not officers:
        return False
    if isinstance(officers, str):
        try:
            officers = json.loads(officers)
        except (TypeError, ValueError):
            return False
    for row in officers or []:
        if not isinstance(row, dict):
            continue
        if row.get('shares') is not None:
            return True
        name = str(row.get('name_jp') or row.get('name') or '')
        # 日本語（ひらがな・カタカナ・漢字）が入っていれば名寄せに使える
        if any('぀' <= ch <= 'ヿ' or '一' <= ch <= '鿿'
               for ch in name):
            return True
    return False


def fetch_and_store_holders_officers(company_code):
    """主要株主・役員を取りに行き、screened_latest に保存する。

    閲覧時のオンデマンド取得（/api/stock/holders-officers）と、
    夜間のバックフィル（scheduled_backfill_holders_officers）の両方から呼ぶ。
    **処理を1本にしておく**。片方だけ直すと、画面から見たときと夜間で
    保存される内容が変わる。

    Returns: (dict, HTTPステータス)
    """
    from datetime import datetime, timezone

    code = normalize_code(company_code)
    symbol = normalize_analysis_symbol(code)
    if not symbol.endswith('.T'):
        return {"status": "skipped", "reason": "日本株のみ対応"}, 200

    existing = get_screened_data(code) or {}

    def _has(key):
        value = existing.get(key)
        if isinstance(value, str):
            value = value.strip()
            return bool(value) and value not in ('[]', 'null')
        return bool(value)

    if _has('major_shareholders_jp') and _has('company_officers'):
        return {"status": "cached"}, 200

    if not _holders_retry_allowed(existing.get('source_status')):
        return {
            "status": "cooldown",
            "reason": "前回の取得から間隔を空けています",
        }, 200

    result = {'source_status': {}}
    analyzer = StockAnalyzer()
    sources = []

    def _fetch():
        # 無料ソース → 確認済み公式キャッシュ → EDINET DB の順。
        # 概要・財務・業績予想の枠は消費しない。
        analyzer._get_holders_and_officers(symbol, result)
        if result.get('major_shareholders_jp') or result.get('company_officers'):
            sources.append(result.get('holders_source') or 'yfinance/yahooquery')

        from official_company_profiles import apply_official_profile_fallback
        if apply_official_profile_fallback(symbol, result):
            sources.append('会社公式開示（確認済みキャッシュ）')

        # yfinance の役員は「Mr. Hideo Misawa」のように英語名だけで、
        # 役職も持株数も入らないことがある。それが埋まっていると
        # apply_edinet_db_fallback が「役員は取得済み」と判断して
        # EDINET DB を呼ばず、**持株数のある役員データが永久に入らない**。
        # 役員の持株数は「経営陣が株主か」を見るための中心的な材料なので、
        # 使えない役員データは一旦どけて、EDINET DB に取りに行かせる。
        weak_officers = None
        if result.get('company_officers') and not _officers_are_useful(
                result.get('company_officers')):
            weak_officers = result.pop('company_officers')

        # 片方だけ取れた場合も、欠けている方はEDINET DBで補う。
        # apply_edinet_db_fallback は欠損しているカテゴリだけ呼ぶので、
        # 揃っている項目のために無料枠を使うことはない。
        if not (result.get('major_shareholders_jp') and result.get('company_officers')):
            from edinet_db_client import apply_edinet_db_fallback
            if apply_edinet_db_fallback(
                    symbol, result, categories={'major_shareholders', 'directors'}):
                sources.append('EDINET DB')

        # EDINET DB でも取れなかったら、どけた分を戻す。
        # 使えないデータでも「役員が誰か」は分かるので、消すよりはよい。
        if weak_officers and not result.get('company_officers'):
            result['company_officers'] = weak_officers

        return True   # 最後まで走ったことの印（時間切れなら None が返る）

    # 画面のリクエストの中で外部（Yahoo・EDINET DB）を待つので、上限を付ける。
    #
    # 本番は gunicorn worker 1本（app.py の APScheduler がプロセス内で動くため
    # 増やせない）。1本のリクエストが詰まると裏で他の画面も待たされ、
    # 詰まり続ければアプリごと 503 になる。2026-08-14 にチャートで実際に
    # 起きたのと同じ形。
    #
    # 株主・役員は無くてもページは成立し、次に開いたときに取り直される。
    # **待たせるくらいなら空で返す。**
    timed_out = False
    try:
        from price_history import call_with_deadline
        if call_with_deadline(_fetch, HOLDERS_FETCH_TIMEOUT_SECONDS) is None:
            timed_out = True
            print(f'株主・役員の取得が{HOLDERS_FETCH_TIMEOUT_SECONDS}秒を超えたため'
                  f'打ち切りました {code}（取れた分だけ保存します）')
    except Exception as e:
        print(f'株主・役員のオンデマンド取得エラー {code}: {e}')

    shareholders = result.get('major_shareholders_jp')
    officers = result.get('company_officers')
    got_any = bool(shareholders or officers)

    # 「両方取れた」「片方だけ」「どちらも未収録」を区別して残す。
    # 片方だけの銘柄を success にすると、欠けている側が再取得されなくなる。
    filled = ([k for k, v in (('major_shareholders_jp', shareholders),
                              ('company_officers', officers)) if v])
    # EDINET DB が予算切れ・レート制限で答えられなかったのか、
    # 本当に未収録なのかを見分ける。**ここを一緒くたにすると事故になる。**
    # 2026-08-15 の初回バックフィルで、予算を使い切ったあとの979銘柄を
    # no_data として記録し、30日のクールダウンに入れてしまった。
    edinet_status = ((result.get('source_status') or {}).get('edinet_db') or {}).get('status')
    budget_hit = edinet_status in ('budget_reserved', 'rate_limited')

    if len(filled) == 2:
        status = 'success'
    elif filled:
        status = 'partial'
    elif budget_hit:
        # 取りに行けなかっただけ。次の機会にすぐ再試行させる
        status = edinet_status
    elif timed_out:
        # 時間切れは「どこにも載っていない」とは違う。no_data にすると
        # 長いクールダウンに入り、実際は取れる銘柄が取れないまま固定される。
        status = 'timeout'
    else:
        status = 'no_data'

    # 取得できた項目だけ書く。空で既存の正常値を消さない。
    update = {
        'source_status': merge_source_status(
            existing.get('source_status'),
            {**result.get('source_status', {}),
             'holders_officers': {
                 'status': status,
                 'source': ' + '.join(sources) if sources else '主要株主・役員取得',
                 'filled': filled,
                 'reason': (None if got_any else
                            ('取得に時間がかかったため打ち切りました（次に開いたときに取り直します）'
                             if timed_out else
                             '無料ソース・公式キャッシュ・EDINET DBのいずれにも未収録')),
                 'fetched_at': datetime.now(timezone.utc).isoformat(),
             }}),
    }
    if shareholders:
        update['major_shareholders_jp'] = json.dumps(
            _convert_timestamps(shareholders), ensure_ascii=False)
    if officers:
        update['company_officers'] = json.dumps(
            _convert_timestamps(officers), ensure_ascii=False)

    try:
        update_screened_data(code, update)
    except Exception as e:
        print(f'株主・役員の保存エラー {code}: {e}')
        return {"status": "error", "reason": "保存に失敗しました"}, 500

    return {
        "status": ('fetched' if got_any else status),
        "major_shareholders_jp": shareholders or [],
        "company_officers": _convert_timestamps(officers) if officers else [],
        "source": ' + '.join(sources) if sources else None,
    }, 200


@app.route('/api/stock/holders-officers/<company_code>', methods=['POST'])
def api_fetch_holders_officers(company_code):
    """銘柄ページを開いたときの後追い取得。

    全銘柄バックフィルは skip_extras=True で株主・役員を取らないため、
    EDINET DBのFree枠（100回/日）を閲覧された銘柄に優先して使う設計。
    画面はキャッシュで先に描画され、この呼び出しは後追いで走る。

    ⚠️ ここは**公開ページが自動で叩く**ので会員限定にできない。
    会員限定にすると /stock/<code> の株主欄が非会員に出なくなり、
    公開ページのSEOごと落ちる。代わりに
    「非会員には保存済みだけ返し、外部へは取りに行かない」にする。
    無料枠（100回/日）を外から使い切られる経路を塞ぐのが目的なので、
    枠を使う側だけを止めれば足りる。
    """
    if not is_member_session():
        code = normalize_code(company_code)
        row = get_screened_data(code) or {}
        return jsonify({
            'company_code': code,
            'major_shareholders_jp': row.get('major_shareholders_jp') or [],
            'major_holders': row.get('major_holders'),
            'institutional_holders': row.get('institutional_holders'),
            'company_officers': row.get('company_officers'),
            'fetched': False,
        }), 200

    payload, status = fetch_and_store_holders_officers(company_code)
    return jsonify(payload), status


@app.route('/api/stock/valuation-history/<company_code>', methods=['GET'])
def api_valuation_history(company_code):
    """PER・PBRの推移を、手元のデータだけで組み立てて返す。

    PER/PBRは現在値の1点しか保存していないため、株価履歴と決算期ごとの
    EPS/BPSを突き合わせて履歴を再現する。外部サイトへは取りに行かない。
    """
    import price_history
    from valuation_history import build_valuation_history, summarize

    code = normalize_code(company_code)
    range_key = request.args.get('range', '1y')

    row = get_screened_data(code) or {}
    financial = row.get('financial_history')
    if isinstance(financial, str):
        try:
            financial = json.loads(financial)
        except (TypeError, ValueError):
            financial = {}
    financial = financial or {}

    try:
        if range_key == '1y':
            points = price_history.get_daily(code)
        else:
            granularity = price_history.granularity_for_range(range_key)
            points = price_history.get_long_term(code, granularity)
    except Exception as e:
        print(f'株価履歴の取得エラー {code}: {e}')
        points = []

    history = build_valuation_history(
        points, financial.get('eps'), financial.get('bps'))

    # PBRが出せない理由を握り潰さない。bpsは再分析で入る。
    notes = {}
    if not history['has_per']:
        notes['per'] = ('EPSの履歴がありません' if not financial.get('eps')
                        else '対象期間に黒字のEPSがありません')
    if not history['has_pbr']:
        notes['pbr'] = ('BPSの履歴がありません（再分析すると入ります）'
                        if not financial.get('bps') else 'BPSが0以下の期間です')

    return jsonify({
        "company_code": code,
        "range": range_key,
        "disclosure_lag_days": history['disclosure_lag_days'],
        "has_per": history['has_per'],
        "has_pbr": history['has_pbr'],
        "summary": summarize(history),
        "points": history['points'],
        "notes": notes,
    }), 200


@app.route('/api/earnings/month/<int:month>', methods=['GET'])
def api_earnings_by_month(month):
    """指定した決算月の銘柄一覧を返す（ページング付き）。

    全件を一度に返すとSupabaseの1000行上限で黙って切れるため、
    必ずrange指定で取り、総件数はcountで別途返す。
    """
    if month < 1 or month > 12:
        return jsonify({"error": "決算月は1〜12で指定してください"}), 400

    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(10, request.args.get('per_page', 50, type=int)))
    offset = (page - 1) * per_page

    client = get_supabase_client()
    if client is None:
        return jsonify({"error": "データベースに接続できません"}), 503

    try:
        from security_filter import exclude_delisted
        q = (client.table('screened_latest')
             .select('company_code, company_name, sector, industry_jp, '
                     'market_segment, market_cap, per_forward, pbr, roe, '
                     'equity_ratio, match_rate, fiscal_month',
                     count='exact')
             .eq('fiscal_month', month))
        # 上場廃止は決算一覧にも出さない。次の決算が来ることはない
        result = (exclude_delisted(q)
                  .order('company_code')
                  .range(offset, offset + per_page - 1)
                  .execute())
    except Exception as e:
        if 'fiscal_month' in str(e):
            return jsonify({
                "error": "fiscal_month列がまだありません。"
                         "supabase/migration_fiscal_month.sql を適用してください。",
                "migration_required": True,
            }), 503
        raise

    total = result.count or 0
    return jsonify({
        "month": month,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total else 0,
        "stocks": result.data or [],
    }), 200


# =============================================
# 勉強会の資料・動画
#
# 見せる相手は**有料会員（4,980円〜）**。無料会員は見られない。
# 段による出し分けはしない（2026-08-25 五島さん判断）ので、既存の
# member_required_api をそのまま使う。**段の判定を新しく作らない。**
# =============================================

@app.route('/api/study-materials', methods=['GET'])
@member_required_api
def api_list_study_materials():
    """会員向けの一覧。公開しているものだけ。

    ⚠️ ファイルのURLは保存せず、**ここで期限つきURLを都度発行する**。
    保存すると、退会したあとも生きているURLを配ることになる。
    """
    import study_materials as sm
    try:
        items = sm.list_materials(published_only=True)
        for item in items:
            if item.get('kind') == 'file':
                item['download_url'] = sm.signed_url(item.get('file_path'))
            item.pop('file_path', None)   # 内部のパスは画面に出さない
        return jsonify({'materials': items, 'ready': sm.table_ready()}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/study-materials', methods=['GET'])
@admin_required_api
def api_admin_list_study_materials():
    """管理用。下書きも含めて全部返す。"""
    import study_materials as sm
    try:
        return jsonify({'materials': sm.list_materials(published_only=False),
                        'ready': sm.table_ready()}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/study-materials', methods=['POST'])
@admin_required_api
def api_admin_create_study_material():
    import study_materials as sm
    data = request.get_json(silent=True) or {}
    error = _validate_study_material(data)
    if error:
        return jsonify({'error': error}), 400
    try:
        return jsonify({'material': sm.create_material(data)}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/study-materials/<material_id>', methods=['PUT'])
@admin_required_api
def api_admin_update_study_material(material_id):
    import study_materials as sm
    data = request.get_json(silent=True) or {}
    try:
        return jsonify({'material': sm.update_material(material_id, data)}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/study-materials/<material_id>', methods=['DELETE'])
@admin_required_api
def api_admin_delete_study_material(material_id):
    import study_materials as sm
    try:
        if not sm.delete_material(material_id):
            return jsonify({'error': '見つかりませんでした'}), 404
        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/study-materials/upload', methods=['POST'])
@admin_required_api
def api_admin_upload_study_material():
    """スライド・画像・PDFを非公開バケットへ置く。

    ⚠️ 動画はここに置かない。1本1GBを50人が見れば50GBの転送になり、費用が
    読めなくなる。動画は限定公開のURLを貼る（kind='video'）。
    """
    import study_materials as sm
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'ファイルが選ばれていません'}), 400
    try:
        return jsonify(sm.upload(file)), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': 'アップロードできませんでした: %s' % e}), 500


def _validate_study_material(data):
    """種別ごとに、必要なものが埋まっているか。

    ⚠️ ここで弾かないと「動画なのにURLが空」の資料が作れてしまい、
    画面には見出しだけが並ぶ（登録した本人も気づけない）。DB側にも
    同じ CHECK 制約を置いてある。
    """
    if not (data.get('title') or '').strip():
        return 'タイトルを入れてください'
    kind = data.get('kind')
    if kind == 'video':
        if not (data.get('video_url') or '').strip():
            return '動画のURLを入れてください'
    elif kind == 'file':
        if not (data.get('file_path') or '').strip():
            return 'ファイルをアップロードしてください'
    else:
        return '種別が正しくありません'
    return None


@app.route('/api/stock/screened/<company_code>', methods=['GET'])
def api_get_screened_stock(company_code):
    """screened_latestから単一銘柄のキャッシュデータ取得（GC/DC日付付き）。

    非会員には無料フィールドだけを返す。会員限定の数値をブラウザに送らない
    ことが唯一の確実な保護（CSSぼかしはDevToolsで外れる）。

    2026-08-11: 判定を「ログイン済みか」から「有料会員か」に変えた。
    それまでは無料登録すれば会員限定データが全部取れていた。
    """
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

            # スコアと12項目の判定はサーバーで作って渡す。
            # 以前はブラウザ側でも同じ計算をしていて、片方を直すと必ずズレた。
            # 会員限定なので非会員には含めない（閾値と判定の作り方が価値の中心）。
            member = is_member_session()
            if member:
                from supabase_client import score_breakdown
                data['score_breakdown'] = score_breakdown(data)

            # 値が無い項目は「なぜ無いか」を添える。
            # 赤字でPERが存在しないのと、取得に失敗したのは別物なので、
            # 画面で一律に --- とだけ出さない。
            from data_gaps import classify_missing_fields
            omissions = classify_missing_fields(data, OMISSION_FIELDS)

            if not member:
                # 非会員には無料フィールドのみ。会員限定はサーバー側で落とす。
                # 「まだ続きがある」ことは件数と案内で伝え、値は送らない。
                data = {k: v for k, v in data.items() if k in FREE_SCREENED_FIELDS}
                omissions = {k: v for k, v in omissions.items()
                             if k in FREE_SCREENED_FIELDS}
                data['member_only_available'] = True
            data['omissions'] = omissions

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
# 過去シミュレーション
# =============================================

@app.route('/api/simulate', methods=['POST'])
@member_required_api
def api_simulate():
    """「いつ買っていたらいくらになっていたか」を計算する。会員限定。

    外部アクセスはしない。stock_price_history に入っている調整後の株価
    （日足1年・月足10年）だけで計算するので速い。
    """
    import json as _json
    from datetime import date, timedelta

    import simulator

    try:
        data = request.get_json() or {}
        code = normalize_code(data.get('company_code') or '')
        if not code:
            return jsonify({"error": "銘柄コードを指定してください"}), 400

        mode = data.get('mode') or 'lump'
        start = data.get('start')
        end = data.get('end')
        if not start or not end:
            return jsonify({"error": "開始日と終了日を指定してください"}), 400
        if str(start) > str(end):
            return jsonify({"error": "開始日が終了日より後になっています"}), 400

        try:
            amount = int(data.get('amount') or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return jsonify({"error": "金額を入力してください"}), 400
        # 上限を置く。桁を1つ間違えたまま結果を見て誤解するのを防ぐ
        if amount > 1_000_000_000:
            return jsonify({"error": "金額が大きすぎます（10億円まで）"}), 400

        client = get_supabase_client()
        row = (client.table('stock_price_history')
               .select('company_code, daily_1y, monthly_10y')
               .eq('company_code', code).limit(1).execute().data or [None])[0]
        if not row:
            return jsonify({"error": "この銘柄の株価履歴がありません"}), 404

        history = {}
        for key in ('daily_1y', 'monthly_10y'):
            v = row.get(key)
            history[key] = _json.loads(v) if isinstance(v, str) else v

        # 月足は「閲覧されたときに取得してキャッシュする」設計なので、
        # ほとんどの銘柄でまだ空（実測 1,200件中1件しか持っていなかった）。
        # 日足は1年ぶんしか無いため、それより前を指定されたらここで取りに行く。
        # チャートと同じ get_long_term() を使うので、取得したぶんは保存され
        # 次回以降は即返る。全銘柄を先回りで持つとDBが重くなるため増やさない。
        needs_long = str(start) < str(date.today() - timedelta(days=330))
        long_fetch_failed = False
        if needs_long and not history.get('monthly_10y'):
            try:
                import price_history
                history['monthly_10y'] = price_history.get_long_term(code, 'monthly') or []
                long_fetch_failed = not history['monthly_10y']
            except Exception as e:
                print(f'長期株価の取得に失敗 {code}: {e}')
                long_fetch_failed = True

        # 端数の扱い。実際には小数株を買えないので既定は1株単位の繰り越し
        buy_mode = data.get('buy_mode') or 'carry'
        if buy_mode not in simulator.BUY_MODES:
            buy_mode = 'carry'

        if mode == 'monthly':
            result = simulator.simulate_monthly(
                history, start, end, amount,
                interval_months=data.get('interval_months') or 1,
                day_of_month=data.get('day_of_month') or 1,
                buy_mode=buy_mode)
        else:
            result = simulator.simulate_lump(history, start, end, amount,
                                             buy_mode=buy_mode)

        if not result.get('ok'):
            # 長期データを取りに行って失敗した場合は、そう言う。
            # 「その時期の株価がありません」だと、銘柄が上場していなかったのか
            # こちらが取得できなかったのかが読み手に区別できない。
            if long_fetch_failed:
                return jsonify({
                    "error": "この銘柄の長期の株価をまだ取得できていません。"
                             "1年以内の期間なら計算できます。時間をおくと取得できることがあります。",
                }), 200
            return jsonify({"error": result.get('reason', '計算できませんでした'),
                            "available_from": result.get('available_from')}), 200

        name = (get_screened_data(code) or {}).get('company_name')
        result['company_code'] = code
        result['company_name'] = name
        # 明細が長くなりすぎないように上限を置く（画面で全部は読まない）
        if len(result.get('buys') or []) > 200:
            result['buys_truncated'] = len(result['buys'])
            result['buys'] = result['buys'][:200]
        return jsonify(result), 200
    except Exception as e:
        print(f'シミュレーションエラー: {e}')
        return jsonify({"error": "計算できませんでした"}), 500


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
        # タイトルは任意。銘柄があれば画面側が「◯◯のノート」を入れてくる。
        # ここでも保険として補い、両方無いときだけ弾く
        # （一覧で見分けが付かなくなるため）。
        if not data.get('title'):
            company = (data.get('company_name') or '').strip()
            if company:
                data['title'] = f'{company}のノート'
        if not data.get('title'):
            return jsonify({"error": "銘柄かタイトルのどちらかを入れてください"}), 400
        if not data.get('content'):
            return jsonify({"error": "本文は必須です"}), 400

        # ⚠️ 投稿ごとの表示名は保存しない（2026-08-25）。
        # 以前は投稿した時点の名前を1件ずつ焼き付けていたため、表示名を変えると
        # 過去のノートは古い名前のまま残り、**同じ人が何人もいるように見えた**。
        # 会員が5人しかいない場では、これは実態を誤って見せることになる。
        # 名前は読むときにアカウントから引く（_resolve_display_name）。
        data.pop('poster_name', None)

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

@app.route('/api/community/questions/unanswered-count', methods=['GET'])
def api_unanswered_count():
    """未回答の質問数を取得"""
    try:
        client = get_supabase_client()
        result = client.table('community_questions').select('id', count='exact').eq(
            'answer_count', 0
        ).eq('is_resolved', False).execute()
        return jsonify({"count": result.count or 0}), 200
    except Exception as e:
        return jsonify({"count": 0}), 200


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

        # 非会員には先頭 FREE_COMMUNITY_QUESTIONS 件だけ返す。
        # 「まだ続きがある」ことは件数で伝え、中身は送らない。
        # CSSでぼかしてもDevTools・curlで読めるので、隠すならサーバー側で落とす。
        total = len(questions)
        hidden = 0
        if not is_member_session():
            hidden = max(0, total - FREE_COMMUNITY_QUESTIONS)
            questions = questions[:FREE_COMMUNITY_QUESTIONS]
            shown_ids = {q['id'] for q in questions}
            user_likes = {k: v for k, v in user_likes.items() if k in shown_ids}

        return jsonify({
            "questions": questions,
            "user_likes": user_likes,
            # 画面が「他N件は会員限定」を出すための数。中身は含まれない。
            "hidden_count": hidden,
            "is_member": is_member_session(),
        }), 200
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

        # 投稿ごとの表示名は保存しない。理由は api_create_note と同じ。
        # 投稿した時点の名前を焼き付けると、表示名を変えたときに過去の投稿が
        # 古い名前のまま残り、同じ人が何人もいるように見える。
        data.pop('poster_name', None)

        question = create_question(user_id, data)
        return jsonify({"question": question}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/community/questions/<question_id>', methods=['GET'])
def api_get_question_detail(question_id):
    """質問詳細＋回答一覧を取得。

    一覧を3件に絞っても、詳細のURLを直接叩けば全部読めてしまう。
    非会員には、一覧で見せている3件だけ詳細も開けるようにする。
    """
    try:
        question = get_question_by_id(question_id)
        if not question:
            return jsonify({"error": "質問が見つかりません"}), 404

        if not is_member_session():
            free_ids = {
                q['id'] for q in get_public_questions(
                    limit=FREE_COMMUNITY_QUESTIONS, offset=0,
                    filter_resolved='all')
            }
            if question_id not in free_ids:
                return jsonify({
                    "error": "この質問は会員限定です",
                    "upgrade_url": "https://gia2018.com/upgrade",
                }), 403

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
        # 投稿ごとの表示名は保存しない。理由は api_create_note と同じ。
        # 投稿した時点の名前を焼き付けると、表示名を変えたときに過去の投稿が
        # 古い名前のまま残り、同じ人が何人もいるように見える。
        data.pop('poster_name', None)

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

def record_job_run(job_id, ok, detail=''):
    """定期実行が実際に何をしたかを1行だけ残す。

    なぜ要るか:
      鮮度パネルはデータの新しさを見ているが、データは
      **「取れなかった」と「変わらなかった」を区別できない**。
      株価の price_updated_at は株価が変わったときしか動かないので、
      取得が0件で終わった日も「土曜に更新済み」に見えてしまう
      （2026-08-31、丸1日それで気づけなかった）。

    ⚠️ **記録の失敗でジョブ本体を止めないこと。** 見張りが本体を殺すのは
       本末転倒なので、ここは例外を飲む。テーブルが未作成でも同じ。
    """
    try:
        rows = get_supabase_client().table('job_runs').insert({
            'job_id': job_id, 'ok': bool(ok), 'detail': (detail or '')[:500],
        }).execute().data or []
        return rows[0] if rows else None
    except Exception as e:
        print(f'[Scheduler] 実行記録を残せませんでした ({job_id}): {str(e)[:120]}')
        return None


# 二重起動を見張る窓と、順番が確定するまでの待ち時間。
#
# 2026-09-01、15:20の株価バッチで開始の印が**3秒以内に3つ**残った。
# 同じ処理が3本同時に走り、Yahooへ3倍のリクエストを投げていた
# （その日のレート制限の原因として有力）。
#
# gunicorn は --workers の指定が無いと Render の WEB_CONCURRENCY を見るため、
# worker が増えるとプロセスごとにスケジューラが立つ。起動コマンドは直したが、
# **設定は人が変えられるので、DB側にも門を置く**。
JOB_CLAIM_WAIT_SECONDS = 5
JOB_CLAIM_WINDOW_MINUTES = 45


def claim_job(job_id):
    """このプロセスがそのジョブを実行してよいかを決める。

    仕組み:
      1. 開始の印を残す
      2. 数秒待つ（同時に立ち上がった他の印が出そろうのを待つ）
      3. 窓の中でいちばん古い印が自分でなければ、**自分の印を消して降りる**

    ⚠️ 降りるとき自分の印を消すのは、残すと「始まったのに終わらない」に
       見えて hung と誤検知されるため。実際には走っていないので印も残さない。

    ⚠️ 完全な排他ではない（DBの一意制約ではなく時刻の比較）。狙いは
       「3本同時」を1本に減らすことで、理論上の同着を無くすことではない。
       取りこぼしても次の定期実行で拾える種類の処理にだけ使う。
    """
    import time as _time
    from datetime import datetime, timedelta, timezone

    mine = record_job_run(job_id + ':start', ok=True, detail='開始')
    if not mine:
        return True          # 記録できないなら見張りは諦め、本体は動かす

    _time.sleep(JOB_CLAIM_WAIT_SECONDS)
    try:
        since = (datetime.now(timezone.utc)
                 - timedelta(minutes=JOB_CLAIM_WINDOW_MINUTES)).isoformat()
        rows = (get_supabase_client().table('job_runs')
                .select('id, ran_at')
                .eq('job_id', job_id + ':start')
                .gte('ran_at', since)
                .order('ran_at').order('id').limit(20).execute().data or [])
    except Exception as e:
        print(f'[Scheduler] 二重起動の確認に失敗 ({job_id}): {str(e)[:120]}')
        return True

    # 直近の完了より後の印だけを見る（前回の実行の印を数えない）
    finished = last_job_finish(job_id)
    if finished:
        rows = [r for r in rows if r['ran_at'] > finished]

    if rows and rows[0]['id'] != mine.get('id'):
        print(f'[Scheduler] {job_id}: 他で実行中のため降ります'
              f'（同時に{len(rows)}本立ち上がりました）')
        try:
            (get_supabase_client().table('job_runs').delete()
             .eq('id', mine['id']).execute())
        except Exception as e:
            print(f'[Scheduler] 自分の印を消せませんでした: {str(e)[:120]}')
        return False
    return True


def last_job_finish(job_id):
    """そのジョブが最後に終わった時刻（ISO文字列）。無ければ None。"""
    try:
        rows = (get_supabase_client().table('job_runs').select('ran_at')
                .eq('job_id', job_id)
                .order('ran_at', desc=True).limit(1).execute().data or [])
    except Exception:
        return None
    return rows[0]['ran_at'] if rows else None


def fetch_prices_batch(codes, chunk_size=200, stats=None):
    """複数銘柄の最新終値をまとめて取得する。{code: price} を返す。

    1銘柄ずつ叩くと3,875件で約23分かかるうえ、リクエスト数もそのまま
    銘柄数になりレート制限に当たりやすい。yfinanceのバッチ取得なら
    実測で1銘柄あたり0.065秒（従来比 約1/11）で済む。

    ⚠️ **取れなかったことを黙って捨てないこと。** 以前はチャンク単位の例外を
       `continue` で握りつぶしていたため、1件も取れなくても呼び出し側は
       ただの空 dict を受け取り、例外も立たなかった。
       2026-08-31、3回の定期実行がすべて0件で終わり、スクリーナーが
       金曜の終値を丸1日出し続けていたのに、画面にもログにも何も出なかった。

    Args:
        stats: 渡すと取得の内訳（要求件数・取得件数・失敗チャンク・エラー例）を
               書き込む。呼び出し側が「空振り」を判定するために使う。
    """
    import yfinance as yf
    import warnings
    import time as _time
    warnings.filterwarnings('ignore')

    prices = {}
    chunks = failed_chunks = 0
    errors = []
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i:i + chunk_size]
        symbols = [c if c.endswith('.T') else f'{c}.T' for c in chunk]
        chunks += 1
        df = None
        # 1回だけ取り直す。まるごと落ちたチャンクは200銘柄が消えるので、
        # 一過性の失敗をそのまま捨てるのは高くつく。
        for attempt in (1, 2):
            try:
                df = yf.download(' '.join(symbols), period='2d', progress=False,
                                 threads=True, auto_adjust=False)
                break
            except Exception as e:
                if attempt == 1:
                    _time.sleep(3)
                    continue
                failed_chunks += 1
                if len(errors) < 3:
                    errors.append(str(e)[:120])
                print(f'[Scheduler] バッチ取得エラー ({i}-{i+len(chunk)}): {e}')
        if df is None:
            continue

        for code, sym in zip(chunk, symbols):
            try:
                series = df['Close'][sym].dropna() if len(symbols) > 1 else df['Close'].dropna()
                if len(series):
                    prices[code] = float(series.iloc[-1])
            except Exception:
                continue

    if stats is not None:
        stats.update({'requested': len(codes), 'fetched': len(prices),
                      'chunks': chunks, 'failed_chunks': failed_chunks,
                      'errors': errors})
    return prices


def scheduled_enqueue_earnings():
    """定期実行: 決算発表のあった銘柄を検知して earnings_queue に積む。

    kabutanの決算ページは「最新営業日の分」しか表示しないため、毎日拾わないと
    その日の発表が翌日には見えなくなり取り逃す。検知だけを自動化し、台帳に
    積んでおく。財務データの再取得（重い・1銘柄10リクエスト）は手動のまま。

    午後と夜の2回走らせる。場中の発表は夕方には一覧から消えることがあり、
    引け後の発表は夜まで出そろわないため、両方を取りこぼさないようにする。
    """
    from datetime import datetime
    print(f"[Scheduler] 決算検知開始: {datetime.now()}")
    try:
        pending, by_source = _enqueue_announced()
        print(f"[Scheduler] 決算検知: 内訳{by_source} / 未処理キュー{len(pending)}件")
    except Exception as e:
        print(f"[Scheduler] 決算検知エラー: {e}")


def scheduled_sync_edinet_codes():
    """定期実行: EDINETの提出者一覧を取り込む（週1回）。

    証券コード→EDINETコードの対応表と、登記上の本店所在地・資本金・法人番号・
    決算日が**1リクエストで全件**取れる。APIキーは要らない。

    週1回で足りる。中身が変わるのは新規上場・商号変更・本店移転のときだけで、
    数日遅れても実害が無い。
    """
    from datetime import datetime
    print(f"[Scheduler] EDINETコード一覧の取り込み開始: {datetime.now()}")
    if not claim_job('edinet_codes'):
        return
    try:
        import edinet_codes
        result = edinet_codes.sync(get_supabase_client())
        print(f"[Scheduler] EDINETコード一覧: 取得{result['fetched']}件 / "
              f"保存{result['saved']}件")
        record_job_run('edinet_codes', ok=bool(result['saved']),
                       detail='取得%d件・保存%d件' % (result['fetched'], result['saved']))
    except Exception as e:
        print(f"[Scheduler] EDINETコード一覧の取り込みエラー: {e}")
        record_job_run('edinet_codes', ok=False, detail=str(e)[:300])


def scheduled_sync_edinet_reports():
    """定期実行: 新しく出た有価証券報告書を取り込む（毎晩）。

    2026-09-01 に一括で入れて役員99.7%まで埋めたが、**有報は年に1回出る**。
    放っておくと来年の有報で古くなるし、新規上場も拾えない。

    ふだんは数社しか出ないので軽い。決算期（6月）だけ数百社ぶん出るが、
    1晩150件・25分の上限で数晩に分けて崩す。
    """
    from datetime import datetime
    print(f"[Scheduler] 有報の取り込み開始: {datetime.now()}")
    if not claim_job('edinet_reports'):
        return
    try:
        import edinet_sync
        r = edinet_sync.run(get_supabase_client())
        print("[Scheduler] 有報の取り込み: 新規%d社 / 更新%d社 / 失敗%d社 / 積み残し%d社"
              % (r['new_reports'], r['updated'], r['failed'], r['backlog']))
        # ⚠️ 新しい有報が無い日は「更新0社」が正常。失敗があるときだけ落とす。
        record_job_run('edinet_reports', ok=(r['failed'] == 0),
                       detail='新規%d社・更新%d社・失敗%d社・積み残し%d社'
                              % (r['new_reports'], r['updated'], r['failed'], r['backlog']))
    except Exception as e:
        print(f"[Scheduler] 有報の取り込みエラー: {e}")
        record_job_run('edinet_reports', ok=False, detail=str(e)[:300])


def scheduled_fetch_tdnet_forecasts():
    """定期実行: TDnetの決算短信から通期予想を取り込む（毎日）。

    業績予想の取得元が Yahoo!JP のHTMLだけだった（充足率83.6%）。短信は
    会社が自分で出した一次情報で、TDnetがログイン不要・無料で配っている。

    ⚠️ **TDnetは直近31日ぶんしか公開されていない。** 取りこぼした日は
       取り返せないので、毎日走らせる。過去に遡って一気に埋める道は無い
       （そこは有料サービスの領域）。落ちた日があれば
       `backfill_tdnet_forecasts.py --days N` で31日以内なら拾い直せる。

    ⚠️ 決算期の山（5月中旬・8月上旬）は1日1,000本を超える。上限を超えたぶんは
       翌日に回らない（その日の分しか見ない）ので、山の時期は
       バックフィルで補うこと。件数は job_runs に残る。
    """
    from datetime import datetime

    print(f"[Scheduler] TDnetの業績予想の取り込み開始: {datetime.now()}")
    if not claim_job('tdnet_forecast'):
        return
    try:
        import tdnet_forecast
        stats = tdnet_forecast.run(get_supabase_client())
        detail = ('短信%d本・予想あり%d本・更新%d件・据え置き%d件・'
                  '未登録%d件・失敗%d件'
                  % (stats['reports'], stats['with_forecast'], stats['updated'],
                     stats['skipped'], stats['unknown'], stats['failed']))
        print(f"[Scheduler] TDnetの業績予想: {detail}")
        # ⚠️ 短信が1本も無い日は正常（休日明けや閑散期）。失敗だけを落とす。
        record_job_run('tdnet_forecast', ok=(stats['failed'] == 0), detail=detail)
    except Exception as e:
        print(f"[Scheduler] TDnetの業績予想の取り込みエラー: {e}")
        record_job_run('tdnet_forecast', ok=False, detail=str(e)[:300])


def scheduled_detect_delisted():
    """定期実行: 上場廃止になった銘柄に印を付ける（週1回）。

    2026年のTOB・MBOの波で、5〜7月だけで22社が上場廃止になっていた。
    印が無いと、株価が最終売買日で凍結されたままスクリーナーにも検索にも出続け、
    上場廃止だとどこにも書かれない。

    判定は `detect_delisted.plan_changes` に一本化してある。
    ⚠️ **ここに独自の条件を書かないこと。** 以前はこちらだけ JPX の一覧を
       見ておらず、手動スクリプトと違う判定をしていた。1年ぶんの足で生死を
       見ていたため、廃止直後の8社に永遠に印が付かなかった（2026-09-03に判明）。

    印を外す側もここでやる。誤って付いた印は、外す仕組みが無いと残り続ける
    （実測で PRO Market の40社が「2026-07-17 上場廃止」のまま7週間残っていた）。

    毎日ではなく週1回。上場廃止は事前に開示されるうえ、数日遅れて印が付いても
    実害は小さい。
    """
    from datetime import datetime

    print(f"[Scheduler] 上場廃止の検出開始: {datetime.now()}")
    try:
        import detect_delisted
        import supabase_client as sc
        if not sc.has_column('screened_latest', 'delisted_at'):
            print("[Scheduler] 上場廃止の検出: delisted_at 列が未適用のためスキップ")
            record_job_run('detect_delisted', ok=False, detail='delisted_at 列が未適用')
            return

        client = sc.get_supabase_client()
        try:
            to_mark, to_clear, held = detect_delisted.plan_changes(
                client, verbose=False)
        except detect_delisted.ListingUnavailable as e:
            # ⚠️ ここを「条件を使わずに続行」にしてはいけない。JPXの一覧が
            #    無いまま判定すると、正しく付いた印を全部外す。
            print(f"[Scheduler] 上場廃止の検出: 中止（{e}）")
            record_job_run('detect_delisted', ok=False,
                           detail='JPXの上場銘柄一覧を取得できず中止')
            return

        marked_now = cleared = failed = 0
        for code, _name, stamp in to_mark:
            try:
                (client.table('screened_latest').update({'delisted_at': stamp})
                 .eq('company_code', code).execute())
                marked_now += 1
            except Exception as e:
                failed += 1
                print(f"[Scheduler] 上場廃止の印付け {code} 失敗: {e}")
        for code in to_clear:
            try:
                (client.table('screened_latest').update({'delisted_at': None})
                 .eq('company_code', code).execute())
                cleared += 1
            except Exception as e:
                failed += 1
                print(f"[Scheduler] 上場廃止の印外し {code} 失敗: {e}")

        detail = (f'印を付けた{marked_now}件・外した{cleared}件・'
                  f'保留{len(held)}件・失敗{failed}件')
        if held:
            # JPXに無いのに値が付く。コード変更や廃止直前の可能性があるので、
            # 自動では触らず、誰が見ても分かるように銘柄コードを残す。
            detail += '（保留: ' + ','.join(c for c, _ in held[:10]) + '）'
        print(f"[Scheduler] 上場廃止の検出終了: {detail}")
        record_job_run('detect_delisted', ok=(failed == 0), detail=detail)
    except Exception as e:
        print(f"[Scheduler] 上場廃止の検出エラー: {e}")
        record_job_run('detect_delisted', ok=False, detail=str(e)[:300])


# 1晩に再分析する決算銘柄の上限と、かける時間の上限。
#
# 決算期（2月・5月・8月・11月の上旬）は1日1,000件を超える。全部やろうとすると
# 朝まで走り、深夜2:00のYahoo項目バックフィルや3:30の日足更新とかち合う。
# 1銘柄あたり実測で約4秒なので、400件で約28分。決算期の山は数晩かけて崩す。
EARNINGS_NIGHTLY_LIMIT = 400
EARNINGS_NIGHTLY_MINUTES = 120


def load_unprocessed_earnings(client, limit):
    """未処理の決算銘柄を古い順に返す。発表日の古いものから片づける。"""
    rows = (client.table('earnings_queue')
            .select('company_code, announced_date')
            .eq('processed', False)
            .order('announced_date')
            .limit(limit)
            .execute().data or [])
    return [r['company_code'] for r in rows]


# 見つけた取りこぼしを一晩に積む上限。全部いっぺんに積むと、翌日の再分析が
# 何百件も走って外部を叩きすぎる。少しずつ拾えば数日で追いつく。
STALE_ENQUEUE_LIMIT = 20


def scheduled_check_earnings_freshness():
    """決算の取りこぼしを見つけて、同じキューに積み直す。

    決算の検知は kabutan のスクレイピング頼み。サイトの構造が変わる・遮断される・
    その銘柄が載らない、のどれかが起きると**その銘柄は決算が出ても古い財務のまま
    残る**。しかもエラーは出ない（「検知しなかった」だけで処理は正常に終わる）。

    決算月は98%の銘柄で分かっているので、「期末から猶予を過ぎたのに最終分析日が
    その期末より前」の銘柄を数えれば漏れが見える。見えたら拾って積み直す。

    ⚠️ 鮮度は analyzed_at で見る。updated_at は一部の保存経路でしか書かれて
       おらず、中身が新しくても2月のまま止まっている行がある。
    """
    from datetime import datetime, timezone
    import earnings_freshness as ef

    try:
        client = get_supabase_client()
        rows, offset = [], 0
        while True:
            page = (client.table('screened_latest')
                    .select('company_code, company_name, fiscal_month, '
                            'analyzed_at, delisted_at')
                    .order('company_code')
                    .range(offset, offset + 499).execute().data)
            if not page:
                break
            rows.extend(page)
            if len(page) < 500:
                break
            offset += 500

        stale = ef.find_stale(rows)
        if not stale:
            print('[Scheduler] 決算の取りこぼし: なし')
            return

        print(f'[Scheduler] ⚠️ 決算の取りこぼし {len(stale)}件 '
              f'（例: {", ".join(s["company_code"] for s in stale[:5])}）')

        # 検知そのものが壊れている可能性があるので、見つけたぶんは
        # 同じキューに積んで翌日の再分析に拾わせる
        now = datetime.now(timezone.utc).isoformat()
        payload = [{
            'company_code': s['company_code'],
            'company_name': s['company_name'],
            'announced_date': s['fiscal_end'],
            'source': '決算の取りこぼし検知',
            'processed': False,
            'updated_at': now,
        } for s in stale[:STALE_ENQUEUE_LIMIT]]
        try:
            client.table('earnings_queue').upsert(payload).execute()
            print(f'[Scheduler] 取りこぼし {len(payload)}件をキューに積んだ')
        except Exception as e:
            print(f'[Scheduler] 取りこぼしのキュー登録に失敗: {e}')
    except Exception as e:
        # ここで落ちても決算の再分析そのものは済んでいる
        print(f'[Scheduler] 決算の取りこぼし検知でエラー: {e}')


def scheduled_process_earnings_queue():
    """定期実行: 決算発表のあった銘柄の財務データを取り直す。

    2026-08-24 まで、検知（15:30・21:00）は自動なのに**再分析は /earnings の
    ボタンからしか動かなかった**。押し忘れると決算をまたいでも古い財務データが
    残り続ける。決算をまたいだ数字を出しているのに気づけないので自動化する。

    21:00の検知の後に走らせる。引け後の発表が出そろうのを待つため。
    """
    global earnings_status
    from datetime import datetime
    import time as _time

    if earnings_status["running"] or daily_update_status["running"]:
        print("[Scheduler] 決算再分析: すでに実行中のためスキップ")
        return

    print(f"[Scheduler] 決算再分析開始: {datetime.now()}")
    try:
        codes = load_unprocessed_earnings(get_supabase_client(),
                                          EARNINGS_NIGHTLY_LIMIT)
    except Exception as e:
        print(f"[Scheduler] 決算再分析: キューの読み取りに失敗 {e}")
        return

    if not codes:
        print("[Scheduler] 決算再分析: 未処理なし")
        return

    earnings_status = {"running": True, "done": 0, "total": len(codes),
                       "errors": 0, "codes": codes, "stop_requested": False,
                       "finished_at": None, "error": None}
    deadline = _time.monotonic() + EARNINGS_NIGHTLY_MINUTES * 60
    try:
        _update_earnings_background(codes, deadline_at=deadline)
    except Exception as e:
        earnings_status["running"] = False
        earnings_status["error"] = str(e)
        print(f"[Scheduler] 決算再分析エラー: {e}")
        return
    print(f"[Scheduler] 決算再分析終了: {earnings_status['done']}/{len(codes)}件 "
          f"/ 失敗{earnings_status['errors']}件")


# 株主・役員のバックフィルで狙う銘柄の条件。
#
# 「経営陣が株主か」を見たいのは**オーナー系の中小型株**。プライムの
# 大型株は大株主が信託銀行ばかりで、この指標に意味が無い
# （6632 JVCケンウッド: 上位5社すべて信託・カストディ銀行）。
# 全3,879銘柄を追うと無料枠では130日かかるが、ここに絞れば1,872銘柄。
HOLDERS_BACKFILL_SEGMENTS = ('スタンダード', 'グロース')
HOLDERS_BACKFILL_MAX_MARKET_CAP = 300     # 億円

# 1回の実行に許す最大秒数。定期実行はリクエストのスレッドを掴まないが、
# 際限なく走らせると翌朝の株価更新と重なる。
HOLDERS_BACKFILL_TIME_BUDGET = 20 * 60

# EDINET DB の残り予算がこれ以下になったら止める。
# **翌日の閲覧のために残す**のではなく、当日のうちに誰かが銘柄ページを
# 開いたときのため（予算は日付で切り替わる）。
HOLDERS_BACKFILL_STOP_AT_REMAINING = 6


def scheduled_backfill_holders_officers():
    """定期実行: 株主・役員が空の銘柄を、その日の**残り予算**で埋める。

    枠を固定で分け合わない理由:
      EDINET DB の無料枠は100回/日。バックフィルに固定枠を与えると
      「誰も見ていないのに枠が余る」日と「見たいのに枠が無い」日が
      両方起きる。**日中は閲覧に使い切ってよく、夜に残りをまとめて使う**
      形にすれば、閲覧が枯渇することが構造的に起きない。

    1銘柄あたり3リクエスト（検索・役員・大株主）なので、丸ごと余った日で
    30銘柄前後。対象1,872銘柄なら2〜3か月で埋まる。無料枠の制約であり、
    どう組んでも短縮できない。
    """
    from datetime import datetime
    import time as _time

    started = _time.time()
    print(f"[Scheduler] 株主・役員バックフィル開始: {datetime.now()}")

    try:
        from edinet_db_client import EdinetDbClient
        budget = EdinetDbClient().budget_snapshot()
        print(f"[Scheduler] EDINET予算: {budget}")
    except Exception as e:
        print(f"[Scheduler] EDINET予算の確認に失敗: {e}")
        return

    try:
        client = get_supabase_client()
        # 役員・大株主のどちらかが空の銘柄を、対象の市場・規模だけ拾う
        rows = []
        for segment in HOLDERS_BACKFILL_SEGMENTS:
            res = (client.table('screened_latest')
                   .select('company_code, company_name, market_cap')
                   .eq('market_segment', segment)
                   .is_('company_officers', 'null')
                   .lt('market_cap', HOLDERS_BACKFILL_MAX_MARKET_CAP)
                   .order('market_cap', desc=True)
                   .limit(500)
                   .execute())
            rows.extend(res.data or [])
    except Exception as e:
        print(f"[Scheduler] 対象銘柄の取得に失敗: {e}")
        return

    print(f"[Scheduler] 未取得の対象: {len(rows)}銘柄")

    done, skipped, stopped = 0, 0, None
    for row in rows:
        if _time.time() - started > HOLDERS_BACKFILL_TIME_BUDGET:
            stopped = '時間切れ'
            break
        try:
            # **必ず共有クライアントを見る。** EdinetDbClient() を新しく作ると
            # 使用数も残数も初期値に戻り、予算切れの判定が一度も働かない
            # （初回に1000銘柄を走り切って979件を no_data にした原因）。
            from edinet_db_client import get_edinet_db_client
            snap = get_edinet_db_client().budget_snapshot()
            remaining = snap.get('remaining_remote')
            if remaining is not None and remaining <= HOLDERS_BACKFILL_STOP_AT_REMAINING:
                stopped = f'残り予算{remaining}'
                break

            payload, _ = fetch_and_store_holders_officers(row['company_code'])
            st = payload.get('status')
            if st == 'fetched':
                done += 1
            elif st in ('budget_reserved', 'rate_limited'):
                # 残数が返らない提供元でも、ここで確実に止まる
                stopped = f'予算切れ（{st}）'
                break
            else:
                skipped += 1
        except Exception as e:
            skipped += 1
            print(f"[Scheduler] {row['company_code']} で失敗: {e}")

    elapsed = int(_time.time() - started)
    print(f"[Scheduler] 株主・役員バックフィル終了: 取得{done}件 / 取れず{skipped}件"
          f" / {elapsed}秒" + (f" / 中断={stopped}" if stopped else ""))


# 株価と一緒に伸縮させる列。ここだけ読めば足りるので financial_history は取らない
# （全銘柄ぶんのJSONを1日3回読むと重い）。スコアの計算し直しは深夜の
# scheduled_update_daily_and_crosses でまとめて行う。
PRICE_SYNC_COLUMNS = ('company_code, stock_price, per_forward, pbr, '
                      'market_cap, dividend_yield, dividend_yield_forward')


# 空振りした回を何分後に、何回まで試し直すか。
#
# 定期実行は 9:25 / 11:45 / 15:20 の3回しかないので、1回落ちると次まで2時間以上
# 開く。レート制限のような一過性の失敗なら、30分後にもう一度で戻ることが多い。
# 3回で打ち切るのは、恒常的に弾かれているときに叩き続けても状況を悪くするだけだから。
PRICE_RETRY_MINUTES = 30
PRICE_RETRY_MAX = 3


def _schedule_price_retry(attempt):
    """空振りした株価バッチを、少し待ってから試し直す。

    ⚠️ 再試行の登録に失敗しても本体を止めないこと。ここで例外を上げると、
       せっかく残した実行記録より後ろで落ちて話が分かりにくくなる。
    """
    if attempt >= PRICE_RETRY_MAX:
        print(f"[Scheduler] 株価バッチ: {attempt}回試して取れないため打ち切ります"
              f"（次の定期実行を待ちます）")
        return
    from datetime import datetime, timedelta
    run_at = (datetime.now(pytz.timezone('Asia/Tokyo'))
              + timedelta(minutes=PRICE_RETRY_MINUTES))
    try:
        scheduler.add_job(scheduled_update_stock_prices, 'date', run_date=run_at,
                          args=[attempt + 1], id='price_update_retry',
                          replace_existing=True, misfire_grace_time=600)
        print(f"[Scheduler] 株価バッチ: {PRICE_RETRY_MINUTES}分後に試し直します"
              f"（{attempt + 1}回目）")
    except Exception as e:
        print(f"[Scheduler] 株価バッチ: 再試行を登録できませんでした: {e}")


def scheduled_update_stock_prices(attempt=0):
    """定期実行: screened_latest全銘柄の株価を更新する。

    ⚠️ 株価だけを書き換えないこと。PER・PBR・時価総額・配当利回りは
    分析した日の株価で計算されており、株価だけ毎日動かすと画面上で
    「今日の株価」と「1か月前のPER」が並ぶ。2026-08-24 に実測したところ
    PERが5%以上ずれている銘柄が64%、20%以上が31%あった。
    multiples.rescale() で5つを一緒に更新する。
    """
    from datetime import datetime
    import multiples
    print(f"[Scheduler] 株価バッチ更新開始: {datetime.now()}")
    # ⚠️ **開始の印を先に残すこと。** 終わりの印だけだと、途中で死んだ回が
    #    「まだ何もしていない」と区別できない。2026-09-01、9:25 と 11:45 の
    #    2回とも記録が1行も残らず、成功も失敗も分からなかった。
    #    始まりが残っていれば、終わりが来ないこと自体が証拠になる。
    #
    # ⚠️ ここで同時に立ち上がった他のプロセスに道を譲る。同じ日の15:20に
    #    3本同時に走り、Yahooへ3倍のリクエストを投げていた。
    if not claim_job('price_update'):
        return
    try:
        client = get_supabase_client()

        # 1000行ずつページングして全件取得する。
        # Supabaseは1回のselectで既定1000行までしか返さないため、
        # ページングしないと先頭1000件しか更新されない。
        rows = []
        page = 0
        while page < 20:
            res = (client.table('screened_latest')
                   .select(PRICE_SYNC_COLUMNS)
                   .range(page * 1000, page * 1000 + 999)
                   .execute())
            batch = res.data or []
            rows.extend(batch)
            if len(batch) < 1000:
                break
            page += 1

        by_code = {r['company_code']: r for r in rows}
        print(f"[Scheduler] 対象銘柄数: {len(by_code)}件")
        stats = {}
        prices = fetch_prices_batch(list(by_code), stats=stats)

        # ⚠️ **取得0件は「変化なし」ではなく失敗。** ここを正常として通すと、
        #    スクリーナーが前営業日の株価を出し続けても誰も気づけない
        #    （2026-08-31、3回の実行がすべて0件で丸1日金曜の値のままだった）。
        #    price_updated_at は株価が変わったときしか動かないので、
        #    鮮度パネルからも「取れなかった」ことは読めない。
        if not prices:
            raise RuntimeError(
                '1件も取得できませんでした（対象%d件 / チャンク%d本中%d本が失敗）: %s'
                % (len(by_code), stats.get('chunks', 0),
                   stats.get('failed_chunks', 0),
                   ' / '.join(stats.get('errors') or ['例外なし'])))

        success_count = 0
        split_count = 0
        for code, price in prices.items():
            row = by_code.get(code) or {}
            try:
                updates = multiples.rescale(row, price)
            except multiples.ImplausibleRatio as e:
                # 株式分割・併合の疑い。分割では株価もEPSも同じ比で動くので
                # PERは変わらない。指標を触らず株価だけ入れるのが正しい。
                split_count += 1
                print(f"[Scheduler] {code} 株価が飛んでいます（分割の疑い）: {e}")
                updates = {'stock_price': price}

            if not updates:
                success_count += 1      # 変化なし＝更新の必要なし
                continue
            try:
                (client.table('screened_latest').update(updates)
                 .eq('company_code', code).execute())
                success_count += 1
            except Exception as e:
                print(f"[Scheduler] {code} 保存エラー: {e}")

        fail_count = len(by_code) - success_count
        print(f"[Scheduler] 株価バッチ更新完了: 成功{success_count}件, "
              f"失敗{fail_count}件, 分割の疑い{split_count}件")
        # 取得はできたが大半が欠けている回も記録に残す。まるごと落ちるより
        # 「半分だけ取れた」のほうが気づきにくい。
        ok = stats.get('fetched', 0) >= len(by_code) * 0.5
        record_job_run('price_update', ok=ok,
                       detail='取得%d/%d件・保存%d件（チャンク%d本中%d本が失敗）'
                              % (stats.get('fetched', 0), len(by_code),
                                 success_count, stats.get('chunks', 0),
                                 stats.get('failed_chunks', 0)))
        if not ok:
            _schedule_price_retry(attempt)
    except Exception as e:
        print(f"[Scheduler] 株価バッチ更新エラー: {e}")
        record_job_run('price_update', ok=False, detail=str(e)[:300])
        _schedule_price_retry(attempt)


def scheduled_update_daily_and_crosses():
    """定期実行: 全銘柄の日足を更新し、続けてGC/DCを再計算する。

    これが無いと、日足だけが銘柄ページを開いたときに更新され（price_history側の
    自動取得）、GC/DCは手で再計算するまで古いまま残る。結果、チャートには出ている
    クロスがトレンド表示に出ない、という食い違いが起きる。

    引け後の値が確定してから走らせたいので深夜に回す。
    """
    global daily_update_status
    from datetime import datetime

    # ⚠️ **どの経路を通っても記録を残すこと。**
    #    最初これを「最後まで通ったとき」だけ書いていたため、
    #    2026-09-01 の3:30は発火したのに記録が1行も残らなかった。
    #    記録が無いことは「異常なし」と区別がつかないので、
    #    黙って何もしない回がいちばん見えなくなる。
    if daily_update_status["running"] or ma_cross_status["running"]:
        print("[Scheduler] 日足更新: すでに実行中のためスキップ")
        record_job_run('daily_and_crosses', ok=False,
                       detail='前回が実行中のままでスキップした')
        return

    print(f"[Scheduler] 日足更新＋GC/DC再計算 開始: {datetime.now()}")
    if not claim_job('daily_and_crosses'):
        return
    daily_update_status = {"running": True, "phase": "準備中", "done": 0, "total": 0,
                           "saved": 0, "stop_requested": False, "finished_at": None, "error": None}
    try:
        _update_daily_and_recalc_background()
        print(f"[Scheduler] 日足更新＋GC/DC再計算 終了: "
              f"{daily_update_status.get('phase')} "
              f"/ 保存{daily_update_status.get('saved')}件")
    finally:
        # 保存0件は「変化なし」ではなく取得の失敗。日足はどの営業日でも1本
        # 増えるので、0件で終わるのは yfinance が返していないとき。
        saved = daily_update_status.get('saved') or 0
        record_job_run('daily_and_crosses',
                       ok=bool(saved) and not daily_update_status.get('error'),
                       detail='保存%d件 / %s' % (
                           saved, daily_update_status.get('error')
                           or daily_update_status.get('phase') or ''))
    _recalculate_scores_after_price_move()
    _recalculate_growth_columns()


def scheduled_update_margin_balances():
    """JPXの週次信用残高を全銘柄に流し込む。

    JPXは毎週、前週末の残高をPDFで出す（公開は火曜〜水曜あたり）。
    以前は銘柄ページを開いたときの後追い取得しか経路が無く、開かれた銘柄
    だけが埋まる形だったので、3,859銘柄のうち22件（0.6%）しか無かった。

    ⚠️ **外部へのリクエストは1回だけ。** PDFに全銘柄が載っており、
    jpx_margin がキャッシュする。銘柄ごとに叩く必要はない。
    """
    from datetime import datetime
    try:
        import backfill_margin as bm
        print(f'[Scheduler] 信用残高の更新 開始: {datetime.now()}')
        updated = bm.run(get_supabase_client())
        print(f'[Scheduler] 信用残高の更新 終了: 更新{updated}件')
    except Exception as e:
        print(f'[Scheduler] 信用残高の更新エラー: {e}')


def _recalculate_growth_columns():
    """増減率・流動比率など、絞り込みに使う派生列を作り直す。

    スコアの判定は financial_history から都度計算しているので列が要らないが、
    スクリーナーはDB側で絞るため列が要る。**派生値なので元の値と一緒に動かす。**
    決算で財務履歴が入れ替わったのに増減率が古いままだと、画面には正しい
    売上高と古い増減率が並ぶ（片方が正しいので壊れて見えない）。

    外部へは一切アクセスしない。DBの中だけで完結する。
    """
    from datetime import datetime
    try:
        import backfill_growth_columns as bgc
        print(f'[Scheduler] 増減率の再計算 開始: {datetime.now()}')
        updated = bgc.run(get_supabase_client())
        print(f'[Scheduler] 増減率の再計算 終了: 更新{updated}件')
    except Exception as e:
        # ここで落ちても日足とスコアの更新は済んでいる。次の晩に持ち越す。
        print(f'[Scheduler] 増減率の再計算エラー: {e}')


def _recalculate_scores_after_price_move():
    """株価が動いたぶんスコアを計算し直す。

    日中の株価cronは PER・PBR を伸縮させるが、match_rate と score_complete までは
    触らない（判定には financial_history が要るので、全銘柄ぶんのJSONを1日3回
    読むことになり重い）。PERが不合格ラインをまたぐと点数が変わるため、
    1日1回ここでまとめて計算し直す。

    外部へは一切アクセスしない。DBの中だけで完結する。
    """
    from datetime import datetime
    try:
        import backfill_score_complete as bsc
        from supabase_client import score_breakdown
        client = get_supabase_client()

        rows = bsc.load_rows(client)
        updates = []
        for row in rows:
            breakdown = score_breakdown(row)
            score = breakdown['score']
            complete = breakdown['status'] == 'complete'
            changed = {}
            if row.get('match_rate') != score:
                changed['match_rate'] = score
            if row.get('score_complete') != complete:
                changed['score_complete'] = complete
            if changed:
                updates.append((row['company_code'], changed))

        written = failed = 0
        for code, changed in updates:
            try:
                (client.table('screened_latest').update(changed)
                 .eq('company_code', code).execute())
                written += 1
            except Exception as e:
                failed += 1
                if failed <= 3:
                    print(f"[Scheduler] スコア再計算 {code} 失敗: {e}")
        print(f"[Scheduler] スコア再計算 {datetime.now()}: "
              f"対象{len(rows)}件 / 更新{written}件 / 失敗{failed}件")
    except Exception as e:
        # ここで落ちても日足とGC/DCは済んでいる。握って翌日に回す
        print(f"[Scheduler] スコア再計算エラー: {e}")


# 1晩に処理する上限。Yahooは実測で約50件連続すると遮断するので、その手前。
NIGHTLY_PROFILE_LIMIT = 60
NIGHTLY_PROFILE_SLEEP = 5.0


def scheduled_backfill_yahoo_profile():
    """定期実行: Yahoo日本版由来の項目（大株主・設立日・業績予想など）を少しずつ埋める。

    手で `backfill_yahoo_fields.py` を回すと400件で約3時間かかる（遮断17回ぶんの
    待ちが効く）。残り3,300件だと8〜9回＝約27時間になり、PCを点けている時間に
    縛られる。**急ぐ理由は無い**（未取得の銘柄はスクリーナーで暫定表示になるだけで、
    間違った数字が出るわけではない）ので、毎晩少しずつ進める。

    ⚠️ ここでは遮断されても**待たない**。web プロセスの中で走るので、
    冷却の10〜60分をスレッドが抱えると他の定期実行とかち合う。
    Yahooは実測で約50件連続すると遮断するため、その手前で自然に止まる。
    残りは翌晩に回せばよい。急ぎたいときは手で回す（そちらは待って続ける）。
    """
    from datetime import datetime
    import time as _time

    print(f"[Scheduler] Yahoo項目バックフィル開始: {datetime.now()}")
    try:
        import yahoo_jp_guard
        from backfill_yahoo_fields import load_targets, fill_one
        from stock_analyzer import StockAnalyzer
    except Exception as e:
        print(f"[Scheduler] Yahoo項目バックフィル: 読み込み失敗 {e}")
        return

    snap = yahoo_jp_guard.status_snapshot()
    if snap.get('tripped') or snap.get('force_disabled'):
        print(f"[Scheduler] Yahoo項目バックフィル: いま遮断中なのでスキップ {snap}")
        return

    try:
        targets = load_targets()[:NIGHTLY_PROFILE_LIMIT]
    except Exception as e:
        print(f"[Scheduler] Yahoo項目バックフィル: 対象の抽出に失敗 {e}")
        return
    if not targets:
        print("[Scheduler] Yahoo項目バックフィル: 対象なし（すべて取得済み）")
        return

    analyzer = StockAnalyzer()
    ok = fail = 0
    touched = []
    for i, code in enumerate(targets):
        if yahoo_jp_guard.status_snapshot().get('tripped'):
            print(f"[Scheduler] Yahoo項目バックフィル: 遮断されたので{i}件で切り上げます")
            break
        try:
            if fill_one(analyzer=analyzer, code=code):
                ok += 1
                touched.append(code)
        except Exception as e:
            fail += 1
            print(f"[Scheduler] {code} バックフィル失敗: {str(e)[:80]}")
        _time.sleep(NIGHTLY_PROFILE_SLEEP)

    print(f"[Scheduler] Yahoo項目バックフィル終了: 保存{ok}件 / 失敗{fail}件 "
          f"/ 残り対象は次回に持ち越し")

    # ⚠️ ここまでの保存は update_screened_data() を通るため、score_complete が
    # 更新されない（あれを書くのは upsert_screened_data_with_match_rate だけ）。
    # 放置すると「予想が埋まったのにスクリーナーは灰色のまま」になり、
    # 実際に毎回そうなって手で流し直していた。さわった銘柄だけ計算し直す。
    if touched:
        try:
            from backfill_score_complete import refresh_score_complete, apply_updates
            from supabase_client import get_supabase_client as _client
            updates, _ = refresh_score_complete(_client(), codes=touched)
            written, failed = apply_updates(_client(), updates)
            print(f"[Scheduler] score_complete 更新: {written}件 / 失敗{failed}件")
        except Exception as e:
            print(f"[Scheduler] score_complete の更新に失敗: {str(e)[:100]}")


scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Tokyo'))
scheduler.add_job(scheduled_fetch_gc_dc, 'cron', hour=9, minute=15, id='gc_dc_morning')
scheduler.add_job(scheduled_fetch_gc_dc, 'cron', hour=17, minute=15, id='gc_dc_evening')
# 株価バッチ更新（9:25 / 11:45 / 15:20 JST）
scheduler.add_job(scheduled_update_stock_prices, 'cron', hour=9, minute=25, id='price_update_morning')
scheduler.add_job(scheduled_update_stock_prices, 'cron', hour=11, minute=45, id='price_update_midday')
scheduler.add_job(scheduled_update_stock_prices, 'cron', hour=15, minute=20, id='price_update_closing')
# 決算検知（15:30 場中の発表 / 21:00 引け後の発表）。検知のみ、更新は手動
scheduler.add_job(scheduled_enqueue_earnings, 'cron', hour=15, minute=30, id='earnings_detect_afternoon')
scheduler.add_job(scheduled_enqueue_earnings, 'cron', hour=21, minute=0, id='earnings_detect_evening')

# 検知した銘柄の再分析。21:00の検知が終わってから動かす
scheduler.add_job(scheduled_process_earnings_queue, 'cron', hour=22, minute=0,
                  id='earnings_process_queue')

# TDnetの決算短信から業績予想を取り込む。短信は15:00〜20:00に出るので、
# 出そろってから。⚠️ **直近31日しか公開されないので毎日走らせること。**
scheduler.add_job(scheduled_fetch_tdnet_forecasts, 'cron', hour=20, minute=0,
                  id='tdnet_forecast')
# 上場廃止の検出。全銘柄の日足を読むので週1回、他が動いていない時間に
scheduler.add_job(scheduled_detect_delisted, 'cron', day_of_week='sun',
                  hour=4, minute=30, id='detect_delisted')
# EDINETの提出者一覧。週1回で足りる（変わるのは新規上場・商号変更・本店移転のみ）。
# 外部への負荷は1リクエストだけなので、他と重ならない時間に軽く置く。
scheduler.add_job(scheduled_sync_edinet_codes, 'cron', day_of_week='sun',
                  hour=5, minute=0, id='edinet_codes')
# 新しく出た有報の取り込み。ふだんは数社なので毎晩でも軽い。
# 決算期だけ数百社ぶん出るので、1晩150件・25分の上限で数晩に分けて崩す。
scheduler.add_job(scheduled_sync_edinet_reports, 'cron', hour=5, minute=40,
                  id='edinet_reports')
# 日足の全銘柄更新＋GC/DC再計算（3:30 JST）。引け後の値が確定してから走らせる
scheduler.add_job(scheduled_update_daily_and_crosses, 'cron', hour=3, minute=30, id='daily_and_crosses')
# JPXは前週末の残高を火曜〜水曜に出す。木曜の朝に取れば確実に最新が載っている。
scheduler.add_job(scheduled_update_margin_balances, 'cron', day_of_week='thu',
                  hour=4, minute=10, id='margin_weekly')
# 決算の再分析（22:00）が終わったころに、拾えていない銘柄が無いか数える。
# 見つかったぶんは翌日のキューに積むので、次の晩に取り直される。
scheduler.add_job(scheduled_check_earnings_freshness, 'cron', hour=23, minute=30,
                  id='earnings_freshness')
# Yahoo項目の穴埋め。他のジョブと重ならない時間に置く（1晩60件・約8分）
scheduler.add_job(scheduled_backfill_yahoo_profile, 'cron', hour=2, minute=0,
                  id='yahoo_profile_backfill')

# 株主・役員のバックフィルは23:00。
# **その日の残り予算を使う**ので、日中の閲覧が終わってから走らせる。
# 朝に回すと、閲覧より先にバックフィルが枠を取ってしまう。
scheduler.add_job(scheduled_backfill_holders_officers, 'cron', hour=23, minute=0,
                  id='holders_backfill')

# スケジューラは1プロセスでのみ起動させる。
# ENABLE_SCHEDULER=false にすると起動しない（将来worker側へcronを分離する際に、
# web側を false にして多重実行を防ぐため）。未設定時は従来通り起動する。
ENABLE_SCHEDULER = os.getenv('ENABLE_SCHEDULER', 'true').lower() not in ('false', '0', 'no')
if ENABLE_SCHEDULER:
    scheduler.start()
    print("[Scheduler] スケジューラ起動（GC/DC取得: 9:15/17:15, 株価更新: 9:25/11:45/15:20, "
          "決算検知: 15:30/21:00, Yahoo項目バックフィル: 2:00, "
          "日足＋GC/DC再計算: 3:30, 株主・役員バックフィル: 23:00 JST）")
    # アプリ終了時にスケジューラも停止
    atexit.register(lambda: scheduler.shutdown(wait=False))
else:
    print("[Scheduler] ENABLE_SCHEDULER=false のためスケジューラは起動しません")


@app.route('/api/admin/data-freshness', methods=['GET'])
@admin_required_api
def api_data_freshness():
    """定期実行が「ちゃんと動いているか」をデータ側から見る。

    ⚠️ /api/scheduler/status とは別物。あちらは**次にいつ動くか**しか出せず、
       ジョブが空振りしていても正常に見える。こちらは
       **最後に実際に値が動いた実績**を返す。
    """
    try:
        import data_freshness
        # スケジューラの次回実行時刻も一緒に渡す。データだけを見ていると、
        # 手でボタンを押した結果で緑になり、ジョブが死んでいるのを隠せてしまう。
        try:
            jobs = [{'id': j.id,
                     'next_run_time': (j.next_run_time.isoformat()
                                       if j.next_run_time else None)}
                    for j in scheduler.get_jobs()]
        except Exception as e:
            print(f'スケジューラの状態を取得できませんでした: {e}')
            jobs = None          # 正常には倒さない（警告として出る）
        return jsonify(data_freshness.summary(jobs=jobs)), 200
    except Exception as e:
        print(f'データ鮮度の集計に失敗: {e}')
        return jsonify({"error": str(e)}), 500


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
@admin_required_api
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
@admin_required_api
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
    """2〜3社の財務指標を比較用に返却（欠損データは自動補完）"""
    try:
        codes_param = request.args.get('codes', '')
        if not codes_param:
            return jsonify({"error": "銘柄コードを指定してください"}), 400

        codes = [c.strip() for c in codes_param.split(',') if c.strip()]
        if len(codes) < 2 or len(codes) > 3:
            return jsonify({"error": "2〜3銘柄を指定してください"}), 400

        results = []
        for code in codes:
            normalized = normalize_code(code)
            data = get_screened_data(normalized)
            if not data:
                return jsonify({"error": f"{code} のデータがありません。先にダッシュボードで分析してください。"}), 404

            # 主要指標が欠損している場合、yfinanceからリアルタイム補完
            key_fields = ['market_cap', 'stock_price', 'per_forward', 'pbr',
                          'dividend_yield', 'roe', 'roa', 'operating_margin',
                          'equity_ratio', 'eps', 'dps', 'payout_ratio']
            missing = [f for f in key_fields if not data.get(f)]

            if missing:
                try:
                    analyzer = StockAnalyzer()
                    stock_data = analyzer.analyze(normalized)
                    if stock_data and stock_data.get('name'):
                        update_fields = {}
                        # market_cap: yfinanceは円単位、DBは億円単位
                        if 'market_cap' in missing and stock_data.get('market_cap'):
                            mc = stock_data['market_cap'] / 1e8
                            data['market_cap'] = mc
                            update_fields['market_cap'] = mc
                        if 'stock_price' in missing and stock_data.get('last_price'):
                            data['stock_price'] = stock_data['last_price']
                            update_fields['stock_price'] = stock_data['last_price']
                        if 'per_forward' in missing and stock_data.get('per'):
                            data['per_forward'] = stock_data['per']
                            update_fields['per_forward'] = stock_data['per']
                        if 'pbr' in missing and stock_data.get('pbr'):
                            data['pbr'] = stock_data['pbr']
                            update_fields['pbr'] = stock_data['pbr']
                        if 'dividend_yield' in missing and stock_data.get('dividend_yield'):
                            data['dividend_yield'] = stock_data['dividend_yield']
                            update_fields['dividend_yield'] = stock_data['dividend_yield']
                        if 'roe' in missing:
                            roe_val = get_latest_value(stock_data.get('roe'))
                            if roe_val:
                                data['roe'] = roe_val
                                update_fields['roe'] = roe_val
                        if 'roa' in missing:
                            roa_val = get_latest_value(stock_data.get('roa'))
                            if roa_val:
                                data['roa'] = roa_val
                                update_fields['roa'] = roa_val
                        if 'operating_margin' in missing and stock_data.get('operating_margin') is not None:
                            data['operating_margin'] = stock_data['operating_margin']
                            update_fields['operating_margin'] = stock_data['operating_margin']
                        if 'equity_ratio' in missing:
                            eq_val = get_latest_value(stock_data.get('equity_ratio_list'))
                            if eq_val:
                                data['equity_ratio'] = eq_val
                                update_fields['equity_ratio'] = eq_val
                        if 'eps' in missing:
                            eps_val = get_latest_value(stock_data.get('eps'))
                            if eps_val:
                                data['eps'] = eps_val
                                update_fields['eps'] = eps_val
                        if 'dps' in missing:
                            # 配当は進行中の年度を拾わない
                            dps_val = get_latest_completed_value(stock_data.get('dps'))
                            if dps_val:
                                data['dps'] = dps_val
                                update_fields['dps'] = dps_val
                        if 'payout_ratio' in missing:
                            pr_val = get_latest_completed_value(stock_data.get('payout_ratio'))
                            if pr_val:
                                data['payout_ratio'] = pr_val
                                update_fields['payout_ratio'] = pr_val

                        # 補完したデータをDBにも保存（次回以降は高速化）
                        if update_fields:
                            update_fields['company_code'] = normalized
                            try:
                                update_screened_data(normalized, update_fields)
                            except Exception:
                                pass  # DB更新失敗は無視（比較結果には影響しない）
                except Exception:
                    pass  # 補完失敗は無視（既存データで返す）

            results.append(data)

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


@app.route('/api/learning/progress', methods=['GET'])
def api_learning_progress():
    """その人が理解済みにした学習項目の一覧を返す。

    どの項目が存在するか（terms）は learning.html が持っている。
    ここは「誰がどれを理解したか」だけを返し、項目定義には関与しない。
    """
    from learning_terms import TERM_IDS, total_terms

    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "ログインが必要です"}), 401
    try:
        rows = get_learning_progress(user_id)
    except LearningProgressUnavailable:
        # migration未適用。学習ノート自体は読めるべきなので、記録機能だけ止める。
        return jsonify({"available": False, "understood": [], "records": [],
                        "total_terms": total_terms(),
                        "reason": "supabase/migration_learning_progress.sql が未適用です"}), 200

    # 項目を廃止・改名した場合に、消えたIDが件数に残らないようにする
    live = [r for r in rows if r.get('term_id') in TERM_IDS]
    return jsonify({
        "available": True,
        "understood": [r['term_id'] for r in live],
        "records": live,
        "total_terms": total_terms(),
    }), 200


@app.route('/api/learning/progress/<term_id>', methods=['PUT', 'DELETE'])
def api_learning_progress_update(term_id):
    """学習項目を理解済みにする / 取り消す"""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "ログインが必要です"}), 401

    # 実在する項目だけ受け付ける。
    # 検証しないと、任意の文字列を送って理解済み件数を水増しできてしまう。
    from learning_terms import is_valid_term

    term_id = (term_id or '').strip()
    if not is_valid_term(term_id):
        return jsonify({"error": "そのような学習項目はありません"}), 404

    try:
        if request.method == 'DELETE':
            unmark_learning_understood(user_id, term_id)
            return jsonify({"understood": False, "term_id": term_id}), 200
        record = mark_learning_understood(user_id, term_id)
        return jsonify({"understood": True, "term_id": term_id,
                        "understood_at": record.get('understood_at')}), 200
    except LearningProgressUnavailable:
        return jsonify({
            "error": "学習の記録はまだ使えません。"
                     "supabase/migration_learning_progress.sql を適用してください。",
            "migration_required": True,
        }), 503


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
# マーケット所感API
# =============================================

@app.route('/api/market-comment/latest', methods=['GET'])
def api_market_comment_latest():
    """最新のマーケット所感を取得（adminユーザーのmarket_commentカラムから）"""
    try:
        client = get_supabase_client()
        result = client.table('app_users').select(
            'market_comment, updated_at'
        ).eq('role', 'admin').not_.is_('market_comment', 'null').order(
            'updated_at', desc=True
        ).limit(1).execute()
        if result.data and result.data[0].get('market_comment'):
            row = result.data[0]
            return jsonify({
                "content": row['market_comment'],
                "updated_at": row['updated_at'],
            }), 200
        return jsonify({}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/market-comment', methods=['POST'])
def api_market_comment_save():
    """マーケット所感を保存（ログイン中のadminユーザーに書き込み）"""
    try:
        data = request.get_json()
        content = data.get('content', '').strip() if data else ''
        if not content:
            return jsonify({"error": "内容を入力してください"}), 400

        # セッションからユーザーIDを取得
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"error": "ログインが必要です"}), 401

        client = get_supabase_client()

        # adminロールか確認
        user = client.table('app_users').select('role').eq('id', user_id).execute()
        if not user.data or user.data[0].get('role') != 'admin':
            return jsonify({"error": "管理者権限が必要です"}), 403

        # market_commentカラムを更新（updated_atはトリガーで自動更新）
        client.table('app_users').update({
            'market_comment': content,
        }).eq('id', user_id).execute()

        return jsonify({"success": True, "message": "マーケット所感を保存しました"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================
# デモ売買API
# =============================================

def _get_demo_user_id():
    """デモ売買用のユーザーIDを取得（セッションベース）。

    ⚠️ session['user_id'] をそのまま信用しない。app_users に居ないIDのまま
    ここを通すと、実在しないユーザーの口座を作ってしまう。
    実際に `layout-check` や素性の分からないUUIDの demo_account 行が残っていた。
    get_current_user() は解決できないセッションを破棄するので、それを通す。
    """
    user = get_current_user()
    if user:
        return user['id']

    # 未ログイン（ゲスト）。デモ売買は試せるようにしておく
    if not session.get('demo_user_id'):
        session['demo_user_id'] = str(uuid.uuid4())
    return session['demo_user_id']


def _get_or_create_demo_account(user_id, initial_amount=1000000):
    """デモ口座を取得（なければ作成）"""
    client = get_supabase_client()
    result = client.table('demo_account').select('*').eq('user_id', user_id).execute()
    if result.data:
        account = result.data[0]
        # total_depositedが未設定の既存アカウントにはデフォルト値を補完
        if account.get('total_deposited') is None:
            account['total_deposited'] = float(account.get('cash_balance', 1000000))
        return account
    # 新規作成
    new_account = {
        'user_id': user_id,
        'cash_balance': initial_amount,
        'total_deposited': initial_amount,
    }
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

        # 各銘柄の現在価格をscreened_latestから一括取得
        holdings = []
        total_value = 0
        codes = [p['company_code'] for p in portfolio.data if p.get('company_code')]
        screened_map = {}
        if codes:
            screened_result = client.table('screened_latest').select(
                'company_code,stock_price'
            ).in_('company_code', codes).execute()
            for s in screened_result.data:
                screened_map[s['company_code']] = s

        for p in portfolio.data:
            screened = screened_map.get(p['company_code'])
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

        total_deposited = float(account.get('total_deposited') or account['cash_balance'])
        total_assets = float(account['cash_balance']) + total_value
        profit = total_assets - total_deposited

        return jsonify({
            "cash_balance": float(account['cash_balance']),
            "total_value": total_value,
            "total_assets": total_assets,
            "total_deposited": total_deposited,
            "profit": profit,
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


@app.route('/api/demo/deposit', methods=['POST'])
def api_demo_deposit():
    """デモ口座に資金を追加"""
    try:
        user_id = _get_demo_user_id()
        data = request.get_json()
        amount = int(data.get('amount', 0)) if data else 0

        if amount <= 0:
            return jsonify({"error": "追加金額を正しく指定してください"}), 400
        if amount > 100000000:
            return jsonify({"error": "一度に追加できるのは1億円までです"}), 400

        account = _get_or_create_demo_account(user_id)
        client = get_supabase_client()

        new_cash = float(account['cash_balance']) + amount
        new_deposited = float(account.get('total_deposited') or account['cash_balance']) + amount

        client.table('demo_account').update({
            'cash_balance': new_cash,
            'total_deposited': new_deposited,
        }).eq('user_id', user_id).execute()

        # 履歴に入金を記録
        client.table('demo_trades').insert({
            'user_id': user_id,
            'company_code': '',
            'company_name': '',
            'trade_type': 'deposit',
            'shares': 0,
            'price': 0,
            'total_amount': amount,
            'reason': f'資金追加: ¥{amount:,.0f}',
        }).execute()

        return jsonify({
            "success": True,
            "message": f"¥{amount:,.0f} を追加しました（現金残高: ¥{new_cash:,.0f}）",
            "new_balance": new_cash,
            "total_deposited": new_deposited,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/demo/reset', methods=['POST'])
def api_demo_reset():
    """デモ口座をリセット（ポートフォリオ・履歴クリア）"""
    try:
        user_id = _get_demo_user_id()
        data = request.get_json() or {}
        initial_amount = int(data.get('initial_amount', 1000000))

        # 入力値チェック
        if initial_amount < 10000 or initial_amount > 100000000:
            initial_amount = 1000000

        client = get_supabase_client()

        # ポートフォリオ削除
        client.table('demo_portfolio').delete().eq('user_id', user_id).execute()
        # 履歴削除
        client.table('demo_trades').delete().eq('user_id', user_id).execute()
        # 残高リセット
        client.table('demo_account').upsert({
            'user_id': user_id,
            'cash_balance': initial_amount,
            'total_deposited': initial_amount,
        }).execute()

        return jsonify({
            "success": True,
            "message": f"デモ口座をリセットしました（初期資金: ¥{initial_amount:,.0f}）"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True)
