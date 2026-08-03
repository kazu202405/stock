import json
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from analysis_quality import (
    analysis_data_status,
    history_has_values,
    history_json_or_none,
    normalize_analysis_symbol,
    parse_data_source,
    serialize_data_source,
)
from official_company_profiles import apply_official_profile_fallback
from report_builder import build_from_screened
from supabase_client import score_breakdown


class AnalysisQualityTest(unittest.TestCase):
    def test_empty_history_is_not_serialized(self):
        history = {'revenue': [], 'op_income': []}
        self.assertFalse(history_has_values(history))
        self.assertIsNone(history_json_or_none(history))

    def test_valid_history_is_serialized(self):
        history = {'revenue': [{'date': '2025-12-31', 'value': 100}]}
        encoded = history_json_or_none(history)
        self.assertEqual(json.loads(encoded), history)

    def test_status_is_stale_when_either_history_group_is_missing(self):
        financial = {'revenue': [{'date': '2025-12-31', 'value': 100}]}
        cashflow = {'operating_cf': []}
        self.assertEqual(analysis_data_status(financial, cashflow), 'stale')
        self.assertEqual(analysis_data_status(financial, {
            'operating_cf': [{'date': '2025-12-31', 'value': 10}],
        }), 'fresh')

    def test_alphanumeric_japanese_code_gets_t_suffix(self):
        self.assertEqual(normalize_analysis_symbol('164A'), '164A.T')
        self.assertEqual(normalize_analysis_symbol('7203'), '7203.T')
        self.assertEqual(normalize_analysis_symbol('AAPL'), 'AAPL')

    def test_source_diagnostics_round_trip_in_text_column(self):
        source = {'yahoo_jp_profile': {'status': 'rate_limited'}}
        encoded = serialize_data_source(source)
        self.assertEqual(parse_data_source(encoded)['sources'], source)
        self.assertEqual(parse_data_source('yfinance')['primary'], 'yfinance')

    def test_official_profile_fills_only_missing_values(self):
        row = {'business_summary_jp': '既存の説明', 'listing_date': None}
        filled = apply_official_profile_fallback('164A.T', row)
        self.assertEqual(row['business_summary_jp'], '既存の説明')
        self.assertEqual(row['established'], '1991-08-07')
        self.assertEqual(row['listing_date'], '2024-03-25')
        self.assertNotIn('business_summary_jp', filled)

    def test_sparse_perfect_score_is_marked_provisional(self):
        # 164Aで起きた状態を再現: 取得済み8項目は全合格だが履歴4項目が未判定。
        row = {
            'market_cap': 26,
            'equity_ratio': 32.1,
            'operating_margin': 12.5,
            'operating_cf': 9.8,
            'free_cf': 7.8,
            'roa': 9.9,
            'per_forward': 9.5,
            'pbr': 1.47,
            'financial_history': {'revenue': [], 'op_income': []},
            'cf_history': {},
        }
        result = score_breakdown(row)
        self.assertEqual(result['score'], 100)
        self.assertEqual(result['judged'], 8)
        self.assertEqual(result['coverage'], 67)
        self.assertEqual(result['status'], 'provisional')
        self.assertFalse(result['is_complete'])
        self.assertCountEqual(result['missing_keys'], [
            'revenue_growth', 'revenue_forecast', 'op_growth', 'op_forecast',
        ])

    def test_sparse_report_explains_which_sections_are_missing(self):
        report = build_from_screened({
            'company_code': '164A',
            'company_name': 'アップルパーク',
            'market_cap': 26,
            'equity_ratio': 32.1,
            'operating_margin': 12.5,
            'operating_cf': 9.8,
            'free_cf': 7.8,
            'roa': 9.9,
            'per_forward': 9.5,
            'pbr': 1.47,
            'financial_history': {'revenue': [], 'op_income': []},
            'cf_history': {},
        })
        quality = report['data_quality']
        self.assertTrue(quality['is_limited'])
        self.assertEqual(quality['score_coverage'], 67)
        self.assertIn('事業概要', quality['missing_sections'])
        self.assertIn('売上高・営業利益の推移', quality['missing_sections'])
        self.assertIn('主要株主・代表者', quality['missing_sections'])

    def test_report_snapshot_contains_established_and_listing_dates(self):
        report = build_from_screened({
            'company_code': '164A', 'company_name': 'アップルパーク',
            'established': '1991-08-07', 'listing_date': '2024-03-25',
        })
        labels = {item['label']: item['value'] for item in report['snapshot']}
        self.assertEqual(labels['設立'], '1991-08-07')
        self.assertEqual(labels['上場日'], '2024-03-25')

    def test_changed_report_templates_compile(self):
        env = Environment(loader=FileSystemLoader('templates'))
        env.get_template('report_view.html')
        env.get_template('_report_body.html')
        env.get_template('stock_detail.html')

    def test_changed_python_files_compile(self):
        for name in ('analysis_quality.py', 'official_company_profiles.py',
                     'yahoo_jp_guard.py', 'jp_company_scraper.py',
                     'stock_analyzer.py', 'supabase_client.py',
                     'report_builder.py', 'app.py'):
            source = Path(name).read_text(encoding='utf-8')
            compile(source, name, 'exec')


if __name__ == '__main__':
    unittest.main()
