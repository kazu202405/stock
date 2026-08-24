"""決算キューの再分析を自動で回す（scheduled_process_earnings_queue）。

2026-08-24 まで、決算発表の**検知**は 15:30 / 21:00 の cron で自動だったのに、
**再分析**は /earnings のボタンからしか動かなかった。押し忘れると決算をまたいでも
古い財務データが残る。しかも画面には何も出ないので気づけない。

⚠️ 要点は「決算期でも朝まで走らせないこと」。1日1,000件を超える日があり、
深夜2:00のYahoo項目バックフィルや3:30の日足更新とかち合うと共倒れする。
件数と時間の両方で上限をかける。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')


class TestNightlyEarningsProcessing(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app
        app.earnings_status = {"running": False, "done": 0, "total": 0,
                               "errors": 0, "codes": [], "stop_requested": False,
                               "finished_at": None, "error": None}
        app.daily_update_status = {"running": False}

    def test_processes_the_pending_codes(self):
        with patch('app.load_unprocessed_earnings', return_value=['1001', '1002']), \
             patch('app.get_supabase_client'), \
             patch('app._update_earnings_background') as run:
            self.app.scheduled_process_earnings_queue()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ['1001', '1002'])

    def test_passes_a_deadline(self):
        """上限時間を渡さないと決算期に朝まで走る。"""
        with patch('app.load_unprocessed_earnings', return_value=['1001']), \
             patch('app.get_supabase_client'), \
             patch('app._update_earnings_background') as run:
            self.app.scheduled_process_earnings_queue()
        self.assertIsNotNone(run.call_args.kwargs.get('deadline_at'))

    def test_caps_the_number_of_codes(self):
        with patch('app.load_unprocessed_earnings', return_value=['1001']) as load, \
             patch('app.get_supabase_client'), \
             patch('app._update_earnings_background'):
            self.app.scheduled_process_earnings_queue()
        self.assertEqual(load.call_args.args[1], self.app.EARNINGS_NIGHTLY_LIMIT)
        self.assertLessEqual(self.app.EARNINGS_NIGHTLY_LIMIT, 500)

    def test_finishes_before_the_2am_job(self):
        """22:00開始。深夜2:00のYahooバックフィルに食い込まないこと。"""
        self.assertLessEqual(self.app.EARNINGS_NIGHTLY_MINUTES, 4 * 60)

    def test_skips_when_already_running(self):
        self.app.earnings_status["running"] = True
        with patch('app.load_unprocessed_earnings') as load, \
             patch('app.get_supabase_client'), \
             patch('app._update_earnings_background') as run:
            self.app.scheduled_process_earnings_queue()
        load.assert_not_called()
        run.assert_not_called()

    def test_skips_while_the_daily_job_runs(self):
        self.app.daily_update_status["running"] = True
        with patch('app.load_unprocessed_earnings') as load, \
             patch('app.get_supabase_client'), \
             patch('app._update_earnings_background') as run:
            self.app.scheduled_process_earnings_queue()
        load.assert_not_called()
        run.assert_not_called()

    def test_empty_queue_is_not_an_error(self):
        with patch('app.load_unprocessed_earnings', return_value=[]), \
             patch('app.get_supabase_client'), \
             patch('app._update_earnings_background') as run:
            self.app.scheduled_process_earnings_queue()
        run.assert_not_called()
        self.assertFalse(self.app.earnings_status["running"])

    def test_a_crash_clears_the_running_flag(self):
        """立てたまま落ちると、翌晩以降ずっとスキップされ続ける。"""
        with patch('app.load_unprocessed_earnings', return_value=['1001']), \
             patch('app.get_supabase_client'), \
             patch('app._update_earnings_background',
                   side_effect=RuntimeError('boom')):
            self.app.scheduled_process_earnings_queue()
        self.assertFalse(self.app.earnings_status["running"])

    def test_queue_read_failure_does_not_raise(self):
        with patch('app.load_unprocessed_earnings',
                   side_effect=RuntimeError('boom')), \
             patch('app.get_supabase_client'), \
             patch('app._update_earnings_background') as run:
            self.app.scheduled_process_earnings_queue()   # 例外が外に出ないこと
        run.assert_not_called()


class TestDeadlineStopsTheLoop(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app
        app.earnings_status = {"running": False, "done": 0, "total": 0,
                               "errors": 0, "codes": [], "stop_requested": False,
                               "finished_at": None, "error": None}

    def test_stops_at_the_deadline(self):
        import time
        with patch('app.StockAnalyzer'), \
             patch('app._analyze_stock_and_save', return_value=True) as one, \
             patch('app.get_supabase_client'), \
             patch('time.sleep'):
            # すでに過ぎた時刻を渡す＝1件も始めない
            self.app._update_earnings_background(['1', '2', '3'],
                                                 deadline_at=time.monotonic() - 1)
        one.assert_not_called()

    def test_no_deadline_processes_everything(self):
        """ボタンからの実行は今までどおり全件やる。"""
        with patch('app.StockAnalyzer'), \
             patch('app._analyze_stock_and_save', return_value=True) as one, \
             patch('app.get_supabase_client'), \
             patch('time.sleep'):
            self.app._update_earnings_background(['1', '2', '3'])
        self.assertEqual(one.call_count, 3)

    def test_marks_each_one_processed_as_it_goes(self):
        """途中で切り上げても、済んだ分は翌晩に再処理されない。"""
        client = MagicMock()
        with patch('app.StockAnalyzer'), \
             patch('app._analyze_stock_and_save', return_value=True), \
             patch('app.get_supabase_client', return_value=client), \
             patch('time.sleep'):
            self.app._update_earnings_background(['1', '2'])
        self.assertEqual(client.table.return_value.update.call_count, 2)


if __name__ == '__main__':
    unittest.main()
