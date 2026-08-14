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
