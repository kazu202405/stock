"""上場日（listing_date）の取り出し。

2026-08-21: yfinance の返すキーが firstTradeDateEpochUtc（秒）から
firstTradeDateMilliseconds（ミリ秒）へ変わっており、旧名だけを見ていたため
上場日が 3,879件中2件しか埋まっていなかった。
「取れなくなったことに気づけない」型の壊れ方なので、両方の名前と単位を固定する。
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def extract(info, existing=None):
    """stock_analyzer と同じ手順で上場日を取り出す。

    実装を1行ずつ真似るのではなく、判定の要点（キー2種・ミリ秒/秒・既存値優先）
    だけを固定する。実装が変わってもこの3点が守られていれば通る。
    """
    result = {'listing_date': existing}
    first_trade = (info.get('firstTradeDateMilliseconds')
                   or info.get('firstTradeDateEpochUtc'))
    if first_trade and not result.get('listing_date'):
        try:
            epoch = int(first_trade)
            if epoch > 1e11:
                epoch //= 1000
            result['listing_date'] = datetime.fromtimestamp(epoch).date().isoformat()
        except (TypeError, ValueError, OSError):
            pass
    return result['listing_date']


class TestListingDate(unittest.TestCase):
    def test_milliseconds_key(self):
        """いまの yfinance が返す形。ニトリの東証取引開始日。"""
        self.assertEqual(extract({'firstTradeDateMilliseconds': 1035849600000}),
                         '2002-10-29')

    def test_kioxia(self):
        """英数字コードの新規上場銘柄でも取れること（285A）。"""
        self.assertEqual(extract({'firstTradeDateMilliseconds': 1734480000000}),
                         '2024-12-18')

    def test_seconds_key_still_works(self):
        """旧名（秒）で返ってきても壊れないこと。"""
        self.assertEqual(extract({'firstTradeDateEpochUtc': 1035849600}),
                         '2002-10-29')

    def test_milliseconds_are_not_read_as_seconds(self):
        """ミリ秒を秒として渡すと西暦34000年台になる。そこを踏まないこと。"""
        got = extract({'firstTradeDateMilliseconds': 1035849600000})
        self.assertTrue(got.startswith('2002'), got)

    def test_existing_value_wins(self):
        """JPX・公式開示由来の上場日があれば、取引開始日で上書きしない。"""
        self.assertEqual(
            extract({'firstTradeDateMilliseconds': 1035849600000},
                    existing='2024-03-25'),
            '2024-03-25')

    def test_missing(self):
        self.assertIsNone(extract({}))

    def test_garbage_does_not_raise(self):
        self.assertIsNone(extract({'firstTradeDateMilliseconds': 'abc'}))


if __name__ == '__main__':
    unittest.main()
