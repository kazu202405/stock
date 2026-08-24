"""株価連動指標の伸縮（multiples.py）。

2026-08-24。stock_price だけを毎日更新し、PER/PBR/時価総額/配当利回りを
分析時のまま置いていたため、画面上で「今日の株価」と「1か月前のPER」が
並んでいた。実測でPERが5%以上ずれている銘柄が64%あった。

⚠️ ここで一番大事なのは **株価÷EPSで計算し直さないこと**。
EPSは報告通貨で入っており、米ドル建ての会社（6269 三井海洋開発など）で
円の株価をドルのEPSで割ると桁が壊れる。比率で伸縮させれば通貨に依存しない。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import multiples


def row(**kw):
    base = {'company_code': '9999', 'stock_price': 1000.0,
            'per_forward': 10.0, 'pbr': 2.0, 'market_cap': 500.0,
            'dividend_yield': 3.0}
    base.update(kw)
    return base


class TestRescale(unittest.TestCase):
    def test_price_doubles_so_per_doubles(self):
        u = multiples.rescale(row(), 2000.0)
        self.assertEqual(u['stock_price'], 2000.0)
        self.assertAlmostEqual(u['per_forward'], 20.0)
        self.assertAlmostEqual(u['pbr'], 4.0)
        self.assertAlmostEqual(u['market_cap'], 1000.0)

    def test_yield_moves_the_other_way(self):
        """配当が同じで株価が2倍なら利回りは半分。"""
        u = multiples.rescale(row(), 2000.0)
        self.assertAlmostEqual(u['dividend_yield'], 1.5)

    def test_price_falls(self):
        u = multiples.rescale(row(), 500.0)
        self.assertAlmostEqual(u['per_forward'], 5.0)
        self.assertAlmostEqual(u['dividend_yield'], 6.0)

    def test_same_price_changes_nothing(self):
        self.assertEqual(multiples.rescale(row(), 1000.0), {})

    def test_every_update_stamps_the_sync_time(self):
        """price_updated_at が「株価と指標が揃っている」印。
        これが無いと、さかのぼって直すバックフィルが同じ行を二度伸縮させる。"""
        self.assertIn('price_updated_at', multiples.rescale(row(), 2000.0))
        self.assertIn('price_updated_at',
                      multiples.rescale(row(stock_price=None), 2000.0))

    def test_missing_fields_are_left_alone(self):
        """PBRが無い銘柄で、無理に0を入れたりしない。"""
        u = multiples.rescale(row(pbr=None, market_cap=None), 2000.0)
        self.assertNotIn('pbr', u)
        self.assertNotIn('market_cap', u)
        self.assertAlmostEqual(u['per_forward'], 20.0)

    def test_no_base_price_only_sets_the_price(self):
        """基準が無ければ伸縮しない。次回から不変条件が成り立つ。"""
        u = multiples.rescale(row(stock_price=None), 2000.0)
        self.assertEqual(u['stock_price'], 2000.0)
        self.assertNotIn('per_forward', u)

    def test_zero_base_price_is_not_a_division_by_zero(self):
        u = multiples.rescale(row(stock_price=0), 2000.0)
        self.assertEqual(u['stock_price'], 2000.0)
        self.assertNotIn('per_forward', u)

    def test_bad_new_price_is_ignored(self):
        for bad in (None, 0, -100, 'abc', float('nan')):
            self.assertEqual(multiples.rescale(row(), bad), {},
                             f'{bad!r} を株価として受け入れている')

    def test_strings_are_accepted_as_numbers(self):
        u = multiples.rescale(row(per_forward='10.0'), '2000')
        self.assertAlmostEqual(u['per_forward'], 20.0)

    def test_booleans_are_not_numbers(self):
        u = multiples.rescale(row(per_forward=True), 2000.0)
        self.assertNotIn('per_forward', u)


class TestImplausibleRatio(unittest.TestCase):
    """株式分割や取得ミスで株価が飛んだときに、指標を巻き込まない。"""

    def test_daily_limit_rejects_a_tripling(self):
        with self.assertRaises(multiples.ImplausibleRatio):
            multiples.rescale(row(), 3000.0)

    def test_daily_limit_rejects_a_crash_to_a_third(self):
        with self.assertRaises(multiples.ImplausibleRatio):
            multiples.rescale(row(), 300.0)

    def test_backfill_allows_a_bigger_move(self):
        u = multiples.rescale(row(), 3000.0,
                              max_ratio=multiples.BACKFILL_MAX_RATIO)
        self.assertAlmostEqual(u['per_forward'], 30.0)

    def test_the_error_carries_the_numbers(self):
        with self.assertRaises(multiples.ImplausibleRatio) as cm:
            multiples.rescale(row(), 5000.0)
        self.assertAlmostEqual(cm.exception.ratio, 5.0)
        self.assertEqual(cm.exception.base_price, 1000.0)


class TestCurrencyIsNotAssumed(unittest.TestCase):
    """報告通貨がドルでも壊れないこと（比率なのでEPSを見ない）。"""

    def test_usd_reporter_keeps_a_sane_per(self):
        # 6269 三井海洋開発: eps は 3.23 ドル。株価は円
        r = row(stock_price=10180.0, per_forward=11.9165, eps=3.23)
        u = multiples.rescale(r, 9881.0)
        self.assertAlmostEqual(u['per_forward'], 11.9165 * 9881.0 / 10180.0)
        self.assertLess(u['per_forward'], 20.0, 'ドルのEPSで割った値になっている')


class TestExplicitBasePrice(unittest.TestCase):
    """さかのぼって直すときは、分析日の株価を明示して渡す。"""

    def test_base_price_overrides_the_row(self):
        # いまの stock_price は毎日の cron で書き換わっているので基準にできない
        r = row(stock_price=3180.0, per_forward=13.8294)
        u = multiples.rescale(r, 3180.0, base_price=2416.0,
                              max_ratio=multiples.BACKFILL_MAX_RATIO)
        self.assertAlmostEqual(u['per_forward'], 13.8294 * 3180.0 / 2416.0)
        self.assertAlmostEqual(u['per_forward'], 18.2, places=1)


class TestRescaleWithScore(unittest.TestCase):
    def test_score_is_recalculated(self):
        r = row()
        u = multiples.rescale_with_score(r, 2000.0)
        self.assertIn('match_rate', u)
        self.assertIn('score_complete', u)

    def test_nothing_to_do_returns_empty(self):
        self.assertEqual(multiples.rescale_with_score(row(), 1000.0), {})

    def test_score_moves_when_per_crosses_the_line(self):
        """PERが跳ね上がればスコアは下がる。据え置きにしない。

        スコアは判定できた項目が MIN_JUDGED_CRITERIA 以上ないと None になるので、
        点が出るだけの項目をそろえた行で確かめる。
        """
        import supabase_client as sc
        r = row(per_forward=8.0, pbr=0.8, equity_ratio=70.0,
                operating_margin=15.0, roa=8.0, operating_cf=50.0,
                free_cf=30.0, market_cap=500.0)
        cheap = sc.score_breakdown(r)
        self.assertIsNotNone(cheap['score'], '判定項目が足りずスコアが出ていない')

        u = multiples.rescale_with_score(r, 1900.0)   # PER 8 → 15.2
        expensive = sc.score_breakdown({**r, **u})
        self.assertEqual(u['match_rate'], expensive['score'])
        self.assertLess(expensive['score'], cheap['score'],
                        'PERが不合格になったのにスコアが下がっていない')


if __name__ == '__main__':
    unittest.main()
