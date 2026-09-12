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
        self.assertEqual(response.headers['Location'],
                         '/login?next=%2Fupgrade%2Fcheckout')

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


class DoubleClickGuardTest(unittest.TestCase):
    """決済の用意に1〜3秒かかるので、押した合図を出して二度押しを止める。

    ⚠️ 連打しても二重契約にはならない（同じ引数なら同じセッションを使い回す）。
       ここで直すのは「押しても反応が無いように見える」こと。
    ⚠️ リンクのままにする。JavaScriptが動かない環境でも押せること。
    """

    def setUp(self):
        self.template = read(TEMPLATE)

    def test_the_button_shows_that_it_started(self):
        self.assertIn("button.textContent = '決済画面をご用意しています…'", self.template)

    def test_the_second_click_is_blocked(self):
        self.assertIn("dataset.started === '1'", self.template)
        self.assertIn('event.preventDefault()', self.template)

    def test_it_is_still_a_plain_link(self):
        """⚠️ JavaScript前提にしない（押せなくなる環境を作らない）。"""
        self.assertIn('<a id="checkoutButton" href="{{ checkout_path }}"', self.template)


class TierComesFromTheEntranceTest(unittest.TestCase):
    """⚠️ 段は「人」ではなく「入口」で決まる（2026-09-12 五島さん確認）。

    /invite（知人にだけ渡すURL・リアルの会あり）から来たら ¥11,000、
    トップ・会員案内から来たら ¥4,980（オンラインのみ）。
    それまでは「一度 /invite を踏んだ人にはアプリのどこでも ¥11,000」だったため、
    トップで ¥4,980 を見た人が、押した先で ¥11,000 を見ることになっていた。
    """

    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.app_module = app_module
        app_module.app.config['TESTING'] = True
        cls.root = __import__('models.root', fromlist=['root'])

    def _client(self):
        client = self.app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = '00000000-0000-4000-8000-000000000009'
            sess['user_role'] = 'user'
        for target, name in ((self.app_module, 'is_member_session'),
                             (self.root, 'is_member')):
            patcher = patch.object(target, name, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
        return client

    def test_the_invite_entrance_shows_the_invited_price(self):
        invite = self.app_module.MEMBERSHIP_TIERS['invite']
        body = self._client().get('/upgrade?plan=invite').get_data(as_text=True)
        self.assertIn(f"{invite['price_yen']:,}", body)
        self.assertIn('href="/upgrade/checkout?plan=invite"', body)

    def test_the_public_entrance_shows_the_public_price(self):
        """⚠️ 招待の印が付いた人でも、トップから来たら公開の段。"""
        public = self.app_module.MEMBERSHIP_TIERS['online']
        invite = self.app_module.MEMBERSHIP_TIERS['invite']
        with patch('supabase_client.get_invited_plan', return_value='invite'):
            body = self._client().get('/upgrade').get_data(as_text=True)
        self.assertIn(f"{public['price_yen']:,}", body)
        self.assertNotIn(f"{invite['price_yen']:,}", body)

    def test_an_unknown_plan_falls_back_to_the_public_one(self):
        public = self.app_module.MEMBERSHIP_TIERS['online']
        body = self._client().get('/upgrade?plan=premium').get_data(as_text=True)
        self.assertIn(f"{public['price_yen']:,}", body)

    def test_the_invite_page_points_at_the_invited_entrance(self):
        self.assertEqual(self.root.INVITE_CHECKOUT_URL, '/upgrade?plan=invite')

    def test_the_login_detour_keeps_the_plan(self):
        """⚠️ 段の指定を落とすと、招待の人がログイン後に公開の段で申し込む。"""
        response = self.app_module.app.test_client().get(
            '/upgrade/checkout?plan=invite')
        self.assertEqual(response.status_code, 302)
        self.assertIn('plan%3Dinvite', response.headers['Location'])

    def test_the_membership_gate_shows_the_public_tier(self):
        """アプリの中のゲートはオンライン流入の受け皿＝公開の段。"""
        invite = self.app_module.MEMBERSHIP_TIERS['invite']
        with patch('supabase_client.get_invited_plan', return_value='invite'):
            body = self._client().get('/membership').get_data(as_text=True)
        self.assertIn(f"{self.app_module.MEMBERSHIP_PRICE_YEN:,}", body)
        self.assertNotIn(f"{invite['price_yen']:,}", body)


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
