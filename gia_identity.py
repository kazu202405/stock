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


def get_membership(user_id: str) -> dict:
    """キャンパスの会員情報を返す。

    applicants.id === auth.users.id なので、ログインで得たUUIDでそのまま引ける。
    株アプリはこの値で機能を制限しない（2026-08-08時点で課金による機能差は無い）。
    将来リアル会限定の機能を出すときに、ここを見て判定する。

    Returns:
        {'found': bool, 'plan': ..., 'subscription_status': ..., 'tier': ...,
         'is_active': bool}
    """
    empty = {'found': False, 'plan': None, 'subscription_status': None,
             'tier': None, 'is_active': False}
    if not user_id:
        return empty
    try:
        client = get_admin_client()
        result = (client.table('applicants')
                  .select('plan, subscription_status, tier')
                  .eq('id', user_id).limit(1).execute())
    except GiaIdentityUnavailable:
        return empty
    except Exception as e:
        print(f'会員情報の取得エラー {user_id}: {e}')
        return empty

    if not result.data:
        return empty
    row = result.data[0]
    return {
        'found': True,
        'plan': row.get('plan'),
        'subscription_status': row.get('subscription_status'),
        'tier': row.get('tier'),
        'is_active': row.get('subscription_status') == 'active',
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
