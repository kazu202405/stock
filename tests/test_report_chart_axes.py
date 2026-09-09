# -*- coding: utf-8 -*-
"""企業レポートの負数がグラフ外へ落ちないための回帰テスト。"""

import os
import re
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class ReportChartAxisTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.template = read('templates', 'report_view.html')

    def test_signed_metrics_use_dynamic_lower_bounds(self):
        self.assertIn('function axisBounds(values)', self.template)
        self.assertIn('Math.min(...nums, 0)', self.template)
        self.assertIn('...axisBounds(RP.revenue.op_income)', self.template)
        self.assertIn('...axisBounds(RP.equity.roa)', self.template)

    def test_operating_income_and_roa_are_not_fixed_to_zero(self):
        for series in ('RP.revenue.op_income', 'RP.equity.roa'):
            scale = re.search(
                r"y1:\s*\{[^\n]+" + re.escape(series) + r"[^\n]+\}",
                self.template,
            )
            self.assertIsNotNone(scale, series)
            self.assertNotIn('min: 0', scale.group(0), series)


if __name__ == '__main__':
    unittest.main()
