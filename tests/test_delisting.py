"""上場廃止の判定（delisting.py）。

2026-08-24。2026年のTOB・MBOの波で5〜7月だけで22社が上場廃止になっていたが、
アプリは生きた銘柄として表示し続けていた（豊田自動織機・ラクスル・養命酒製造・
タカラバイオ・MCJ 等）。株価は最終売買日で凍結、検索にもスクリーナーにも出て、
上場廃止だとはどこにも書かれていなかった。

⚠️ 一番こわいのは**上場中の会社を廃止扱いにすること**。アプリから消えてしまい、
しかも誰も報告してこない。判定はゆるく（30日）、確定は yfinance の確認を経てから。
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import delisting

JST = timezone(timedelta(hours=9))


def bar(day):
    d = datetime.fromisoformat(day).replace(tzinfo=JST)
    return {'time': int(d.timestamp()), 'close': 100.0}


class TestLastBarDate(unittest.TestCase):
    def test_picks_the_newest(self):
        bars = [bar('2026-06-01'), bar('2026-06-15'), bar('2026-06-10')]
        self.assertEqual(delisting.last_bar_date(bars), date(2026, 6, 15))

    def test_empty(self):
        self.assertIsNone(delisting.last_bar_date([]))
        self.assertIsNone(delisting.last_bar_date(None))

    def test_broken_bars_are_skipped(self):
        bars = [{'close': 1}, {'time': 'abc'}, bar('2026-06-01')]
        self.assertEqual(delisting.last_bar_date(bars), date(2026, 6, 1))


class TestIsChartStale(unittest.TestCase):
    def test_yesterday_is_alive(self):
        self.assertFalse(delisting.is_chart_stale(
            [bar('2026-08-21')], today=date(2026, 8, 24)))

    def test_new_year_holiday_is_not_delisting(self):
        """年末年始は9日ほど開く。ここで廃止扱いにしてはいけない。"""
        self.assertFalse(delisting.is_chart_stale(
            [bar('2025-12-30')], today=date(2026, 1, 8)))

    def test_two_months_of_silence_is_stale(self):
        self.assertTrue(delisting.is_chart_stale(
            [bar('2026-06-15')], today=date(2026, 8, 24)))

    def test_exactly_at_the_line_is_not_stale(self):
        """境界は「超えたら」。ちょうど30日は生きている側に倒す。"""
        self.assertFalse(delisting.is_chart_stale(
            [bar('2026-07-25')], today=date(2026, 8, 24)))

    def test_no_bars_at_all_is_stale(self):
        self.assertTrue(delisting.is_chart_stale([], today=date(2026, 8, 24)))

    def test_the_threshold_leaves_room_for_holidays(self):
        self.assertGreaterEqual(delisting.STALE_CHART_DAYS, 14)


class TestDelistedTimestamp(unittest.TestCase):
    def test_uses_the_last_trading_day(self):
        """「いつまでの数字か」を画面に出せるようにする。"""
        stamp = delisting.delisted_timestamp([bar('2026-06-15')])
        self.assertEqual(delisting.describe(stamp), '2026-06-15')

    def test_falls_back_to_now_when_there_is_no_history(self):
        self.assertIsNotNone(delisting.delisted_timestamp([]))


class TestDescribe(unittest.TestCase):
    def test_date_only(self):
        self.assertEqual(
            delisting.describe('2026-06-15T15:00:00+09:00'), '2026-06-15')

    def test_none_stays_none(self):
        self.assertIsNone(delisting.describe(None))
        self.assertIsNone(delisting.describe(''))


class TestRealCases(unittest.TestCase):
    """実際に上場廃止になった銘柄の日付で確かめる（2026-08-24 実測）。"""

    CASES = {
        '6201 豊田自動織機': '2026-06-01',
        '4384 ラクスル': '2026-05-28',
        '2540 養命酒製造': '2026-06-17',
        '6670 MCJ': '2026-06-15',
    }

    def test_all_are_detected_as_stale(self):
        today = date(2026, 8, 24)
        for name, last in self.CASES.items():
            self.assertTrue(
                delisting.is_chart_stale([bar(last)], today=today),
                f'{name} を上場中と判定している')


if __name__ == '__main__':
    unittest.main()
