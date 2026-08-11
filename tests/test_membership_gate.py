"""会員ゲートのリグレッション。

設計の要点:
  - 判定は gia_identity.is_paid_member() に集約（gia-next の isActiveMember と同義）
  - subscription_status は見ない。管理側で手動付与した無料枠を締め出さないため
  - **取得失敗と非会員を混同しない**。GIAへの通信が一瞬落ちただけで
    課金者が締め出され、しかもキャッシュに焼き付くのを防ぐ
"""

import os
import unittest
import unittest.mock

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import gia_identity


def _membership(plan=None, tier=None, error=False):
    return {'found': not error, 'plan': plan, 'subscription_status': None,
            'tier': tier, 'is_active': False, 'error': error}


class IsPaidMemberTest(unittest.TestCase):
    def setUp(self):
        gia_identity.clear_membership_cache()

    def _check(self, membership, user='u1'):
        with unittest.mock.patch.object(
                gia_identity, 'get_membership', return_value=membership):
            return gia_identity.is_paid_member(user, use_cache=False)

    def test_membership_plans_are_members(self):
        for plan in ('online', 'real', 'invite', 'premium'):
            self.assertTrue(self._check(_membership(plan=plan)), plan)

    def test_legacy_contracts_are_not_locked_out(self):
        """現役の契約者がいるので締め出さない"""
        self.assertTrue(self._check(_membership(plan='terakoya')))
        self.assertTrue(self._check(_membership(tier='paid')))

    def test_free_user_is_not_a_member(self):
        self.assertFalse(self._check(_membership()))
        self.assertFalse(self._check(_membership(plan='')))

    def test_unknown_plan_is_not_a_member(self):
        self.assertFalse(self._check(_membership(plan='someday')))

    def test_manual_grant_without_stripe_counts(self):
        """subscription_status を見ないので、手動付与の無料枠も会員になる"""
        m = _membership(plan='online')
        m['subscription_status'] = None
        self.assertTrue(self._check(m))


class LookupFailureTest(unittest.TestCase):
    """取得失敗を「非会員」と決めつけない"""

    def setUp(self):
        gia_identity.clear_membership_cache()

    def test_failure_does_not_overwrite_a_known_member(self):
        with unittest.mock.patch.object(
                gia_identity, 'get_membership',
                return_value=_membership(plan='online')):
            self.assertTrue(gia_identity.is_paid_member('u2'))

        # 通信が落ちても、直前に会員だと分かっている人は会員のまま
        with unittest.mock.patch.object(
                gia_identity, 'get_membership',
                return_value=_membership(error=True)):
            self.assertTrue(gia_identity.is_paid_member('u2', use_cache=False))

    def test_failure_is_not_cached(self):
        """失敗を焼き付けると、復旧しても数分間締め出されたままになる"""
        with unittest.mock.patch.object(
                gia_identity, 'get_membership',
                return_value=_membership(error=True)):
            self.assertFalse(gia_identity.is_paid_member('u3'))

        with unittest.mock.patch.object(
                gia_identity, 'get_membership',
                return_value=_membership(plan='real')) as m:
            self.assertTrue(gia_identity.is_paid_member('u3'))
            m.assert_called()  # キャッシュではなく取り直している


class GetMembershipErrorFlagTest(unittest.TestCase):
    def test_missing_connection_is_reported_as_error(self):
        with unittest.mock.patch.object(
                gia_identity, 'get_admin_client',
                side_effect=gia_identity.GiaIdentityUnavailable('no env')):
            self.assertTrue(gia_identity.get_membership('u4')['error'])

    def test_absent_row_is_not_an_error(self):
        """問い合わせは成功したが行が無い＝GIAに登録が無いだけ"""
        client = unittest.mock.MagicMock()
        client.table.return_value.select.return_value.eq.return_value \
            .limit.return_value.execute.return_value.data = []
        with unittest.mock.patch.object(
                gia_identity, 'get_admin_client', return_value=client):
            result = gia_identity.get_membership('u5')
        self.assertFalse(result['error'])
        self.assertFalse(result['found'])


if __name__ == '__main__':
    unittest.main()
