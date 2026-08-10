"""欠損データの補完まわりのリグレッション。

対象:
  - EPSが欠けている決算期を純利益÷株数で補完する（_fill_missing_eps）
  - 財務健全性カードがcf_historyを参照する（stock_detail.html）
"""

import os
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from analysis_quality import derive_fiscal_month
from stock_analyzer import StockAnalyzer


def _frame(rows, dates):
    """yfinanceの財務諸表と同じ形（行=項目名、列=決算日）のDataFrameを作る"""
    columns = [pd.Timestamp(d) for d in dates]
    return pd.DataFrame(list(rows.values()), index=list(rows.keys()), columns=columns)


class FillMissingEpsTest(unittest.TestCase):
    def setUp(self):
        self.analyzer = StockAnalyzer()

    def _result(self, eps, net_income):
        return {
            'eps': [dict(d) for d in eps],
            'net_income': [dict(d) for d in net_income],
            'source_status': {},
        }

    def test_derives_eps_from_balance_sheet_shares(self):
        """Yahooが最新期のEPSを返さない場合、期末株数で補完する（5261の実データ形）"""
        financials = _frame(
            {'Basic EPS': [float('nan'), 350.98]},
            ['2026-03-31', '2025-03-31'],
        )
        ticker = Mock()
        ticker.balance_sheet = _frame(
            {'Ordinary Shares Number': [5557069.0, 5556174.0]},
            ['2026-03-31', '2025-03-31'],
        )
        result = self._result(
            eps=[{'date': '2025-03-31', 'value': 350.98}],
            net_income=[{'date': '2026-03-31', 'value': 2708000000.0},
                        {'date': '2025-03-31', 'value': 1950000000.0}],
        )

        self.analyzer._fill_missing_eps(ticker, financials, result)

        derived = [d for d in result['eps'] if d.get('derived')]
        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0]['date'], '2026-03-31')
        self.assertAlmostEqual(derived[0]['value'], 487.31, places=1)
        self.assertEqual(result['source_status']['eps']['status'], 'derived')

    def test_prefers_average_shares_from_income_statement(self):
        """期中平均株数が取れるときはそちらを分母にする"""
        financials = _frame(
            {'Basic EPS': [float('nan')], 'Basic Average Shares': [2000000.0]},
            ['2026-03-31'],
        )
        ticker = Mock()
        ticker.balance_sheet = _frame(
            {'Ordinary Shares Number': [4000000.0]}, ['2026-03-31'])
        result = self._result(
            eps=[], net_income=[{'date': '2026-03-31', 'value': 1000000000.0}])

        self.analyzer._fill_missing_eps(ticker, financials, result)

        self.assertAlmostEqual(result['eps'][0]['value'], 500.0)
        self.assertEqual(
            result['source_status']['eps']['derived_periods'][0]['shares_source'],
            'Basic Average Shares')

    def test_does_not_guess_when_period_does_not_match(self):
        """期がズレた株数では割らない。推測せずno_dataとして残す"""
        financials = _frame({'Basic EPS': [float('nan')]}, ['2026-03-31'])
        ticker = Mock()
        ticker.balance_sheet = _frame(
            {'Ordinary Shares Number': [5000000.0]}, ['2025-03-31'])
        result = self._result(
            eps=[], net_income=[{'date': '2026-03-31', 'value': 1000000000.0}])

        self.analyzer._fill_missing_eps(ticker, financials, result)

        self.assertEqual(result['eps'], [])
        self.assertEqual(result['source_status']['eps']['status'], 'no_data')

    def test_no_status_noise_when_nothing_is_missing(self):
        """欠損が無い銘柄にはsource_statusを足さない"""
        financials = _frame({'Basic EPS': [60.34]}, ['2025-09-30'])
        ticker = Mock()
        ticker.balance_sheet = _frame(
            {'Ordinary Shares Number': [32000000.0]}, ['2025-09-30'])
        result = self._result(
            eps=[{'date': '2025-09-30', 'value': 60.34}],
            net_income=[{'date': '2025-09-30', 'value': 1945000000.0}])

        self.analyzer._fill_missing_eps(ticker, financials, result)

        self.assertNotIn('eps', result['source_status'])
        self.assertEqual(len(result['eps']), 1)

    def test_survives_balance_sheet_failure(self):
        """貸借対照表の取得が失敗しても例外を投げない"""
        financials = _frame({'Basic EPS': [float('nan')]}, ['2026-03-31'])
        ticker = Mock()
        type(ticker).balance_sheet = property(
            lambda self: (_ for _ in ()).throw(RuntimeError('429')))
        result = self._result(
            eps=[], net_income=[{'date': '2026-03-31', 'value': 1000000000.0}])

        self.analyzer._fill_missing_eps(ticker, financials, result)

        self.assertEqual(result['source_status']['eps']['status'], 'no_data')


