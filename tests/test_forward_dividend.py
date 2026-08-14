"""予想配当利回りは自分で計算し、桁が合わなければ採らない。

2026-08-14 追加。実績（直近12か月に支払われた配当÷株価）だけを出して
いたため、決算期をまたいだ年が実態より高く見えていた
（367A: 実績165円で6.18% ／ 予想120円で4.24%）。

設計の要点2つをここで固定する。

1. **利回りは Yahoo の利回り値を使わず、配当額÷株価で出す。**
   yfinance は %（4.24）、yahooquery は小数（0.0424）で返す。この単位の
   推測が 2026-08-12 の「利回り47%」事故の原因だった。配当額（円）には
   単位の曖昧さが無い。

2. **確定した決算年度の配当と桁が合わなければ捨てる。**
   分割調整されていない配当額を掴むことがある
   （4918: 実際15円のところ150円 → 47.5%）。増配・減配では説明の
   つかない乖離は採らず、None にする。誤った数字より「不明」がよい。
"""
import os
import sys
import unittest

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_analyzer import forward_dividend_yield


class TestForwardDividendYield(unittest.TestCase):

    def test_実データで計算できる(self):
        """367A: 予想120円 ÷ 株価2832円 = 4.24%。確定年度は105円。"""
        self.assertAlmostEqual(
            forward_dividend_yield(120.0, 2832.0, confirmed_dps=105.0),
            4.2373, places=3)

    def test_単位を推測しない(self):
        """配当額から割るので、Yahooの利回りが小数でも%でも影響を受けない。"""
        self.assertAlmostEqual(
            forward_dividend_yield(76.0, 2103.5, confirmed_dps=76.0),
            3.6129, places=3)

    def test_分割調整漏れは採らない(self):
        """4918の事故パターン。確定年度15円に対し予想150円は10倍で説明がつかない。"""
        self.assertIsNone(
            forward_dividend_yield(150.0, 316.0, confirmed_dps=15.0))

    def test_妥当な増配は採る(self):
        """105円 → 120円のような増配は通す（捨てすぎると意味がない）。"""
        self.assertIsNotNone(
            forward_dividend_yield(120.0, 2832.0, confirmed_dps=105.0))

    def test_妥当な減配も採る(self):
        self.assertIsNotNone(
            forward_dividend_yield(50.0, 2000.0, confirmed_dps=100.0))

    def test_利回りの上限を超えたら採らない(self):
        """20%超は、増配ではなく取り違えを疑う。"""
        self.assertIsNone(
            forward_dividend_yield(500.0, 1000.0, confirmed_dps=480.0))

    def test_無配はゼロでなくNone(self):
        """「配当を出していない」と「利回りが0%」は別物。0.0だと見分けがつかない。"""
        self.assertIsNone(forward_dividend_yield(0, 2000.0))

    def test_値が欠けていればNone(self):
        self.assertIsNone(forward_dividend_yield(None, 2000.0))
        self.assertIsNone(forward_dividend_yield(100.0, None))
        self.assertIsNone(forward_dividend_yield(100.0, 0))
        self.assertIsNone(forward_dividend_yield('---', 2000.0))

    def test_確定年度が無くても計算はする(self):
        """検証材料が無いだけで、上限の確認は効いている。"""
        self.assertIsNotNone(forward_dividend_yield(100.0, 2000.0))
        self.assertIsNone(forward_dividend_yield(9000.0, 2000.0))


if __name__ == '__main__':
    unittest.main()
