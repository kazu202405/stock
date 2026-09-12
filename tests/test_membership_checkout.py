# -*- coding: utf-8 -*-
"""会員の申し込みを Company Note の中で始める（2026-09-12）。

これまでは公開の段も招待の段も gia2018.com の申込ページへ送っていた。
別ドメインなので Cookie が別＝Company Note にログイン済みの人にも
ログインし直しを求めていた。申し込みはこのアプリの中で始め、支払いのときだけ
Stripe へ出る。会員の印を書くのは、これまで通り gia-next の webhook。
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


class MetadataTest(unittest.TestCase):
    """⚠️ webhook が読む印。1つでも欠けると「課金は通ったのに会員にならない」。"""

    def test_it_matches_what_the_webhook_reads(self):
        source = read('membership_checkout.py')
        for key in ("'purpose': 'membership'", "'plan': plan", "'user_id': user_id"):
            self.assertIn(key, source, key)
        # 更新・解約は subscription で飛んでくるので、そちらにも同じ印が要る
        self.assertIn("'subscription_data': {'metadata': metadata}", source)

    def test_the_plans_match_the_gia_side(self):
        import membership_checkout
        self.assertEqual(sorted(membership_checkout.PLAN_PRICE_ENV),
                         ['invite', 'online'])

    def test_an_unknown_plan_is_refused(self):
        import membership_checkout
        with self.assertRaises(membership_checkout.CheckoutUnavailable):
            membership_checkout._price_id('terakoya')


class GuardTest(unittest.TestCase):
    """二重契約と、状態が読めないときの扱い。"""

    def setUp(self):
        import membership_checkout
        self.mc = membership_checkout

    def _create(self, membership):
        with patch.object(self.mc.gia_identity, 'get_membership',
                          return_value=membership):
            return self.mc.create_checkout(
                'online', 'user-1', 'a@example.com', 'https://x/ok', 'https://x/ng')

    def test_an_active_member_is_refused(self):
        with self.assertRaises(self.mc.AlreadyMember):
            self._create({'plan': 'online', 'subscription_status': 'active',
                          'error': False})

    def test_it_stops_when_the_state_cannot_be_read(self):
        """⚠️ 分からないまま作ると、同じ人に2本の契約が立つ。"""
        with self.assertRaises(self.mc.CheckoutUnavailable):
            self._create({'plan': None, 'subscription_status': None, 'error': True})

    def test_a_missing_key_is_reported_as_unavailable(self):
        """設定漏れで生の500を出さない（画面は「準備中」に倒す）。"""
        with patch.dict(os.environ, {self.mc.SECRET_ENV: ''}, clear=False):
            with self.assertRaises(self.mc.CheckoutUnavailable):
                self._create({'plan': None, 'subscription_status': None,
                              'error': False})


class IdempotencyKeyTest(unittest.TestCase):
    """連打対策の鍵が、その人を締め出さないこと（2026-09-12 本番で発生）。

    Stripe: "Keys for idempotent requests can only be used with the same
    parameters they were first used with."
    鍵を日付だけで区切っていたため、戻り先の作りを変えた日に、同じ人が
    その日いっぱい「決済の準備中です」になり申し込めなかった。
    """

    def setUp(self):
        import membership_checkout
        self.mc = membership_checkout

    def test_the_key_changes_when_the_parameters_change(self):
        base = {'mode': 'subscription', 'success_url': 'https://x/ok'}
        changed = {'mode': 'subscription', 'success_url': 'https://x/ok2'}
        self.assertNotEqual(self.mc._idempotency_key('online', 'u1', base),
                            self.mc._idempotency_key('online', 'u1', changed))

    def test_the_same_request_keeps_the_same_key(self):
        """同じ引数での連打は、同じセッションを使い回す（増やさない）。"""
        params = {'mode': 'subscription', 'success_url': 'https://x/ok'}
        self.assertEqual(self.mc._idempotency_key('online', 'u1', params),
                         self.mc._idempotency_key('online', 'u1', dict(params)))

    def test_different_people_get_different_keys(self):
        params = {'mode': 'subscription'}
        self.assertNotEqual(self.mc._idempotency_key('online', 'u1', params),
                            self.mc._idempotency_key('online', 'u2', params))

    def test_a_key_clash_is_retried_without_the_key(self):
        """⚠️ 万一衝突しても申し込めること（締め出さない）。"""
        calls = []

        class FakeSession:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                if 'idempotency_key' in kwargs:
                    raise Exception(
                        'Keys for idempotent requests can only be used with '
                        'the same parameters they were first used with.')
                return {'url': 'https://checkout.stripe.com/c/pay/cs_test_1'}

        class FakeStripe:
            checkout = type('c', (), {'Session': FakeSession})

        with patch.object(self.mc, '_client', return_value=FakeStripe()), \
             patch.object(self.mc, '_price_id', return_value='price_x'), \
             patch.object(self.mc, '_customer_id', return_value=None), \
             patch.object(self.mc.gia_identity, 'get_membership',
                          return_value={'plan': None, 'subscription_status': None,
                                        'error': False}):
            url = self.mc.create_checkout('online', 'u1', 'a@example.com',
                                          'https://x/ok', 'https://x/ng')
        self.assertIn('checkout.stripe.com', url)
        self.assertEqual(len(calls), 2, '鍵なしでの作り直しが行われていない')
        self.assertNotIn('idempotency_key', calls[1])


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.app_module = app_module
        app_module.app.config['TESTING'] = True
        cls.root = __import__('models.root', fromlist=['root'])

    def _client(self, member=False):
        client = self.app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 'test-user'
            sess['user_email'] = 'test@example.com'
            sess['user_role'] = 'user'
        for target, name in ((self.app_module, 'is_member_session'),
                             (self.root, 'is_member')):
            patcher = patch.object(target, name, return_value=member)
            patcher.start()
            self.addCleanup(patcher.stop)
        return client

    def test_it_sends_the_person_to_stripe(self):
        with patch('membership_checkout.create_checkout',
                   return_value='https://checkout.stripe.com/c/pay/cs_test_123') as create:
            response = self._client().get('/upgrade/checkout')
        self.assertEqual(response.status_code, 303)
        self.assertIn('checkout.stripe.com', response.headers['Location'])
        # 戻り先はこのアプリの中
        kwargs = create.call_args.kwargs
        self.assertIn('/upgrade/complete', kwargs['success_url'])
        self.assertIn('/upgrade', kwargs['cancel_url'])

    def test_the_plan_comes_from_the_entrance(self):
        """段は入口で決まる（2026-09-12 五島さん確認）。

        /invite から来たら invite（¥11,000・リアルの会あり）、
        それ以外は online（¥4,980）。

        ⚠️ **URLで選べるのは高いほうだけ**、という向きを保つこと。
           安い段をURLで選ばせる形にすると、招待した人が値引きで入れてしまう。
        """
        source = read(os.path.join('models', 'root.py'))
        start = source.index('def _requested_tier():')
        block = source[start:source.index('@app.route(\'/upgrade\')', start)]
        self.assertIn("request.args.get('plan')", block)
        self.assertIn('DEFAULT_MEMBERSHIP_TIER', block)

        import app as app_module
        tiers = app_module.MEMBERSHIP_TIERS
        self.assertGreater(tiers['invite']['price_yen_tax_in'],
                           tiers[app_module.DEFAULT_MEMBERSHIP_TIER]['price_yen_tax_in'],
                           'URLで選べる段が既定より安い＝値引きになっている')

    def test_the_plan_reaches_stripe(self):
        """⚠️ 画面で招待の段を出したのに、決済が公開の段では意味が無い。"""
        with patch('membership_checkout.create_checkout',
                   return_value='https://checkout.stripe.com/c/pay/cs_test_9') as create:
            self._client().get('/upgrade/checkout?plan=invite')
        self.assertEqual(create.call_args.args[0], 'invite')

    def test_it_needs_a_login(self):
        """⚠️ 戻り先はここ（押した場所）。申込ページに戻すと、もう一度押させる。"""
        response = self.app_module.app.test_client().get('/upgrade/checkout')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'],
                         '/login?next=%2Fupgrade%2Fcheckout')

    def test_members_are_not_charged_twice(self):
        response = self._client(member=True).get('/upgrade/checkout')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/dashboard', response.headers['Location'])

    def test_a_failure_falls_back_to_a_calm_message(self):
        import membership_checkout
        with patch('membership_checkout.create_checkout',
                   side_effect=membership_checkout.CheckoutUnavailable('鍵が無い')):
            response = self._client().get('/upgrade/checkout')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/upgrade', response.headers['Location'])

    def test_the_reason_is_shown_on_the_page(self):
        """⚠️ 黙って同じ画面に戻さない（2026-09-12 本番で実際に起きた）。

        決済を作れないとき /upgrade へ戻しているが、申込ページが
        メッセージを出していなかったため、押した人には「押しても同じ画面に
        戻るだけ」に見えていた。原因が分からず、こちらにも伝わらない。
        """
        import membership_checkout
        client = self._client()
        with patch('membership_checkout.create_checkout',
                   side_effect=membership_checkout.CheckoutUnavailable('鍵が無い')):
            body = client.get('/upgrade/checkout',
                              follow_redirects=True).get_data(as_text=True)
        self.assertIn('決済の準備中', body)
        # テンプレート側にメッセージの出し口があること（次に消されないように）
        self.assertIn('get_flashed_messages', read(os.path.join('templates', 'upgrade.html')))

    def test_the_completion_page_waits_for_the_webhook(self):
        """⚠️ 支払い直後は会員の印がまだ付いていない。失敗と読ませない。"""
        with patch('membership_checkout.session_is_paid', return_value=True):
            body = self._client().get(
                '/upgrade/complete?session_id=cs_test_1').get_data(as_text=True)
        self.assertIn('お支払いを確認しました', body)
        self.assertIn('反映', body)


if __name__ == '__main__':
    unittest.main()
