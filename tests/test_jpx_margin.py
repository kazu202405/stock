import unittest
from unittest.mock import patch

from jpx_margin import parse_margin_text
from stock_analyzer import StockAnalyzer


class JpxMarginTests(unittest.TestCase):
    def test_parse_weekly_margin_row_and_ratio(self):
        text = """2026/7/31 申込み現在
B トヨタ自動車　普通株式 72030 JP3633400001 1,213,200 219,500 17,347,800 ▲ 4,540,700 307,400 161,100 905,800 58,400 6,360,400 ▲ 1,792,300 10,987,400 ▲ 2,748, 400
"""

        rows, as_of = parse_margin_text(text)

        self.assertEqual("2026-07-31", as_of)
        self.assertEqual(1213200, rows["7203"]["margin_trading_sell"])
        self.assertEqual(17347800, rows["7203"]["margin_trading_buy"])
        self.assertEqual(14.3, rows["7203"]["margin_trading_ratio"])

    @patch("jpx_margin.get_margin_balance")
    @patch("yahoo_jp_guard.fetch_result")
    def test_analyzer_uses_jpx_when_yahoo_is_blocked(self, yahoo_fetch, jpx_fetch):
        yahoo_fetch.return_value = {
            "html": None, "status": "rate_limited", "http_status": 429,
            "error": None, "url": "https://finance.yahoo.co.jp/example",
        }
        jpx_fetch.return_value = ({
            "margin_trading_sell": 100,
            "margin_trading_buy": 500,
            "margin_trading_ratio": 5.0,
            "as_of": "2026-07-31",
        }, {
            "status": "success", "source": "JPX 銘柄別信用取引週末残高",
            "as_of": "2026-07-31",
        })
        result = {
            "margin_trading_sell": None,
            "margin_trading_buy": None,
            "margin_trading_ratio": None,
            "source_status": {},
        }

        StockAnalyzer()._get_margin_trading_data("7203.T", result)

        self.assertEqual(5.0, result["margin_trading_ratio"])
        self.assertEqual("2026-07-31", result["margin_trading_as_of"])
        self.assertEqual("success", result["source_status"]["margin_trading"]["status"])
        self.assertEqual(
            "rate_limited",
            result["source_status"]["margin_trading"]["yahoo_attempt"]["status"],
        )


if __name__ == "__main__":
    unittest.main()
