"""毎日の株価cronが、PER等を株価と一緒に更新すること。

2026-08-24。cron は stock_price だけを書き換えており、そこから計算される
PER・PBR・時価総額・配当利回りは分析した日のまま置かれていた。
結果、銘柄ページに「今日の株価」と「1か月前のPER」が並んでいた
（PERが5%以上ずれている銘柄が64%、20%以上が31%）。

⚠️ このテストの主眼は「株価だけを書く経路を作らせない」こと。
片方だけ更新できてしまうと、同じズレが静かに再発する。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')


class FakeTable:
    """screened_latest の select/update を記録する最小のスタブ"""

    def __init__(self, rows, log):
        self._rows = rows
        self._log = log
        self._payload = None

    def select(self, *a, **k):
        return self

    def range(self, start, end):
        self._page = self._rows[start:end + 1]
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def eq(self, _col, value):
        self._code = value
        return self

    def execute(self):
        if self._payload is not None:
            self._log.append((self._code, self._payload))
            self._payload = None
            return MagicMock(data=[])
        return MagicMock(data=getattr(self, '_page', self._rows))


class TestPriceCronSyncsMultiples(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app
        self.rows = [{
            'company_code': '9999', 'stock_price': 1000.0,
            'per_forward': 10.0, 'pbr': 2.0, 'market_cap': 500.0,
            'dividend_yield': 3.0, 'dividend_yield_forward': 3.2,
        }]
        self.writes = []

    def _run(self, prices):
        client = MagicMock()
        client.table.return_value = FakeTable(self.rows, self.writes)
        with patch('app.get_supabase_client', return_value=client), \
             patch('app.fetch_prices_batch', return_value=prices):
            self.app.scheduled_update_stock_prices()
        return dict(self.writes)

    def test_multiples_are_written_with_the_price(self):
        written = self._run({'9999': 2000.0})
        payload = written['9999']
        self.assertEqual(payload['stock_price'], 2000.0)
        self.assertAlmostEqual(payload['per_forward'], 20.0)
        self.assertAlmostEqual(payload['pbr'], 4.0)
        self.assertAlmostEqual(payload['market_cap'], 1000.0)
        self.assertAlmostEqual(payload['dividend_yield'], 1.5)
        self.assertAlmostEqual(payload['dividend_yield_forward'], 1.6)

    def test_never_writes_the_price_alone(self):
        """これが落ちたら、また株価だけが動く状態に戻っている。"""
        written = self._run({'9999': 1100.0})
        self.assertNotEqual(set(written['9999']), {'stock_price'})
        self.assertIn('per_forward', written['9999'])

    def test_sync_time_is_stamped(self):
        self.assertIn('price_updated_at', self._run({'9999': 1100.0})['9999'])

    def test_unchanged_price_writes_nothing(self):
        self.assertEqual(self._run({'9999': 1000.0}), {})

    def test_split_writes_only_the_price(self):
        """株式分割では株価もEPSも同じ比で動くのでPERは変わらない。
        指標を伸縮させると分割の比だけ嘘が乗る。"""
        payload = self._run({'9999': 5000.0})['9999']
        self.assertEqual(payload['stock_price'], 5000.0)
        self.assertNotIn('per_forward', payload)

    def test_a_row_without_multiples_still_gets_its_price(self):
        self.rows[0] = {'company_code': '9999', 'stock_price': 1000.0}
        self.assertEqual(self._run({'9999': 1200.0})['9999']['stock_price'], 1200.0)


if __name__ == '__main__':
    unittest.main()
