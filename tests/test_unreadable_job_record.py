# -*- coding: utf-8 -*-
"""実行記録が「読めなかった」ときに誤検知しない（2026-09-04）。

## 本番ログで確定した事実

    実行記録の取得に失敗 (price_update): Server disconnected
    [health/db] 定期実行の異常を検出: price     ← 直後に誤検知
    "HEAD /health/db" 503
    "HEAD /health/db" 200                      ← 15秒後には正常

Supabase への接続が一瞬切れる（Server disconnected）たびに、監視が503を
返していた。**ジョブは正常に動いており、データも正しい。**

原因は `last_run()` が、失敗も「記録が無い」も同じ `None` で返すこと。
`job_state()` は None を「まだ1回も走っていない」と読むので、

    開始の印は読めた → 完了の印が読めなかった（None）
    → 「始まったまま終わっていない」＝ hung と判定

⚠️ **「読めなかった」と「無かった」は別物。** 混ぜると、通信が一瞬切れる
   だけで異常扱いになる。実測で 9/3 の数時間だけで5回以上 503 を出していた。

⚠️ **ただし「分からない」を正常に倒しすぎない。** ここで無視してよいのは
   *実行記録が読めない* ことだけ。**ジョブそのものの生死**は
   `scheduler_item`（次回予定を見る）が別途担保しており、そちらは
   読めなければ warn のままにしてある。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import data_freshness as df  # noqa: E402

FUTURE = [{'id': 'a', 'next_run_time': '2099-01-01T00:00:00+09:00'}]


class Failing:
    """接続が切れるクライアント。"""

    def table(self, *a):
        raise RuntimeError('Server disconnected')


class Empty:
    """記録が1件も無いクライアント。"""

    def table(self, *a):
        return self

    def select(self, *a):
        return self

    def eq(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        class R:
            data = []
        return R()


class 読めないと無いを区別する(unittest.TestCase):

    def test_読めなければunknown(self):
        state, _, _ = df.job_state(Failing(), 'price_update')
        self.assertEqual('unknown', state)

    def test_記録が無ければnone(self):
        state, _, _ = df.job_state(Empty(), 'price_update')
        self.assertEqual('none', state)

    def test_同じ値で返さない(self):
        """⚠️ ここを同じにすると、通信が切れるたびに hung と誤検知する。"""
        self.assertNotEqual(df.job_state(Failing(), 'x')[0],
                            df.job_state(Empty(), 'x')[0])


class 監視は読めないだけで鳴らさない(unittest.TestCase):

    def test_読めなくても503にしない(self):
        """本物の異常が埋もれるので、通信の一時的な失敗では鳴らさない。"""
        ok, problem = df.health(FUTURE, client=Failing())
        self.assertTrue(ok)
        self.assertIsNone(problem)

    def test_ジョブの生死は別で見ている(self):
        """⚠️ 「分からない」を全部正常に倒さない。次回予定が読めないときは
        warn のまま（ここを ok にすると監視が黙って無効になる）。"""
        self.assertEqual('warn', df.scheduler_item(None)['status'])
        ok, problem = df.health(None, client=Empty())
        self.assertFalse(ok)
        # 2026-09-04: どのジョブが遅れているかを後ろに付けたので前方一致で見る
        self.assertTrue(problem.startswith('scheduler'), problem)


class パネルは赤くしない(unittest.TestCase):
    """unknown は failed / hung と別扱い。"""

    def test_赤くするのは失敗と死んだ実行だけ(self):
        src = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data_freshness.py'), encoding='utf-8').read()
        # 状態で赤くする判定に unknown を混ぜていない
        for line in src.split('\n'):
            if "('failed', 'hung')" in line:
                self.assertNotIn('unknown', line)


if __name__ == '__main__':
    unittest.main()
