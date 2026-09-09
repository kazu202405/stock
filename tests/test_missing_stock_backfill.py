# -*- coding: utf-8 -*-
"""初回取得で漏れた銘柄を夜間に拾い直す。"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import missing_stock_backfill as targets


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, pages):
        self.pages = pages
        self.range_start = 0

    def select(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start, _end):
        self.range_start = start
        return self

    def execute(self):
        return FakeResult(self.pages.get(self.range_start, []))


class TargetSelectionTest(unittest.TestCase):
    def test_master_excludes_non_operating_products(self):
        rows = [
            {'c': '2798', 'n': 'ワイズテーブルコーポレーション'},
            {'c': '9999', 'n': '日経平均ブル2倍上場投信'},
            {'c': '2798', 'n': '重複'},
        ]
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False,
                                         encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False)
            path = f.name
        try:
            self.assertEqual(['2798'], targets.load_master_codes(path))
        finally:
            os.unlink(path)

    def test_analyzed_codes_are_loaded_with_paging(self):
        client = MagicMock()
        client.table.return_value = FakeQuery({
            0: [
                {'company_code': '1001', 'analyzed_at': '2026-09-01'},
                {'company_code': '1002', 'analyzed_at': None},
            ],
            2: [{'company_code': '1003.T', 'analyzed_at': '2026-09-02'}],
        })
        self.assertEqual({'1001', '1003'},
                         targets.load_analyzed_codes(client, page_size=2))

    def test_retries_failures_without_blocking_new_codes(self):
        attempts = {
            '1001': {'ok': False, 'ran_at': '2026-09-08T00:00:00Z'},
            '1002': {'ok': False, 'ran_at': '2026-09-07T00:00:00Z'},
            '1003': {'ok': False, 'ran_at': '2026-09-06T00:00:00Z'},
        }
        selected = targets.select_targets(
            ['1001', '1002', '1003', '1004', '1005', '1006'], attempts, 4)
        self.assertEqual(['1003', '1002', '1004', '1005'], selected)

    def test_uses_remaining_slots_when_every_code_was_tried(self):
        attempts = {
            '1001': {'ok': False, 'ran_at': '2026-09-08T00:00:00Z'},
            '1002': {'ok': False, 'ran_at': '2026-09-07T00:00:00Z'},
            '1003': {'ok': False, 'ran_at': '2026-09-06T00:00:00Z'},
        }
        selected = targets.select_targets(['1001', '1002', '1003'], attempts, 4)
        self.assertEqual(['1003', '1002', '1001'], selected)


class ScheduledBackfillTest(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app

    def test_saves_success_and_records_each_failure(self):
        records = []
        with patch.object(self.app, 'claim_job', return_value=True), \
             patch('missing_stock_backfill.find_missing_analysis_targets',
                   return_value=(['2798', '9999'], 12)), \
             patch.object(self.app, '_analyze_stock_and_save',
                          side_effect=[{'company_code': '2798'}, None]), \
             patch.object(self.app, 'StockAnalyzer'), \
             patch.object(self.app, 'record_job_run',
                          side_effect=lambda job_id, ok, detail='':
                          records.append((job_id, ok, detail))), \
             patch('time.sleep'):
            self.app.scheduled_backfill_missing_stocks()

        self.assertTrue(any(r[0].endswith(':2798') and r[1] for r in records))
        self.assertTrue(any(r[0].endswith(':9999') and not r[1] for r in records))
        overall = [r for r in records if r[0] == 'missing_stock_backfill'][-1]
        self.assertFalse(overall[1])
        self.assertIn('残り11件', overall[2])

    def test_no_targets_is_a_successful_run(self):
        records = []
        with patch.object(self.app, 'claim_job', return_value=True), \
             patch('missing_stock_backfill.find_missing_analysis_targets',
                   return_value=([], 0)), \
             patch.object(self.app, '_analyze_stock_and_save') as analyze, \
             patch.object(self.app, 'record_job_run',
                          side_effect=lambda job_id, ok, detail='':
                          records.append((job_id, ok, detail))):
            self.app.scheduled_backfill_missing_stocks()
        analyze.assert_not_called()
        self.assertEqual(('missing_stock_backfill', True, '未取得銘柄なし'),
                         records[-1])

    def test_duplicate_scheduler_process_does_not_run(self):
        with patch.object(self.app, 'claim_job', return_value=False), \
             patch('missing_stock_backfill.find_missing_analysis_targets') as find:
            self.app.scheduled_backfill_missing_stocks()
        find.assert_not_called()


class ManualSaveFiscalMonthTest(unittest.TestCase):
    def test_manual_save_uses_same_fiscal_month_path(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'app.py')
        with open(path, encoding='utf-8') as f:
            source = f.read()
        start = source.index('def _save_analysis_to_screened(')
        end = source.index('\n\n# バックグラウンド分析の進捗管理', start)
        body = source[start:end]
        self.assertIn("'fiscal_month': derive_fiscal_month(", body)
        self.assertIn('_authoritative_fiscal_month(company_code)', body)
        self.assertIn('_save_screened_tolerating_new_columns(screened_data)', body)


if __name__ == '__main__':
    unittest.main()
