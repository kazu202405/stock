# -*- coding: utf-8 -*-
"""Company Note の中の申込ページ /upgrade（2026-09-12）。

これまでは会員案内から gia2018.com/upgrade へ送っていた。あちらは
セミナーや人の繋がりから来た人に向けた別のページで、言葉づかいも違ううえ、
別ドメインなので Cookie が別＝Company Note にログイン済みの人にも
ログインし直しを求めていた。申込ページをアプリの中に置く。

⚠️ **金額をテンプレートに直書きしない。** 同じ数字が GIA の /upgrade・
   /plans・招待ページ・/membership にもある。直書きすると値上げのときに漏れる。
"""

import os
import re
import unittest
from unittest.mock import patch

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, 'templates', 'upgrade.html')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _without_comments(source):
    """Jinjaの注意書き（{# ... #}）を落とす。

    ⚠️ 見張りが自分の注意書きに引っかかると、書いた本人が原因を探すことになる。
    """
    return re.sub(r'\{#.*?#\}', '', source, flags=re.S)


class UpgradePageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module

        cls.app_module = app_module
        app_module.app.config['TESTING'] = True

    def _client(self, member=False):
        client = self.app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 'test-user'
            sess['user_name'] = 'テスト'
            sess['user_role'] = 'user'
        root = __import__('models.root', fromlist=['root'])
        for target, name in ((self.app_module, 'is_member_session'),
                             (root, 'is_member')):
            patcher = patch.object(target, name, return_value=member)
            patcher.start()
            self.addCleanup(patcher.stop)
        return client

    def test_anyone_can_read_it_without_an_account(self):
        """⚠️ 読む前にログインを求めない。

        2026-09-12: 最初はログイン必須にしていたが、トップの料金欄からも
        ここへ来るので、アカウントの無い人が「会員限定です」の手前で
        ログイン画面に当たっていた（GIA側で同じ失敗をしている）。
        中身と金額は誰でも読めて、ログインが要るのは押したあと。
        """
        response = self.app_module.app.test_client().get('/upgrade')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(f'{self.app_module.MEMBERSHIP_PRICE_YEN:,}', body)
        self.assertIn('href="/upgrade/checkout"', body)

    def test_pressing_it_asks_for_a_login_and_comes_back_to_the_same_place(self):
        """⚠️ 戻り先を申込ページにしない。もう一度押させることになる。"""
        response = self.app_module.app.test_client().get('/upgrade/checkout')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/login?next=/upgrade/checkout')

    def test_members_do_not_see_it(self):
        response = self._client(member=True).get('/upgrade')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/dashboard', response.headers['Location'])

    def test_it_shows_the_price_and_the_button(self):
        body = self._client().get('/upgrade').get_data(as_text=True)
        self.assertIn(f'{self.app_module.MEMBERSHIP_PRICE_YEN:,}', body)
        self.assertIn(f'{self.app_module.MEMBERSHIP_PRICE_YEN_TAX_IN:,}', body)
        # 押した先もアプリの中（別ドメインの申込ページへ飛ばさない）。
        # ⚠️ ページ全体から gia2018.com を探さない。共通レイアウトには解約の
        #    入口（gia2018.com/members/app/settings）が正当に入っている。
        self.assertIn('href="/upgrade/checkout"', body)
        self.assertNotIn('gia2018.com/upgrade', body)
        # ⚠️ 注意書きは走査しない。テンプレートには「gia2018.com へ送らない」と
        #    いう戒めが書いてあり、そのままだとテストが自分の注意書きで落ちる。
        self.assertNotIn('gia2018.com', _without_comments(read(TEMPLATE)))

    def test_the_price_is_not_hardcoded_in_the_template(self):
        source = re.sub(r'\{#.*?#\}', '', read(TEMPLATE), flags=re.S)
        prices = (self.app_module.MEMBERSHIP_PRICE_YEN,
                  self.app_module.MEMBERSHIP_PRICE_YEN_TAX_IN)
        stray = [f'{p:,}' for p in prices
                 if f'{p:,}' in source or str(p) in source]
        self.assertEqual(stray, [], 'テンプレートに金額が直書きされている')

    def test_the_feature_list_is_shared_with_the_membership_page(self):
        """⚠️ 2箇所に書くと、機能を足したとき片方が古いまま残る。"""
        root = __import__('models.root', fromlist=['root'])
        self.assertGreaterEqual(len(root.MEMBER_FEATURES), 5)
        source = read(os.path.join(ROOT, 'models', 'root.py'))
        self.assertEqual(source.count('member_features=MEMBER_FEATURES'), 2)


class NextPathTest(unittest.TestCase):
    """ログイン・登録を挟んでも元の画面へ戻す。"""

    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.app_module = app_module
        cls.root = __import__('models.root', fromlist=['root'])

    def _next_for(self, query):
        with self.app_module.app.test_request_context('/login' + query):
            return self.root.safe_next_path()

    def test_an_internal_path_is_kept(self):
        self.assertEqual(self._next_for('?next=/upgrade'), '/upgrade')

    def test_an_external_url_is_refused(self):
        """⚠️ 外部URLを受けるとフィッシングの踏み台になる。"""
        for bad in ('?next=https://evil.example.com',
                    '?next=//evil.example.com',
                    '?next=javascript:alert(1)'):
            self.assertEqual(self._next_for(bad), '', bad)

    def test_both_forms_carry_it(self):
        for name in ('login.html', 'register.html'):
            body = read(os.path.join(ROOT, 'templates', name))
            self.assertIn('name="next" value="{{ next_path }}"', body, name)

    def test_login_and_register_send_the_person_back(self):
        source = read(os.path.join(ROOT, 'models', 'root.py'))
        self.assertEqual(source.count('redirect(safe_next_path() or home_path())'), 2)


if __name__ == '__main__':
    unittest.main()
