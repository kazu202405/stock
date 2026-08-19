"""Yahoo!JPサーキットブレーカーの半開放。

2026-08-19以前は一度開くと `reset()` を手で呼ぶまで戻らず、
プロセスが生きている限りYahoo!JPを一切見なかった。
その結果、今期予想が3,879件中348件（9.0%）しか入らなくなっていた。
「時間が経てば1本だけ試す」が効いていることを固定する。
"""

import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yahoo_jp_guard as g


class _Resp:
    def __init__(self, status_code, text='<html>ok</html>'):
        self.status_code = status_code
        self.text = text
        self.encoding = 'utf-8'


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        g.reset()
        self.addCleanup(g.reset)


class TestTrip(GuardTestCase):
    def test_opens_after_threshold(self):
        for _ in range(g.FAILURE_THRESHOLD):
            g.record_failure()
        self.assertTrue(g._state['tripped'])

    def test_does_not_open_before_threshold(self):
        for _ in range(g.FAILURE_THRESHOLD - 1):
            g.record_failure()
        self.assertFalse(g._state['tripped'])

    def test_success_resets_the_counter(self):
        for _ in range(g.FAILURE_THRESHOLD - 1):
            g.record_failure()
        g.record_success()
        g.record_failure()
        self.assertFalse(g._state['tripped'])


class TestHalfOpen(GuardTestCase):
    def test_blocked_while_cooling_down(self):
        for _ in range(g.FAILURE_THRESHOLD):
            g.record_failure()
        with patch('requests.get') as get:
            res = g.fetch_result('https://example.com/x')
        self.assertEqual(res['status'], 'circuit_open')
        get.assert_not_called()

    def test_one_request_passes_after_cooldown(self):
        for _ in range(g.FAILURE_THRESHOLD):
            g.record_failure()
        # 冷却が明けた状態を作る
        g._state['tripped_at'] = time.time() - g._cooldown_seconds() - 1
        with patch('requests.get', return_value=_Resp(200)) as get:
            res = g.fetch_result('https://example.com/x')
        get.assert_called_once()
        self.assertEqual(res['status'], 'success')
        # 通ったら閉じる
        self.assertFalse(g._state['tripped'])

    def test_failed_probe_reopens_with_longer_cooldown(self):
        for _ in range(g.FAILURE_THRESHOLD):
            g.record_failure()
        first = g._cooldown_seconds()
        g._state['tripped_at'] = time.time() - first - 1
        with patch('requests.get', return_value=_Resp(500)):
            res = g.fetch_result('https://example.com/x')
        self.assertEqual(res['status'], 'source_error')
        self.assertTrue(g._state['tripped'])
        # 開き直すたびに待ち時間が伸びる
        self.assertGreater(g._cooldown_seconds(), first)

    def test_cooldown_is_capped(self):
        g._state['trip_count'] = 99
        self.assertEqual(g._cooldown_seconds(), g.MAX_COOLDOWN_SECONDS)

    def test_probe_flag_is_released_on_exception(self):
        for _ in range(g.FAILURE_THRESHOLD):
            g.record_failure()
        g._state['tripped_at'] = time.time() - g._cooldown_seconds() - 1
        with patch('requests.get', side_effect=RuntimeError('boom')):
            g.fetch_result('https://example.com/x')
        # ここが立ちっぱなしだと、以降ずっと半開放に入れなくなる
        self.assertFalse(g._state['probing'])


class TestIsAvailable(GuardTestCase):
    def test_true_when_closed(self):
        self.assertTrue(g.is_available())

    def test_false_while_cooling_down(self):
        for _ in range(g.FAILURE_THRESHOLD):
            g.record_failure()
        self.assertFalse(g.is_available())

    def test_true_again_after_cooldown(self):
        """バッチの中断判定にも使われる。ここが False のままだと
        「もう二度と回復しない」と同じ意味になる。"""
        for _ in range(g.FAILURE_THRESHOLD):
            g.record_failure()
        g._state['tripped_at'] = time.time() - g._cooldown_seconds() - 1
        self.assertTrue(g.is_available())

    def test_force_disabled_wins(self):
        with patch.dict(os.environ, {'SKIP_YAHOO_JP': 'true'}):
            self.assertFalse(g.is_available())
            res = g.fetch_result('https://example.com/x')
        self.assertEqual(res['status'], 'disabled')


class TestNotBrokenBy404(GuardTestCase):
    def test_404_does_not_trip(self):
        """404は「その銘柄にそのページが無い」だけ。遮断ではない。"""
        with patch('requests.get', return_value=_Resp(404)):
            for _ in range(g.FAILURE_THRESHOLD + 2):
                g.fetch_result('https://example.com/x')
        self.assertFalse(g._state['tripped'])


class TestSnapshot(GuardTestCase):
    def test_reports_remaining_wait(self):
        for _ in range(g.FAILURE_THRESHOLD):
            g.record_failure()
        snap = g.status_snapshot()
        self.assertTrue(snap['tripped'])
        self.assertGreater(snap['retry_in_seconds'], 0)


if __name__ == '__main__':
    unittest.main()
