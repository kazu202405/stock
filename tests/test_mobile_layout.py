"""スマホ表示の崩れに対するリグレッション。

実機幅375pxで測定した結果、次の3種類の崩れがあった。

  1. タブが縮んで「お/気/に/入/り」と1文字ずつ縦積み（高さ173px）
  2. 管理ボタン列が横657pxに並び、ページごと横スクロール（doc幅698px）
  3. 表のセルが潰れて社名が1文字ずつ縦積み（幅26px・高さ112px）

いずれもCSSの指定漏れなので、指定が消えたら気づけるようにする。
"""

import os
import re
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault('ENABLE_SCHEDULER', 'false')


def _read(name):
    return Path(name).read_text(encoding='utf-8')


class ScoreCardMarkupTest(unittest.TestCase):
    """会員/非会員の分岐で div の対応が崩れないこと。

    非会員の分岐が score-card を if の中で閉じており、endif の後の </div> が
    親の .content-grid を閉じていた。結果、チャートがグリッドの外に出て
    スコアの右ではなく下に回っていた。会員は分岐を通らないので気づけない。
    """

    def _render(self, logged_in, member):
        import app as app_module
        app_module.app.config['TESTING'] = True
        client = app_module.app.test_client()
        if logged_in:
            with client.session_transaction() as s:
                s['user_id'] = 'u'
                s['user_name'] = 't'
                s['user_role'] = 'user'
        with unittest.mock.patch.object(
                app_module, 'is_member_session', return_value=member), \
             unittest.mock.patch('models.root.is_member', return_value=member):
            return client.get('/stock/7203').get_data(as_text=True)

    def _grid(self, html):
        """content-grid の開始タグから、対応する閉じタグまでを切り出す"""
        mo = re.search(r'<div[^>]*class="[^"]*content-grid', html)
        self.assertIsNotNone(mo, 'content-grid が見つからない')
        start = mo.start()
        depth = 0
        for t in re.finditer(r'<div\b|</div>', html[start:]):
            depth += 1 if t.group().startswith('<div') else -1
            if depth == 0:
                return html[start:start + t.end()]
        self.fail('content-grid の閉じタグが見つからない（div の対応が崩れている）')

    def test_chart_stays_inside_the_grid(self):
        for label, logged_in, member in (
                ('未ログイン', False, False),
                ('無料会員', True, False),
                ('有料会員', True, True)):
            with self.subTest(label):
                grid = self._grid(self._render(logged_in, member))
                self.assertIn('chart-card', grid,
                              'チャートがグリッドの外に出ている（スコアの下に回る）')
                self.assertEqual(grid.count('score-card'), 1)


class NoSidewaysDragTest(unittest.TestCase):
    """閉じたスライドメニューが全ページで横スクロールを作っていた"""

    def setUp(self):
        self.layout = _read('templates/layout.html')

    def test_root_clips_horizontal_overflow(self):
        self.assertIn('overflow-x: clip', self.layout)

    def test_uses_clip_not_hidden_to_keep_sticky_header(self):
        """hidden はスクロールコンテナを作り、sticky ヘッダーを壊す。

        コメント中の言及は拾わないよう、CSS宣言としての出現だけを見る。
        """
        declarations = [line.split('/*')[0].strip()
                        for line in self.layout.splitlines()]
        self.assertNotIn('overflow-x: hidden;', declarations)
        self.assertIn('position: sticky', self.layout)


class DashboardLayoutTest(unittest.TestCase):
    def setUp(self):
        self.stock = _read('templates/stock.html')

    def test_tabs_do_not_shrink_into_vertical_text(self):
        """flex-shrink が効くとタブが潰れて1文字ずつ縦に並ぶ"""
        self.assertIn('flex: 0 0 auto', self.stock)
        self.assertIn('white-space: nowrap', self.stock)

    def test_tabs_scroll_instead_of_squeezing(self):
        self.assertIn('.watchlist-tabs', self.stock)
        tabs_css = self.stock.split('.watchlist-tabs {')[1].split('}')[0]
        self.assertIn('overflow-x: auto', tabs_css)

    def test_action_buttons_wrap(self):
        """ボタン列が横に並び切らずページを押し広げていた"""
        buttons_css = self.stock.split('.action-buttons {')[1].split('}')[0]
        self.assertIn('flex-wrap: wrap', buttons_css)

    def test_mobile_stacks_action_buttons(self):
        self.assertIn('@media (max-width: 768px)', self.stock)
        self.assertIn('@media (max-width: 420px)', self.stock)

    def test_watchlist_table_cells_do_not_wrap(self):
        """min-width 700px では列が足りず、社名が1文字ずつ折り返していた"""
        self.assertIn('min-width: 880px', self.stock)
        self.assertIn('.watchlist-table th,', self.stock)


class AdminUsersLayoutTest(unittest.TestCase):
    def setUp(self):
        self.admin = _read('templates/admin_users.html')

    def test_table_has_min_width_so_it_scrolls(self):
        """overflow-x-auto があっても w-full だけではスクロールが起きない"""
        self.assertIn('.admin-table { min-width', self.admin)
        self.assertIn('admin-table', self.admin.split('<table')[1][:120])

    def test_table_cells_do_not_wrap(self):
        self.assertIn('white-space: nowrap', self.admin)


class MobileNavPlacementTest(unittest.TestCase):
    """モバイルの導線配置。

    ボトムタブは「行き先」だけを置き、開閉するもの（スライドメニュー）は
    ヘッダー右端に寄せている。この配置で気をつける点は1つで、
    スライドメニューにしか無い項目（スクリーニング・テーマ・決算情報・
    レポート・マーケット・ログアウト）が、ヘッダーのボタンを失うと
    モバイルから到達できなくなること。開く手段の存在を固定する。
    """

    def setUp(self):
        layout = _read('templates/layout.html')
        self.header = layout.split('<nav class="bg-white site-header"')[1].split('</nav>')[0]
        # CSSの .bottom-tab-nav ではなくマークアップ側を見る
        self.bottom_tabs = (layout.split('<div class="bottom-tab-nav')[1]
                            .split('<!-- スライドメニュー')[0])

    def test_header_has_mobile_menu_button(self):
        """これが無いとスライドメニューを開く手段がモバイルに無くなる"""
        self.assertIn('toggleSlideMenu(true)', self.header)
        button = self.header.split('toggleSlideMenu(true)')[0]
        self.assertIn('md:hidden', button[-300:])

    def test_bottom_tabs_include_mypage(self):
        self.assertIn('data-tab-path="/mypage"', self.bottom_tabs)
        self.assertIn('マイノート', self.bottom_tabs)

    def test_bottom_tabs_are_links_only(self):
        """開閉ボタンをボトムタブに戻さない（ヘッダーと二重になる）"""
        self.assertNotIn('toggleSlideMenu', self.bottom_tabs)

    def test_every_bottom_tab_marks_active_state(self):
        """data-tab-path が無いタブは現在地が光らない"""
        self.assertEqual(self.bottom_tabs.count('class="bottom-tab-item"'),
                         self.bottom_tabs.count('data-tab-path='))


class ScreenerLayoutTest(unittest.TestCase):
    def test_table_cells_do_not_wrap(self):
        screener = _read('templates/screener.html')
        self.assertIn('white-space: nowrap', screener)
        self.assertIn('.overflow-x-auto > table th', screener)


if __name__ == '__main__':
    unittest.main()
