"""GIA（キャンパス）側の認証と会員情報を参照する。

なぜ株アプリの外に認証を出すのか:
    株アプリは独自の `app_users` にパスワードを持っていたが、キャンパス
    (gia-next) は別のSupabaseプロジェクトで Supabase Auth を使っている。
    同じ人が2つのアカウントを持つ状態で、キャンパスで課金しても株アプリは
    それを知る手段が無かった。課金は gia-next の Stripe に一本化したので、
    認証も同じ場所（auth.users）に寄せる。

役割分担:
    GIAプロジェクト   … 認証(auth.users) と 会員情報(applicants)
    株アプリのDB      … 銘柄データと、プロフィール・紹介ツリー(app_users)

    app_users は残すが、主キーを auth.users.id と同じUUIDにそろえる。
    パスワードは持たない（password_hash は移行期の名残で参照しない）。

管理者:
    gia-next は is_admin() というSQL関数にメールを直書きしているが、
    こちらは環境変数で持つ。変更のたびにmigrationを流す運用にしないため。
    既定値は gia-next の is_admin() と同じアドレスにそろえてある。
"""

import os
import threading
import time

from supabase import Client, create_client

DEFAULT_ADMIN_EMAILS = ('global.information.academy@gmail.com',)

_lock = threading.Lock()
_auth_client: Client = None
_admin_client: Client = None


class GiaIdentityUnavailable(Exception):
    """GIAプロジェクトへの接続情報が無い。

    設定漏れに気づかず「パスワードが違います」と表示してしまうと原因が
    分からなくなるため、認証失敗とは別の例外にする。
    """


def _env(name):
    value = (os.getenv(name) or '').strip()
    return value or None


def admin_emails() -> set:
    """管理者として扱うメールアドレス。カンマ区切りで複数指定できる。"""
    raw = _env('GIA_ADMIN_EMAILS')
    if not raw:
        return {e.lower() for e in DEFAULT_ADMIN_EMAILS}
    return {e.strip().lower() for e in raw.split(',') if e.strip()}


def is_admin_email(email) -> bool:
    return bool(email) and email.strip().lower() in admin_emails()


def get_auth_client() -> Client:
    """ログイン用。anonキーを使う（サービスロールで認証しない）。"""
    global _auth_client
    if _auth_client is not None:
        return _auth_client
    url, key = _env('GIA_SUPABASE_URL'), _env('GIA_SUPABASE_ANON_KEY')
    if not url or not key:
        raise GiaIdentityUnavailable(
            'GIA_SUPABASE_URL / GIA_SUPABASE_ANON_KEY が未設定です')
    with _lock:
        if _auth_client is None:
            _auth_client = create_client(url, key)
    return _auth_client


def get_admin_client() -> Client:
    """会員情報の参照とユーザー作成用。サービスロールキーを使う。"""
    global _admin_client
    if _admin_client is not None:
        return _admin_client
    url, key = _env('GIA_SUPABASE_URL'), _env('GIA_SUPABASE_SERVICE_ROLE_KEY')
    if not url or not key:
        raise GiaIdentityUnavailable(
            'GIA_SUPABASE_URL / GIA_SUPABASE_SERVICE_ROLE_KEY が未設定です')
    with _lock:
        if _admin_client is None:
            _admin_client = create_client(url, key)
    return _admin_client


def is_configured() -> bool:
    return bool(_env('GIA_SUPABASE_URL') and _env('GIA_SUPABASE_ANON_KEY'))


def project_url() -> str:
    """再設定ページがブラウザから直接叩くGIAプロジェクトのURL。"""
    return _env('GIA_SUPABASE_URL')


def anon_key() -> str:
    """anonキー。公開前提の値なので、再設定ページのJSに渡してよい。

    サービスロールキーは絶対に渡さない（渡すと誰でも全ユーザーを操作できる）。
    """
    return _env('GIA_SUPABASE_ANON_KEY')


