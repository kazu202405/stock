# -*- coding: utf-8 -*-
"""点数の隣に出す「規模」（2026-09-06）。

なぜ要るか:
  12項目の適合度で緑の100点を取れるのは 3,886社中185社だが、実測すると
  1000億円を超える会社は**1社も**100点を取っていない（300〜1000億が64社、
  100〜300億が71社、100億未満が50社）。身軽な会社ほど12項目が揃うため。
  一覧に「100」が並んでも同じ土俵の100ではないので、規模を添える。

⚠️ **しきい値を画面ごとに書かない。** 判定は static/js/size-chip.js が唯一の正。
   スコアの色が3画面でバラバラだった件と同じ轍を踏まない。
⚠️ **規模に色を付けない。** 緑と赤はスコアとGC/DCで使っている。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 規模を出す一覧（テンプレート, その画面での書き方）
USES = (
    ('templates/stock.html', 'SizeChip.chip('),
    ('templates/screener.html', 'SizeChip.of('),
)


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


class SizeChipTest(unittest.TestCase):
    def setUp(self):
        self.js = read('static/js/size-chip.js')

    def test_the_thresholds_live_in_one_file(self):
        """しきい値がテンプレートに散らばっていないこと。"""
        for path, _ in USES:
            body = read(path)
            body = re.sub(r'\{#.*?#\}', '', body, flags=re.S)   # 注意書きは走査しない
            body = re.sub(r'<!--.*?-->', '', body, flags=re.S)
            for word in ('超小型', '中型'):
                self.assertNotIn(word, body,
                                 f'{path} が規模の呼び名を自前で持っている')

    def test_every_list_shows_the_size(self):
        for path, marker in USES:
            with self.subTest(path=path):
                self.assertIn(marker, read(path), f'{path} が規模を出していない')

    def test_the_helper_is_loaded_everywhere(self):
        self.assertIn("js/size-chip.js", read('templates/layout.html'))

    def test_the_boundaries(self):
        """しきい値そのもの。変えるときはここも一緒に直す。"""
        cases = [(50, '超小型'), (99, '超小型'), (100, '小型'), (999, '小型'),
                 (1000, '中型'), (9999, '中型'), (10000, '大型'), (240000, '大型')]
        for value, expected in cases:
            with self.subTest(value=value):
                # JS の定義をそのまま読み取って突き合わせる（二重定義にしない）
                limits = re.findall(r"limit:\s*([0-9]+|Infinity),\s*label:\s*'([^']+)'",
                                    self.js)
                self.assertEqual(len(limits), 4, '規模の定義を読めない')
                got = None
                for limit, label in limits:
                    cap = float('inf') if limit == 'Infinity' else int(limit)
                    if value < cap:
                        got = label
                        break
                self.assertEqual(got, expected, f'{value}億円')

    def test_the_size_is_not_coloured(self):
        """⚠️ 緑・赤はスコアとGC/DCの意味で使っている。3つ目を載せない。"""
        css_start = read('templates/layout.html').find('.size-chip {')
        self.assertNotEqual(css_start, -1, '.size-chip のCSSが無い')
        block = read('templates/layout.html')[css_start:css_start + 400]
        for banned in ('#15803d', '#dc2626', '#16a34a', 'green', 'red'):
            self.assertNotIn(banned, block, f'規模に {banned} を使っている')


if __name__ == '__main__':
    unittest.main()
