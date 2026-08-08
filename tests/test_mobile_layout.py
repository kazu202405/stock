"""スマホ表示の崩れに対するリグレッション。

実機幅375pxで測定した結果、次の3種類の崩れがあった。

  1. タブが縮んで「お/気/に/入/り」と1文字ずつ縦積み（高さ173px）
  2. 管理ボタン列が横657pxに並び、ページごと横スクロール（doc幅698px）
  3. 表のセルが潰れて社名が1文字ずつ縦積み（幅26px・高さ112px）

いずれもCSSの指定漏れなので、指定が消えたら気づけるようにする。
"""

import unittest
from pathlib import Path


def _read(name):
    return Path(name).read_text(encoding='utf-8')


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


class ScreenerLayoutTest(unittest.TestCase):
    def test_table_cells_do_not_wrap(self):
        screener = _read('templates/screener.html')
        self.assertIn('white-space: nowrap', screener)
        self.assertIn('.overflow-x-auto > table th', screener)


if __name__ == '__main__':
    unittest.main()
