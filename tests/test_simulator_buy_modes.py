"""買い方（端数の扱い）3モード。

実際には小数株は買えない。そこで買い方を選べるようにした:
  fraction … 端数も買う。ドルコスト平均法の理論値
  carry    … 1株単位。買えなかった端数を次回に回す（口座に残るので現実的）
  floor    … 1株単位。端数はその回では使わず、現金として積み上がる

⚠️ どのモードでも**積み立てた総額を分母にする**。端数を勘定から外すと
切り捨てだけ成績が良く見えてしまう。ここが一番間違えやすいので固定する。

単元株(100株)は入れていない。単元未満株で1株から買えるため。
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import simulator as sim


def bar(d, close):
    dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
    return {'time': int(dt.timestamp()), 'close': close}


class TestBuyableShares(unittest.TestCase):
    def test_fraction_allows_decimals(self):
        self.assertAlmostEqual(sim.buyable_shares(10000, 3000, 'fraction'), 10000 / 3000)

    def test_carry_and_floor_round_down(self):
        for mode in ('carry', 'floor'):
            self.assertEqual(sim.buyable_shares(10000, 3000, mode), 3.0)

    def test_cannot_afford_one_share(self):
        self.assertEqual(sim.buyable_shares(1000, 3000, 'carry'), 0.0)

    def test_zero_price_does_not_divide_by_zero(self):
        self.assertEqual(sim.buyable_shares(10000, 0, 'carry'), 0.0)


class TestLumpBuyModes(unittest.TestCase):
    def setUp(self):
        # 3,000円で買って4,000円になる
        self.hist = {'daily_1y': [bar('2024-01-05', 3000), bar('2024-06-05', 4000)]}

    def test_fraction_uses_all_cash(self):
        r = sim.simulate_lump(self.hist, '2024-01-05', '2024-06-05', 10000, 'fraction')
        self.assertEqual(r['cash'], 0)
        self.assertEqual(r['invested'], 10000)

    def test_floor_leaves_change_as_cash(self):
        r = sim.simulate_lump(self.hist, '2024-01-05', '2024-06-05', 10000, 'floor')
        self.assertEqual(r['shares'], 3.0)
        self.assertEqual(r['invested'], 9000)
        self.assertEqual(r['cash'], 1000)
        # 株12,000 + 現金1,000 = 13,000。積んだ10,000に対して +3,000
        self.assertEqual(r['value'], 12000)
        self.assertEqual(r['total'], 13000)
        self.assertEqual(r['profit'], 3000)

    def test_change_is_not_thrown_away(self):
        """余りを勘定から外すと切り捨てだけ得に見える。総資産に必ず含める。"""
        r = sim.simulate_lump(self.hist, '2024-01-05', '2024-06-05', 10000, 'floor')
        self.assertEqual(r['total'], r['value'] + r['cash'])
        self.assertEqual(r['deposited'], 10000)

    def test_cannot_buy_even_one_share(self):
        r = sim.simulate_lump(self.hist, '2024-01-05', '2024-06-05', 1000, 'floor')
        self.assertTrue(r['ok'])
        self.assertEqual(r['shares'], 0.0)
        self.assertEqual(r['cash'], 1000)
        self.assertEqual(r['profit'], 0)      # 現金のままなので増減なし
        self.assertEqual(r['buys'], [])


class TestMonthlyBuyModes(unittest.TestCase):
    def setUp(self):
        # 毎月 3,000円。10,000円ずつ積み立てる
        self.hist = {'daily_1y': [bar('2024-01-15', 3000), bar('2024-02-15', 3000),
                                  bar('2024-03-15', 3000)]}

    def _run(self, mode):
        return sim.simulate_monthly(self.hist, '2024-01-01', '2024-03-15',
                                    10000, 1, 15, mode)

    def test_floor_accumulates_cash(self):
        """毎回1,000円ずつ余る。3回で3,000円が寝る。"""
        r = self._run('floor')
        self.assertEqual(r['deposits'], 3)
        self.assertEqual(r['times'], 3)
        self.assertEqual(r['shares'], 9.0)      # 毎回3株
        self.assertEqual(r['cash'], 3000)
        self.assertEqual(r['deposited'], 30000)

    def test_carry_spends_the_change_later(self):
        """繰り越すと3回目に余りが効いて1株多く買える。"""
        r = self._run('carry')
        self.assertEqual(r['deposited'], 30000)
        self.assertGreater(r['shares'], 9.0)
        self.assertLess(r['cash'], 3000)

    def test_fraction_leaves_no_cash(self):
        r = self._run('fraction')
        self.assertEqual(r['cash'], 0)
        self.assertAlmostEqual(r['shares'], 10.0)

    def test_all_modes_share_the_same_denominator(self):
        """3モードで積立総額は同じ。ここがズレると比較にならない。"""
        totals = {m: self._run(m)['deposited'] for m in ('fraction', 'carry', 'floor')}
        self.assertEqual(set(totals.values()), {30000})

    def test_money_is_never_lost(self):
        """株＋現金が、積み立てた総額と釣り合うこと（株価が動かない前提）。"""
        for mode in ('fraction', 'carry', 'floor'):
            r = self._run(mode)
            self.assertAlmostEqual(r['invested'] + r['cash'], r['deposited'], delta=1,
                                   msg=f'{mode} でお金が消えている')

    def test_deposits_and_times_differ_when_too_poor_to_buy(self):
        """積んだ回数と買えた回数を分けて持つ。
        「36回積んで4回しか買えていない」が見えるようにするため。"""
        hist = {'daily_1y': [bar('2024-01-15', 8000), bar('2024-02-15', 8000),
                             bar('2024-03-15', 8000)]}
        r = sim.simulate_monthly(hist, '2024-01-01', '2024-03-15', 5000, 1, 15, 'carry')
        self.assertEqual(r['deposits'], 3)      # 3回積んだ
        self.assertEqual(r['times'], 1)         # 買えたのは1回だけ
        self.assertEqual(r['shares'], 1.0)

    def test_never_affordable_is_an_error_with_advice(self):
        hist = {'daily_1y': [bar('2024-01-15', 90000), bar('2024-02-15', 90000)]}
        r = sim.simulate_monthly(hist, '2024-01-01', '2024-02-15', 1000, 1, 15, 'floor')
        self.assertFalse(r['ok'])
        self.assertIn('1回も買えませんでした', r['reason'])


if __name__ == '__main__':
    unittest.main()
