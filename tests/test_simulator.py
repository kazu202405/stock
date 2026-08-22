"""過去株価シミュレーションの計算。

外部アクセスなしで完結する純粋計算なので、境界だけを固定する。
"""

import os
import sys
import unittest
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import simulator as sim


def bar(d, close):
    """'2024-01-15' → {'time': epoch, 'close': close}"""
    dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
    return {'time': int(dt.timestamp()), 'close': close}


class TestPurchaseDates(unittest.TestCase):
    def test_monthly(self):
        got = sim.purchase_dates('2024-01-10', '2024-04-30', 1, 15)
        self.assertEqual([d.isoformat() for d in got],
                         ['2024-01-15', '2024-02-15', '2024-03-15', '2024-04-15'])

    def test_every_three_months(self):
        got = sim.purchase_dates('2024-01-01', '2024-12-31', 3, 1)
        self.assertEqual([d.isoformat() for d in got],
                         ['2024-01-01', '2024-04-01', '2024-07-01', '2024-10-01'])

    def test_day_31_falls_back_to_month_end(self):
        """31日が無い月を飛ばさない。2月は末日に寄せる。"""
        got = [d.isoformat() for d in sim.purchase_dates('2024-01-31', '2024-04-30', 1, 31)]
        self.assertEqual(got, ['2024-01-31', '2024-02-29', '2024-03-31', '2024-04-30'])

    def test_start_after_day_skips_first_month(self):
        got = [d.isoformat() for d in sim.purchase_dates('2024-01-20', '2024-03-31', 1, 15)]
        self.assertEqual(got, ['2024-02-15', '2024-03-15'])

    def test_year_rollover(self):
        got = [d.isoformat() for d in sim.purchase_dates('2024-11-01', '2025-02-28', 1, 1)]
        self.assertEqual(got, ['2024-11-01', '2024-12-01', '2025-01-01', '2025-02-01'])


class TestPriceOn(unittest.TestCase):
    def setUp(self):
        self.series = sim.normalize_series([
            bar('2024-01-05', 100), bar('2024-01-09', 110), bar('2024-01-12', 120)])

    def test_exact_day(self):
        self.assertEqual(sim.price_on(self.series, '2024-01-09')[1], 110)

    def test_falls_back_to_previous_trading_day(self):
        """休日に指定したら、その前の終値で買う。"""
        self.assertEqual(sim.price_on(self.series, '2024-01-10')[1], 110)

    def test_before_first_bar(self):
        self.assertIsNone(sim.price_on(self.series, '2024-01-01'))

    def test_too_far_back_is_rejected(self):
        """さかのぼりすぎは「データが無い」として扱う。"""
        self.assertIsNone(sim.price_on(self.series, '2024-03-01', max_lookback_days=14))


class TestNormalizeSeries(unittest.TestCase):
    def test_drops_zero_and_missing(self):
        s = sim.normalize_series([bar('2024-01-05', 100), {'time': 1, 'close': 0},
                                  {'time': 2}, {'close': 50}])
        self.assertEqual(len(s), 1)

    def test_sorted(self):
        s = sim.normalize_series([bar('2024-02-01', 200), bar('2024-01-01', 100)])
        self.assertEqual([p[1] for p in s], [100, 200])


class TestLump(unittest.TestCase):
    def setUp(self):
        self.hist = {'daily_1y': [bar('2024-01-05', 100), bar('2024-06-05', 150)]}

    def test_doubling_is_not_claimed(self):
        r = sim.simulate_lump(self.hist, '2024-01-05', '2024-06-05', 100000)
        self.assertTrue(r['ok'])
        self.assertEqual(r['invested'], 100000)
        self.assertEqual(r['value'], 150000)
        self.assertEqual(r['profit'], 50000)
        self.assertEqual(r['return_pct'], 50.0)

    def test_loss(self):
        hist = {'daily_1y': [bar('2024-01-05', 200), bar('2024-06-05', 150)]}
        r = sim.simulate_lump(hist, '2024-01-05', '2024-06-05', 100000)
        self.assertEqual(r['profit'], -25000)
        self.assertEqual(r['return_pct'], -25.0)

    def test_missing_start(self):
        r = sim.simulate_lump(self.hist, '2020-01-05', '2024-06-05', 100000)
        self.assertFalse(r['ok'])
        self.assertIn('available_from', r)

    def test_no_history(self):
        self.assertFalse(sim.simulate_lump({}, '2024-01-05', '2024-06-05', 1000)['ok'])


