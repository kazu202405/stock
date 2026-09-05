from config import *
from flask import render_template, redirect, request, session, flash
from models.common import *
from models.model import *
from supabase_client import (
    ensure_app_user, get_user_by_id, get_user_by_referral_code,
    migrate_guest_notes, get_screened_data, get_supabase_client
)
import gia_identity


def _store_session(user, email):
    """ログイン状態をセッションに保存する。

    管理者判定はメールで行う（GIA_ADMIN_EMAILS。既定は gia-next の
    is_admin() と同じアドレス）。app_users.role も見るのは、株アプリ側で
    agent 等を付けている運用を壊さないため。
    """
    session['user_id'] = user['id']
    session['user_name'] = user.get('name') or (email or '').split('@')[0]
    session['user_email'] = email
    session['user_role'] = ('admin' if gia_identity.is_admin_email(email)
                            else (user.get('role') or 'user'))
    session.permanent = True


def normalize_code(code):
    """'7203.T' でも '7203' でもDB保存形式（.Tなし）に揃える"""
    return (code or '').replace('.T', '').strip()


def _require_login():
    """ログイン必須チェック。未ログインならログインページへリダイレクト"""
    if not session.get('user_id'):
        return redirect('/login')
    return None


def is_member():
    """いまのログインユーザーが有料会員か。

    判定は gia_identity.is_paid_member() に集約している（gia-next 側の
    isActiveMember() と同じ定義）。admin は常に会員として扱う。
    運営が自分の画面で確認できないと、会員向けの表示を検証できないため。
    """
    if session.get('user_role') == 'admin':
        return True
    return gia_identity.is_paid_member(session.get('user_id'))


def home_path():
    """ログイン後に着地させる場所。

    /dashboard は会員限定にしたので、ここを固定で /dashboard にすると
    無料会員は登録した直後に「この機能は会員限定です」に飛ばされる。
    無料会員には、無料で使えて最初に触るであろう画面を出す。

    2026-08-25: /search（銘柄検索）は /compare（企業比較）になった。
    銘柄を探すのはヘッダーの検索窓でどのページからでもできるので、
    着地はテーマ・業種の一覧にする（会社を眺めて回れる入口）。

    2026-09-06: /themes はヘッダーから管理者限定にした（中身を確認してから
    公開する判断）。着地だけ残っていたので、無料会員は毎回ログイン直後に
    「メニューに無いページ」へ降ろされ、離れたら戻れなかった。
    ⚠️ **着地先はメニューから辿れるページにする。** 着地とメニューは
       別々に直されるので、片方を閉じたらもう片方を必ず確認する。
    スクリーナーは合致度の上位3件を非会員にも見せるので、無料会員でも
    中身のある画面になる（FREE_SCREEN_ROWS）。
    """
    return '/dashboard' if is_member() else '/screener'


def _require_member():
    """有料会員必須チェック。

    未ログイン → ログインへ。ログイン済みの無料会員 → 案内ページへ。
    「ログインしろ」と言われ続けると、既にログインしている人が混乱するため
    行き先を分ける。
    """
    if not session.get('user_id'):
        return redirect('/login')
    if not is_member():
        return redirect('/membership')
    return None


def _require_admin():
    """admin必須チェック。未ログインまたは非adminならリダイレクト"""
    if not session.get('user_id'):
        return redirect('/login')
    if session.get('user_role') != 'admin':
        return redirect(home_path())
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
            # テンプレートで会員向け表示を出し分けるために渡す。
            # ただし会員限定の「値」はテンプレートに渡さずサーバー側で落とすこと
            # （CSSで隠してもAPIやソース表示から丸見えになる）。
            'is_member': is_member(),
            # ⚠️ ロゴ・「ホーム」の行き先をテンプレートに直書きしない。
            #    /dashboard は会員限定なので、無料会員が押すと
            #    「この機能は会員限定です」に落ちる。着地（home_path）と
            #    同じ場所を指すこと。
            'home_path': home_path(),
        }
    return {'is_logged_in': False, 'is_member': False, 'home_path': '/'}


@app.context_processor
def inject_user():
    """全テンプレートにユーザー情報を注入"""
    return _get_user_context()


