"""配当は「終わった決算年度」の値を使う。

2026-08-14 のバグ修正を固定するテスト。367A（8月決算）の銘柄ページで
1株配当が 105円 → 60円 の減配に見えていた。減配ではなく、60円は
進行中の2026年8月期の中間配当だった。

`stock_analyzer` は配当を決算年度ごとに合計し、行の日付を期末
（`2026-08-28`）にしている。日付が最も新しい行を拾う
`get_latest_value()` は、この**未来の日付＝まだ終わっていない年度**を
最新値として保存していた。

同じ画面の配当性向は確定した前期のものだったため、
1株配当60円・配当性向51.4%（実際は 105 ÷ EPS204.25）という、
読者が検算できない組み合わせになっていた。
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import get_latest_completed_value, get_latest_value


def _date(days_from_now):
    return (datetime.now() + timedelta(days=days_from_now)).strftime('%Y-%m-%d')


class TestGetLatestCompletedValue(unittest.TestCase):

    def test_未来日付の年度は拾わない(self):
        """367Aの実データ。進行中の2026年8月期(60円)ではなく確定した105円。"""
        series = [
            {'date': _date(14), 'value': 60.0},    # 進行中（中間配当のみ）
            {'date': _date(-351), 'value': 105.0},  # 確定
        ]
        self.assertEqual(get_latest_completed_value(series), 105.0)

    def test_修正前の挙動と比べる(self):
        """従来の get_latest_value は進行中の年度を拾ってしまう。"""
        series = [
            {'date': _date(14), 'value': 60.0},
            {'date': _date(-351), 'value': 105.0},
        ]
        self.assertEqual(get_latest_value(series), 60.0)

    def test_すべて過去なら最新を返す(self):
        series = [
            {'date': _date(-10), 'value': 50.0},
            {'date': _date(-380), 'value': 40.0},
        ]
        self.assertEqual(get_latest_completed_value(series), 50.0)

    def test_確定年度が1つも無ければNone(self):
        """半端な値を出すより「不明」にする（画面は欠損として理由を出す）。"""
        series = [{'date': _date(30), 'value': 10.0}]
        self.assertIsNone(get_latest_completed_value(series))

    def test_空とNoneとスカラー(self):
        self.assertIsNone(get_latest_completed_value(None))
        self.assertIsNone(get_latest_completed_value([]))
        self.assertEqual(get_latest_completed_value(30.0), 30.0)

    def test_日付が無い行は最新にならない(self):
        """date が空の行が最新と判定されると、年度不明の値が表に出る。

        空文字は日付の比較で必ず負けるため、日付のある行が優先される。
        """
        series = [{'value': 99.0}, {'date': _date(-10), 'value': 50.0}]
        self.assertEqual(get_latest_completed_value(series), 50.0)


if __name__ == '__main__':
    unittest.main()
