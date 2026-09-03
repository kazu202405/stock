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
import io
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
    TODAY = date(2026, 8, 24)

    def test_date_only(self):
        self.assertEqual(
            delisting.describe('2026-06-15T15:00:00+09:00', today=self.TODAY),
            '2026-06-15')

    def test_none_stays_none(self):
        self.assertIsNone(delisting.describe(None))
        self.assertIsNone(delisting.describe(''))

    def test_分からない印は出さない(self):
        """日足が1本も無い銘柄は、印を付けた時刻がそのまま入る。
        それを最終売買日として出すと「今日まで売買されていた会社が上場廃止」
        という嘘になる（7420 佐鳥電機・2692 伊藤忠食品で実際に出た）。

        見分けるのは時刻。本物は 15:00 JST（取引終了時刻）で作る。"""
        self.assertIsNone(
            delisting.describe('2026-08-24T09:00:00+09:00', today=self.TODAY))
        self.assertIsNone(
            delisting.describe('2026-08-10T09:00:00+09:00', today=self.TODAY))

    def test_新しくても本物なら出す(self):
        """⚠️ 以前は「30日以内の日付なら分からない印」と推測していた。
        JPXの一覧に無ければ廃止の当日から印を付けられるようになったので、
        その推測は本物の日付まで隠すようになった（実測で3社が「不明」と出た）。"""
        self.assertEqual(
            '2026-08-28',
            delisting.describe('2026-08-28T15:00:00+09:00', today=self.TODAY))

    def test_DBから素の文字列で戻っても読める(self):
        """保存はUTC。15:00 JST は 06:00 UTC。"""
        self.assertEqual(
            '2026-08-28',
            delisting.describe('2026-08-28T06:00:00', today=self.TODAY))

    def test_garbage_is_not_shown(self):
        self.assertIsNone(delisting.describe('not-a-date'))


class TestProbePeriod(unittest.TestCase):
    """生死は「最後の足がいつか」で見る（2026-09-03 に 1y から作り直した）。

    1年ぶんの足があるかで見ると、廃止から1年は古い足が残っていて
    「値が返る」になり、廃止したばかりの銘柄に永遠に印が付かない（実測で8社）。

    短い期間に変えられたのは、**先にJPXの一覧で PRO Market を落としているから**。
    PRO Market は売買が年に数回しかなく、直近数日に足が無いのが普通で、
    かつて5日で判定したときは動力・横浜ライト工業など約40社が一斉に
    「2026-07-17 上場廃止」と出た（そんな日は無い）。順番が条件になっている。
    """

    def test_最後の足の日付で見る(self):
        """⚠️ 「期間内に足が1本でもあるか」では、廃止直後に古い足が残っていて
        「生きている」と読んでしまう。実測で 2026-08-27〜28 に取引が終わった
        3社（nmsHD・ディーブイエックス・神鋼鋼線工業）がすり抜けた。"""
        import detect_delisted
        self.assertEqual(detect_delisted.PROBE_PERIOD, '1mo')
        self.assertTrue(detect_delisted.PROBE_STALE_DAYS >= 1)
        src = io.open(detect_delisted.__file__, encoding='utf-8').read()
        block = src.split('def probe_has_recent_trading(', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('closes.index[-1]', block)
        self.assertIn('business_days_between', block)

    def test_JPXの一覧が先に効いている(self):
        """5日判定を安全にしている前提。ここが消えたら5日に戻せない。"""
        import detect_delisted
        src = io.open(detect_delisted.__file__, encoding='utf-8').read()
        block = src.split('def plan_changes(', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('listed_codes()', block)
        self.assertIn('not in listed', block)


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