@app.context_processor
def inject_price_freshness():
    """株価が古いとき、全ページに「◯/◯時点」と出すための値を渡す。

    2026-08-31、株価の取得が3回とも空振りし、スクリーナーが金曜の終値を
    **今日の株価の顔で**丸1日出していた。取得が失敗すること自体は外部次第で
    避けきれないが、いつ時点の値かを書いておけば見る人の判断は狂わない。

    ⚠️ **古いときだけ出す。** 平常時に毎回出していると誰も読まなくなり、
       本当に古い日に効かなくなる。
    ⚠️ 全ページの描画を通るので、5分キャッシュ＋例外を出さない作りにしてある。
       出せないときは何も出さない（画面を壊さないほうを優先）。
    """
    none = {'price_stale_as_of': None, 'price_fetch_failing': False}
    try:
        import data_freshness
        when, age = data_freshness.price_as_of()
        stale = (when is not None and age is not None
                 and age > data_freshness.PRICE_BANNER_STALE_DAYS)
        # ⚠️ 成功した記録が1件も無いときも帯を出す。取得がずっと弾かれている
        #    最中に公開すると、いつ時点かを言えないまま古い株価が出続ける。
        failing = data_freshness.price_fetch_failing()
        if not stale and not failing:
            return none
        return {'price_stale_as_of': ('%d月%d日' % (when.month, when.day)
                                      if stale else None),
                'price_fetch_failing': failing}
    except Exception:
        return none


@app.route('/')
def index():
    """ランディングページ"""
    return render_template('lp.html')


@app.route('/welcome')
def welcome():
    """知人に直接URLを渡すための案内ページ。

    `/` の lp.html は検索から来た人向け（機能の説明が主）。こちらは
    「五島から渡された」人向けで、製品説明ではなく、まず1社ひいてもらう
    ことを目的にしている。sitemap には載せず、noindex。

    `?ref=<紹介コード>` を受けたら紹介者名を表示し、そのまま /register へ
    引き継ぐ。口伝てで配ると誰が連れてきたか残らないため、ここで拾う。
    """
    from urllib.parse import quote

    ref_code = (request.args.get('ref') or '').strip()
    referrer_name = ''
    register_url = '/register'
    if ref_code:
        referrer = get_user_by_referral_code(ref_code)
        if referrer:
            referrer_name = referrer['name']
            register_url = '/register?ref=' + quote(ref_code, safe='')

    return render_template('welcome.html',
                           referrer_name=referrer_name,
                           register_url=register_url,
                           page_url=request.url_root.rstrip('/') + '/welcome')


# 勉強会の案内ページの内容。**開催のたびにここだけ書き換える。**
# date_label を空にすると「日程調整中」表示に切り替わり、申込の文言も
# 「候補日をお送りします」に変わる（開催日が都度変わるため、空が既定）。
SEMINAR = {
    'session_no':  '',            # 例 '第1回'。空なら出さない
    'date_label':  '',            # 例 '2026年9月12日（金）19:00〜21:00'
    'venue':       '大阪府大阪市中央区石町2丁目3-1-201',
    'venue_zip':   '〒540-0033',
    'venue_area':  '中央区石町',
    'capacity':    12,
    'seats_taken': 0,             # 埋まった席数。残席表示に使う
    'fee_venue':   '2,000円',
    'fee_party':   '6,000円前後',
    'host':        '株式会社GIA',
}

# 訴求のテスト用。渡す相手ごとに ?v= を変えて、LINEの返信数で比べる。
# 変わるのは見出しまわりだけで、中身は共通。
SEMINAR_HEADLINES = {
    'a': {
        'title': '他社を測る目盛りで、自分の会社を見る',
        'h1_a':  '他社を測る目盛りで、',
        'h1_b':  '自分の会社を見る。',
        'lead':  '経営者12名で学ぶ、株式投資・企業分析の実践勉強会。'
                 '決算の数字から会社の状態を読み、自分の判断軸をつくります。',
        'cta':   '参加希望をLINEで伝える',
    },
    'b': {
        'title': '投資先を見る目は、会社を見る目から',
        'h1_a':  '投資先を見る目は、',
        'h1_b':  '会社を見る目から。',
        'lead':  '経営者同士で学ぶ、株式投資・企業分析の実践勉強会。'
                 '株価の動きや誰かのおすすめに頼らず、会社の中身を自分で読めるようになります。',
        'cta':   '参加希望をLINEで伝える',
    },
    'c': {
        'title': '数字で会社を語れる人が、社内に何人いますか',
        'h1_a':  '数字で会社を語れる人が、',
        'h1_b':  '社内に何人いますか。',
        'lead':  '経営者12名の少人数勉強会。他社の決算を読む練習を通して、'
                 '自社の数字を語れる目盛りを手に入れます。',
        'cta':   '参加希望をLINEで伝える',
    },
}

