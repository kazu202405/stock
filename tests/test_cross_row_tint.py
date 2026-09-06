# -*- coding: utf-8 -*-
"""GC/DC による行の薄い色を、どの一覧にも同じように付ける（2026-09-06）。

好調企業・GC・DC・テクニカルの4つには入っていたが、**お気に入りだけ
抜けていた**。同じ銘柄が、開いたタブによって色が付いたり付かなかったり
していた。

⚠️ 判定は `getLatestCross` の1本に寄せる。一覧ごとに書くと、片方だけ
   基準がずれる（「直近のクロス」の定義が2つになる）。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 色を付ける一覧を描いている関数。増やしたらここに足す。
RENDERERS = (
    'applyFilterAndSort',      # 好調企業（renderWatchlist はここへ委譲する）
    'renderFavoriteStocks',    # お気に入り
)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class CrossRowTintTest(unittest.TestCase):
    def setUp(self):
        self.html = read('templates', 'stock.html')

    def test_the_tint_styles_exist(self):
        for cls in ('tr.row-trend-up', 'tr.row-trend-down'):
            self.assertIn(cls, self.html, cls)

    def test_every_list_tints_its_rows(self):
        """一覧ごとに <tr> へ row-trend-* を付けていること。"""
        applied = re.findall(r"'row-' \+ \w+\.cls", self.html)
        # ⚠️ 0件でも通る形にしない。書き方を変えて拾えなくなったら落とす。
        self.assertGreaterEqual(len(applied), 5,
                                '行の色付けを拾えていない（走査が壊れている可能性）')

    def test_the_favorites_list_is_not_left_out(self):
        """お気に入りだけ抜けていた実例があるので、名指しで見る。"""
        for name in RENDERERS:
            with self.subTest(renderer=name):
                start = self.html.find(f'function {name}(')
                self.assertNotEqual(start, -1, f'{name} が見つからない')
                # 次の関数定義までを、その一覧の範囲とする
                nxt = self.html.find('\n    function ', start + 1)
                block = self.html[start:nxt if nxt != -1 else start + 4000]
                self.assertIn('getLatestCross(', block,
                              f'{name} が直近のクロスを見ていない')
                self.assertIn("'row-' +", block,
                              f'{name} が行に色を付けていない')


if __name__ == '__main__':
    unittest.main()
