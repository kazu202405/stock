"""知人・紹介者向け /invite の回帰確認。"""

import os
import unittest

os.environ.setdefault('ENABLE_SCHEDULER', 'false')


class InvitePageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module

        app_module.app.config['TESTING'] = True
        cls.client = app_module.app.test_client()

    def test_page_is_public_and_points_to_invite_checkout(self):
        response = self.client.get('/invite')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('¥11,000', body)
        # 2026-09-12: 申し込みはアプリの中で始める（別ドメインだとログインし直しになる）。
        # 段は入口で決まるので、招待ページからは招待の段へ進む。
        self.assertIn('href="/upgrade?plan=invite"', body)
        self.assertIn('招待を受け取って参加する', body)
        self.assertNotIn('いますぐ参加する', body)
        self.assertNotIn('会員のご案内を見る', body)
        self.assertIn('noindex, nofollow', body)
        # 宣伝色を落とすため THE TOOL / HOST の節は撤去済み（再追加の検知）
        self.assertNotIn('THE TOOL', body)
        self.assertNotIn('主催者について', body)

    def test_headline_variants_change_only_the_entry_message(self):
        response = self.client.get('/invite?v=c')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('決算書を、', body)
        self.assertIn('経営者同士で読む。', body)
        self.assertIn('ご招待プランに', body)
        self.assertIn('含まれるもの', body)

    def test_inviter_name_is_sanitized_before_rendering(self):
        response = self.client.get('/invite?from=%E5%B1%B1%E7%94%B0%3Cscript%3E')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn('<script>', body)
        self.assertIn('山田scriptさんから届いたご案内', body)


if __name__ == '__main__':
    unittest.main()