# 申込の受け口。いまは「紹介者への個別LINE」なので、ページ内の申込欄へ送るだけ。
# LINE公式アカウントに変えるときは、ここを https://lin.ee/... に差し替える。
SEMINAR_LINE_URL = '#entry'


# 知人・紹介者へ個別に渡す月額11,000円プランの案内。
# `?v=a|b|c` は入口の言葉だけを変える。本文や料金は共通にして、
# 何に反応したかをLINEの返信・申込で比べられるようにする。
INVITE_HEADLINES = {
    'a': {
        'title': '投資先を見る目は、会社を見る目から',
        'line_a': '投資先を見る目は、',
        'line_b': '会社を見る目から。',
        'lead': '上場企業の数字を読むアプリを使って、経営者同士で会社を読む。'
                'ご紹介者のみのコミュニティです。',
    },
    'b': {
        'title': '他社を測る目盛りで、自分の会社を見る',
        'line_a': '他社を測る目盛りで、',
        'line_b': '自分の会社を見る。',
        'lead': '上場企業の決算を題材に、売上・利益・財務の見方を身につける。'
                '経営者同士で続ける企業分析の会です。',
    },
    'c': {
        'title': '決算書を、経営者同士で読む',
        'line_a': '決算書を、',
        'line_b': '経営者同士で読む。',
        'lead': '誰かのおすすめを聞く場ではありません。会社の中身を自分で読み、'
                '自分の言葉で話せるようになるための少人数会です。',
    },
}

# ⚠️ **from=note を落とさないこと。** これが無いと、決済のあと GIA 側の
#    マイページに着地して、買った本人が何を買ったのか分からなくなる。
#    受け取る側は gia-next の /upgrade/[plan] と /upgrade/success。
INVITE_CHECKOUT_URL = 'https://gia2018.com/upgrade/invite?from=note'


@app.route('/seminar')
def seminar():
    """経営者向け少人数勉強会の案内ページ。

    チラシのWeb版だが、紙と違って順番を効かせている。
    `/welcome`（アプリの案内）とは別物で、こちらが売っているのは勉強会。

    `?v=a|b|c` で見出しだけ差し替える（訴求のテスト用）。
    `?from=<名前>` を付けると「◯◯さんへ参加希望とご返信ください」と名指しできる。
    紹介者はアプリの登録者とは限らないので、紹介コードではなく表示名で受ける。
    そのぶん任意の文字列が入るため、名前に使う文字だけ残して長さも切る。
    """
    import re

    variant = (request.args.get('v') or 'a').lower()
    head = SEMINAR_HEADLINES.get(variant, SEMINAR_HEADLINES['a'])

    raw = request.args.get('from') or ''
    inviter = re.sub(r'[^0-9A-Za-z぀-ゟ゠-ヿ一-鿿々ー・\s]', '', raw)
    inviter = ' '.join(inviter.split())[:24]

    return render_template('seminar.html',
                           s=SEMINAR,
                           head=head,
                           inviter=inviter,
                           line_url=SEMINAR_LINE_URL,
                           page_url=request.url_root.rstrip('/') + '/seminar')


