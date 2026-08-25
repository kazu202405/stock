"""増減率・流動比率の派生列（2026-08-25 に追加）。

スコアの12項目は financial_history から都度計算しているので列が要らないが、
スクリーナーはDB側で絞るため列が要る。それまで成長率の列は**全銘柄で空**で、
「増収率10%以上」で探すことができなかった。

⚠️ 主眼は2つ。
  1. スコアが出す増減率と、絞り込みに使う列の値が**同じであること**。
     違うと「スコアでは増収なのに増収率で絞ると出てこない」が起きる。
  2. 派生値なので**元の値と一緒に動かすこと**。決算で財務履歴が入れ替わった
     のに増減率が古いままだと、正しい売上高と古い増減率が並ぶ
     （片方が正しいので壊れて見えない）。
"""

import os
import re
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from analysis_quality import GROWTH_COLUMNS, derive_growth_columns

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def row(revenue, op_income=None, forecast_revenue=None, forecast_op=None,
        current_assets=None, current_liabilities=None):
    """[(日付, 億円), ...] で渡す。新しい順でも古い順でもよい。"""
    def series(pairs):
        return [{'date': d, 'value': v * 1e8} for d, v in (pairs or [])]
    return {
        'financial_history': json.dumps({
            'revenue': series(revenue),
            'op_income': series(op_income),
        }),
        'cf_history': json.dumps({
            'current_assets': series(current_assets),
            'current_liabilities': series(current_liabilities),
        }),
        'forecast_revenue': forecast_revenue,
        'forecast_op_income': forecast_op,
    }


class GrowthMathTest(unittest.TestCase):

    def test_直近と1期前で増減率を出す(self):
        out = derive_growth_columns(row(
            [('2024-03-31', 100), ('2025-03-31', 110), ('2026-03-31', 121)]))

        self.assertAlmostEqual(out['revenue_cy'], 121)
        self.assertAlmostEqual(out['revenue_1y'], 110)
        self.assertAlmostEqual(out['revenue_2y'], 100)
        self.assertAlmostEqual(out['revenue_growth_1y_cy'], 10.0)
        self.assertAlmostEqual(out['revenue_growth_2y_1y'], 10.0)

    def test_今期予想との比較(self):
        out = derive_growth_columns(row(
            [('2026-03-31', 100)], forecast_revenue=120))

        self.assertAlmostEqual(out['revenue_ny'], 120)
        self.assertAlmostEqual(out['revenue_growth_cy_ny'], 20.0)

    def test_日付の順に依存しない(self):
        asc = derive_growth_columns(row(
            [('2024-03-31', 100), ('2025-03-31', 110)]))
        desc = derive_growth_columns(row(
            [('2025-03-31', 110), ('2024-03-31', 100)]))
        self.assertEqual(asc, desc)

    def test_分母がマイナスなら判定不能(self):
        """赤字から赤字縮小は「改善」だが、式は -50% を返してしまう。
        スコアの evaluate_score_criteria と同じ扱いにそろえる。"""
        out = derive_growth_columns(row(
            [('2025-03-31', -10), ('2026-03-31', -5)],
            op_income=[('2025-03-31', -10), ('2026-03-31', -5)]))

        self.assertIsNone(out['revenue_growth_1y_cy'])
        self.assertIsNone(out['op_growth_1y_cy'])

    def test_1期分しか無ければ増減率は出さない(self):
        out = derive_growth_columns(row([('2026-03-31', 100)]))
        self.assertIsNone(out['revenue_growth_1y_cy'])
        self.assertAlmostEqual(out['revenue_cy'], 100)

    def test_履歴が無くても落ちない(self):
        out = derive_growth_columns({'financial_history': None, 'cf_history': None})
        for key in GROWTH_COLUMNS:
            self.assertIsNone(out[key], key)


class CurrentRatioTest(unittest.TestCase):

    def test_同じ決算期どうしでだけ割る(self):
        """期がずれた流動資産と流動負債を割ると、増資や大型返済のあった年に
        実態と違う比率が出る。"""
        same = derive_growth_columns(row(
            [('2026-03-31', 100)],
            current_assets=[('2026-03-31', 150)],
            current_liabilities=[('2026-03-31', 100)]))
        self.assertAlmostEqual(same['current_ratio'], 150.0)

        shifted = derive_growth_columns(row(
            [('2026-03-31', 100)],
            current_assets=[('2026-03-31', 150)],
            current_liabilities=[('2025-03-31', 100)]))
        self.assertIsNone(shifted['current_ratio'])

    def test_流動負債がゼロなら出さない(self):
        out = derive_growth_columns(row(
            [('2026-03-31', 100)],
            current_assets=[('2026-03-31', 150)],
            current_liabilities=[('2026-03-31', 0)]))
        self.assertIsNone(out['current_ratio'])


class MatchesScoreTest(unittest.TestCase):
    """スコアが出す増減率と、絞り込みに使う列の値が同じであること。"""

    def test_スコアと同じ式(self):
        import supabase_client

        data = row([('2024-03-31', 100), ('2025-03-31', 110), ('2026-03-31', 121)],
                   forecast_revenue=133.1)
        derived = derive_growth_columns(data)
        score = {i['key']: i for i in supabase_client.evaluate_score_criteria(data)}

        # スコアの「売上高増減率(2期前→前期)」= 直近 ÷ 1期前
        self.assertEqual(score['revenue_growth']['display'],
                         '%.1f%%' % derived['revenue_growth_1y_cy'])
        # スコアの「売上高増減率(前期→今期予)」= 今期予想 ÷ 直近
        self.assertEqual(score['revenue_forecast']['display'],
                         '%.1f%%' % derived['revenue_growth_cy_ny'])


class StaysFreshTest(unittest.TestCase):
    """派生値は元の値と一緒に動かす。"""

    def test_毎晩作り直している(self):
        source = read('app.py')
        self.assertIn('_recalculate_growth_columns()', source)
        self.assertIn('import backfill_growth_columns', source)
        # 日足とスコアの更新と同じ流れで呼ばれること
        block = source.split('def scheduled_update_daily_and_crosses', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('_recalculate_growth_columns()', block)

    def test_外部へ取りに行かない(self):
        """手元の履歴と会社予想から計算するだけ。"""
        source = read('backfill_growth_columns.py')
        for word in ('yfinance', 'requests', 'yahoo', 'http'):
            self.assertNotIn(word, source.lower().split('"""')[2], word)


class ScreenerWiringTest(unittest.TestCase):

    def test_絞り込みで使える(self):
        block = read('app.py').split('SCREEN_FILTERS = {', 1)[1].split('\n}', 1)[0]
        for param, column in (('revenue_growth_min', 'revenue_growth_1y_cy'),
                              ('op_growth_min', 'op_growth_1y_cy'),
                              ('revenue_forecast_growth_min', 'revenue_growth_cy_ny'),
                              ('current_ratio_min', 'current_ratio')):
            self.assertIn("'%s': ('%s'" % (param, column), block, param)

    def test_画面にも入力欄がある(self):
        html = read('templates', 'screener.html')
        for key in ('revenue_growth_min', 'op_growth_min',
                    'revenue_forecast_growth_min', 'current_ratio_min'):
            self.assertIn('filters.%s' % key, html, key)


if __name__ == '__main__':
    unittest.main()
