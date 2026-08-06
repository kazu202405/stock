"""yfinanceのレート制限に当たったときの待機・再試行のリグレッション。

実際に待つとテストが止まるので、sleepは差し替えて待機時間だけ検証する。
"""

import unittest

from yfinance_guard import (
    INITIAL_BACKOFF_SECONDS, MAX_BACKOFF_SECONDS, MAX_RETRIES_PER_ITEM,
    MAX_SLEEP_SECONDS, RateLimitExhausted, RateLimitGuard, is_rate_limit_error,
)


class FakeRateLimitError(Exception):
    """yfinance.exceptions.YFRateLimitError と同じ名前の当たり判定を再現する"""


FakeRateLimitError.__name__ = 'YFRateLimitError'


class DetectRateLimitTest(unittest.TestCase):
    def test_detects_by_exception_name(self):
        self.assertTrue(is_rate_limit_error(FakeRateLimitError('boom')))

    def test_detects_by_message(self):
        for message in ('Too Many Requests. Rate limited. Try after a while.',
                        'HTTP 429', 'rate-limit exceeded'):
            self.assertTrue(is_rate_limit_error(Exception(message)), message)

    def test_ignores_unrelated_errors(self):
        for message in ('connection refused', 'no data found', 'KeyError'):
            self.assertFalse(is_rate_limit_error(Exception(message)), message)
        self.assertFalse(is_rate_limit_error(None))


class RateLimitGuardTest(unittest.TestCase):
    def setUp(self):
        self.waits = []
        self.guard = RateLimitGuard(
            base_sleep=0.5, sleep_fn=self.waits.append)

    def test_passes_through_on_success(self):
        self.assertEqual(self.guard.run(lambda: 'ok'), 'ok')
        self.assertEqual(self.waits, [])
        self.assertEqual(self.guard.rate_limit_hits, 0)

    def test_retries_the_same_item_after_waiting(self):
        """当たった銘柄を捨てない。待ってから同じ銘柄をやり直す。"""
        calls = []

        def _flaky():
            calls.append(1)
            if len(calls) < 3:
                raise FakeRateLimitError('Too Many Requests')
            return 'done'

        self.assertEqual(self.guard.run(_flaky), 'done')
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(self.waits), 2)
        self.assertEqual(self.guard.rate_limit_hits, 2)

    def test_backoff_grows_exponentially(self):
        def _always():
            raise FakeRateLimitError('429')

        with self.assertRaises(RateLimitExhausted):
            self.guard.run(_always)

        self.assertEqual(len(self.waits), MAX_RETRIES_PER_ITEM - 1)
        # ばらつきを足しているので範囲で見る
        self.assertGreaterEqual(self.waits[0], INITIAL_BACKOFF_SECONDS)
        self.assertLess(self.waits[0], INITIAL_BACKOFF_SECONDS * 1.2)
        for earlier, later in zip(self.waits, self.waits[1:]):
            self.assertGreater(later, earlier)

    def test_backoff_is_capped(self):
        guard = RateLimitGuard(base_sleep=0, sleep_fn=self.waits.append)

        def _always():
            raise FakeRateLimitError('429')

        with self.assertRaises(RateLimitExhausted):
            guard.run(_always)
        for wait in self.waits:
            self.assertLessEqual(wait, MAX_BACKOFF_SECONDS * 1.2)

    def test_interval_widens_after_hitting_the_limit(self):
        """当たった後は次の銘柄までの間隔も広げる（当たりに行かない）"""
        calls = []

        def _flaky():
            calls.append(1)
            if len(calls) < 2:
                raise FakeRateLimitError('429')
            return 'ok'

        before = self.guard.current_sleep
        self.guard.run(_flaky)
        self.assertGreater(self.guard.current_sleep, before)

    def test_interval_recovers_on_clean_runs(self):
        self.guard.current_sleep = 4.0
        for _ in range(5):
            self.guard.run(lambda: 'ok')
        self.assertLess(self.guard.current_sleep, 4.0)
        self.assertGreaterEqual(self.guard.current_sleep, self.guard.base_sleep)

    def test_interval_never_exceeds_the_cap(self):
        def _always_limited():
            raise FakeRateLimitError('429')

        for _ in range(6):
            try:
                self.guard.run(_always_limited)
            except RateLimitExhausted:
                pass
        self.assertLessEqual(self.guard.current_sleep, MAX_SLEEP_SECONDS)

    def test_other_errors_are_not_swallowed(self):
        """レート制限以外は握り潰さず呼び出し側へ返す"""
        def _broken():
            raise ValueError('壊れたデータ')

        with self.assertRaises(ValueError):
            self.guard.run(_broken)
        self.assertEqual(self.waits, [])

    def test_pause_uses_the_current_interval(self):
        self.guard.current_sleep = 1.25
        self.guard.pause()
        self.assertEqual(self.waits, [1.25])


class BackfillWiringTest(unittest.TestCase):
    def test_valuation_backfill_uses_the_guard(self):
        from pathlib import Path
        source = Path('backfill_valuation_inputs.py').read_text(encoding='utf-8')
        self.assertIn('RateLimitGuard', source)
        self.assertIn('RateLimitExhausted', source)
        # 素のsleepに戻っていないこと
        self.assertNotIn('time.sleep(args.sleep)', source)


if __name__ == '__main__':
    unittest.main()
