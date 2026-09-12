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

    def test_landing_page_explains_the_product_community_and_story(self):
        """トップだけで「何を見る・どう使う・なぜ作った」が分かる。"""
        body = self._anonymous().get('/').get_data(as_text=True)
        visible_text = re.sub(r'<[^>]+>', '', body)

        for copy in (
            '投資する前に、',
            '会社を知る。',
            '会社の実力を、ひと目で。',
            '企業分析に、',
            'みんなの視点を。',
            '公開ノートと質問・回答をすべて読む',
            'なぜ、アプリまで作ったのか。',
            '五島 一将',
        ):
            self.assertIn(copy, visible_text)

    def test_hero_uses_the_requested_images_and_plan_anchor(self):
        """ヒーローは料金欄へ進み、登録CTAを重ねて置かない。"""
        body = self._anonymous().get('/').get_data(as_text=True)
        hero = body.split('<section class="hero">', 1)[1].split('</section>', 1)[0]

        self.assertIn('href="#plan">オンラインプランを見る</a>', hero)
        self.assertNotIn('会社を調べてみる', hero)
        self.assertNotIn('無料で始める', hero)
        self.assertNotIn('登録なしでも、銘柄検索と基本情報をご覧いただけます。', hero)
        self.assertIn('/static/images/lp/kioxia-company-overview.png', hero)
        self.assertIn('/static/images/lp/financial-trends.png', body)
        self.assertNotIn('会社を調べてみる', body)
        self.assertNotIn('product-window::before', body)

    def test_landing_page_price_comes_from_the_public_tier(self):
        """LPの金額だけが決済・会員案内とずれない。"""
        import app as app_module

        body = self._anonymous().get('/').get_data(as_text=True)
        tier = app_module.MEMBERSHIP_TIERS['online']
        self.assertIn(f"¥{tier['price_yen']:,}", body)
        # ⚠️ 税込の額は、金額と違うときだけ添える。Stripeの価格は税込なので、
        #    同じ額を「税込」としてもう一度出すと、払う額が2つあるように読める。
        # ⚠️ 探すのは重複表示そのもの（「税込 ¥4,980」）。ページ全体から
        #    「税込」を探すと、規約の文言など無関係な箇所を拾って落ちる。
        if tier['price_yen_tax_in'] != tier['price_yen']:
            self.assertIn(f"税込 ¥{tier['price_yen_tax_in']:,}", body)
        else:
            self.assertNotIn(f"税込 ¥{tier['price_yen']:,}", body,
                             '同じ額を税込としてもう一度出している')
        self.assertIn(tier['upgrade_url'], body)
        # 公開プランにはリアル会・会場費の別途実費は含めない。
        self.assertNotIn('別途実費', body)


if __name__ == '__main__':
    unittest.main()