@app.route('/invite')
def invite():
    """知人・紹介者向けの月額11,000円プラン案内。

    一般公開の料金比較ではなく、五島さん本人または紹介者からURLを受け取った
    経営者に向けたページ。検索には載せず、決済はGIA側の既存Stripe導線へ渡す。

    `?v=a|b|c` で見出しをテストでき、`?from=<名前>` で誰から届いた案内かを
    ページ内に出せる。任意文字列なので表示前に文字種と長さを制限する。
    """
    import re

    variant = (request.args.get('v') or 'a').lower()
    head = INVITE_HEADLINES.get(variant, INVITE_HEADLINES['a'])

    raw = request.args.get('from') or ''
    inviter = re.sub(r'[^0-9A-Za-z぀-ゟ゠-ヿ一-鿿々ー・\s]', '', raw)
    inviter = ' '.join(inviter.split())[:24]

    return render_template('invite.html',
                           head=head,
                           inviter=inviter,
                           checkout_url=INVITE_CHECKOUT_URL,
                           page_url=request.url_root.rstrip('/') + '/invite')


@app.route('/membership')
def membership():
    """会員限定機能に無料会員が来たときの案内。

    「ログインしてください」ではなく「会員限定です」と伝える。
    既にログインしている人にログインを促すと、何が足りないのか分からなくなる。
    申し込みは gia2018.com 側（決済はそちらに一本化している）。
    """
    if not session.get('user_id'):
        return redirect('/login')

    # ここはキャッシュを使わず取り直す。
    # 会員になった直後の人が「まだ会員限定と言われる」と確認しに来る場所なので、
    # ここだけは最新の状態を見る。会員になっていれば、そのままホームへ通す。
    gia_identity.clear_membership_cache(session.get('user_id'))
    if is_member():
        return redirect('/dashboard')
    return render_template('membership.html', member_features=[
        'ホーム（好調企業・高配当企業・テクニカル分析）',
        'スクリーナー（全銘柄からの絞り込み・並べ替え）',
        '企業分析レポート',
        '決算情報（決算月ごとの銘柄一覧）',
        '銘柄ページの数年分のfinancials・キャッシュフロー・財務健全性',
        '会社予想・成長率・ROA と、12項目の合致度スコアの内訳',
    ])


@app.route('/dashboard')
def dashboard():
    """分析ダッシュボード（好調企業・高配当企業・テクニカル分析）

    2026-08-11: 会員限定にした。どの銘柄を拾ってどう並べるかが判断そのもので、
    ここが有料の中身にあたる。
    """
    guard = _require_member()
    if guard: return guard
    return render_template('stock.html', is_admin=False)


@app.route('/screener')
def screener():
    """好調企業ランキングページ。

    絞り込み・並べ替え・全件は会員価値。非会員には合致度の上位3件だけを
    見せる（件数を切るのは API 側＝app._screen_preview。テンプレートで
    隠しても、APIを直に叩けば中身が読めるため）。
    """
    guard = _require_login()
    if guard: return guard
    # ウォッチリストは全体で1つの共有リスト。消す操作は管理者だけに出す
    return render_template('screener.html',
                           is_admin=session.get('user_role') == 'admin')


@app.route('/market')
def market():
    """マーケット（日経平均・S&P500などの指数チャート）"""
    guard = _require_login()
    if guard: return guard
    import market_data as md
    return render_template('market.html', indexes=md.INDEXES)


@app.route('/simulator')
def simulator_page():
    """過去シミュレーション（いつ買っていたらいくらになっていたか）。会員限定。

    銘柄を横断して条件を変えながら試す機能なので、スクリーナーと同じ扱いにする。
    計算は保存済みの株価履歴だけで完結し、外部へは取りに行かない。
    """
    guard = _require_member()
    if guard: return guard
    return render_template('simulator.html')


@app.route('/earnings')
def earnings():
    """決算情報（決算月ごとの銘柄一覧）

    扱うのは決算"期"の月であって、決算"発表予定日"ではない。
    発表予定日は全銘柄を無料で取れる取得元が未整理のため、ここには出さない。

    2026-08-11: 会員限定。銘柄を横断して絞り込む機能なのでスクリーナーと同性質。
    """
    guard = _require_member()
    if guard: return guard

    client = get_supabase_client()
    counts = {m: 0 for m in range(1, 13)}
    total = 0
    migration_ready = True

    if client is not None:
        try:
            # 全件を引いてJS側で数えると1000行上限に切られるため、
            # 件数はDB側のcountで月ごとに取る（headのみで行本体は転送しない）。
            for month in range(1, 13):
                result = (client.table('screened_latest')
                          .select('company_code', count='exact')
                          .eq('fiscal_month', month).limit(1).execute())
                counts[month] = result.count or 0
                total += counts[month]
        except Exception as e:
            if 'fiscal_month' in str(e):
                migration_ready = False
            else:
                raise

    return render_template('earnings.html',
                           counts=counts,
                           total=total,
                           migration_ready=migration_ready)


