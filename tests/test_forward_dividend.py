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

from stock_analyzer import (annualized_dividend_from_payments,
                            forecast_annual_dividend, forward_dividend_yield,
                            trailing_dividend_yield_from_payments)


class TestForecastAnnualDividend(unittest.TestCase):
    """予想配当は決算年度に分けて数える。

    2026-08-14。「直近1回 × 年間回数」だけでは、直近の支払いが
    〈前期を締めた期末配当〉のときに破綻する。日本企業は中間と期末で
    額が違うことが多く、全3,127銘柄で確定年度と比べたところ
    0.6倍未満が28件・1.6倍超が57件あった。
    """

    TODAY = '2026-08-14'

    def _run(self, payments, fiscal_end_month):
        return forecast_annual_dividend(payments, fiscal_end_month, today=self.TODAY)

    def test_進行中年度に支払いが無ければ前期並みと見る(self):
        """7273 イクヨ(3月決算): 中間30円+期末3円=年33円。

        直近は期末3円で、これは前期を締めたもの。年換算して6円にすると
        実態の1/5になる。
        """
        self.assertEqual(self._run([
            ('2025-03-28', 3.0), ('2025-09-29', 30.0), ('2026-03-30', 3.0)], 3), 33.0)

    def test_残りは前期の対応する回の額で埋める(self):
        """7505 扶桑電通(**9月決算**): 中間7.5円 + 期末79.5円 = 年87円。

        進行中の年度は中間7.5円まで。「直近の額×残り回数」だと期末も
        7.5円と見て年15円になる。前期の期末79.5円を当てて87円。

        ⚠️ この銘柄を3月決算だと思い込んで実装し、本番データで初めて
        9月決算だと分かった。決算月は推測せず fiscal_month を見る。
        """
        self.assertEqual(self._run([
            ('2024-09-27', 39.0), ('2025-03-28', 7.5),
            ('2025-09-29', 79.5), ('2026-03-30', 7.5)], 9), 87.0)

    def test_進行中の増配は残りにも反映する(self):
        """中間が前期の2倍なら、期末も2倍と見て年間を出す。"""
        self.assertEqual(self._run([
            ('2025-03-28', 10.0), ('2025-09-29', 20.0), ('2026-03-30', 20.0)], 9),
            60.0)

    def test_増配していても前期の合計を採る(self):
        """5706 三井金属(3月決算): 90→100→145と増配中でも、進行中年度に
        支払いが無いうちは前期合計245円。増配分は次の支払いで反映される。"""
        self.assertEqual(self._run([
            ('2025-03-28', 90.0), ('2025-09-29', 100.0), ('2026-03-30', 145.0)], 3),
            245.0)

    def test_進行中年度に支払いがあれば残りを直近額で埋める(self):
        """367A(8月決算): 前期は期末105円のみ、当期は中間60円まで。

        前期の件数(1回)だけで判断すると年60円になる。直近12か月に2回
        払っているので年2回とみなし、60+60=120円。
        """
        self.assertEqual(self._run([
            ('2025-08-28', 105.0), ('2026-02-26', 60.0)], 8), 120.0)

    def test_年1回払いを2倍にしない(self):
        """2737 トーメンデバイス(3月決算): 年1回540円。"""
        self.assertEqual(self._run([
            ('2025-03-28', 300.0), ('2026-03-30', 540.0)], 3), 540.0)

    def test_12月決算(self):
        """6871 日本マイクロニクス: 年1回95円。"""
        self.assertEqual(self._run([
            ('2024-12-27', 70.0), ('2025-12-29', 95.0)], 12), 95.0)

    def test_四半期払い(self):
        self.assertEqual(self._run([
            ('2024-12-27', 38.0), ('2025-06-27', 38.0),
            ('2025-12-29', 38.0), ('2026-06-26', 38.0)], 12), 76.0)

    def test_決算月が不明なら3月として扱う(self):
        """日本企業で最も多い。決算月が無いだけで予想を捨てない。"""
        self.assertEqual(self._run([
            ('2025-03-28', 3.0), ('2025-09-29', 30.0), ('2026-03-30', 3.0)], None),
            33.0)

    def test_確定年度がまだ無ければ進行中の実績を返す(self):
        """上場直後。半端でも「これまでに払った額」は事実。"""
        self.assertEqual(self._run([('2026-02-26', 60.0)], 8), 60.0)

    def test_無配と空(self):
        self.assertIsNone(self._run([], 3))
        self.assertIsNone(self._run(None, 3))

    def test_未来日付の支払いは数えない(self):
        self.assertEqual(self._run([
            ('2025-03-28', 3.0), ('2025-09-29', 30.0),
            ('2026-03-30', 3.0), ('2026-09-29', 99.0)], 3), 33.0)


