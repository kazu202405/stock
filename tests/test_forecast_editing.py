"""今期の会社予想を手で入れられるようにした（2026-08-25）。

取得できるのは6割ほど（forecast_revenue 62.7%）。決算短信を見れば分かる
数字なので、管理画面で手で埋められるようにする。

⚠️ 一番大事なのは「経常利益を必須にしないこと」。
   IFRSを適用している会社には**経常利益という区分が無い**。実測で160社
   （トヨタを含む）が売上・営業・純利益だけを持っている。4つ必須にすると
   その会社の予想が画面から消える。
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


class ApiAcceptsForecastTest(unittest.TestCase):

    def setUp(self):
        source = read('app.py')
        marker = "@app.route('/api/watchlist/update', methods=['POST'])"
        self.body = source.split(marker, 1)[1].split('@app.route', 1)[0]

    def test_4つの金額を受け取る(self):
        for key in ('forecast_revenue', 'forecast_op_income',
                    'forecast_ordinary_income', 'forecast_net_income'):
            self.assertIn("'%s'" % key, self.body, key)

    def test_決算期は日付の形を確かめる(self):
        """数値の列と混ぜない。壊れた文字列を入れると絞り込みが黙って外れる。"""
        self.assertIn('forecast_year', self.body)
        self.assertIn(r'^\d{4}-\d{2}-\d{2}$', self.body)
        self.assertIn('400', self.body)

    def test_管理者だけが書ける(self):
        self.assertIn("session.get('user_role') != 'admin'", self.body)


class EditorTest(unittest.TestCase):

    def setUp(self):
        self.html = read('templates', 'admin_stock_data.html')

    def test_予想の入力欄がある(self):
        self.assertIn('FORECAST_FIELDS', self.html)
        self.assertIn('data-forecast=', self.html)
        for key in ('forecast_year', 'forecast_revenue', 'forecast_op_income',
                    'forecast_ordinary_income', 'forecast_net_income'):
            self.assertIn("key: '%s'" % key, self.html, key)

    def test_決算期は数値に変えない(self):
        self.assertIn("(key === 'forecast_year')", self.html)

    def test_経常利益は任意と書いてある(self):
        self.assertIn('任意', self.html)
        self.assertIn('IFRS', self.html)

    def test_触っていない欄は送らない(self):
        block = self.html.split("input[data-forecast]", 1)[1][:400]
        self.assertIn('String(input.dataset.original)', block)


class StockPageDisplayTest(unittest.TestCase):
    """揃ったときだけ予想の行を出す。"""

    def setUp(self):
        self.html = read('templates', 'stock_detail.html')

    def test_3つそろったときだけ出す(self):
        """1つ2つしか無い予想の行は、他が空欄のまま並んで
        「減収に見える」「利益が消えたように見える」を作る。"""
        self.assertIn('const forecastReady =', self.html)
        for key in ('forecast_revenue', 'forecast_op_income', 'forecast_net_income'):
            self.assertIn('data.%s != null' % key, self.html, key)
        self.assertIn('if (forecastReady) {', self.html)

    def test_経常利益は条件に入れない(self):
        """IFRSには経常利益という区分が無い。必須にするとトヨタの予想が消える。"""
        block = self.html.split('const forecastReady =', 1)[1].split(';', 1)[0]
        self.assertNotIn('forecast_ordinary_income', block)


if __name__ == '__main__':
    unittest.main()
