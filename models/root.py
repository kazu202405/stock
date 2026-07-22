from config import *
from flask import render_template, redirect, request, session, flash
from models.common import *
from models.model import *
from supabase_client import (
    authenticate_user, create_user as create_app_user,
    get_user_by_id, get_user_by_referral_code, migrate_guest_notes,
    get_screened_data, get_supabase_client
)


def normalize_code(code):
    """'7203.T' でも '7203' でもDB保存形式（.Tなし）に揃える"""
    return (code or '').replace('.T', '').strip()


def _require_login():
    """ログイン必須チェック。未ログインならログインページへリダイレクト"""
    if not session.get('user_id'):
        return redirect('/login')
    return None


def _require_admin():
    """admin必須チェック。未ログインまたは非adminならリダイレクト"""
    if not session.get('user_id'):
        return redirect('/login')
    if session.get('user_role') != 'admin':
        return redirect('/dashboard')
    return None


def _get_user_context():
    """テンプレートに渡すユーザーコンテキストを取得"""
    user_id = session.get('user_id')
    if user_id:
        return {
            'user_id': user_id,
            'user_name': session.get('user_name', ''),
            'user_role': session.get('user_role', 'user'),
            'is_logged_in': True,
        }
    return {'is_logged_in': False}


@app.context_processor
def inject_user():
    """全テンプレートにユーザー情報を注入"""
    return _get_user_context()


@app.route('/')
def index():
    """ランディングページ"""
    return render_template('lp.html')


@app.route('/dashboard')
def dashboard():
    """分析ダッシュボード（閲覧専用）"""
    guard = _require_login()
    if guard: return guard
    return render_template('stock.html', is_admin=False)


@app.route('/screener')
def screener():
    """好調企業ランキングページ"""
    guard = _require_login()
    if guard: return guard
    return render_template('screener.html')


@app.route('/report')
def report_select():
    """レポートを見る企業を選ぶ入口"""
    guard = _require_login()
    if guard: return guard
    return render_template('report_select.html')


@app.route('/report/sample')
def report_sample():
    """レポートの完成イメージ（固定データ）。

    実データが揃っていない銘柄でも、レポートがどこまで書けるものかを
    確認できるようにするためのページ。
    ⚠️ 中身は特定企業の実例なので、他銘柄のレポートには絶対に流用しない。
    """
    guard = _require_login()
    if guard: return guard
    return render_template('report_view.html', report=None, show_sample=True)


@app.route('/report/<source>/<key>')
def report_view(source, key):
    """企業分析レポート本体。

    source はデータ源。将来 'own'（経営者が自社決算から作る）を足せるよう
    URLに含めている。描画側は共通で、build_report が返す構造だけを見る。
    """
    guard = _require_login()
    if guard: return guard

    import report_builder
    if source not in ('listed',):
        return render_template('report_view.html', report=None,
                               error='このデータ源にはまだ対応していません'), 400

    try:
        report = report_builder.build_report(source, normalize_code(key))
    except Exception as e:
        print(f'レポート生成エラー {source}/{key}: {e}')
        return render_template('report_view.html', report=None,
                               error='レポートを作成できませんでした'), 500

    if not report:
        return render_template('report_view.html', report=None,
                               error='この銘柄のデータがまだありません'), 404

    return render_template('report_view.html', report=report)


@app.route('/mypage')
def mypage():
    """マイノート"""
    guard = _require_login()
    if guard: return guard
    return render_template('mypage.html')


@app.route('/learning')
def learning():
    """学習ノート（用語解説・企業分析の基礎知識）"""
    guard = _require_login()
    if guard: return guard
    return render_template('learning.html')