class TestAnnualizedDividendFromPayments(unittest.TestCase):
    """予想配当は支払い実績から自分で年換算する（Yahooの要約値を使わない）。

    2026-08-14 に判明: `info`/`summary_detail` の `dividendRate` は株式併合に
    追随しないことがある。`lastDividendValue` を2倍しただけの、併合前の額で
    止まっていた銘柄が4つ見つかった。支払い実績は調整済みなので狂わない。
    """

    TODAY = '2026-08-14'

    def _run(self, payments):
        return annualized_dividend_from_payments(payments, today=self.TODAY)

    def test_年2回払いは直近の1回を2倍する(self):
        """367A: 中間60円 → 年120円。Yahooの値とも一致する。"""
        self.assertEqual(self._run([
            ('2025-08-28', 105.0), ('2026-02-26', 60.0)]), 120.0)

    def test_併合済み銘柄でも実額で計算する(self):
        """5706 三井金属: Yahooは28円（併合前の14円×2）。実績なら145×2=290円。"""
        self.assertEqual(self._run([
            ('2024-09-27', 90.0), ('2025-03-28', 90.0),
            ('2025-09-29', 100.0), ('2026-03-30', 145.0)]), 290.0)

    def test_併合済み銘柄2(self):
        """8377 ほくほく: Yahooは15円（7.5×2）。実績なら65×2=130円。"""
        self.assertEqual(self._run([
            ('2024-09-27', 22.5), ('2025-03-28', 27.5),
            ('2025-09-29', 45.0), ('2026-03-30', 65.0)]), 130.0)

    def test_年1回払いは倍にしない(self):
        """2737 トーメンデバイス: 年1回540円。2倍すると実態の倍になる。"""
        self.assertEqual(self._run([
            ('2025-03-28', 300.0), ('2026-03-30', 540.0)]), 540.0)

    def test_直近1年に支払いが無ければNone(self):
        """無配。0.0 にすると配当を出している企業と見分けがつかない。"""
        self.assertIsNone(self._run([('2024-03-28', 50.0)]))

    def test_空とNone(self):
        self.assertIsNone(self._run([]))
        self.assertIsNone(self._run(None))

    def test_未来日付の支払いは数えない(self):
        """予定が入っていても実績ではない。"""
        self.assertEqual(self._run([
            ('2026-02-26', 60.0), ('2026-12-01', 60.0)]), 60.0)

    def test_壊れた行は飛ばす(self):
        self.assertEqual(self._run([
            ('2026-02-26', 60.0), ('日付なし', 'x'), (None, None)]), 60.0)

    def test_順不同でも直近を選ぶ(self):
        self.assertEqual(self._run([
            ('2026-02-26', 60.0), ('2025-08-28', 105.0)]), 120.0)


class TestTrailingDividendYieldFromPayments(unittest.TestCase):
    """実績利回りは支払い実績の合計÷株価。判定は1か所にまとめる。

    分析側（StockAnalyzer._trailing_dividend_yield）とバックフィルで
    違う値が出ると、どちらが正か分からなくなる。
    """

    def test_直近12か月の合計で計算する(self):
        """7505 扶桑電通: 期末79.5円 + 中間7.5円 = 87円 ÷ 2277円。"""
        value = trailing_dividend_yield_from_payments(
            [('2024-09-27', 39.0), ('2025-09-29', 79.5), ('2026-03-30', 7.5)],
            2277.0, today='2026-07-21')
        self.assertAlmostEqual(value, 3.8208, places=3)

    def test_窓の外の支払いは数えない(self):
        value = trailing_dividend_yield_from_payments(
            [('2024-09-27', 39.0)], 2277.0, today='2026-07-21')
        self.assertIsNone(value)

    def test_上限を超えたら採らない(self):
        """分割・単位の取り違えを疑う。誤った数字より「不明」がよい。"""
        self.assertIsNone(trailing_dividend_yield_from_payments(
            [('2026-03-30', 500.0)], 1000.0, today='2026-07-21'))

    def test_無配と欠損(self):
        self.assertIsNone(trailing_dividend_yield_from_payments([], 1000.0))
        self.assertIsNone(trailing_dividend_yield_from_payments(None, 1000.0))
        self.assertIsNone(trailing_dividend_yield_from_payments(
            [('2026-03-30', 10.0)], None))
        self.assertIsNone(trailing_dividend_yield_from_payments(
            [('2026-03-30', 10.0)], 0))


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
