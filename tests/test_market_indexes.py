"""マーケットページに並べる指数（2026-08-25 に入れ替え）。

- TOPIX を外した。指数そのものが配信されないため連動ETF（1306）の価格で
  代用しており、「値そのものは指数値ではない」という但し書き付きだった。
  日経平均があるので、代用品を並べてまで日本株の平均を2本持つ必要がない。
- 金（GC=F）と WTI原油（CL=F）を、ドル円とビットコインの間に入れた。

⚠️ 代用品（ETFなど）を指数として並べない。値が指数と一致せず、
   但し書きを読まない人には分からない。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import market_data as md


class IndexListTest(unittest.TestCase):

    def setUp(self):
        self.keys = [i['key'] for i in md.INDEXES]

    def test_TOPIXを並べていない(self):
        self.assertNotIn('topix', self.keys)

    def test_ETFを指数の代わりに使っていない(self):
        """'1306.T' のような銘柄コードは指数ではない。"""
        for idx in md.INDEXES:
            symbol = idx['symbol']
            self.assertFalse(
                symbol.endswith('.T') and symbol[0].isdigit(),
                '%s は個別銘柄（ETF）を指数として並べている' % symbol)

    def test_金はドル円とビットコインの間(self):
        self.assertIn('gold', self.keys)
        self.assertLess(self.keys.index('usdjpy'), self.keys.index('gold'))
        self.assertLess(self.keys.index('gold'), self.keys.index('btcjpy'))

    def test_原油も商品として並べる(self):
        """多くの日本企業にとって原油は原価そのもの。電力・運輸・海運・
        化学・素材の決算を読むときの背景になる。"""
        self.assertIn('wti', self.keys)
        wti = md.INDEX_BY_KEY['wti']
        self.assertEqual(wti['symbol'], 'CL=F')
        self.assertEqual(wti['region'], '商品')

    def test_キーとシンボルが重複していない(self):
        symbols = [i['symbol'] for i in md.INDEXES]
        self.assertEqual(len(set(self.keys)), len(self.keys))
        self.assertEqual(len(set(symbols)), len(symbols))

    def test_先物には限月の注記を付ける(self):
        """限月が変わるときに価格が飛ぶ。説明が無いと不具合に見える。"""
        for key in ('gold', 'wti'):
            idx = md.INDEX_BY_KEY[key]
            self.assertIn('限月', idx['note'], key)

    def test_必要な項目が全部そろっている(self):
        needed = ('key', 'prefix', 'decimals', 'symbol', 'name',
                  'short_name', 'currency', 'region', 'description', 'note')
        for idx in md.INDEXES:
            for field in needed:
                self.assertIn(field, idx, '%s に %s が無い' % (idx.get('key'), field))


if __name__ == '__main__':
    unittest.main()
