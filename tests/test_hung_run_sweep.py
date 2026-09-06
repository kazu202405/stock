# -*- coding: utf-8 -*-
"""死んだ実行を、次の実行を待たずに終端する（2026-09-06）。

何が起きていたか:
  close_hung_run を呼ぶのは「同じジョブの次の実行」だった。株価は
  1日3回なので数時間で片付くが、daily_and_crosses（3:30の1日1回）は
  死ぬと翌日まで赤いままで、2026-09-04 は手で終端していた。

  ⚠️ プロセスが落ちると finally は走らない。開始の印だけが残る。
     検知できても自力で戻れないなら、結局は人が見張ることになる。
"""

import datetime as dt
import os
import unittest

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import data_freshness as df

UTC = dt.timezone.utc


class FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return type('R', (), {'data': self._rows})()


class FakeClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        return FakeTable(self._rows)


class BrokenClient:
    def table(self, name):
        raise RuntimeError('接続できません')


def row(job_id, minutes_ago, now):
    return {'job_id': job_id,
            'ran_at': (now - dt.timedelta(minutes=minutes_ago)).isoformat()}


class HungSweepTest(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.now(UTC)

    def _hung(self, rows, **kw):
        return df.hung_jobs(FakeClient(rows), now=self.now, **kw)

    def test_a_start_with_no_finish_is_swept(self):
        rows = [row('daily_and_crosses:start', 300, self.now)]
        self.assertEqual(self._hung(rows), ['daily_and_crosses'])

    def test_a_finished_job_is_left_alone(self):
        rows = [row('daily_and_crosses', 290, self.now),
                row('daily_and_crosses:start', 300, self.now)]
        self.assertEqual(self._hung(rows), [])

    def test_a_job_still_running_is_left_alone(self):
        """⚠️ 生きている実行を終端すると、本物の終了で記録が二重になる。"""
        rows = [row('price_update:start', 5, self.now)]
        self.assertEqual(self._hung(rows), [])

    def test_the_sweep_waits_longer_than_the_panel_warns(self):
        """パネルは45分で警告、終端はもっと確実になってから。"""
        self.assertGreater(df.JOB_SWEEP_MINUTES, df.JOB_HUNG_MINUTES)
        rows = [row('backfill:start', df.JOB_HUNG_MINUTES + 5, self.now)]
        self.assertEqual(self._hung(rows), [],
                         '警告と同じ時間で終端している（長く走るジョブを殺す）')

    def test_a_restarted_job_is_not_swept_by_its_old_finish(self):
        """終了より後に始まった実行は、まだ終わっていない扱い。"""
        rows = [row('price_update:start', 200, self.now),
                row('price_update', 400, self.now)]
        self.assertEqual(self._hung(rows), ['price_update'])

    def test_unreadable_records_are_not_treated_as_dead(self):
        """⚠️「分からない」を「死んだ」にしない。"""
        self.assertEqual(df.hung_jobs(BrokenClient(), now=self.now), [])

    def test_the_health_check_sweeps(self):
        """5分おきの /health/db から呼ばれていること。

        呼び出しが外れると、検知はできるのに片付かない状態に戻る。
        """
        import inspect

        import app as app_module

        source = inspect.getsource(app_module._jobs_health)
        self.assertIn('sweep_hung_runs()', source)


if __name__ == '__main__':
    unittest.main()
