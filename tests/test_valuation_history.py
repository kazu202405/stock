"""PER・PBRの推移を自前計算する部分のリグレッション。

保存しているPER/PBRは現在値の1点だけなので、株価履歴と決算期ごとの
EPS/BPSから組み立てる。外部取得は発生しない。
"""

import os
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from report_builder import build_from_screened
from valuation_history import (
    DISCLOSURE_LAG_DAYS, build_valuation_history, summarize,
)


def _price(date_str, close):
    """stock_price_history と同じ形（UNIX秒 + 終値）"""
    dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return {'time': int(dt.timestamp()), 'close': close}


class BuildValuationHistoryTest(unittest.TestCase):
    def test_uses_the_eps_that_was_public_at_the_time(self):
        """決算期末の翌日にはまだ公表されていない。45日のラグを効かせる。"""
        eps = [{'date': '2025-03-31', 'value': 100.0},
               {'date': '2026-03-31', 'value': 200.0}]
        prices = [_price('2026-04-10', 2000.0),   # 期末後だがまだ未公表
                  _price('2026-05-20', 2000.0)]   # 45日経過後
        result = build_valuation_history(prices, eps_series=eps)

        self.assertEqual(result['points'][0]['eps'], 100.0)
        self.assertEqual(result['points'][0]['per'], 20.0)
        self.assertEqual(result['points'][1]['eps'], 200.0)
        self.assertEqual(result['points'][1]['per'], 10.0)

    def test_switchover_happens_exactly_after_the_lag(self):
        eps = [{'date': '2026-03-31', 'value': 487.31}]
        before = build_valuation_history([_price('2026-05-14', 7000.0)], eps_series=eps)
        after = build_valuation_history([_price('2026-05-15', 7000.0)], eps_series=eps)
        self.assertIsNone(before['points'][0]['per'])
        self.assertIsNotNone(after['points'][0]['per'])
        self.assertEqual(DISCLOSURE_LAG_DAYS, 45)

    def test_no_per_before_any_disclosure(self):
        """最初の決算が公表される前の期間はPERを出さない"""
        eps = [{'date': '2026-03-31', 'value': 100.0}]
        result = build_valuation_history([_price('2020-01-01', 1000.0)], eps_series=eps)
        self.assertIsNone(result['points'][0]['per'])
        self.assertFalse(result['has_per'])

    def test_loss_making_period_has_no_per(self):
        """赤字ではPERが存在しない。マイナスの倍率を出さない。"""
        eps = [{'date': '2025-03-31', 'value': -50.0}]
        result = build_valuation_history([_price('2026-01-01', 1000.0)], eps_series=eps)
        self.assertIsNone(result['points'][0]['per'])
        self.assertFalse(result['has_per'])

    def test_pbr_is_computed_even_when_loss_making(self):
        """赤字でも純資産はあるのでPBRは出る。PERとの違い。"""
        result = build_valuation_history(
            [_price('2026-01-01', 1000.0)],
            eps_series=[{'date': '2025-03-31', 'value': -50.0}],
            bps_series=[{'date': '2025-03-31', 'value': 500.0}])
        self.assertIsNone(result['points'][0]['per'])
        self.assertEqual(result['points'][0]['pbr'], 2.0)
        self.assertTrue(result['has_pbr'])

    def test_absurd_multiples_are_dropped(self):
        """分母がほぼゼロの銘柄で数万倍が出るとグラフが読めなくなる"""
        result = build_valuation_history(
            [_price('2026-01-01', 10000.0)],
            eps_series=[{'date': '2024-03-31', 'value': 0.01}])
        self.assertIsNone(result['points'][0]['per'])

    def test_missing_bps_leaves_pbr_empty_without_guessing(self):
        result = build_valuation_history(
            [_price('2026-01-01', 1000.0)],
            eps_series=[{'date': '2024-03-31', 'value': 100.0}])
        self.assertTrue(result['has_per'])
        self.assertFalse(result['has_pbr'])
        self.assertIsNone(result['points'][0]['pbr'])

    def test_ignores_broken_rows(self):
        result = build_valuation_history(
            [_price('2026-01-01', 1000.0), {'time': None, 'close': 1}, 'ゴミ',
             {'time': 1, 'close': None}],
            eps_series=[{'date': 'こわれた', 'value': 1},
                        {'date': '2024-03-31', 'value': None},
                        {'date': '2024-03-31', 'value': 100.0}])
        self.assertEqual(len(result['points']), 1)
        self.assertEqual(result['points'][0]['per'], 10.0)

    def test_zero_or_negative_price_is_skipped(self):
        result = build_valuation_history(
            [_price('2026-01-01', 0.0), _price('2026-01-02', -5.0)],
            eps_series=[{'date': '2024-03-31', 'value': 100.0}])
        self.assertEqual(result['points'], [])

    def test_summary_reports_range_and_latest(self):
        eps = [{'date': '2024-03-31', 'value': 100.0}]
        result = build_valuation_history(
            [_price('2026-01-01', 1000.0), _price('2026-01-02', 2000.0),
             _price('2026-01-03', 1500.0)], eps_series=eps)
        stats = summarize(result)['per']
        self.assertEqual(stats['min'], 10.0)
        self.assertEqual(stats['max'], 20.0)
        self.assertEqual(stats['avg'], 15.0)
        self.assertEqual(stats['latest'], 15.0)
        self.assertEqual(stats['count'], 3)

    def test_empty_input_is_safe(self):
        result = build_valuation_history([], None, None)
        self.assertEqual(result['points'], [])
        self.assertFalse(result['has_per'])
        self.assertIsNone(summarize(result)['per'])