class FinancialHealthTemplateTest(unittest.TestCase):
    """財務健全性カードはスカラー列だけでなくcf_historyを見る。

    スカラー列は全銘柄バックフィルの保存パスが書いておらず、
    3,879銘柄中25件しか埋まっていなかった。
    """

    def setUp(self):
        self.detail = Path('templates/stock_detail.html').read_text(encoding='utf-8')

    def test_cached_view_reads_cf_history(self):
        for token in ('cfHistory.cash',
                      'cfHistory.current_liabilities',
                      'cfHistory.current_assets'):
            self.assertIn(token, self.detail)

    def test_current_ratio_compares_same_period(self):
        self.assertIn('latestAssets.date === latestLiab.date', self.detail)

    def test_derived_eps_is_marked(self):
        self.assertIn('eps_derived', self.detail)


class DeriveFiscalMonthTest(unittest.TestCase):
    """決算月は財務履歴の決算日から導出する（外部取得なし）"""

    def test_derives_march_from_financial_history(self):
        history = {'revenue': [{'date': '2026-03-31', 'value': 30404000000.0},
                               {'date': '2025-03-31', 'value': 28400000000.0}]}
        self.assertEqual(derive_fiscal_month(history), 3)

    def test_accepts_json_string(self):
        history = '{"net_income": [{"date": "2025-09-30", "value": 1945000000.0}]}'
        self.assertEqual(derive_fiscal_month(history), 9)

    def test_ignores_dividend_record_dates(self):
        """dpsは権利確定日なので決算期の判定に混ぜない（3月期を3月と判定する）"""
        history = {
            'revenue': [{'date': '2026-03-31', 'value': 1.0}],
            'dps': [{'date': '2026-03-28', 'value': 110.0},
                    {'date': '2025-09-28', 'value': 50.0},
                    {'date': '2024-09-28', 'value': 50.0}],
        }
        self.assertEqual(derive_fiscal_month(history), 3)

    def test_uses_most_frequent_month(self):
        """1年だけ決算期がずれても最頻値に引きずられない"""
        history = {'revenue': [{'date': '2026-03-31', 'value': 1.0},
                               {'date': '2025-03-31', 'value': 1.0},
                               {'date': '2024-03-31', 'value': 1.0},
                               {'date': '2023-12-31', 'value': 1.0}]}
        self.assertEqual(derive_fiscal_month(history), 3)

    def test_falls_back_to_cf_history(self):
        self.assertEqual(
            derive_fiscal_month({}, {'cash': [{'date': '2026-06-30', 'value': 1.0}]}), 6)

    def test_returns_none_without_financial_data(self):
        """ETF等は財務履歴が無い。推測せずNoneを返す"""
        self.assertIsNone(derive_fiscal_month(None))
        self.assertIsNone(derive_fiscal_month({}))
        self.assertIsNone(derive_fiscal_month({'revenue': []}))
        self.assertIsNone(derive_fiscal_month('壊れたJSON'))

    def test_ignores_rows_without_value(self):
        history = {'revenue': [{'date': '2026-03-31', 'value': None}]}
        self.assertIsNone(derive_fiscal_month(history))


class EarningsPageTest(unittest.TestCase):
    """決算情報ページ。決算「期」であって発表「予定日」ではない。"""

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()
        app_module.app.config['TESTING'] = True

    def _login(self):
        with self.client.session_transaction() as s:
            s['user_id'] = 'test'
            s['user_role'] = 'user'
            s['user_name'] = 'テスト'

    def test_rejects_month_out_of_range(self):
        self._login()
        for month in (0, 13):
            response = self.client.get(f'/api/earnings/month/{month}')
            self.assertEqual(response.status_code, 400)

    def test_reports_missing_migration_instead_of_500(self):
        """fiscal_month列が無いときは、落とさずに適用手順を返す"""
        self._login()
        broken = Mock()
        broken.table.side_effect = Exception(
            'column screened_latest.fiscal_month does not exist')
        with unittest.mock.patch.object(
                self.app_module, 'get_supabase_client', return_value=broken):
            response = self.client.get('/api/earnings/month/3')
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.get_json()['migration_required'])

    def test_month_listing_is_paginated(self):
        """1000行上限に当たらないよう、必ずrangeで取って総数はcountで返す"""
        self._login()
        chain = Mock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.range.return_value = chain
        chain.execute.return_value = Mock(
            count=2183,
            data=[{'company_code': '5261', 'company_name': 'リソルホールディングス'}])
        client = Mock()
        client.table.return_value = chain

        with unittest.mock.patch.object(
                self.app_module, 'get_supabase_client', return_value=client):
            response = self.client.get('/api/earnings/month/3?page=2&per_page=50')

        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body['total'], 2183)
        self.assertEqual(body['total_pages'], 44)
        chain.range.assert_called_once_with(50, 99)

    def test_per_page_is_capped(self):
        """per_pageで大量取得させない"""
        self._login()
        chain = Mock()
        for name in ('select', 'eq', 'order', 'range'):
            getattr(chain, name).return_value = chain
        chain.execute.return_value = Mock(count=0, data=[])
        client = Mock()
        client.table.return_value = chain

        with unittest.mock.patch.object(
                self.app_module, 'get_supabase_client', return_value=client):
            self.client.get('/api/earnings/month/3?per_page=9999')

        chain.range.assert_called_once_with(0, 99)


