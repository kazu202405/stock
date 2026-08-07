"""欠損理由の分類のリグレッション。

実データを数えると、欠損の大半は取得の失敗ではない。
  PER欠損244件 → 赤字213 / ETF等21 / 本当に取得元に無いのは数件
一律に「取得元にデータなし」と出すと事実と違ううえ誤解を広げるため、
「その指標が存在しない」ケースを先に切り分ける。
"""

import os
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from data_gaps import classify, classify_missing_fields, has_no_financials


class ClassifyTest(unittest.TestCase):
    def test_loss_making_company_has_no_per(self):
        """赤字は取得の問題ではない。指標として存在しない。"""
        row = {'financial_history': {'eps': [{'date': '2026-03-31', 'value': -20.0}],
                                     'net_income': [{'date': '2026-03-31', 'value': -1e8}]}}
        info = classify('per', row)
        self.assertEqual(info['status'], 'loss_making')
        self.assertIn('赤字', info['message'])
        self.assertIsNone(info['source'])

    def test_etf_has_no_financial_metrics(self):
        """ETFに決算が無いのは取得元のせいではない"""
        row = {'company_code': '1305', 'financial_history': {}, 'source_status': {}}
        for field in ('per', 'pbr', 'equity_ratio', 'cash'):
            info = classify(field, row)
            self.assertEqual(info['status'], 'no_financials', field)
            self.assertIn('ETF', info['message'])

    def test_negative_equity_has_no_pbr(self):
        row = {'financial_history': {'net_income': [{'date': '2026-03-31', 'value': 1e8}],
                                     'bps': [{'date': '2026-03-31', 'value': -50.0}]}}
        info = classify('pbr', row)
        self.assertEqual(info['status'], 'negative_equity')

    def test_pro_market_has_no_margin_ratio(self):
        row = {'market': 'TOKYO PRO Market', 'source_status': {}}
        info = classify('margin_trading_ratio', row)
        self.assertEqual(info['status'], 'not_applicable')

    def test_names_the_source_only_when_it_is_the_source_problem(self):
        """取得元の名前を出すのは、本当に取得元に無いときだけ"""
        row = {'financial_history': {'net_income': [{'date': '2026-03-31', 'value': 1e8}],
                                     'revenue': [{'date': '2026-03-31', 'value': 1e9}]}}
        info = classify('pbr', row)
        self.assertEqual(info['status'], 'no_data')
        self.assertIn('Yahoo', info['source'])

    def test_fetch_failure_is_distinct_from_missing_data(self):
        row = {'source_status': {'margin_trading': {'status': 'rate_limited',
                                                    'source': 'Yahoo Japan'}},
               'financial_history': {'net_income': [{'date': '2026-03-31', 'value': 1e8}]}}
        info = classify('margin_trading_ratio', row)
        self.assertEqual(info['status'], 'fetch_failed')

    def test_never_attempted_is_distinct(self):
        row = {'source_status': {},
               'financial_history': {'net_income': [{'date': '2026-03-31', 'value': 1e8}]}}
        info = classify('margin_trading_ratio', row)
        self.assertEqual(info['status'], 'not_attempted')

    def test_not_disclosed_is_distinct(self):
        row = {'source_status': {'margin_trading': {'status': 'not_disclosed'}},
               'financial_history': {'net_income': [{'date': '2026-03-31', 'value': 1e8}]}}
        self.assertEqual(
            classify('margin_trading_ratio', row)['status'], 'not_disclosed')

    def test_detects_missing_financials(self):
        self.assertTrue(has_no_financials({'financial_history': {}}))
        self.assertFalse(has_no_financials(
            {'financial_history': {'revenue': [{'date': '2026-03-31', 'value': 1.0}]}}))


class ClassifyMissingFieldsTest(unittest.TestCase):
    def test_fields_with_values_are_not_reported(self):
        row = {'per_forward': 15.0, 'pbr': 2.0, 'financial_history': {},
               'source_status': {}}
        result = classify_missing_fields(row, ('per', 'pbr'))
        self.assertEqual(result, {})

    def test_per_reads_the_per_forward_column(self):
        """列名は per_forward だが分類上の項目名は per"""
        row = {'per_forward': None, 'financial_history':
               {'eps': [{'date': '2026-03-31', 'value': -1.0}]}, 'source_status': {}}
        result = classify_missing_fields(row, ('per',))
        self.assertEqual(result['per']['status'], 'loss_making')

    def test_values_shown_from_history_are_not_called_missing(self):
        """スカラー列が空でも cf_history から表示できているなら欠損ではない。

        これを見ないと「画面に出ているのに『未取得』と書かれる」矛盾が起きる。
        """
        row = {
            'cash': None, 'current_liabilities': None, 'current_ratio': None,
            'financial_history': {'net_income': [{'date': '2026-03-31', 'value': 1e8}]},
            'cf_history': {
                'cash': [{'date': '2026-03-31', 'value': 3969000000.0}],
                'current_liabilities': [{'date': '2026-03-31', 'value': 1.004e10}],
                'current_assets': [{'date': '2026-03-31', 'value': 8.37e9}],
            },
            'source_status': {},
        }
        result = classify_missing_fields(
            row, ('cash', 'current_liabilities', 'current_ratio'))
        self.assertEqual(result, {})

    def test_empty_json_array_counts_as_missing(self):
        row = {'company_officers': '[]', 'financial_history':
               {'net_income': [{'date': '2026-03-31', 'value': 1e8}]},
               'source_status': {}}
        result = classify_missing_fields(row, ('company_officers',))
        self.assertIn('company_officers', result)


class ScreenedApiOmissionsTest(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def _get(self, row, logged_in=True):
        if logged_in:
            with self.client.session_transaction() as s:
                s['user_id'] = 'test'
        with unittest.mock.patch.object(
                self.app_module, 'get_screened_data', return_value=row), \
             unittest.mock.patch.object(
                 self.app_module, 'get_supabase_client', return_value=None):
            return self.client.get('/api/stock/screened/9999').get_json()

    def test_response_carries_the_reason(self):
        body = self._get({
            'company_code': '9999', 'gc_date': 'x', 'dc_date': 'y',
            'per_forward': None, 'pbr': 1.2,
            'financial_history': {'eps': [{'date': '2026-03-31', 'value': -5.0}],
                                  'net_income': [{'date': '2026-03-31', 'value': -1e8}]},
            'source_status': {},
        })
        self.assertEqual(body['omissions']['per']['status'], 'loss_making')
        self.assertNotIn('pbr', body['omissions'])


class DetailTemplateTest(unittest.TestCase):
    def setUp(self):
        self.detail = Path('templates/stock_detail.html').read_text(encoding='utf-8')

    def test_detail_page_renders_the_reason(self):
        for token in ('metricOmissions', 'data.omissions', 'metric-sub-reason'):
            self.assertIn(token, self.detail)

    def test_report_and_detail_share_one_classifier(self):
        """分類の真実を2箇所に持たない"""
        report = Path('report_builder.py').read_text(encoding='utf-8')
        self.assertIn('from data_gaps import classify', report)


if __name__ == '__main__':
    unittest.main()