class ValuationHistoryApiTest(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def test_explains_why_pbr_is_missing(self):
        """出せない理由を握り潰さない"""
        row = {'financial_history': {'eps': [{'date': '2024-03-31', 'value': 100.0}]}}
        with unittest.mock.patch.object(
                self.app_module, 'get_screened_data', return_value=row), \
             unittest.mock.patch('price_history.get_daily',
                                 return_value=[_price('2026-01-01', 1000.0)]):
            body = self.client.get('/api/stock/valuation-history/5261').get_json()

        self.assertTrue(body['has_per'])
        self.assertFalse(body['has_pbr'])
        self.assertIn('再分析', body['notes']['pbr'])

    def test_does_not_fetch_external_sources_for_valuation(self):
        """PER/PBRの履歴はDB内のデータだけで作る"""
        row = {'financial_history': {'eps': [{'date': '2024-03-31', 'value': 100.0}],
                                     'bps': [{'date': '2024-03-31', 'value': 500.0}]}}
        with unittest.mock.patch.object(
                self.app_module, 'get_screened_data', return_value=row), \
             unittest.mock.patch('price_history.get_daily',
                                 return_value=[_price('2026-01-01', 1000.0)]), \
             unittest.mock.patch('price_history.fetch_ohlc') as fetch:
            body = self.client.get('/api/stock/valuation-history/5261').get_json()

        fetch.assert_not_called()
        self.assertEqual(body['points'][0]['per'], 10.0)
        self.assertEqual(body['points'][0]['pbr'], 2.0)


class ReportOmissionTest(unittest.TestCase):
    """レポートは項目を黙って消さない。理由を内部に残す。"""

    def test_margin_ratio_is_not_in_financial_health(self):
        """信用倍率は市況・需給データであって財務健全性の指標ではない"""
        row = {'company_code': '7203', 'company_name': 'トヨタ',
               'margin_trading_ratio': 5.0, 'cash': 100.0,
               'current_liabilities': 50.0, 'current_ratio': 200.0,
               'operating_cf': 30.0, 'payout_ratio': 30.0}
        report = build_from_screened(row)
        self.assertNotIn('信用倍率', [i['label'] for i in report['health']])
        self.assertIn('信用倍率', [i['label'] for i in report['snapshot']])

    def test_pro_market_margin_ratio_is_not_applicable(self):
        """TOKYO PRO Marketは信用取引の対象外。取得失敗と混同しない。"""
        row = {'company_code': '164A', 'market': 'TOKYO PRO Market',
               'source_status': {}}
        report = build_from_screened(row)
        entry = next(o for o in report['data_quality']['omitted_items']
                     if o['label'] == '信用倍率')
        self.assertEqual(entry['status'], 'not_applicable')

    def test_never_attempted_differs_from_fetch_failure(self):
        never = build_from_screened({'company_code': '5261', 'source_status': {}})
        failed = build_from_screened({
            'company_code': '5261',
            'source_status': {'margin_trading': {'status': 'rate_limited'}}})

        def _status(report):
            return next(o for o in report['data_quality']['omitted_items']
                        if o['label'] == '信用倍率')['status']

        self.assertEqual(_status(never), 'not_attempted')
        self.assertEqual(_status(failed), 'fetch_failed')

    def test_no_data_is_recorded_as_not_listed(self):
        report = build_from_screened({
            'company_code': '5261',
            'source_status': {'margin_trading': {'status': 'no_data'}}})
        entry = next(o for o in report['data_quality']['omitted_items']
                     if o['label'] == '信用倍率')
        self.assertEqual(entry['status'], 'no_data')


class ValuationCardTemplateTest(unittest.TestCase):
    def setUp(self):
        self.detail = Path('templates/stock_detail.html').read_text(encoding='utf-8')

    def test_card_and_loader_exist(self):
        for token in ('id="vh-chart"', 'loadValuationHistory',
                      '/api/stock/valuation-history/'):
            self.assertIn(token, self.detail)

    def test_no_trading_prompts_in_the_card(self):
        """煽り表現を入れない（アプリの禁止パターン）"""
        for banned in ('買い時', '売り時', '今すぐ', '割安です', '狙い目'):
            self.assertNotIn(banned, self.detail)


if __name__ == '__main__':
    unittest.main()
