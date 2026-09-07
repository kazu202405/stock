# -*- coding: utf-8 -*-
"""好調企業の登録・削除が、その場で画面に反映されること（2026-09-07）。

原因だったもの:
  - 書き込み後の再読込でも sessionStorage の古い一覧を先に描いていた
  - 一括登録が銘柄数ぶんのHTTP POSTを直列で送っていた
  - 好調企業モーダルが、先にある高配当モーダルの登録ボタンを掴んでいた
"""

import os
import unittest
from unittest.mock import patch

import supabase_client as sc


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


def function_block(source, name):
    start = source.index(('async function ' if 'async function ' + name in source else 'function ') + name)
    candidates = [
        source.find('\n    function ', start + 1),
        source.find('\n    async function ', start + 1),
    ]
    ends = [pos for pos in candidates if pos != -1]
    return source[start:min(ends) if ends else len(source)]


def route_block(source, route):
    start = source.index(route)
    end = source.find('@app.route(', start + len(route))
    return source[start:end if end != -1 else len(source)]


class BulkRegisterTest(unittest.TestCase):
    def setUp(self):
        self.html = read('templates/stock.html')
        self.app_source = read('app.py')

    def test_browser_sends_one_request_for_all_codes(self):
        block = function_block(self.html, 'submitBulkRegister')
        self.assertIn("fetch('/api/watchlist/add-bulk'", block)
        self.assertIn('company_codes: uniqueCodes', block)
        self.assertNotIn('for (let i = 0; i < uniqueCodes.length', block)

    def test_the_visible_modal_button_shows_progress(self):
        self.assertIn('id="bulkRegisterSubmitBtn"', self.html)
        block = function_block(self.html, 'submitBulkRegister')
        self.assertIn("getElementById('bulkRegisterSubmitBtn')", block)
        self.assertNotIn("querySelector('.modal-submit-btn')", block)

    def test_bulk_api_is_limited_and_admin_only(self):
        block = route_block(self.app_source, "'/api/watchlist/add-bulk'")
        self.assertIn('@admin_required_api', block)
        self.assertIn('WATCHLIST_BULK_ADD_MAX', block)
        self.assertIn('company_codes', block)
        self.assertIn('add_to_watchlist_bulk(codes)', block)

    def test_empty_bulk_add_does_not_touch_the_database(self):
        self.assertEqual(
            sc.add_to_watchlist_bulk([]),
            {'added': 0, 'already': 0, 'added_codes': []},
        )

    def test_database_write_is_batched(self):
        block = read('supabase_client.py').split('def add_to_watchlist_bulk(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn(".in_('company_code', codes)", block)
        self.assertIn("[{'company_code': code} for code in fresh]", block)
        self.assertIn("on_conflict='company_code'", block)


class BulkRegisterApiTest(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 'admin-test'
            sess['user_role'] = 'admin'

    def test_api_normalizes_and_deduplicates_codes(self):
        expected = {'added': 2, 'already': 0,
                    'added_codes': ['7203', '6758']}
        with patch.object(self.app_module, 'add_to_watchlist_bulk',
                          return_value=expected) as add:
            response = self.client.post('/api/watchlist/add-bulk', json={
                'company_codes': ['7203.T', '7203', '6758'],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), expected)
        add.assert_called_once_with(['7203', '6758'])

    def test_api_rejects_more_than_the_limit(self):
        codes = [str(1000 + i) for i in range(201)]
        with patch.object(self.app_module, 'add_to_watchlist_bulk') as add:
            response = self.client.post('/api/watchlist/add-bulk', json={
                'company_codes': codes,
            })
        self.assertEqual(response.status_code, 400)
        add.assert_not_called()


class ImmediateDeleteFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.html = read('templates/stock.html')

    def test_mutation_refresh_can_skip_stale_session_cache(self):
        block = function_block(self.html, 'loadWatchlist')
        self.assertIn('options.useCache !== false', block)
        self.assertIn("cache: 'no-store'", block)

    def test_single_delete_updates_the_view_before_refetch(self):
        block = function_block(self.html, 'removeFromWatchlist')
        local = block.index('removeCodesFromWatchlistView([code])')
        refetch = block.index('loadWatchlist({ useCache: false')
        self.assertLess(local, refetch)

    def test_clear_all_updates_the_view_before_refetch(self):
        block = function_block(self.html, 'removeAllWatchlist')
        local = block.index('commitWatchlistView([])')
        refetch = block.index('loadWatchlist({ useCache: false')
        self.assertLess(local, refetch)

    def test_selected_delete_updates_the_view_before_refetch(self):
        block = function_block(self.html, 'bulkRemoveFromList')
        local = block.index('removeCodesFromWatchlistView(codes)')
        refetch = block.index('loadWatchlist({ useCache: false')
        self.assertLess(local, refetch)


if __name__ == '__main__':
    unittest.main()
