"""スクリーナーの絞り込み（2026-08-25 に拡張）。

画面に出していた条件は10個だったが、実際に埋まっている列はもっとあった。
2026-08-25 の実測:

  使える（95%以上）  pbr 99.5 / market_cap 99.3 / roa 98.0 / operating_cf 98.0
                     roe 97.9 / free_cf 97.8 / equity_ratio 97.8
                     operating_margin 95.4 / fiscal_month 98.1 / score_complete 100
  そこそこ（80%前後） dividend_yield_forward 82.0 / payout_ratio 79.7
  ⚠️ ほぼ空          revenue_growth_* 0.0 / op_growth_* 0.0 / total_assets 0.0
                     equity 0.0 / current_ratio 1.9 / margin_trading_ratio 0.6

⚠️ 主眼は「画面に出した条件がサーバーで無視されないこと」。
   入力欄だけ足してサーバー側を忘れると、**打っても件数が変わらない**。
   エラーにならないので気づけない。
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def ui_filter_keys():
    """screener.html の filters に並んでいるキー。"""
    html = read('templates', 'screener.html')
    block = html.split('filters: {', 1)[1].split('},', 1)[0]
    return set(re.findall(r"(\w+):\s*''", block))


def server_handled_keys():
    """サーバーが受け取るパラメータ。"""
    source = read('app.py')
    block = source.split('SCREEN_FILTERS = {', 1)[1].split('\n}', 1)[0]
    keys = set(re.findall(r"'(\w+)':\s*\(", block))
    # SCREEN_FILTERS を通らない、個別に処理しているもの
    keys |= {'q', 'industry', 'sector', 'market', 'business',
             'fiscal_month', 'score_complete'}
    return keys


class UiAndServerAgreeTest(unittest.TestCase):

    def test_画面に出した条件をサーバーが全部受け取る(self):
        """入力欄だけ足してサーバーを忘れると、打っても件数が変わらない。"""
        missing = sorted(ui_filter_keys() - server_handled_keys())
        self.assertEqual(missing, [],
                         'サーバーが見ていない条件: %s' % missing)

    def test_個別に処理するものは一括処理に混ぜない(self):
        """決算月は 1〜12 の一致、データの揃い方は真偽値。
        大小比較の一括処理に混ぜると意味の違う絞り方になる。"""
        source = read('app.py')
        block = source.split('SCREEN_FILTERS = {', 1)[1].split('\n}', 1)[0]
        for key in ('fiscal_month', 'score_complete'):
            self.assertNotIn("'%s'" % key, block, key)
        self.assertIn("query.eq('fiscal_month', int(fiscal_month))", source)
        self.assertIn("query.eq('score_complete', True)", source)

    def test_決算月は1から12だけ受ける(self):
        source = read('app.py')
        self.assertIn('1 <= int(fiscal_month) <= 12', source)


class AndOnlyTest(unittest.TestCase):
    """条件どうしは AND 固定。OR は用意しない。"""

    def test_ORのパラメータを作っていない(self):
        source = read('app.py')
        block = source.split('SCREEN_FILTERS = {', 1)[1].split('\n}', 1)[0]
        for word in ('_or', 'match_any', 'logic'):
            self.assertNotIn(word, block, word)

    def test_方針を書き残している(self):
        """次に触る人が「ORも足そう」と考えたときに理由が読めること。"""
        html = read('templates', 'screener.html')
        self.assertIn('すべて AND', html)


class AdvancedPanelTest(unittest.TestCase):

    def setUp(self):
        self.html = read('templates', 'screener.html')

    def test_よく使う条件は常に見えている(self):
        block = self.html.split('basicFilterKeys: [', 1)[1].split('],', 1)[0]
        basic = set(re.findall(r"'(\w+)'", block))
        self.assertEqual(basic - ui_filter_keys(), set(),
                         'filters に無いキーを常時表示に数えている')
        self.assertGreaterEqual(len(basic), 10)

    def test_閉じていても効いている条件の数を出す(self):
        """閉じたままでも条件は効く。隠れていると件数が合わない理由が
        画面から分からなくなる。"""
        self.assertIn('get advancedCount()', self.html)
        self.assertIn('advancedCount > 0', self.html)

    def test_詳細は開いたときだけ出す(self):
        self.assertIn('x-show="advancedOpen"', self.html)
        self.assertIn('advancedOpen: false', self.html)


class SortableTest(unittest.TestCase):

    def test_足した列で並べ替えられる(self):
        block = read('app.py').split('SCREEN_SORTABLE = {', 1)[1].split('}', 1)[0]
        for col in ('roa', 'payout_ratio', 'operating_cf', 'free_cf',
                    'dividend_yield_forward'):
            self.assertIn("'%s'" % col, block, col)


if __name__ == '__main__':
    unittest.main()