@app.route('/report')
def report_select():
    """レポートを見る企業を選ぶ入口（会員限定）"""
    guard = _require_member()
    if guard: return guard
    return render_template('report_select.html')


@app.route('/report/sample')
def report_sample():
    """レポートの完成イメージ（固定データ）。

    実データが揃っていない銘柄でも、レポートがどこまで書けるものかを
    確認できるようにするためのページ。
    ⚠️ 中身は特定企業の実例なので、他銘柄のレポートには絶対に流用しない。
    """
    guard = _require_member()
    if guard: return guard
    return render_template('report_view.html', report=None, show_sample=True)


@app.route('/report/<source>/<key>')
def report_view(source, key):
    """企業分析レポート本体。

    source はデータ源。将来 'own'（経営者が自社決算から作る）を足せるよう
    URLに含めている。描画側は共通で、build_report が返す構造だけを見る。
    """
    guard = _require_member()
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
    # 既にログイン済みなら、その人が使える場所へ
    if session.get('user_id'):
        return redirect(home_path())

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''

        if not email or not password:
            flash('メールアドレスとパスワードを入力してください', 'error')
            return render_template('login.html')

        # 認証はGIA（キャンパス）のSupabase Authで行う。
        # 株アプリ独自のパスワードは持たない（アカウントを二重に持たせないため）。
        try:
            account = gia_identity.sign_in(email, password)
        except gia_identity.GiaIdentityUnavailable as e:
            # 設定漏れを「パスワードが違う」と表示すると原因が分からなくなる
            print(f'GIA接続の設定不備: {e}')
            flash('ログイン機能の設定が完了していません。管理者にご連絡ください。', 'error')
            return render_template('login.html', saved_email=email)

        if not account:
            flash('メールアドレスまたはパスワードが正しくありません', 'error')
            return render_template('login.html', saved_email=email)

        user = ensure_app_user(account['id'], account['email'])

        # ゲストノートの引き継ぎ
        guest_id = session.get('guest_user_id')
        if guest_id:
            migrate_guest_notes(guest_id, user['id'])
            session.pop('guest_user_id', None)

        _store_session(user, account['email'])
        return redirect(home_path())

    return render_template('login.html')


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """パスワード再設定メールの送信を受け付ける。

    認証の正本はGIAのSupabase Authなので、メールもSupabaseに送らせる。
    株アプリは自前のSMTPを持たない。
    """
    if request.method != 'POST':
        return render_template('forgot_password.html')

    email = (request.form.get('email') or '').strip()
    if not email:
        flash('メールアドレスを入力してください', 'error')
        return render_template('forgot_password.html')

    redirect_to = request.url_root.rstrip('/') + '/reset-password'
    try:
        gia_identity.send_password_reset(email, redirect_to)
    except gia_identity.GiaIdentityUnavailable as e:
        print(f'GIA接続の設定不備: {e}')
        flash('再設定機能の設定が完了していません。管理者にご連絡ください。', 'error')
        return render_template('forgot_password.html', saved_email=email)
    except RuntimeError as e:
        # レート制限やSMTP未設定。ここを「送信しました」と嘘をつくと、
        # 届かない理由を誰も追えなくなる。
        print(f'再設定メールの送信失敗 {email}: {e}')
        flash('メールを送信できませんでした。時間をおいて再度お試しいただくか、'
              '管理者にご連絡ください。', 'error')
        return render_template('forgot_password.html', saved_email=email)

    # 登録の有無は明かさない（未登録アドレスを判別できると総当たりの材料になる）
    return render_template('forgot_password.html', sent_to=email)


@app.route('/reset-password')
def reset_password():
    """メールのリンクから戻ってくる先。新しいパスワードを設定する。

    Supabaseは #access_token=... をURLのフラグメントに付けて返す。
    フラグメントはサーバーに送られないため、ここはページを返すだけで、
    トークンの取り出しと更新はブラウザ側で行う。
    """
    return render_template(
        'reset_password.html',
        gia_url=gia_identity.project_url(),
        gia_anon_key=gia_identity.anon_key())


