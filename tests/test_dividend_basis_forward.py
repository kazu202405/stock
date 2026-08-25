"""配当利回りの基準を「予想」に寄せたこと（2026-08-25）。

発端: ダッシュボードは実績、銘柄ページは予想を出しており、同じ 367A が
一覧で 5.93%、開くと 4.08% になっていた。実績は決算期をまたぐと期末配当と
翌期の中間配当が同じ12か月の窓に入って跳ね上がる。

⚠️ 主眼は「判定を1か所に置いたままにする」こと。画面ごとに書き直されると
同じ食い違いが静かに戻る。
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


class ScreenerServerSideTest(unittest.TestCase):

    def test_絞り込みは予想利回りを見る(self):
        source = read('app.py')
        m = re.search(r"'dividend_yield_min': \('([a-z_]+)', 'gte'\)", source)
        self.assertIsNotNone(m, 'dividend_yield_min の対応が見つからない')
        self.assertEqual(m.group(1), 'dividend_yield_forward')

    def test_予想利回りで並べ替えられる(self):
        block = read('app.py').split('SCREEN_SORTABLE = {', 1)[1].split('}', 1)[0]
        self.assertIn("'dividend_yield_forward'", block)

    def test_予想利回りは非会員にも返す(self):
        """実績だけを返すと、無料の人には一番誤解を招く数字しか見えない。"""
        block = read('app.py').split('FREE_SCREENED_FIELDS = {', 1)[1].split('}', 1)[0]
        self.assertIn("'dividend_yield_forward'", block)
        self.assertIn("'dps_forecast'", block)


class SharedModuleTest(unittest.TestCase):
    """判定は static/js/dividend-basis.js だけに置く。"""

    JS = 'static/js/dividend-basis.js'

    def test_共通部品が全画面に読み込まれている(self):
        self.assertIn('js/dividend-basis.js', read('templates', 'layout.html'))

    def test_予想を主にして実績へ落とす(self):
        source = read(self.JS)
        # 予想があればそれを返し、無いときだけ実績を返す、という順序
        self.assertLess(source.index('if (forward !== null) {'),
                        source.index('return { value: trailing,'))

    def test_一覧の表とカードは共通部品からセルを作る(self):
        source = read('templates', 'stock.html')
        self.assertEqual(source.count('DividendBasis.cell(item)'), 4)
        self.assertIn('DividendBasis.cell(r)', source)

    def test_実績のまま出しているのはgc_stocks由来の表だけ(self):
        """gc_stocks は kabutan 由来で今期予想を持たない。そこだけ実績のまま
        残すが、**基準を書かずには出さない**（列名に「(実績)」と入れる）。"""
        source = read('templates', 'stock.html')
        self.assertEqual(source.count("label: '配当利回り(実績)'"), 1)
        self.assertEqual(source.count('<th>配当利回り(実績)</th>'), 1)

    def test_銘柄ページと一覧も共通部品を通す(self):
        for name in ('stock_detail.html', 'screener.html', 'stock.html'):
            self.assertIn('DividendBasis.', read('templates', name), name)


class DisplayedFieldsTest(unittest.TestCase):

    def test_一覧の並べ替えキーが表示値と同じ(self):
        """表示は予想・並べ替えは実績、という食い違いを作らない。"""
        source = read('templates', 'stock.html')
        for fn in ('sortWatchlist', 'sortFavColumn', 'sortDivColumn', 'sortTechColumn'):
            self.assertIn(fn + "('dividend_yield_display')", source, fn)
        self.assertNotIn("sortDivColumn('dividend_yield')", source)

    def test_絞り込みも表示値を見る(self):
        source = read('templates', 'stock.html')
        self.assertNotIn('i.dividend_yield != null && i.dividend_yield >= divMin', source)
        self.assertEqual(
            source.count('i.dividend_yield_display != null && i.dividend_yield_display >= divMin'),
            4, '4つの一覧すべてが表示値で絞り込むこと')

    def test_一覧は取得直後に表示値を持たせている(self):
        """normalize を通し忘れると、並べ替えキーが undefined で全行同着になる。"""
        source = read('templates', 'stock.html')
        self.assertEqual(source.count('DividendBasis.normalize(list)'), 4)

    def test_スクリーナーの表は予想を先に置く(self):
        """列の定義と <td> の並びがずれると見出しと中身が食い違う。"""
        source = read('templates', 'screener.html')
        self.assertLess(source.index("{ key: 'dividend_yield_forward', label: '配当利回り(予想)' }"),
                        source.index("{ key: 'dividend_yield',         label: '配当利回り(実績)'"))
        self.assertLess(source.index('x-text="fmtPct(row.dividend_yield_forward, 2)"'),
                        source.index('x-text="fmtPct(row.dividend_yield, 2)"'))

    def test_管理者が直すのも予想の列(self):
        """画面に出しているのは予想なので、実績の列に書き戻すと
        「直した数字が画面に反映されない」ように見える。

        2026-08-25: 手入力は /search から /admin/stock-data へ移した。"""
        source = read('templates', 'admin_stock_data.html')
        self.assertIn("key: 'dividend_yield_forward'", source)
        self.assertNotIn("key: 'dividend_yield',", source)


class WatchlistDefaultSortTest(unittest.TestCase):
    """「好調企業」タブの既定の並び（2026-08-25）。

    以前は並べ替えを設定しておらず、サーバーが返す順（＝登録した順）のまま
    出ていた。タブ名は「好調企業」なのに中身は「最近登録した順」で、名前と
    中身がずれていた。開いた人が期待するのは「良い順」のほう。
    """

    def setUp(self):
        self.html = read('templates', 'stock.html')

    def test_既定はスコアの高い順(self):
        self.assertIn("let currentSort = { key: 'match_rate', asc: false };", self.html)

    def test_同点はGCの新しい順(self):
        self.assertIn('return compare(a.gc_date, b.gc_date, false);', self.html)

    def test_GCで並べているときは二重に見ない(self):
        self.assertIn("if (currentSort.key === 'gc_date') return 0;", self.html)

    def test_値の無い行は常に最後(self):
        """「未取得」を「一番小さい値」として上に出さない。"""
        block = self.html.split('const compare = (va, vb, asc)', 1)[1][:400]
        self.assertIn('if (va == null) return 1;', block)
        self.assertIn('if (vb == null) return -1;', block)


if __name__ == '__main__':
    unittest.main()