class TestMonthly(unittest.TestCase):
    def test_dollar_cost_average(self):
        """価格が下がってから戻ると、平均取得単価は最初の価格より下がる。"""
        hist = {'daily_1y': [bar('2024-01-15', 100), bar('2024-02-15', 50),
                             bar('2024-03-15', 100)]}
        r = sim.simulate_monthly(hist, '2024-01-01', '2024-03-15', 10000, 1, 15)
        self.assertTrue(r['ok'])
        self.assertEqual(r['times'], 3)
        self.assertEqual(r['invested'], 30000)
        # 100円で100株, 50円で200株, 100円で100株 = 400株。評価 40000円
        self.assertEqual(r['shares'], 400.0)
        self.assertEqual(r['value'], 40000)
        self.assertEqual(r['avg_cost'], 75.0)

    def test_interval_three_months(self):
        hist = {'daily_1y': [bar('2024-01-01', 100), bar('2024-04-01', 100),
                             bar('2024-07-01', 100)]}
        r = sim.simulate_monthly(hist, '2024-01-01', '2024-07-01', 1000, 3, 1)
        self.assertEqual(r['times'], 3)

    def test_no_buyable_data(self):
        hist = {'daily_1y': [bar('2024-06-01', 100)]}
        r = sim.simulate_monthly(hist, '2020-01-01', '2020-12-31', 1000, 1, 1)
        self.assertFalse(r['ok'])


class TestSeriesSelection(unittest.TestCase):
    def test_long_range_uses_monthly(self):
        hist = {'daily_1y': [bar('2026-01-05', 100)],
                'monthly_10y': [bar('2016-01-05', 10), bar('2026-01-05', 100)]}
        _, grain = sim.pick_series(hist, '2016-01-05', '2026-01-05')
        self.assertEqual(grain, 'monthly')

    def test_short_range_uses_daily(self):
        hist = {'daily_1y': [bar('2026-01-05', 100), bar('2026-06-05', 120)],
                'monthly_10y': [bar('2016-01-05', 10)]}
        _, grain = sim.pick_series(hist, '2026-02-01', '2026-06-05')
        self.assertEqual(grain, 'daily')


class TestEvaluationPrice(unittest.TestCase):
    """評価は買付と別に、いちばん新しい系列から引く。

    月足の最終バーから数十日空いていると、月足だけを見ていた頃は
    「2026-08-21 時点の株価がありません」になっていた（実際に踏んだ）。
    """

    def setUp(self):
        self.hist = {
            'daily_1y': [bar('2026-08-20', 3100)],
            'monthly_10y': [bar('2016-01-31', 800), bar('2026-06-30', 3000)],
        }

    def test_uses_daily_even_when_buying_from_monthly(self):
        got = sim.evaluation_price(self.hist, '2026-08-21')
        self.assertEqual(got[1], 3100)
        self.assertEqual(got[0].isoformat(), '2026-08-20')

    def test_falls_back_to_latest_bar_when_end_is_in_the_future(self):
        """評価日が持っているデータより後でも、最新バーで評価する。"""
        got = sim.evaluation_price(self.hist, '2030-01-01')
        self.assertEqual(got[1], 3100)

    def test_monthly_only(self):
        got = sim.evaluation_price({'monthly_10y': [bar('2026-06-30', 3000)]}, '2026-08-21')
        self.assertEqual(got[1], 3000)

    def test_empty(self):
        self.assertIsNone(sim.evaluation_price({}, '2026-08-21'))

    def test_long_range_lump_does_not_fail_on_stale_monthly(self):
        """1984年開始・今日評価。以前はここで落ちていた。"""
        r = sim.simulate_lump(self.hist, '2016-01-31', '2026-08-21', 1000000)
        self.assertTrue(r['ok'], r.get('reason'))
        self.assertEqual(r['buy_date'], '2016-01-31')
        self.assertEqual(r['sell_date'], '2026-08-20')
        self.assertEqual(r['sell_price'], 3100)


if __name__ == '__main__':
    unittest.main()