class HoldersOnDemandTest(unittest.TestCase):
    """株主・役員は閲覧された銘柄だけ取りに行く（EDINET DB無料枠は100回/日）"""

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def _status(self, status, hours_ago):
        from datetime import datetime, timezone, timedelta
        when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return {'holders_officers': {'status': status,
                                     'fetched_at': when.isoformat()}}

    def _offline(self, screened, analyzer_fills=None):
        """外部取得を全部止めた状態でエンドポイントを叩くための後始末付きパッチ。

        エンドポイントは関数内importでフォールバックを読むため、
        呼び出し元(app)ではなく定義元モジュールを差し替える。
        """
        import contextlib
        import edinet_db_client
        import official_company_profiles

        def _fake_analyzer():
            instance = Mock()
            instance._get_holders_and_officers.side_effect = (
                lambda symbol, result: result.update(analyzer_fills or {}))
            return instance

        stack = contextlib.ExitStack()
        stack.enter_context(unittest.mock.patch.object(
            self.app_module, 'get_screened_data', return_value=screened))
        stack.enter_context(unittest.mock.patch.object(
            self.app_module, 'StockAnalyzer', side_effect=_fake_analyzer))
        stack.enter_context(unittest.mock.patch.object(
            official_company_profiles, 'apply_official_profile_fallback',
            return_value=[]))
        stack.enter_context(unittest.mock.patch.object(
            edinet_db_client, 'apply_edinet_db_fallback', return_value=[]))
        return stack

    def test_batch_skipped_is_retried_immediately(self):
        """高速バッチのskippedは待たずに取りに行ってよい"""
        allowed = self.app_module._holders_retry_allowed(
            {'holders_officers': {'status': 'skipped'}})
        self.assertTrue(allowed)

    def test_recent_success_is_not_refetched(self):
        self.assertFalse(
            self.app_module._holders_retry_allowed(self._status('success', 1)))

    def test_success_is_refetched_after_a_week(self):
        self.assertTrue(
            self.app_module._holders_retry_allowed(self._status('success', 24 * 8)))

    def test_no_data_waits_longer_than_success(self):
        """未収録の銘柄を毎週叩き直さない"""
        self.assertFalse(
            self.app_module._holders_retry_allowed(self._status('no_data', 24 * 10)))
        self.assertTrue(
            self.app_module._holders_retry_allowed(self._status('no_data', 24 * 40)))

    def test_partial_is_retried_like_success(self):
        """片方だけ取れた銘柄は success と同じ間隔で残りを取りに行く"""
        self.assertFalse(
            self.app_module._holders_retry_allowed(self._status('partial', 1)))
        self.assertTrue(
            self.app_module._holders_retry_allowed(self._status('partial', 24 * 8)))

    def test_broken_timestamp_does_not_block_forever(self):
        allowed = self.app_module._holders_retry_allowed(
            {'holders_officers': {'status': 'success', 'fetched_at': 'こわれた日付'}})
        self.assertTrue(allowed)

    def test_cached_stock_does_not_hit_external_sources(self):
        """すでに両方あるならキャッシュを返して外部を叩かない"""
        cached = {'major_shareholders_jp': '[{"name": "三井不動産株式会社"}]',
                  'company_officers': '[{"name": "Mr. Masaru Osawa"}]',
                  'source_status': {}}
        with self._offline(cached) as _:
            analyzer = self.app_module.StockAnalyzer
            response = self.client.post('/api/stock/holders-officers/5261')
            self.assertEqual(response.get_json()['status'], 'cached')
            analyzer.assert_not_called()

    def test_empty_json_array_counts_as_missing(self):
        """'[]'を「データあり」と誤判定してキャッシュ扱いしない"""
        cached = {'major_shareholders_jp': '[]', 'company_officers': '[]',
                  'source_status': {}}
        with self._offline(cached), \
             unittest.mock.patch.object(self.app_module, 'update_screened_data'):
            response = self.client.post('/api/stock/holders-officers/5261')
        self.assertNotEqual(response.get_json()['status'], 'cached')

    def test_does_not_overwrite_existing_values_with_empty(self):
        """取得できなかった項目はDBへ書かない（正常値を空で消さない）"""
        with self._offline({'source_status': {}}), \
             unittest.mock.patch.object(
                 self.app_module, 'update_screened_data') as update:
            response = self.client.post('/api/stock/holders-officers/5261')

        self.assertEqual(response.get_json()['status'], 'no_data')
        written = update.call_args[0][1]
        self.assertNotIn('major_shareholders_jp', written)
        self.assertNotIn('company_officers', written)
        self.assertEqual(
            written['source_status']['holders_officers']['status'], 'no_data')

    def test_partial_result_is_recorded_as_partial(self):
        """役員だけ取れた銘柄をsuccessにすると、株主が二度と取りに行かれない"""
        with self._offline({'source_status': {}},
                           analyzer_fills={'company_officers': [{'name': 'Mr. Osawa'}],
                                           'holders_source': 'yfinance/yahooquery'}), \
             unittest.mock.patch.object(
                 self.app_module, 'update_screened_data') as update:
            response = self.client.post('/api/stock/holders-officers/5261')

        self.assertEqual(response.get_json()['status'], 'fetched')
        entry = update.call_args[0][1]['source_status']['holders_officers']
        self.assertEqual(entry['status'], 'partial')
        self.assertEqual(entry['filled'], ['company_officers'])

    def test_non_japanese_symbol_is_skipped(self):
        response = self.client.post('/api/stock/holders-officers/AAPL')
        self.assertEqual(response.get_json()['status'], 'skipped')


