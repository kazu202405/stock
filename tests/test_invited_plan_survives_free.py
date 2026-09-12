"""招待の内容が「無料で使う」をまたいで残るか（2026-09-07）。

/invite（月額11,000円・Company Note + 講義録画 + 月1回の企業研究会）に
「まず無料ではじめる」を足した。ここで壊れやすいのは次の一点:

  無料で入った人がアプリ内の /membership を開いたとき、公開の段（4,980円）
  しか出てこないと、その人は**招待の段に二度と戻れない**。
  しかも画面はきれいに表示されるので、不具合として報告されない。

なので「招待を踏んだ人の /membership に招待の段が出るか」を全経路で見る。
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

USER_ID = '11111111-2222-3333-4444-555555555555'


class InvitedPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module
        import models.root as root

        cls.app_module = app_module
        cls.root = root
        app_module.app.config['TESTING'] = True

    def setUp(self):
        # DBは触らない。印の置き場だけを模して、経路のつながりを見る。
        self.stored = {}

        def fake_mark(user_id, plan):
            self.stored.setdefault(user_id, plan)  # 上書きしない
            return True

        def fake_get(user_id):
            return self.stored.get(user_id)

        for target in ('mark_invited_plan', 'get_invited_plan'):
            fake = fake_mark if target == 'mark_invited_plan' else fake_get
            patcher = patch(f'supabase_client.{target}', side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

        # /membership は「無料会員」にだけ出る画面
        for target, name in ((self.app_module, 'is_member_session'),
                             (self.root, 'is_member')):
            patcher = patch.object(target, name, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(self.root.gia_identity, 'clear_membership_cache')
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self):
        return self.app_module.app.test_client()

    def _sign_in(self, client, user_id=USER_ID):
        """画面を通さずログイン状態だけ作る（招待の付け替えは通らない）。"""
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['user_name'] = 'テスト'
            sess['user_role'] = 'user'

    def _log_in_for_real(self, client, user_id=USER_ID):
        """実際に POST /login を通す。

        ⚠️ セッションを手で書くのでは、招待の印を付け替える場所
           （_store_session）を通らない。**そこが今回つなげた所**なので、
           見張りは本物のログインを通す。
        """
        account = {'id': user_id, 'email': 'friend@example.com'}
        user = {'id': user_id, 'name': 'テスト', 'role': 'user'}
        with patch.object(self.root.gia_identity, 'sign_in',
                          return_value=account),              patch.object(self.root, 'ensure_app_user', return_value=user),              patch.object(self.root.gia_identity, 'is_admin_email',
                          return_value=False):
            return client.post('/login', data={
                'email': account['email'], 'password': 'pw'})

    # ── 本体：無料をまたいで残るか ────────────────────────

    def test_invited_visitor_who_signs_up_free_still_sees_the_invited_tier(self):
        """/invite → （無料の）ログイン → /membership に11,000円の段が出る。

        同じブラウザで続けて操作する、いちばん普通の流れをそのまま通す。
        """
        client = self._client()
        client.get('/invite')  # 未ログインなのでセッションに預かる
        self.assertIsNone(self.stored.get(USER_ID),
                          'まだ本人が分からない段階で行に書いている')

        self._log_in_for_real(client)
        self.assertEqual(self.stored.get(USER_ID), self.root.INVITE_PLAN,
                         '招待の印が本人の行に移っていない')

        body = client.get('/membership').get_data(as_text=True)

        invited = self.app_module.MEMBERSHIP_TIERS['invite']
        self.assertIn(f"{invited['price_yen']:,}", body,
                      '招待された人の会員案内に招待の金額が出ていない')
        self.assertIn(invited['upgrade_url'], body,
                      '申込先が招待の段になっていない')

    def test_a_plain_free_user_still_sees_the_public_tier(self):
        """招待を踏んでいない人まで11,000円にしない。"""
        client = self._client()
        self._sign_in(client, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
        body = client.get('/membership').get_data(as_text=True)

        public = self.app_module.MEMBERSHIP_TIERS['online']
        self.assertIn(f"{public['price_yen']:,}", body)
        self.assertIn(public['upgrade_url'], body)
        # 2026-09-12: 申込の行き先は段によらず同じ（/upgrade）になったので、
        # 「どの段を見せているか」は金額で見る。招待の金額が混ざっていないこと。
        self.assertNotIn(
            f"{self.app_module.MEMBERSHIP_TIERS['invite']['price_yen']:,}", body)

    def test_the_invited_tier_is_not_cheaper_by_accident(self):
        """招待の段が公開の段より安くなったら、招待は値引きになっている。

        並べて出さない設計の前提が崩れるので、金額をいじったら気づけるように。
        """
        tiers = self.app_module.MEMBERSHIP_TIERS
        self.assertGreater(tiers['invite']['price_yen_tax_in'],
                           tiers['online']['price_yen_tax_in'])

    # ── 経路の抜け ────────────────────────────────────

    def test_every_tier_the_database_allows_exists_in_the_app(self):
        """DBが許す段は、必ず app.py 側に定義があること。

        ⚠️ 無い段を書くと membership_tier_for() が黙って公開の段に落とす。
           エラーは出ず、招待した本人には「なぜか安いほうしか出ない」と
           しか見えない。SQLの CHECK と MEMBERSHIP_TIERS を突き合わせる。
        """
        import re
        sql_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'supabase', 'migration_app_users_invited_plan.sql')
        with open(sql_path, encoding='utf-8') as f:
            sql = f.read()
        # CHECK (... IN ('invite')) の中身だけを取る
        match = re.search(r"invited_plan\s+IN\s*\(([^)]*)\)", sql)
        self.assertIsNotNone(match, 'CHECK 制約が読み取れない')
        allowed = set(re.findall(r"'([^']+)'", match.group(1)))
        self.assertTrue(allowed, '許可された段が1つも無い')
        for plan in allowed:
            self.assertIn(plan, self.app_module.MEMBERSHIP_TIERS,
                          f'DBは {plan} を許すのに MEMBERSHIP_TIERS に無い')

    def test_the_plan_written_by_the_invite_page_is_a_real_tier(self):
        self.assertIn(self.root.INVITE_PLAN, self.app_module.MEMBERSHIP_TIERS)

    def test_the_marker_is_not_overwritten_by_a_later_visit(self):
        """先にもらった招待を、あとから踏んだURLで上書きしない。"""
        import supabase_client
        supabase_client.mark_invited_plan(USER_ID, 'invite')
        supabase_client.mark_invited_plan(USER_ID, 'premium')
        self.assertEqual(self.stored[USER_ID], 'invite')

    def test_signed_in_visitors_are_not_offered_a_dead_signup_button(self):
        """ログイン済みの人に「まず無料ではじめる」を出さない（押しても空振り）。"""
        client = self._client()
        self._sign_in(client)
        body = client.get('/invite').get_data(as_text=True)
        self.assertNotIn('まず無料で中を見る', body)
        self.assertNotIn('会員のご案内を見る', body)
        self.assertIn('招待を受け取って参加する', body)
        self.assertIn('href="/upgrade"', body)

    def test_the_free_door_exists_for_new_visitors(self):
        body = self._client().get('/invite').get_data(as_text=True)
        self.assertIn('まず無料で中を見る', body)
        self.assertIn('/register', body)
        self.assertIn('/login', body)
        # 有料の扉も消さない
        self.assertIn('href="/upgrade"', body)


if __name__ == '__main__':
    unittest.main()
