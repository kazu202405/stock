"""夜間のYahoo項目バックフィル（scheduled_backfill_yahoo_profile）。

手で回すと400件で3時間かかり（遮断17回ぶんの待ち）、残り3,300件だと
約27時間になる。急ぐ理由は無いので毎晩少しずつ進める設計にした。

このジョブは **web プロセスの中で動く**ので、
「遮断されたら待たずに切り上げる」ことが要点。待つと冷却の10〜60分を
スレッドが抱え、他の定期実行とかち合う。そこを固定する。
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')


class TestNightlyProfileBackfill(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app

    def test_skips_when_breaker_is_open(self):
        """遮断中は1件も叩かない。開いている相手に行っても無駄打ちになる。"""
        with patch('yahoo_jp_guard.status_snapshot',
                   return_value={'tripped': True, 'force_disabled': False}), \
             patch('backfill_yahoo_fields.load_targets') as targets, \
             patch('backfill_yahoo_fields.fill_one') as fill:
            self.app.scheduled_backfill_yahoo_profile()
        targets.assert_not_called()
        fill.assert_not_called()

    def test_skips_when_force_disabled(self):
        with patch('yahoo_jp_guard.status_snapshot',
                   return_value={'tripped': False, 'force_disabled': True}), \
             patch('backfill_yahoo_fields.fill_one') as fill:
            self.app.scheduled_backfill_yahoo_profile()
        fill.assert_not_called()

    def test_stops_when_breaker_trips_midway(self):
        """途中で遮断されたら**待たずに**切り上げる。翌晩に回せばよい。"""
        calls = {'n': 0}

        def snapshot():
            # 最初の確認と2件目までは閉じている。3件目の手前で開く
            calls['n'] += 1
            return {'tripped': calls['n'] > 3, 'force_disabled': False}

        with patch('yahoo_jp_guard.status_snapshot', side_effect=snapshot), \
             patch('backfill_yahoo_fields.load_targets',
                   return_value=['1001', '1002', '1003', '1004', '1005']), \
             patch('backfill_yahoo_fields.fill_one', return_value=3) as fill, \
             patch('stock_analyzer.StockAnalyzer'), \
             patch('time.sleep'):
            self.app.scheduled_backfill_yahoo_profile()

        self.assertLess(fill.call_count, 5, '遮断後も叩き続けている')
        self.assertGreater(fill.call_count, 0, '1件も処理していない')

    def test_respects_nightly_limit(self):
        """1晩の上限を超えて回さない。Yahooは約50件で遮断する。"""
        with patch('yahoo_jp_guard.status_snapshot',
                   return_value={'tripped': False, 'force_disabled': False}), \
             patch('backfill_yahoo_fields.load_targets') as targets, \
             patch('backfill_yahoo_fields.fill_one', return_value=1), \
             patch('stock_analyzer.StockAnalyzer'), \
             patch('time.sleep'):
            targets.return_value = [str(i) for i in range(1000)]
            self.app.scheduled_backfill_yahoo_profile()
        self.assertLessEqual(self.app.NIGHTLY_PROFILE_LIMIT, 60)

    def test_one_failure_does_not_stop_the_run(self):
        """1銘柄の失敗で夜間ジョブ全体を落とさない。"""
        with patch('yahoo_jp_guard.status_snapshot',
                   return_value={'tripped': False, 'force_disabled': False}), \
             patch('backfill_yahoo_fields.load_targets',
                   return_value=['1001', '1002', '1003']), \
             patch('backfill_yahoo_fields.fill_one',
                   side_effect=[RuntimeError('boom'), 5, 5]) as fill, \
             patch('stock_analyzer.StockAnalyzer'), \
             patch('time.sleep'):
            self.app.scheduled_backfill_yahoo_profile()
        self.assertEqual(fill.call_count, 3)

    def test_no_targets_is_not_an_error(self):
        with patch('yahoo_jp_guard.status_snapshot',
                   return_value={'tripped': False, 'force_disabled': False}), \
             patch('backfill_yahoo_fields.load_targets', return_value=[]), \
             patch('backfill_yahoo_fields.fill_one') as fill:
            self.app.scheduled_backfill_yahoo_profile()
        fill.assert_not_called()

    def test_does_not_spend_the_edinet_budget(self):
        """EDINETの無料枠(100回/日)は閲覧と23時のジョブに残す。
        夜間ジョブが先に食うと、日中に見たい人が枠切れになる。"""
        with patch('yahoo_jp_guard.status_snapshot',
                   return_value={'tripped': False, 'force_disabled': False}), \
             patch('backfill_yahoo_fields.load_targets', return_value=['1001']), \
             patch('backfill_yahoo_fields.fill_one', return_value=1) as fill, \
             patch('stock_analyzer.StockAnalyzer'), \
             patch('time.sleep'):
            self.app.scheduled_backfill_yahoo_profile()
        _, kwargs = fill.call_args
        self.assertFalse(kwargs.get('use_edinet_forecasts', False))


if __name__ == '__main__':
    unittest.main()
