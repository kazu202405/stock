# -*- coding: utf-8 -*-
"""一覧を空にするボタンの文言と置き場所（2026-09-06）。

何が起きたか:
  好調企業タブの「すべて削除」を押すと「**ウォッチリスト**を空にします」と出た。
  画面のタブは「**好調企業**」なので、五島さんが「お気に入りが消えるのか」と
  確認することになった。**同じものを2つの名前で呼んでいた。**

⚠️ 元に戻せない操作は、押す前に次の3つが読めること:
     1. どの一覧が対象か（タブと同じ言葉で）
     2. 何件消えるか
     3. 何が消えないか（分析データ・お気に入り）
   さらにこの2つは会員全員の共有リストなので、そう書く。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


def handler(html, name):
    start = html.find('function %s(' % name)
    assert start != -1, name
    nxt = html.find('\n    function ', start + 1)
    nxt2 = html.find('\n    async function ', start + 1)
    ends = [e for e in (nxt, nxt2) if e != -1]
    return html[start:min(ends) if ends else start + 2500]


class ConfirmWordingTest(unittest.TestCase):
    def setUp(self):
        self.html = read('templates/stock.html')

    def test_the_watchlist_dialog_uses_the_tab_name(self):
        block = handler(self.html, 'removeAllWatchlist')
        self.assertIn('好調企業', block, 'タブと違う言葉で聞いている')
        self.assertNotIn("title: 'ウォッチリストを空にします'", block)

    def test_both_dialogs_say_what_survives(self):
        """「元には戻せません」だけでは、何が残るか分からない。"""
        for name in ('removeAllWatchlist', 'removeAllDividendStocks'):
            with self.subTest(name=name):
                block = handler(self.html, name)
                self.assertIn('分析データ', block, '消えないものが書かれていない')
                self.assertIn('共有', block, '共有リストであることが書かれていない')
                self.assertIn('count', block, '件数を出していない')

    def test_the_watchlist_dialog_mentions_favourites_are_safe(self):
        """誤解の中身そのものを打ち消す。"""
        block = handler(self.html, 'removeAllWatchlist')
        self.assertIn('お気に入り', block)


class ButtonPlacementTest(unittest.TestCase):
    """登録するものと同じタブにボタンを置く。"""

    def setUp(self):
        self.html = read('templates/stock.html')

    def _tab_block(self, tab_id):
        start = self.html.find('id="tabContent-%s"' % tab_id)
        self.assertNotEqual(start, -1, tab_id)
        nxt = self.html.find('<div class="tab-content"', start + 1)
        return self.html[start:nxt if nxt != -1 else start + 9000]

    def test_the_dividend_register_button_moved_to_its_own_tab(self):
        self.assertIn('openDividendRegisterModal()',
                      self._tab_block('dividend'))
        self.assertNotIn('openDividendRegisterModal()',
                         self._tab_block('watchlist'))

    def test_each_tab_clears_its_own_list(self):
        self.assertIn('removeAllWatchlist()', self._tab_block('watchlist'))
        self.assertIn('removeAllDividendStocks()', self._tab_block('dividend'))

    def test_the_clear_buttons_are_admin_only(self):
        """⚠️ 画面で隠すだけにしない（APIにも admin_required_api がある）。"""
        source = read('app.py')
        for route in ("'/api/watchlist/remove-all'",
                      "'/api/dividend-stocks/remove-all'"):
            i = source.find(route)
            self.assertNotEqual(i, -1, route)
            self.assertIn('@admin_required_api', source[i:i + 200], route)


class DividendClearTest(unittest.TestCase):
    """高配当は旗を落とすだけ。分析データを消さない。"""

    def test_it_only_lowers_the_flag(self):
        source = read('app.py')
        i = source.find("'/api/dividend-stocks/remove-all'")
        block = source[i:i + 1200]
        self.assertIn("update({'is_dividend': False})", block)
        # ⚠️ 行を消したら分析データごと失う
        self.assertNotIn('.delete()', block, '行そのものを消している')

    def test_it_reports_how_many_were_cleared(self):
        source = read('app.py')
        i = source.find("'/api/dividend-stocks/remove-all'")
        self.assertIn('"removed"', source[i:i + 1200],
                      '何件外れたか返していない')


if __name__ == '__main__':
    unittest.main()
