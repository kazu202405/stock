import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
from stock_analyzer import StockAnalyzer, extract_yahoo_forecast_data
from supabase_client import attach_score_quality, merge_source_status, score_breakdown


class AnalysisQualityTest(unittest.TestCase):
    def test_safe_sources_mode_skips_limited_and_html_sources(self):
        analyzer = StockAnalyzer()
        with patch('stock_analyzer.yf.Ticker', return_value=Mock()), \
                patch.object(analyzer, '_get_basic_metrics'), \
                patch.object(analyzer, '_get_financial_data'), \
                patch.object(analyzer, '_get_five_year_financial_data'), \
                patch.object(analyzer, '_calculate_roe_roa'), \
                patch.object(analyzer, '_get_industry_sector') as industry, \
                patch.object(analyzer, '_get_jp_labels') as labels, \
                patch.object(analyzer, '_get_forecast_data') as forecast, \
                patch.object(analyzer, '_get_holders_and_officers') as holders, \
                patch.object(analyzer, '_get_business_summary') as summary, \
                patch.object(analyzer, '_get_margin_trading_data') as margin:
            result = analyzer.analyze(
                '7089.T', skip_chart=True, safe_sources_only=True)

        self.assertNotIn('error', result)
        industry.assert_called_once()
        self.assertFalse(industry.call_args.kwargs['allow_yahoo_jp'])
        labels.assert_called_once()
        self.assertFalse(labels.call_args.kwargs['allow_yahoo_jp'])
        forecast.assert_not_called()
        holders.assert_not_called()
        summary.assert_not_called()
        margin.assert_not_called()
        self.assertEqual('skipped', result['source_status']['forecast']['status'])
        self.assertIn('EDINET DB', result['source_status']['acquisition_mode']['excluded'])

    def test_yahoo_forecast_parser_accepts_strings_zero_and_negative_values(self):
        parsed = extract_yahoo_forecast_data(
            '&quot;forecast&quot;:{&quot;yearEndDate&quot;:&quot;2027-03-31&quot;,'
            '&quot;netSales&quot;:&quot;12,300,000,000&quot;,'
            '&quot;operatingIncome&quot;:-50000000,'
            '&quot;ordinaryIncome&quot;:0}'
        )

        self.assertEqual('success', parsed['_forecast_status'])
        self.assertEqual(123.0, parsed['forecast_revenue'])
        self.assertEqual(-0.5, parsed['forecast_op_income'])
        self.assertEqual(0.0, parsed['forecast_ordinary_income'])
        self.assertEqual('2027-03-31', parsed['forecast_year'])

    def test_yahoo_forecast_parser_distinguishes_company_non_disclosure(self):
        parsed = extract_yahoo_forecast_data(
            '"forecast":{"yearEndDate":"2026-11-30",'
            '"accountingStandard":"JAPAN_GAAP","updatedDate":"2026-06-30"}'
        )

        self.assertEqual('not_disclosed', parsed['_forecast_status'])
        self.assertEqual('2026-11-30', parsed['_forecast_period'])
        self.assertNotIn('forecast_year', parsed)

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

    def test_successful_source_is_preserved_when_later_batch_skips_it(self):
        old = {'edinet_db': {'status': 'success', 'filled': ['revenue']}}
        new = {'edinet_db': {'status': 'budget_reserved'},
               'financials': {'status': 'success'}}

        merged = merge_source_status(old, new)

        self.assertEqual('success', merged['edinet_db']['status'])
        self.assertEqual('budget_reserved', merged['edinet_db']['last_attempt']['status'])
        self.assertEqual('success', merged['financials']['status'])

    @patch('jp_company_scraper.get_all_jp_company_data')
    def test_japanese_summary_skips_english_yahoo_lookup(self, get_jp_data):
        get_jp_data.return_value = {
            'business_summary_jp': '日本語の概要',
            'source_status': {},
        }
        analyzer = StockAnalyzer()
        analyzer._get_english_business_summary = Mock()
        result = {'business_summary': None, 'business_summary_jp': None,
                  'source_status': {}}

        analyzer._get_business_summary('7203.T', Mock(), result)

        self.assertEqual('日本語の概要', result['business_summary_jp'])
        analyzer._get_english_business_summary.assert_not_called()

    @patch('jp_company_scraper.get_all_jp_company_data')
    def test_missing_japanese_summary_tries_english_yahoo_lookup(self, get_jp_data):
        get_jp_data.return_value = {'business_summary_jp': None, 'source_status': {}}
        analyzer = StockAnalyzer()
        analyzer._get_english_business_summary = Mock(
            side_effect=lambda symbol, ticker, result: result.update(
                {'business_summary': 'English summary'}))
        result = {'business_summary': None, 'business_summary_jp': None,
                  'source_status': {}}

        analyzer._get_business_summary('7203.T', Mock(), result)

        analyzer._get_english_business_summary.assert_called_once()
        self.assertEqual('English summary', result['business_summary'])

    @patch('jp_company_scraper.get_all_jp_company_data')
    def test_successful_free_source_data_is_used_even_if_another_source_failed(
            self, get_jp_data):
        get_jp_data.return_value = {
            'error': 'Yahoo Japan failed',
            'business_summary_jp': None,
            'officers_jp': [{'name': '役員 太郎', 'title': '代表取締役'}],
            'major_shareholders_jp': [
                {'name': '大株主株式会社', 'shares': 100, 'ratio': 10.0},
            ],
            'source_status': {
                'yahoo_jp_profile': {'status': 'error'},
                'jlic_officers': {'status': 'success'},
                'strainer_shareholders': {'status': 'success'},
            },
        }
        analyzer = StockAnalyzer()
        analyzer._get_english_business_summary = Mock()
        result = {'business_summary': None, 'business_summary_jp': None,
                  'company_officers': [], 'major_shareholders_jp': [],
                  'source_status': {}}

        analyzer._get_business_summary('7203.T', Mock(), result)

        self.assertEqual('役員 太郎', result['company_officers'][0]['name'])
        self.assertEqual('大株主株式会社', result['major_shareholders_jp'][0]['name'])
        analyzer._get_english_business_summary.assert_called_once()

    @patch('jp_company_scraper.time.sleep')
    @patch('jp_company_scraper.get_shareholders_from_strainer')
    @patch('jp_company_scraper.get_officers_from_jlic')
    @patch('jp_company_scraper.get_yahoo_japan_profile')
    def test_jp_sources_continue_after_yahoo_exception(
            self, get_yahoo, get_officers, get_shareholders, _sleep):
        from jp_company_scraper import get_all_jp_company_data

        get_yahoo.side_effect = RuntimeError('Yahoo blocked')
        get_officers.return_value = (
            [{'name': '役員 花子', 'title': '取締役'}], {'status': 'success'})
        get_shareholders.return_value = (
            [{'name': '株主A', 'shares': 50, 'ratio': 5.0}], {'status': 'success'})

        result = get_all_jp_company_data('7203')

        self.assertEqual('役員 花子', result['officers_jp'][0]['name'])
        self.assertEqual('株主A', result['major_shareholders_jp'][0]['name'])
        self.assertEqual('error', result['source_status']['yahoo_jp_profile']['status'])

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

    def test_screener_quality_fields_mark_sparse_score_provisional(self):
        row = {
            'match_rate': 100,
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

        attach_score_quality(row)

        self.assertEqual(100, row['match_rate'])
        self.assertEqual('provisional', row['score_status'])
        self.assertEqual(67, row['score_coverage'])
        self.assertEqual(8, row['score_judged'])
        self.assertEqual(12, row['score_total'])

    def test_score_explains_company_forecast_non_disclosure(self):
        result = score_breakdown({
            'financial_history': {
                'revenue': [{'date': '2025-12-31', 'value': 100}],
                'op_income': [{'date': '2025-12-31', 'value': 10}],
            },
            'source_status': {'forecast': {'status': 'not_disclosed'}},
        })
        by_key = {item['key']: item for item in result['items']}

        self.assertFalse(by_key['revenue_forecast']['judged'])
        self.assertEqual('会社予想非開示', by_key['revenue_forecast']['display'])
        self.assertEqual('会社予想非開示', by_key['op_forecast']['display'])

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
        env.get_template('screener.html')

        layout = Path('templates/layout.html').read_text(encoding='utf-8')
        detail = Path('templates/stock_detail.html').read_text(encoding='utf-8')
        screener = Path('templates/screener.html').read_text(encoding='utf-8')
        self.assertIn('site-header', layout)
        self.assertIn('target="_blank" rel="noopener noreferrer"', detail)
        self.assertIn('id="establishedInfo"', detail)
        self.assertIn('id="listingDateInfo"', detail)
        self.assertIn('id="gcDateValue"', detail)
        self.assertIn('id="dcDateValue"', detail)
        self.assertIn('id="safeRefreshBtn"', detail)
        self.assertIn('openStockDetail(row.company_code)', screener)

    def test_changed_python_files_compile(self):
        for name in ('analysis_quality.py', 'official_company_profiles.py',
                     'yahoo_jp_guard.py', 'jp_company_scraper.py',
                     'stock_analyzer.py', 'supabase_client.py',
                     'report_builder.py', 'app.py'):
            source = Path(name).read_text(encoding='utf-8')
            compile(source, name, 'exec')


if __name__ == '__main__':
    unittest.main()
