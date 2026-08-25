"""出来高・売買代金・回転日数（2026-08-25 追加）。

出来高は yfinance のレスポンスに最初から入っていたのに、price_history.py が
読まずに捨てていた。そのため「その銘柄が1日どれくらい売買されているか」を
測る手段が無く、信用倍率も倍率だけで、重いのか軽いのか決められなかった。

⚠️ 主眼は2つ。
  1. 1日だけの値を使わないこと。決算発表日や指数入れ替えを拾って、
     普段の姿にならない。20営業日で均す。
  2. 週足・月足の volume は期間の合計であって「1日あたり」ではない。
     集約後のデータから流動性を出さないこと。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import price_history as ph


def bar(day, close, volume):
    """1日ぶんの足。time は日付の順序さえ合っていればよい。"""
    return {'time': 1700000000 + day * 86400, 'open': close, 'high': close,
            'low': close, 'close': close, 'volume': volume}


class LiquiditySummaryTest(unittest.TestCase):

    def test_直近20営業日の平均を出す(self):
        # 古い30日は出来高100、直近20日は出来高200。直近だけを見ること。
        rows = [bar(i, 1000, 100) for i in range(30)]
        rows += [bar(30 + i, 1000, 200) for i in range(20)]

        s = ph.liquidity_summary(rows)

        self.assertEqual(s['days'], 20)
        self.assertAlmostEqual(s['avg_volume'], 200.0)
        self.assertAlmostEqual(s['avg_turnover'], 200 * 1000)

    def test_1日の跳ねに引きずられない(self):
        """決算発表日に出来高が20倍になっても、平均は2倍未満に収まる。"""
        rows = [bar(i, 1000, 100) for i in range(19)] + [bar(19, 1000, 2000)]

        s = ph.liquidity_summary(rows)

        self.assertLess(s['avg_volume'], 200)
        self.assertGreater(s['avg_volume'], 100)

    def test_売買代金は日ごとに掛けてから平均する(self):
        """平均出来高×平均株価では、値動きの大きい銘柄でずれる。"""
        rows = [bar(0, 1000, 100), bar(1, 2000, 900)]

        s = ph.liquidity_summary(rows)

        # 正: (100*1000 + 900*2000) / 2 = 950,000
        # 誤: 平均出来高500 × 平均株価1500 = 750,000
        self.assertAlmostEqual(s['avg_turnover'], 950000.0)

    def test_20営業日に満たなくても出す(self):
        rows = [bar(i, 1000, 100) for i in range(5)]

        s = ph.liquidity_summary(rows)

        self.assertEqual(s['days'], 5)

    def test_出来高が無い足は数えない(self):
        """保存済みの古いデータには volume が入っていない。"""
        rows = [bar(0, 1000, None), bar(1, 1000, 100)]
        rows[0]['volume'] = None

        s = ph.liquidity_summary(rows)

        self.assertEqual(s['days'], 1)
        self.assertAlmostEqual(s['avg_volume'], 100.0)

    def test_全部欠けていれば出さない(self):
        rows = [bar(0, 1000, None)]
        rows[0]['volume'] = None

        self.assertIsNone(ph.liquidity_summary(rows))
        self.assertIsNone(ph.liquidity_summary([]))


class TurnoverDaysTest(unittest.TestCase):

    def test_買残が平均何日分か(self):
        self.assertAlmostEqual(ph.margin_turnover_days(500000, 100000), 5.0)

    def test_材料が欠けたら出さない(self):
        self.assertIsNone(ph.margin_turnover_days(None, 100000))
        self.assertIsNone(ph.margin_turnover_days(500000, None))
        self.assertIsNone(ph.margin_turnover_days(500000, 0))


class DownsampleVolumeTest(unittest.TestCase):

    def test_週足の出来高は合計(self):
        """平均でも最後の値でもない。その週の商いの総量。"""
        rows = [bar(i, 1000, 100) for i in range(5)]  # 同じ週に入る5営業日

        weekly = ph.downsample(rows, 'weekly')

        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0]['volume'], 500)

    def test_出来高の無い足が混ざっても落ちない(self):
        rows = [bar(0, 1000, None), bar(1, 1000, 100)]
        rows[0]['volume'] = None

        weekly = ph.downsample(rows, 'weekly')

        self.assertEqual(weekly[0]['volume'], 100)


class ApiContractTest(unittest.TestCase):

    def test_日足のときだけ流動性を返す(self):
        """週足・月足の volume は期間合計なので「1日あたり」にならない。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'app.py'), encoding='utf-8') as f:
            source = f.read()

        self.assertIn("ph.liquidity_summary(rows) if granularity == 'daily' else None",
                      source)

    def test_両方の取得経路が出来高を読む(self):
        """1銘柄ずつの fetch_ohlc とバッチの fetch_ohlc_batch がある。
        片方だけだと、通った経路によって出来高の有無が変わる。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'price_history.py'), encoding='utf-8') as f:
            source = f.read()

        self.assertEqual(source.count("row['Volume']"), 2)


if __name__ == '__main__':
    unittest.main()