@app.route('/community')
def community():
    """みんなの企業研究ノート（コミュニティ）"""
    guard = _require_login()
    if guard: return guard
    return render_template('community.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """ログインページ"""
    # 既にログイン済みならダッシュボードへ
    if session.get('user_id'):
        return redirect('/dashboard')

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''

        if not email or not password:
            flash('メールアドレスとパスワードを入力してください', 'error')
            return render_template('login.html')

        user = authenticate_user(email, password)
        if not user:
            flash('メールアドレスまたはパスワードが正しくありません', 'error')
            return render_template('login.html', saved_email=email)

        # ゲストノートの引き継ぎ
        guest_id = session.get('guest_user_id')
        if guest_id:
            migrate_guest_notes(guest_id, user['id'])
            session.pop('guest_user_id', None)

        # セッションにログイン状態を保存
        session['user_id'] = user['id']
        session['user_name'] = user['name']
        session['user_role'] = user['role']
        session.permanent = True

        return redirect('/dashboard')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    """ユーザー登録ページ"""
    # 既にログイン済みならダッシュボードへ
    if session.get('user_id'):
        return redirect('/dashboard')

    # URLパラメータから紹介コードを取得
    ref_code = request.args.get('ref', '')
    referrer_name = ''
    if ref_code:
        referrer = get_user_by_referral_code(ref_code)
        if referrer:
            referrer_name = referrer['name']

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        password_confirm = request.form.get('password_confirm') or ''
        referral_code = (request.form.get('referral_code') or '').strip()

        # バリデーション
        if not name:
            flash('名前を入力してください', 'error')
            return render_template('register.html', ref_code=referral_code,
                                   referrer_name=referrer_name, saved_name=name, saved_email=email)
        if not email:
            flash('メールアドレスを入力してください', 'error')
            return render_template('register.html', ref_code=referral_code,
                                   referrer_name=referrer_name, saved_name=name, saved_email=email)
        if len(password) < 6:
            flash('パスワードは6文字以上で入力してください', 'error')
            return render_template('register.html', ref_code=referral_code,
                                   referrer_name=referrer_name, saved_name=name, saved_email=email)
        if password != password_confirm:
            flash('パスワードが一致しません', 'error')
            return render_template('register.html', ref_code=referral_code,
                                   referrer_name=referrer_name, saved_name=name, saved_email=email)

        try:
            user = create_app_user(
                name=name,
                email=email,
                password=password,
                referred_by_code=referral_code if referral_code else None
            )

            # ゲストノートの引き継ぎ
            guest_id = session.get('guest_user_id')
            if guest_id:
                migrate_guest_notes(guest_id, user['id'])
                session.pop('guest_user_id', None)

            # セッションにログイン状態を保存
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_role'] = user['role']
            session.permanent = True

            return redirect('/dashboard')
        except ValueError as e:
            flash(str(e), 'error')
            return render_template('register.html', ref_code=referral_code,
                                   referrer_name=referrer_name, saved_name=name, saved_email=email)

    return render_template('register.html', ref_code=ref_code, referrer_name=referrer_name)


@app.route('/logout')
def logout():
    """ログアウト → ログイン画面へ遷移"""
    session.pop('user_id', None)
    session.pop('user_name', None)
    session.pop('user_role', None)
    return redirect('/login')


@app.route('/stock/<code>')
def stock_detail(code):
    """個別銘柄詳細ページ。

    ログイン不要で開ける。検索エンジンに拾わせるための入口であり、
    ここを閉じていると全銘柄ページがインデックスされず、検索流入が発生しないため。
    深い情報（合致度・5年財務・株主等）はテンプレート側でぼかす。

    ⚠️ クローキング（検索エンジンにだけ全文を見せる）は規約違反になるため、
    未ログインユーザーとクローラーには必ず同じ内容を返すこと。
    """
    company = get_screened_data(normalize_code(code)) or {}
    return render_template(
        'stock_detail.html',
        stock_code=code,
        company=company,
        is_logged_in=bool(session.get('user_id')),
    )


@app.route('/robots.txt')
def robots_txt():
    """クローラー向けの指示。sitemapの場所を伝えるのが主目的。
    ログインが要る画面や管理画面はクロールさせない。"""
    base = request.url_root.rstrip('/')
    body = '\n'.join([
        'User-agent: *',
        'Allow: /',
        'Disallow: /mypage',
        'Disallow: /dashboard',
        'Disallow: /admin',
        'Disallow: /login',
        'Disallow: /register',
        'Disallow: /api/',
        '',
        f'Sitemap: {base}/sitemap.xml',
        '',
    ])
    return app.response_class(body, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    """全銘柄ページのsitemap。

    これが無いとGoogleは数千ページの存在を知れない。
    銘柄ページは相互リンクが薄く、辿って発見してもらうのが難しいため
    sitemapでの申告が実質必須になる。
    """
    from xml.sax.saxutils import escape
    base = request.url_root.rstrip('/')

    urls = [(f'{base}/', '1.0')]
    try:
        client = get_supabase_client()
        page = 0
        while page < 60:   # 上限を設けて暴走を防ぐ
            res = (client.table('screened_latest')
                   .select('company_code, analyzed_at')
                   .range(page * 1000, page * 1000 + 999)
                   .execute())
            rows = res.data or []
            for r in rows:
                code = r.get('company_code')
                if code:
                    urls.append((f'{base}/stock/{escape(str(code))}', '0.8'))
            if len(rows) < 1000:
                break
            page += 1
    except Exception as e:
        print(f'sitemap生成エラー: {e}')

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, priority in urls:
        parts.append(f'<url><loc>{loc}</loc><priority>{priority}</priority></url>')
    parts.append('</urlset>')
    return app.response_class('\n'.join(parts), mimetype='application/xml')


@app.route('/search')
def search():
    """企業情報ページ（銘柄検索・企業比較）"""
    guard = _require_login()
    if guard: return guard
    return render_template('search.html', is_admin=True)


@app.route('/dashboard/admin')
def admin():
    """銘柄管理画面（編集可能・admin専用）"""
    guard = _require_admin()
    if guard: return guard
    return render_template('stock.html', is_admin=True)


@app.route('/admin/users')
def admin_users():
    """ユーザー管理画面（admin専用）"""
    guard = _require_admin()
    if guard: return guard
    return render_template('admin_users.html')
