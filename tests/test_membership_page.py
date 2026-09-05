"""会員案内 /membership と、その周りのゲート（2026-09-06）。

ゲートに当たった人が全員たどり着く受け皿。以前はここに金額が無く、
「会員のご案内を見る」で別ドメインへ送っていたため、いくらか知るまでに
3画面かかっていた。

⚠️ **金額をテンプレートに直書きしない。** 同じ数字が GIA の /upgrade・
   /plans・招待ページにもある。直書きすると値上げのときに漏れる。
"""

import os
import re
import unittest
from unittest.mock import patch

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

MEMBERSHIP_TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'templates', 'membership.html')


class MembershipPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module

        cls.app_module = app_module
        app_module.app.config['TESTING'] = True

    def _free_client(self):
        client = self.app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 'test-free-user'
            sess['user_name'] = 'テスト'
            sess['user_role'] = 'user'
        for target in (self.app_module, __import__('models.root', fromlist=['root'])):
            name = 'is_member_session' if target is self.app_module else 'is_member'
            patcher = patch.object(target, name, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
        return client

    def test_the_price_is_shown(self):
        body = self._free_client().get('/membership').get_data(as_text=True)
        self.assertIn(f'{self.app_module.MEMBERSHIP_PRICE_YEN:,}', body,
                      '会員案内に金額が出ていない')
        self.assertIn(f'{self.app_module.MEMBERSHIP_PRICE_YEN_TAX_IN:,}', body,
                      '税込の金額が出ていない')

    def test_the_price_is_not_hardcoded_in_the_template(self):
        """値上げのとき、定数を直せば画面も変わること。

        ⚠️ 探すのは金額そのものだけ。桁数で探すとCSSの色（#1b4332）を拾い、
           誤検知だらけの見張りは誰も見なくなる。
        """
        with open(MEMBERSHIP_TEMPLATE, encoding='utf-8') as f:
            source = f.read()
        source = re.sub(r'\{#.*?#\}', '', source, flags=re.S)  # 注意書きは走査しない

        prices = (self.app_module.MEMBERSHIP_PRICE_YEN,
                  self.app_module.MEMBERSHIP_PRICE_YEN_TAX_IN)
        stray = [f'{p:,}' for p in prices
                 if f'{p:,}' in source or str(p) in source]
        self.assertEqual(stray, [],
                         'テンプレートに金額が直書きされている: ' + ', '.join(stray))

    def test_the_apply_button_goes_straight_to_checkout(self):
        body = self._free_client().get('/membership').get_data(as_text=True)
        self.assertIn(f'href="{self.app_module.UPGRADE_URL}"', body)

    def test_the_sample_report_is_open_to_free_users(self):
        """見本は「まだ会員でない人」に見せるためのもの。"""
        self.assertEqual(
            self._free_client().get('/report/sample').status_code, 200)

    def test_the_report_picker_is_still_members_only(self):
        """見本を開けても、実銘柄のレポートは会員のまま。"""
        response = self._free_client().get('/report', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/membership', response.headers.get('Location', ''))

    def test_the_simulator_is_not_offered_to_free_users(self):
        """押しても門前払いになるメニューを出さない。

        ⚠️ PC のドロップダウンとスマホのスライドメニューの両方を見る。
           片方だけ隠すと、スマホからは見えたままになる。
        """
        body = self._free_client().get('/learning').get_data(as_text=True)
        self.assertNotIn('href="/simulator"', body)


if __name__ == '__main__':
    unittest.main()
