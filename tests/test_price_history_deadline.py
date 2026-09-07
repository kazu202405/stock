"""チャートの取得は必ず時間内に返す（本番の503対策）。

2026-08-14。キオクシア(285A)のチャートが延々と読み込み中になり、
最終的に 503 が出た。その後は他のページも 503 になった。

構造の問題:
  `get_daily` / `get_long_term` は「保存済みが古い」というだけで、
  その場で Yahoo に取りに行っていた。外部が遅いとリクエストが
  何十秒も返らない。長期足は10年分を取ってから週足・月足に間引くので
  さらに重い（285Aは long_term_updated_at が空＝毎回この経路だった）。

  本番は worker 1本（app.py のAPSchedulerが多重起動するため増やせない）。
  1本のリクエストが詰まると、その裏で他の画面も待たされる。

直した形:
  - 保存済みがあれば**古くてもすぐ返し**、取り直しは裏で行う
  - 保存が何も無いときだけ待つ。それも上限つきで打ち切る

外部が遅いことは避けられない。避けられるのは「待ち続けること」。
"""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import price_history as ph


class TestCallWithDeadline(unittest.TestCase):

    def test_時間内に終われば値を返す(self):
        self.assertEqual(ph._call_with_deadline(lambda: 'ok', 5), 'ok')

    def test_上限を超えたら打ち切ってNoneを返す(self):
        started = time.time()
        result = ph._call_with_deadline(lambda: time.sleep(5), 0.3)
        elapsed = time.time() - started
        self.assertIsNone(result)
        # 呼び出し側は待たされない。処理自体は裏で走り続けるが、
        # リクエストが返ることの方が大事。
        self.assertLess(elapsed, 2.0)

    def test_例外は握り潰さない(self):
        def boom():
            raise ValueError('取得失敗')
        with self.assertRaises(ValueError):
            ph._call_with_deadline(boom, 5)


class TestFetchFailure(unittest.TestCase):

    @patch('yfinance.Ticker')
    def test_取得元が扱わない銘柄でも空配列を返す(self, ticker):
        """5075（名証単独上場）で例外がAPIの500まで漏れていた。"""
        instance = MagicMock()
        instance.history.side_effect = RuntimeError('symbol is not supported')
        ticker.return_value = instance

        self.assertEqual(ph.fetch_ohlc('5075.T'), [])


class TestListingMarketDetection(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.app_module = app_module
        app_module.app.config['TESTING'] = True

    def test_名証単独と東証との重複上場を区別する(self):
        detect = self.app_module._is_nagoya_only_listing
        self.assertTrue(detect({'market': '名証メイン'}))
        self.assertTrue(detect({'market_jp': '名古屋証券取引所 ネクスト'}))
        self.assertFalse(detect({
            'market': '名証プレミア', 'market_segment': 'プライム'}))
        self.assertFalse(detect({'market': '東証スタンダード'}))

    def test_名証単独は取得元を待たず空データの理由を返す(self):
        with patch.object(self.app_module, 'get_screened_data', return_value={
                'company_code': '5075', 'market': '名証メイン'}), \
                patch('price_history.get_stored', return_value=None), \
                patch('price_history.get_daily') as get_daily:
            response = self.app_module.app.test_client().get(
                '/api/stock/price-history/5075?range=1y')

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual([], data['rows'])
        self.assertEqual('nagoya_only_not_supported', data['unavailable_reason'])
        self.assertEqual('名証メイン', data['market'])
        get_daily.assert_not_called()

    def test_名証単独でも保存済みチャートは即座に返す(self):
        cached = [{'time': 1, 'open': 10, 'high': 11, 'low': 9,
                   'close': 10, 'volume': 100}]
        with patch.object(self.app_module, 'get_screened_data', return_value={
                'company_code': '5075', 'market': '名証メイン'}), \
                patch('price_history.get_stored', return_value={'daily_1y': cached}), \
                patch('price_history.get_daily') as get_daily:
            response = self.app_module.app.test_client().get(
                '/api/stock/price-history/5075?range=1y')

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(cached, data['rows'])
        self.assertIsNone(data['unavailable_reason'])
        get_daily.assert_not_called()


class TestRefreshInBackground(unittest.TestCase):

    def test_同じ銘柄の裏更新は多重に起動しない(self):
        """画面を開き直すたびに取得が積み上がると、外部への負荷になる。"""
        calls = []
        release = []

        def slow():
            calls.append(1)
            while not release:
                time.sleep(0.01)

        ph._refresh_in_background('test:dup', slow)
        for _ in range(5):
            ph._refresh_in_background('test:dup', slow)

        time.sleep(0.2)
        try:
            self.assertEqual(len(calls), 1)
        finally:
            release.append(True)
            time.sleep(0.1)

    def test_終わったら次を受け付ける(self):
        calls = []
        ph._refresh_in_background('test:seq', lambda: calls.append(1))
        time.sleep(0.2)
        ph._refresh_in_background('test:seq', lambda: calls.append(1))
        time.sleep(0.2)
        self.assertEqual(len(calls), 2)

    def test_裏の失敗はリクエストに影響しない(self):
        """裏更新が落ちても画面には保存済みが出ているので、握って記録するだけ。"""
        def boom():
            raise RuntimeError('Yahooが落ちている')
        ph._refresh_in_background('test:err', boom)
        time.sleep(0.2)
        # ここまで例外が漏れてこなければよい
        self.assertNotIn('test:err', ph._refreshing)


if __name__ == '__main__':
    unittest.main()
