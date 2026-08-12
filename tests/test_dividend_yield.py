"""配当利回りは「実際に支払われた配当 ÷ 株価」で出す。

2026-08-12 のバグ修正を固定するテスト。スクリーナーを配当利回り順に並べると
40%超の銘柄が並んでいた。原因は Yahoo の要約値をそのまま信じていたこと。

  - `dividendYield` は常に%（0.4 は 0.4%）。それを「0.5未満なら小数」と推測して
    100倍する分岐があり、利回り0.5%未満の銘柄を軒並み壊していた
    （9720: 0.4% → 40%、153A: 0.43% → 43%）
  - `trailingAnnualDividendRate` は分割調整されないことがある
    （4918: 実際15円のところ150円 → 47.5%）

支払い履歴 `ticker.dividends` は分割調整済みなので、単位を推測しなくてよい。
"""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeTicker:
    """`ticker.dividends` だけを持つスタブ。"""

    def __init__(self, dividends):
        self.dividends = dividends


def _series(pairs):
    """[(日付文字列, 金額)] から tz付きの配当履歴を作る。"""
    if not pairs:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([p[0] for p in pairs]).tz_localize('Asia/Tokyo')
    return pd.Series([p[1] for p in pairs], index=idx)


def _recent(days_ago):
    return (pd.Timestamp.now(tz='Asia/Tokyo') - pd.Timedelta(days=days_ago)
            ).strftime('%Y-%m-%d')


class TrailingDividendYieldTest(unittest.TestCase):
    def setUp(self):
        from stock_analyzer import StockAnalyzer
        self.analyzer = StockAnalyzer()

    def test_sums_the_last_twelve_months(self):
        """中間配当と期末配当を足して年間利回りにする。"""
        ticker = _FakeTicker(_series([(_recent(200), 12.5), (_recent(30), 12.5)]))
        self.assertEqual(
            self.analyzer._trailing_dividend_yield(ticker, 1000.0), 2.5)

    def test_ignores_payments_older_than_a_year(self):
        ticker = _FakeTicker(_series([(_recent(500), 50.0), (_recent(10), 10.0)]))
        self.assertEqual(
            self.analyzer._trailing_dividend_yield(ticker, 1000.0), 1.0)

    def test_low_yield_stays_low(self):
        """9720 の再現。0.4% が 40% になっていた。

        小数と%を値の大きさで見分けようとすると、必ずこの帯で誤る。
        """
        ticker = _FakeTicker(_series([(_recent(60), 25.0)]))
        self.assertAlmostEqual(
            self.analyzer._trailing_dividend_yield(ticker, 6280.0), 0.3981, places=3)

    def test_no_dividend_history_returns_none(self):
        self.assertIsNone(
            self.analyzer._trailing_dividend_yield(_FakeTicker(_series([])), 1000.0))

    def test_suspended_dividend_returns_none_not_zero(self):
        """無配は 0.0 ではなく None。

        0.0 を入れると画面に「0.00%」と出て、配当を出している企業と
        見分けがつかなくなる。「出していない」と「0%」は違う。
        """
        ticker = _FakeTicker(_series([(_recent(800), 30.0)]))
        self.assertIsNone(self.analyzer._trailing_dividend_yield(ticker, 1000.0))

    def test_absurd_yield_is_dropped(self):
        """4918 の再現。分割未調整の配当額が来たら採らない。

        150円の配当が304円の株から出ることはない。誤った数字を出すより
        「不明」の方がよい。
        """
        ticker = _FakeTicker(_series([(_recent(60), 150.0)]))
        self.assertIsNone(self.analyzer._trailing_dividend_yield(ticker, 304.0))

    def test_missing_price_returns_none(self):
        ticker = _FakeTicker(_series([(_recent(60), 10.0)]))
        self.assertIsNone(self.analyzer._trailing_dividend_yield(ticker, None))
        self.assertIsNone(self.analyzer._trailing_dividend_yield(ticker, 0))

    def test_broken_ticker_does_not_raise(self):
        class Boom:
            @property
            def dividends(self):
                raise RuntimeError('yfinance error')

        self.assertIsNone(self.analyzer._trailing_dividend_yield(Boom(), 1000.0))


if __name__ == '__main__':
    unittest.main()