@app.route('/register', methods=['GET', 'POST'])
def register():
    """ユーザー登録ページ"""
    # 既にログイン済みなら、その人が使える場所へ
    if session.get('user_id'):
        return redirect(home_path())

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
            # アカウントはGIA（キャンパス）側に作る。株アプリとキャンパスで
            # 別アカウントになると、課金しても株アプリが会員だと分からない。
            account = gia_identity.create_auth_user(email, password)
            user = ensure_app_user(
                account['id'], account['email'], name=name,
                referred_by_code=referral_code if referral_code else None)

            # ゲストノートの引き継ぎ
            guest_id = session.get('guest_user_id')
            if guest_id:
                migrate_guest_notes(guest_id, user['id'])
                session.pop('guest_user_id', None)

            _store_session(user, account['email'])
            return redirect(home_path())
        except gia_identity.GiaIdentityUnavailable as e:
            print(f'GIA接続の設定不備: {e}')
            flash('登録機能の設定が完了していません。管理者にご連絡ください。', 'error')
            return render_template('register.html', ref_code=referral_code,
                                   referrer_name=referrer_name, saved_name=name, saved_email=email)
        except ValueError as e:
            flash(str(e), 'error')
            return render_template('register.html', ref_code=referral_code,
                                   referrer_name=referrer_name, saved_name=name, saved_email=email)
        except Exception as e:
            message = str(e)
            if 'already' in message.lower() or 'registered' in message.lower():
                flash('このメールアドレスは既に登録されています', 'error')
            else:
                print(f'登録エラー: {e}')
                flash('登録に失敗しました。時間をおいて再度お試しください', 'error')
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
    import company_lookup

    normalized = normalize_code(code)
    company = get_screened_data(normalized) or {}

    # 会社名のままURLに乗ってくることがある（例 /stock/キオクシア）。
    # 検索欄が「銘柄コードまたは会社名」を受けるので、サジェストを選ばずに
    # Enterを押すと名前がそのままパスになる。空のページを見せる前にコードへ寄せる。
    if not company and company_lookup.looks_like_name(normalized):
        resolved = company_lookup.resolve(normalized)
        if resolved:
            return redirect(f'/stock/{resolved}')
        # 1つに決められないときは飛ばさず、候補を出して選んでもらう
        return render_template('stock_not_found.html',
                               reason='name', query=normalized,
                               candidates=company_lookup.suggest(normalized),
                               listed_name=None,
                               is_admin=session.get('user_role') == 'admin'), 404

    # コードは正しいがDBに行が無い場合。「取得に失敗した」と「まだ分析していない」は
    # 別物なので分けて伝える（data_gaps.py と同じ考え方）。
    if not company:
        listed = company_lookup.is_listed_code(normalized)
        return render_template('stock_not_found.html',
                               reason='listed' if listed else 'code',
                               query=normalized,
                               candidates=[],
                               listed_name=company_lookup.name_of(normalized),
                               is_admin=session.get('user_role') == 'admin'), 404

    # テーマは検索エンジンにも読ませたいのでサーバー側で出す。
    # テーマページへの相互リンクにもなり、銘柄ページ同士がつながる。
    tags = []
    try:
        rows = (get_supabase_client().table('stock_tag_map')
                .select('tag_name')
                .eq('company_code', normalized)
                .execute().data or [])
        names = [r['tag_name'] for r in rows]
        if names:
            # 業種を先に、テーマを後に。表示順を安定させる
            master = (get_supabase_client().table('stock_tags')
                      .select('name, kind, sort_order')
                      .in_('name', names[:100])
                      .execute().data or [])
            master.sort(key=lambda m: (0 if m.get('kind') == 'industry' else 1,
                                       m.get('sort_order') or 0))
            tags = [m['name'] for m in master]
    except Exception as e:
        print(f'テーマの取得エラー {normalized}: {e}')

    # ETF・REIT等は検索やスクリーナーからは辿れないが、URLを直接叩けば開ける。
    # 何も言わずに空のページを見せると「壊れている」と受け取られるので、
    # 事業会社ではないこと（＝このアプリの見方が当てはまらないこと）を明示する。
    from security_filter import EXCLUDED_CODES, is_non_operating_name
    is_fund = (normalized in EXCLUDED_CODES
               or is_non_operating_name(company.get('company_name')))

    # 上場廃止。株価は最終売買日で凍結されているので、そう書かずに数字だけ出すと
    # 「いまの株価」だと読まれる。2026年のTOB・MBOで5〜7月だけで22社あった。
    import delisting
    # 印の有無と最終売買日は別物。日足が1本も無い銘柄は日付が分からないが、
    # それでも「上場廃止である」ことは伝えなければならない
    # （分けていなかったため 2692 伊藤忠食品でバナーが丸ごと消えていた）。
    is_delisted = bool(company.get('delisted_at'))
    delisted_on = delisting.describe(company.get('delisted_at'))

    return render_template(
        'stock_detail.html',
        stock_code=code,
        company=company,
        tags=tags,
        is_fund=is_fund,
        is_delisted=is_delisted,
        delisted_on=delisted_on,
        # PRO Market は売買が成立しない日が続くのが正常。画面で説明しないと
        # 出来高ゼロを「データの欠落」と読まれる（2026-08-26 実際に誤診した）
        market_segment=company.get('market_segment'),
        is_logged_in=bool(session.get('user_id')),
        is_admin=session.get('user_role') == 'admin',
    )