def send_password_reset(email: str, redirect_to: str) -> None:
    """パスワード再設定メールを送る。

    メールはSupabase Authが送る。リンクは
    /auth/v1/verify?type=recovery&... を経由し、成功すると redirect_to に
    #access_token=... を付けて戻ってくる（implicitフロー）。

    重要: redirect_to は Supabase の Authentication > URL Configuration の
    Redirect URLs に登録されていないと無視され、Site URL に飛ばされる。

    Raises:
        GiaIdentityUnavailable: 接続情報が無いとき
        RuntimeError: 送信自体が失敗したとき（レート制限・SMTP未設定など）
    """
    client = get_auth_client()
    try:
        client.auth.reset_password_email(
            (email or '').strip(), {'redirect_to': redirect_to})
    except Exception as e:
        # 「そのメールは存在しない」は Supabase が返さない（存在の有無を
        # 漏らさないため）。ここに来るのは送信基盤側の失敗。
        raise RuntimeError(str(e)) from e


def sign_in(email: str, password: str) -> dict:
    """GIAのSupabase Authでログインする。

    Returns:
        成功: {'id': auth.users.id, 'email': ...}
        失敗: None（メールもパスワードも区別しない）

    Raises:
        GiaIdentityUnavailable: 接続情報が無いとき
    """
    client = get_auth_client()
    try:
        result = client.auth.sign_in_with_password(
            {'email': (email or '').strip(), 'password': password or ''})
    except Exception as e:
        # 認証失敗は例外で返ってくる。設定不備と混ぜないようここで握る。
        message = str(e).lower()
        if 'invalid' in message or 'credentials' in message or 'not confirmed' in message:
            return None
        print(f'GIA認証エラー: {e}')
        return None

    user = getattr(result, 'user', None)
    if not user or not getattr(user, 'id', None):
        return None
    return {'id': str(user.id), 'email': getattr(user, 'email', '') or ''}


# 会員として扱う plan。gia-next の lib/membership/plans.ts と揃える。
# 片方だけ足すと「キャンパスでは会員なのに株アプリでは非会員」になる。
MEMBERSHIP_PLANS = ('online', 'real', 'invite', 'premium')
# 過去の契約。現役の契約者がいるので締め出さない。
LEGACY_MEMBER_PLANS = ('terakoya',)

# 会員判定のキャッシュ。判定のたびにGIAへ問い合わせると、1ページ表示で
# 何度も外部リクエストが飛ぶ。
#
# 会員(True)と非会員(False)でTTLを変える理由:
#   会員になった瞬間に使えないのは体験として悪い。管理画面から段を付与しても
#   最大5分間「会員限定です」と言われ続けると、付与した側も本人も混乱する。
#   一方、会員が数分長く会員のままでも実害はない（解約直後に少し使える程度）。
#   非会員の判定は短くして、付与がすぐ効くようにする。
_MEMBERSHIP_TTL_SECONDS = 300
_NON_MEMBER_TTL_SECONDS = 30
_membership_cache = {}
_membership_cache_lock = threading.Lock()


def is_paid_member(user_id: str, use_cache: bool = True) -> bool:
    """有料会員か。画面・APIの出し分けはすべてこの関数を通す。

    判定は gia-next の isActiveMember() と同じ:
        plan が会員の段のいずれか / 旧テラこや / tier=='paid'

    subscription_status は見ない。管理側で手動付与した会員（Stripeの契約が
    無い無料枠）を締め出さないため。解約時は webhook が plan を外すので、
    plan を見れば足りる。
    """
    if not user_id:
        return False

    if use_cache:
        with _membership_cache_lock:
            hit = _membership_cache.get(user_id)
        if hit:
            ttl = _MEMBERSHIP_TTL_SECONDS if hit[1] else _NON_MEMBER_TTL_SECONDS
            if (time.time() - hit[0]) < ttl:
                return hit[1]

    m = get_membership(user_id)

    if m.get('error'):
        # 取得に失敗しただけ。非会員と断定しない。
        # 直前の判定が残っていればそれを使い、無ければ非会員として扱うが
        # **キャッシュしない**（次のリクエストで取り直す）。
        # ここで False を焼き付けると、通信が数秒詰まっただけで課金者が
        # 5分間締め出される。
        with _membership_cache_lock:
            stale = _membership_cache.get(user_id)
        return stale[1] if stale else False

    plan = (m.get('plan') or '').strip().lower()
    result = (plan in MEMBERSHIP_PLANS
              or plan in LEGACY_MEMBER_PLANS
              or m.get('tier') == 'paid')

    with _membership_cache_lock:
        _membership_cache[user_id] = (time.time(), result)
    return result


