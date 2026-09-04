# -*- coding: utf-8 -*-
"""止まったスケジューラを自分で起こし直す（2026-09-04）。

## 何が起きていたか

次回実行時刻が過去のまま進まなくなり、ジョブが発火しない状態が2回起きた
（3:02〜3:30、9:15〜10:00）。どちらも**再起動すると直った**。

⚠️ **再起動でしか直らない状態を残さない。** 誰かが気づいて Render の画面から
   Restart するまで、その日のジョブは走らない。夜中に起きたら翌朝まで止まる。

外形監視（UptimeRobot）が5分おきに /health/db を叩くので、そこに相乗りする。
新しい常駐スレッドは足さない。

    1. まず wakeup() … 眠っているだけならこれで動く（軽い）
    2. それでも直らなければ起動し直す … ループが死んでいる場合
"""

import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def jobs_at(minutes_late, job_id='gc_dc'):
    """minutes_late 分だけ遅れているジョブ1本。"""
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_late)
    return [{'id': job_id, 'next_run_time': when.isoformat()}]


class 起こす条件(unittest.TestCase):

    def setUp(self):
        import app
        self.app = app
        app._last_revive['at'] = None
        self.calls = []
        self._orig = app.record_job_run
        app.record_job_run = lambda job_id, ok, detail='': self.calls.append(
            (job_id, ok))
        # ⚠️ **他のテストが先に app を読み込むと ENABLE_SCHEDULER=false で
        #    起動しておらず、_thread が None になる。** 環境変数を setUp で
        #    立てても import 済みなので効かない。生きているスレッドを
        #    差し込んで、判定の中身だけを見る。
        self._thread = getattr(app.scheduler, '_thread', None)
        if self._thread is None or not self._thread.is_alive():
            class Alive:
                def is_alive(self):
                    return True
            app.scheduler._thread = Alive()
        self._wake = app.scheduler.wakeup
        app.scheduler.wakeup = lambda: None

    def tearDown(self):
        self.app.record_job_run = self._orig
        self.app.scheduler._thread = self._thread
        self.app.scheduler.wakeup = self._wake
        self.app._last_revive['at'] = None

    def test_予定が先なら触らない(self):
        future = [{'id': 'a', 'next_run_time':
                   (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}]
        self.assertFalse(self.app.revive_scheduler_if_stalled(future))
        self.assertEqual([], self.calls)

    def test_大きく遅れていたら起こす(self):
        self.assertTrue(self.app.revive_scheduler_if_stalled(jobs_at(30)))
        self.assertEqual([('scheduler_revive', True)], self.calls)

    def test_少しの遅れでは触らない(self):
        """⚠️ 警告のしきい値（15分）と同じにすると、警告が出た瞬間に
        毎回起こしにいくことになる。"""
        self.assertFalse(self.app.revive_scheduler_if_stalled(jobs_at(20)))
        self.assertGreater(self.app.SCHEDULER_REVIVE_AFTER_MINUTES, 15)

    def test_続けて呼んでも間隔をあける(self):
        """⚠️ 復帰の途中に何度も叩くと、走り始めたジョブを落とす。"""
        self.assertTrue(self.app.revive_scheduler_if_stalled(jobs_at(30)))
        self.assertFalse(self.app.revive_scheduler_if_stalled(jobs_at(45)))

    def test_ジョブが無ければ触らない(self):
        self.assertFalse(self.app.revive_scheduler_if_stalled([]))
        self.assertFalse(self.app.revive_scheduler_if_stalled(None))

    def test_起こしたことを記録する(self):
        """記録が無いと、何度も起きているのか一度きりなのか分からない。"""
        self.app.revive_scheduler_if_stalled(jobs_at(30))
        self.assertEqual([('scheduler_revive', True)], self.calls)


class 未起動なら触らない(unittest.TestCase):
    """ENABLE_SCHEDULER=false は意図的な設定。勝手に起動しない。"""

    def test_スレッドが無ければ何もしない(self):
        import app
        app._last_revive['at'] = None
        original = getattr(app.scheduler, '_thread', None)
        try:
            app.scheduler._thread = None
            self.assertFalse(app.revive_scheduler_if_stalled(jobs_at(60)))
        finally:
            app.scheduler._thread = original


class 作りの決まり(unittest.TestCase):

    def setUp(self):
        src = read('app.py')
        i = src.index('def revive_scheduler_if_stalled(')
        body = src[i:]
        cut = re.search(r'\n(?=(def |@app\.route|class ))', body[1:])
        self.block = body[:cut.start() + 1] if cut else body

    def test_wakeupが失敗しても死活判定まで進む(self):
        """⚠️ 分けないと、wakeup が投げた時点で「死んでいるか」を見ずに終わる。"""
        wake = self.block.index('scheduler.wakeup()')
        alive = self.block.index('thread.is_alive()')
        self.assertLess(wake, alive)
        # wakeup 自体が try で囲まれている
        head = self.block[:wake]
        self.assertGreaterEqual(head.count('try:'), 2)

    def test_死んでいたら起動し直す(self):
        self.assertIn('scheduler.shutdown(wait=False)', self.block)
        self.assertIn('scheduler.start()', self.block)

    def test_監視から呼ばれている(self):
        """気づくだけで放っておかない。"""
        src = read('app.py')
        i = src.index('def _jobs_health():')
        block = src[i:i + 1200]
        self.assertIn('revive_scheduler_if_stalled(jobs)', block)


if __name__ == '__main__':
    unittest.main()
