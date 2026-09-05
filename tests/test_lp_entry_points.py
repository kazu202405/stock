"""トップ（/）の入口ボタンが、未ログインの人でも進める先を指しているか。

2026-09-05 の事故:
  「無料で始める」が `/dashboard` を指していた。`/dashboard` は
  `_require_member()` なので、
    未ログイン        → /login（登録画面ではなくログイン画面）
    ログイン済み無料  → /membership =「この機能は会員限定です」
  無料で始めたい人に「会員限定です」と表示していた。

⚠️ **文字列で href を見張らない。** 行き先のガードが後から変わると、
   href は正しいまま意味だけ壊れる。**実際に踏んで、ログイン要求や
   会員案内に飛ばされないこと**で確かめる。
"""

import os
import re
import unittest

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

# 未ログインで踏んで良い（＝そこへ送るのが正しい）行き先
INTENTIONAL_LOGIN_LINKS = {'/login'}


class LandingPageEntryPointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module

        app_module.app.config['TESTING'] = True
        cls.app = app_module.app

    def _anonymous(self):
        """セッションを持たない、まっさらな訪問者。"""
        return self.app.test_client()

    def _lp_links(self):
        response = self._anonymous().get('/')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        links = re.findall(r'<a\s[^>]*href="(/[^"#?]*)"', body)
        # ⚠️ 0件でも通ってしまう形にしない。リンクを拾えなくなったら落とす。
        self.assertGreaterEqual(len(links), 3,
                                'LPのリンクを拾えていない（抽出が壊れている可能性）')
        return links

    def test_landing_page_ctas_are_reachable_without_an_account(self):
        blocked = []
        for href in sorted(set(self._lp_links())):
            if href in INTENTIONAL_LOGIN_LINKS:
                continue
            response = self._anonymous().get(href, follow_redirects=False)
            location = response.headers.get('Location', '')
            if response.status_code in (301, 302, 303, 307, 308) and (
                    '/login' in location or '/membership' in location):
                blocked.append(f'{href} -> {location}')

        self.assertEqual(blocked, [],
                         'トップのボタンが、未ログインでは進めない先を指している: '
                         + ', '.join(blocked))

    def test_the_free_signup_button_leads_to_registration(self):
        body = self._anonymous().get('/').get_data(as_text=True)
        match = re.search(r'<a\s[^>]*href="([^"]+)"[^>]*>\s*無料で始める\s*</a>', body)
        self.assertIsNotNone(match, '「無料で始める」ボタンが見つからない')
        self.assertEqual(match.group(1), '/register')

        # 登録画面そのものが未ログインで開けること
        self.assertEqual(self._anonymous().get('/register').status_code, 200)


if __name__ == '__main__':
    unittest.main()