# テーマページに載せる銘柄数の上限。
# 多すぎるとページが重くなり、少なすぎると一覧としての価値が出ない。
THEME_PAGE_LIMIT = 120

THEME_COLUMNS = (
    'company_code, company_name, industry_jp, market_segment, '
    'business_summary_jp, market_cap, stock_price, per_forward, pbr, '
    'roe, equity_ratio, dividend_yield, match_rate'
)


def _theme_codes(client, tag_name):
    """そのテーマが付いている銘柄コードを返す"""
    codes = []
    page = 0
    while page < 20:
        res = (client.table('stock_tag_map')
               .select('company_code')
               .eq('tag_name', tag_name)
               .range(page * 1000, page * 1000 + 999)
               .execute())
        rows = res.data or []
        codes.extend(r['company_code'] for r in rows)
        if len(rows) < 1000:
            break
        page += 1
    return codes


@app.route('/themes')
def themes_index():
    """テーマ・業種の索引ページ。

    ログイン不要。個別のテーマページへ辿れる導線がここしか無いため、
    クローラーにテーマページの存在を知らせる役割も兼ねる。
    """
    client = get_supabase_client()
    try:
        tags = (client.table('stock_tags')
                .select('name, kind, category, description, sort_order')
                .eq('display_active', True)
                .order('sort_order')
                .execute().data or [])
        counts = {}
        page = 0
        while page < 50:
            res = (client.table('stock_tag_map').select('tag_name')
                   .range(page * 1000, page * 1000 + 999).execute())
            rows = res.data or []
            for r in rows:
                counts[r['tag_name']] = counts.get(r['tag_name'], 0) + 1
            if len(rows) < 1000:
                break
            page += 1
    except Exception as e:
        print(f'テーマ索引の取得エラー: {e}')
        tags, counts = [], {}

    groups = {}
    for t in tags:
        n = counts.get(t['name'], 0)
        if not n:
            continue   # 該当0件のテーマは開いても空なので出さない
        cat = t.get('category') or 'その他'
        groups.setdefault(cat, {'category': cat, 'kind': t.get('kind'), 'tags': []})
        groups[cat]['tags'].append({**t, 'count': n})

    # 業種を先頭に置く。事実データで網羅性があり、入口として分かりやすい
    ordered = sorted(groups.values(),
                     key=lambda g: (0 if g['kind'] == 'industry' else 1,
                                    -len(g['tags'])))
    return render_template('themes.html', groups=ordered,
                           is_logged_in=bool(session.get('user_id')))