class PendingMigrationSaveTest(unittest.TestCase):
    """migrationを手で適用する運用のため、列が来る前でも保存を止めない"""

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module._missing_columns.clear()

    def tearDown(self):
        self.app_module._missing_columns.clear()

    def test_drops_missing_column_and_saves_the_rest(self):
        calls = []

        def _upsert(payload):
            calls.append(payload)
            if 'fiscal_month' in payload:
                raise Exception(
                    'column screened_latest.fiscal_month does not exist')

        with unittest.mock.patch.object(
                self.app_module, 'upsert_screened_data_with_match_rate', _upsert):
            self.app_module._save_screened_tolerating_new_columns(
                {'company_code': '5261', 'eps': 487.31, 'fiscal_month': 3})

        self.assertEqual(len(calls), 2)
        self.assertNotIn('fiscal_month', calls[1])
        self.assertEqual(calls[1]['eps'], 487.31)

    def test_remembers_missing_column_for_later_saves(self):
        """2銘柄目からは最初から外す（毎回1回失敗させない）"""
        calls = []

        def _upsert(payload):
            calls.append(dict(payload))
            if 'fiscal_month' in payload:
                raise Exception('fiscal_month does not exist')

        with unittest.mock.patch.object(
                self.app_module, 'upsert_screened_data_with_match_rate', _upsert):
            self.app_module._save_screened_tolerating_new_columns(
                {'company_code': '5261', 'fiscal_month': 3})
            self.app_module._save_screened_tolerating_new_columns(
                {'company_code': '3687', 'fiscal_month': 9})

        self.assertEqual(len(calls), 3)
        self.assertNotIn('fiscal_month', calls[2])

    def test_unrelated_errors_still_raise(self):
        """関係ないDBエラーを握り潰さない"""
        def _upsert(payload):
            raise Exception('connection refused')

        with unittest.mock.patch.object(
                self.app_module, 'upsert_screened_data_with_match_rate', _upsert):
            with self.assertRaises(Exception):
                self.app_module._save_screened_tolerating_new_columns(
                    {'company_code': '5261', 'fiscal_month': 3})


class NavigationTest(unittest.TestCase):
    def test_earnings_link_is_in_both_menus(self):
        layout = Path('templates/layout.html').read_text(encoding='utf-8')
        self.assertIn('href="/earnings" class="nav-dropdown-item"', layout)
        self.assertIn('href="/earnings" class="slide-menu-link"', layout)

    def test_page_states_it_is_not_the_announcement_date(self):
        """取れていないものを取れたように見せない"""
        page = Path('templates/earnings.html').read_text(encoding='utf-8')
        self.assertIn('予定日', page)


if __name__ == '__main__':
    unittest.main()
