"""有利子負債・利益剰余金の取り込みと、cf_history の組み立て。

2026-08-25。銘柄ページの財務健全性は現預金と流動負債しか出しておらず、
「利息の付く借金がいくらあるか」が分からなかった。

⚠️ このテストの主眼は2つ。
  1. yfinance の 'Total Debt' を**総負債と取り違えない**こと
     （Total Debt は有利子負債。総負債は Total Liabilities Net Minority Interest）
  2. cf_history の組み立てが1か所に集約されたままであること。
     以前は同じ辞書が app.py の3箇所にあり、片方だけ項目を足す事故が起きた。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import pandas as pd

from analysis_quality import build_cf_history
import backfill_balance_debt as bbd


def _balance_sheet():
    """367A プレミアグループの実データ（2025-08期・2024-08期）を模した表。"""
    return pd.DataFrame(
        {
            pd.Timestamp('2025-08-31'): {
                'Total Debt': 18072000000.0,
                'Current Debt': 996000000.0,
                'Long Term Debt': 12447000000.0,
                'Capital Lease Obligations': 4629000000.0,
                'Retained Earnings': 6278000000.0,
                'Stockholders Equity': 18051000000.0,
                'Total Assets': 45949000000.0,
                'Total Liabilities Net Minority Interest': 27898000000.0,
            },
            pd.Timestamp('2024-08-31'): {
                'Total Debt': 20660000000.0,
                'Current Debt': 900000000.0,
                'Long Term Debt': 14000000000.0,
                'Capital Lease Obligations': 5760000000.0,
                'Retained Earnings': 2710000000.0,
                'Stockholders Equity': 14000000000.0,
                'Total Assets': 42000000000.0,
                'Total Liabilities Net Minority Interest': 28000000000.0,
            },
        }
    )


class ExtractSeriesTest(unittest.TestCase):

    def test_有利子負債と利益剰余金を年度ごとに取る(self):
        series = bbd.extract_series(_balance_sheet())

        self.assertEqual(series['interest_bearing_debt'], [
            {'date': '2025-08-31', 'value': 18072000000.0},
            {'date': '2024-08-31', 'value': 20660000000.0},
        ])
        self.assertEqual(series['retained_earnings'], [
            {'date': '2025-08-31', 'value': 6278000000.0},
            {'date': '2024-08-31', 'value': 2710000000.0},
        ])

    def test_有利子負債は総負債ではない(self):
        """取り違えると自己資本比率が実際より高く出る（39%→50%）。"""
        bs = _balance_sheet()
        series = bbd.extract_series(bs)
        debt = series['interest_bearing_debt'][0]['value']
        total_liabilities = bs.loc['Total Liabilities Net Minority Interest',
                                   pd.Timestamp('2025-08-31')]

        self.assertLess(debt, total_liabilities)
        # 短期＋長期＋リースの合計になっている
        col = pd.Timestamp('2025-08-31')
        self.assertAlmostEqual(
            debt,
            bs.loc['Current Debt', col] + bs.loc['Long Term Debt', col]
            + bs.loc['Capital Lease Obligations', col],
            places=2)

    def test_欠損年度は飛ばして詰めない(self):
        bs = _balance_sheet()
        bs.loc['Retained Earnings', pd.Timestamp('2024-08-31')] = float('nan')
        series = bbd.extract_series(bs)

        self.assertEqual([r['date'] for r in series['retained_earnings']],
                         ['2025-08-31'])
        # 有利子負債の方は2年とも残る（片方の欠損に巻き込まれない）
        self.assertEqual(len(series['interest_bearing_debt']), 2)

    def test_行が無い銘柄では空になる(self):
        bs = pd.DataFrame({pd.Timestamp('2025-08-31'): {'Total Assets': 1.0}})
        series = bbd.extract_series(bs)

        self.assertEqual(series['interest_bearing_debt'], [])
        self.assertEqual(series['retained_earnings'], [])

    def test_空の貸借対照表でも落ちない(self):
        series = bbd.extract_series(pd.DataFrame())
        self.assertEqual(series['interest_bearing_debt'], [])

    def test_取得済み判定は値のある行を見る(self):
        self.assertFalse(bbd._has_debt({}))
        self.assertFalse(bbd._has_debt({'interest_bearing_debt': []}))
        self.assertFalse(bbd._has_debt(
            {'interest_bearing_debt': [{'date': '2025-08-31', 'value': None}]}))
        self.assertTrue(bbd._has_debt(
            {'interest_bearing_debt': [{'date': '2025-08-31', 'value': 1.0}]}))


class BuildCfHistoryTest(unittest.TestCase):

    def test_有利子負債と利益剰余金が入る(self):
        stock_data = {
            'interest_bearing_debt': [{'date': '2025-08-31', 'value': 1.0}],
            'retained_earnings': [{'date': '2025-08-31', 'value': 2.0}],
        }
        cf = build_cf_history(stock_data)

        self.assertEqual(cf['interest_bearing_debt'], stock_data['interest_bearing_debt'])
        self.assertEqual(cf['retained_earnings'], stock_data['retained_earnings'])

    def test_無い項目は空配列で埋まる(self):
        cf = build_cf_history({})
        for key in ('operating_cf', 'cash', 'current_liabilities', 'current_assets',
                    'interest_bearing_debt', 'retained_earnings',
                    'equity_ratio', 'roe', 'roa'):
            self.assertEqual(cf[key], [], key)

    def test_保存パスが1か所に集約されている(self):
        """app.py に cf_history の辞書リテラルが復活していないこと。

        3箇所に同じ辞書があった頃は、項目を足すときに片方を直し忘れると
        通った保存パスによって銘柄ごとに項目の有無が変わった。
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'app.py'), encoding='utf-8') as f:
            source = f.read()

        self.assertNotIn("'current_liabilities': stock_data.get(", source)
        self.assertEqual(source.count('cf_history = build_cf_history(stock_data)'), 3)


if __name__ == '__main__':
    unittest.main()