@app.route('/theme/<name>')
def theme_detail(name):
    """テーマ・業種ごとの銘柄一覧。

    ログイン不要。銘柄ページは相互リンクが薄く単独では発見されにくいので、
    テーマページが検索の入口と内部リンクの結節点を兼ねる。

    ⚠️ クローキング（検索エンジンにだけ内容を見せる）は規約違反になるため、
    未ログインユーザーとクローラーには必ず同じ内容を返すこと。
    """
    tag_name = (name or '').strip()
    client = get_supabase_client()

    tag = None
    rows = []
    total = 0
    try:
        found = (client.table('stock_tags')
                 .select('name, kind, category, description')
                 .eq('name', tag_name).limit(1).execute().data or [])
        tag = found[0] if found else None

        if tag:
            codes = _theme_codes(client, tag_name)
            total = len(codes)
            if codes:
                # 時価総額の大きい順。知られた会社が上に来るほうが読み手に親切
                from security_filter import exclude_delisted
                q = (client.table('screened_latest')
                     .select(THEME_COLUMNS)
                     .in_('company_code', codes[:2000])
                     .not_.is_('company_name', 'null'))
                # 上場廃止はテーマ一覧にも出さない（株価が凍結されており、
                # 時価総額順に並べると実態と合わない位置に居座る）
                res = (exclude_delisted(q)
                       .order('market_cap', desc=True)
                       .limit(THEME_PAGE_LIMIT)
                       .execute())
                rows = res.data or []
    except Exception as e:
        print(f'テーマページの取得エラー {tag_name}: {e}')

    html = render_template('theme_detail.html',
                           tag=tag, tag_name=tag_name, rows=rows,
                           total=total, shown=len(rows),
                           limit=THEME_PAGE_LIMIT,
                           is_logged_in=bool(session.get('user_id')))
    # 存在しないテーマで200を返すと、中身の無いページが検索結果に載ってしまう
    return (html, 200) if tag else (html, 404)


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

    from urllib.parse import quote

    urls = [(f'{base}/', '1.0'), (f'{base}/themes', '0.9')]
    try:
        client = get_supabase_client()

        # テーマページ。銘柄ページより内部リンクの結節点として重要なので
        # 優先度を高くしておく
        tags = (client.table('stock_tags').select('name')
                .eq('display_active', True).execute().data or [])
        for t in tags:
            urls.append((f'{base}/theme/{quote(t["name"], safe="")}', '0.9'))

        from security_filter import exclude_non_operating

        page = 0
        while page < 60:   # 上限を設けて暴走を防ぐ
            # ETF・REIT等はsitemapに載せない（中身が無いページをGoogleに申告しない）
            q = exclude_non_operating(
                client.table('screened_latest').select('company_code, analyzed_at'))
            res = q.range(page * 1000, page * 1000 + 999).execute()
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


@app.route('/compare')
def compare():
    """企業比較（2〜3社を並べる）。

    2026-08-25 に /search から切り出した。あのページは検索窓のほかに
    事業概要・財務データ5年・CF・財務健全性・主要株主/役員を持っていたが、
    **その5つは /stock/<code> と同じもの**だった。銘柄検索はヘッダーの窓へ、
    管理者の手入力は /admin/stock-data へ移し、ここには比較だけを残した。
    """
    guard = _require_login()
    if guard: return guard
    return render_template('compare.html')


@app.route('/search')
def search():
    """旧「企業情報」ページ。中身は /compare に移した。

    外から貼られたリンクとブックマークのために残す。消すと 404 になる。
    """
    return redirect('/compare', code=301)


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


@app.route('/admin/stock-data')
def admin_stock_data():
    """財務データの手入力（管理者専用）。

    2026-08-25 まで /search（企業情報ページ）の中にあり、しかも
    is_admin=True を固定で渡していたので**ログインしていれば誰にでも編集欄が
    出ていた**。保存先のAPIにも認証が無かった。管理の機能は管理のメニューへ。
    """
    guard = _require_admin()
    if guard: return guard
    return render_template('admin_stock_data.html')


@app.route('/admin/study-materials')
def admin_study_materials():
    """勉強会の資料・動画（管理者専用）。"""
    guard = _require_admin()
    if guard: return guard
    return render_template('admin_study_materials.html')


@app.route('/admin/themes')
def admin_themes():
    """テーマ運用画面（admin専用）。

    テーマの過剰/未使用の把握、手動でのタグ付け、細分化テーマの仕込み、
    表示ON/OFF、LLMによる候補提案を行う。
    """
    guard = _require_admin()
    if guard: return guard
    return render_template('admin_themes.html', is_admin=True)
