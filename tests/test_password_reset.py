"""パスワード再設定のリグレッション。

設計の要点:
  - 認証の正本はGIAのSupabase Auth。メールもSupabaseに送らせる
    （株アプリは自前のSMTPを持たない）
  - 再設定ページに渡してよいのは anon キーだけ。サービスロールキーを
    出すと、ページを開いた誰もが全ユーザーを操作できてしまう
  - 送信フォームは「そのメールが登録済みか」を漏らさない
"""

import os
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import gia_identity


def _read(name):
    return Path(name).read_text(encoding='utf-8')


class SendResetMailTest(unittest.TestCase):
    def test_passes_redirect_to_supabase(self):
        """redirect_to を渡さないと Site URL（gia2018.com）に飛ばされる"""
        client = unittest.mock.MagicMock()
        with unittest.mock.patch.object(gia_identity, 'get_auth_client',
                                        return_value=client):
            gia_identity.send_password_reset(
                ' user@example.com ', 'https://note.gia2018.com/reset-password')

        client.auth.reset_password_email.assert_called_once_with(
            'user@example.com', {'redirect_to': 'https://note.gia2018.com/reset-password'})

    def test_send_failure_is_not_swallowed(self):
        """レート制限やSMTP未設定を「送信しました」と表示すると原因を追えない"""
        client = unittest.mock.MagicMock()
        client.auth.reset_password_email.side_effect = Exception('rate limit exceeded')
        with unittest.mock.patch.object(gia_identity, 'get_auth_client',
                                        return_value=client):
            with self.assertRaises(RuntimeError):
                gia_identity.send_password_reset('user@example.com', 'https://x/y')


class ResetPageSecretsTest(unittest.TestCase):
    """ページに出してよい鍵とそうでない鍵"""

    def setUp(self):
        self.page = _read('templates/reset_password.html')

    def test_uses_anon_key_only(self):
        self.assertIn('gia_anon_key', self.page)

    def test_never_renders_the_service_role_key(self):
        self.assertNotIn('SERVICE_ROLE', self.page.upper())

    def test_does_not_leave_the_token_in_the_url(self):
        """履歴やリファラからトークンが漏れないようにする"""
        self.assertIn('history.replaceState', self.page)

    def test_not_indexed(self):
        self.assertIn('name="robots" content="noindex"', self.page)


class ForgotPageTest(unittest.TestCase):
    def setUp(self):
        self.page = _read('templates/forgot_password.html')

    def test_does_not_reveal_whether_the_address_is_registered(self):
        """登録済みかを判別できると、会員のアドレス探索に使われる"""
        for leak in ('登録されていません', 'このメールアドレスは登録', 'アカウントが見つかりません'):
            self.assertNotIn(leak, self.page)

    def test_not_indexed(self):
        self.assertIn('name="robots" content="noindex"', self.page)


class LoginEntryPointTest(unittest.TestCase):
    def test_login_page_links_to_reset(self):
        """入口が無いと、実装しても誰も辿り着けない"""
        self.assertIn('/forgot-password', _read('templates/login.html'))


class RoutesRegisteredTest(unittest.TestCase):
    def test_routes_exist(self):
        import app as app_module
        paths = {rule.rule for rule in app_module.app.url_map.iter_rules()}
        self.assertIn('/forgot-password', paths)
        self.assertIn('/reset-password', paths)

    def test_forgot_password_accepts_post(self):
        import app as app_module
        methods = {rule.rule: rule.methods
                   for rule in app_module.app.url_map.iter_rules()}
        self.assertIn('POST', methods['/forgot-password'])


if __name__ == '__main__':
    unittest.main()