def clear_membership_cache(user_id: str = None):
    """課金直後など、キャッシュを待たずに反映したいときに使う。"""
    with _membership_cache_lock:
        if user_id:
            _membership_cache.pop(user_id, None)
        else:
            _membership_cache.clear()


def get_membership(user_id: str) -> dict:
    """キャンパスの会員情報を返す。

    applicants.id === auth.users.id なので、ログインで得たUUIDでそのまま引ける。
    機能の出し分けは is_paid_member() を使うこと（判定を1箇所に集約するため）。

    Returns:
        {'found': bool, 'plan': ..., 'subscription_status': ..., 'tier': ...,
         'is_active': bool}
    """
    empty = {'found': False, 'plan': None, 'subscription_status': None,
             'tier': None, 'is_active': False, 'error': False}
    if not user_id:
        return empty

    # 「取得できなかった」と「会員ではない」を区別する。
    # 同じ空データで返すと、GIAへの通信が一時的に落ちただけで
    # 課金している人が非会員として扱われ、しかもキャッシュに焼き付く。
    def failed(reason):
        print(f'会員情報の取得に失敗 {user_id}: {reason}')
        d = dict(empty)
        d['error'] = True
        return d

    try:
        client = get_admin_client()
        result = (client.table('applicants')
                  .select('plan, subscription_status, tier')
                  .eq('id', user_id).limit(1).execute())
    except GiaIdentityUnavailable as e:
        return failed(f'接続情報なし: {e}')
    except Exception as e:
        return failed(e)

    if not result.data:
        # 問い合わせは成功したが行が無い＝GIA側に会員登録が無い。これは失敗ではない。
        return empty
    row = result.data[0]
    return {
        'found': True,
        'plan': row.get('plan'),
        'subscription_status': row.get('subscription_status'),
        'tier': row.get('tier'),
        'is_active': row.get('subscription_status') == 'active',
        'error': False,
    }


def create_auth_user(email: str, password: str, confirm: bool = True) -> dict:
    """GIA側にユーザーを作る（新規登録・移行で使う）。

    Returns: {'id': ..., 'email': ...} / 既に存在する場合もそのユーザーを返す
    """
    client = get_admin_client()
    email = (email or '').strip()
    try:
        result = client.auth.admin.create_user({
            'email': email,
            'password': password,
            'email_confirm': confirm,
        })
        user = getattr(result, 'user', None) or result
        return {'id': str(user.id), 'email': getattr(user, 'email', email)}
    except Exception as e:
        if 'already' in str(e).lower():
            found = find_auth_user_by_email(email)
            if found:
                return found
        raise


def find_auth_user_by_email(email: str) -> dict:
    """メールから auth.users を引く。移行時の突き合わせに使う。"""
    email = (email or '').strip().lower()
    if not email:
        return None
    client = get_admin_client()
    page = 1
    while page <= 20:
        try:
            result = client.auth.admin.list_users(page=page, per_page=200)
        except TypeError:
            result = client.auth.admin.list_users()
            page = 99
        users = result if isinstance(result, list) else getattr(result, 'users', [])
        if not users:
            break
        for u in users:
            if (getattr(u, 'email', '') or '').lower() == email:
                return {'id': str(u.id), 'email': u.email}
        if len(users) < 200:
            break
        page += 1
    return None
