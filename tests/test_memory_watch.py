# -*- coding: utf-8 -*-
"""定期ジョブのメモリ使用量をログに残す（2026-09-11）。

Render（512MB）の OOM が続いたが、何が重いのかの記録が無かった。
OOM はプロセスごと殺されるので、**落ちる直前の1行が残ること**がいちばん大事。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memory_watch as mw


class 使用量の読み取り(unittest.TestCase):

    def _status(self, text):
        f = tempfile.NamedTemporaryFile('w', suffix='.status', delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.remove, f.name)
        return f.name

    def test_VmRSSをMBで返す(self):
        path = self._status('Name:\tgunicorn\nVmPeak:\t 900000 kB\n'
                            'VmRSS:\t  204800 kB\nThreads:\t12\n')
        self.assertEqual(200.0, mw.rss_mb(path))

    def test_ファイルが無ければNone(self):
        """手元の Windows には /proc が無い。例外で本体を止めない。"""
        self.assertIsNone(mw.rss_mb(os.path.join(tempfile.gettempdir(),
                                                 'no-such-status')))

    def test_VmRSSの行が無ければNone(self):
        self.assertIsNone(mw.rss_mb(self._status('Name:\tx\n')))


class ジョブの記録(unittest.TestCase):

    def setUp(self):
        mw._running.clear()
        self.logs = []
        patcher = mock.patch.object(mw, '_log', self.logs.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(mw._running.clear)

    def _rss(self, *values):
        patcher = mock.patch.object(mw, 'rss_mb', side_effect=list(values))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_開始_最大_終了を1行にまとめる(self):
        self._rss(200.0, 450.0, 260.0)
        mw.job_started('price_update_morning', now=1000)
        mw.sample()
        line = mw.job_finished('price_update_morning', now=1215)
        self.assertEqual('price_update_morning 終了: 開始 200MB → 最大 450MB '
                         '→ 終了 260MB（3分35秒）', line)
        self.assertEqual({}, mw._running)

    def test_走っている間の行にジョブ名が出る(self):
        """落ちた回は「終了」が出ない。直前の行だけで何が走っていたか分かること。"""
        self._rss(200.0, 480.0)
        mw.job_started('daily_and_crosses', now=0)
        mw.sample()
        self.assertIn('480MB 実行中: daily_and_crosses', self.logs[-1])

    def test_同じジョブが重なったら本数を出し_最後の1本で要約する(self):
        """9/9 から「完了」が2回ずつ記録されている。重なりをログで見えるようにする。"""
        self._rss(200.0, 300.0, 350.0, 320.0, 280.0)
        mw.job_started('gc_dc_morning', now=0)
        mw.job_started('gc_dc_morning', now=3)
        self.assertIn('2本同時', self.logs[-1])
        mw.sample()
        self.assertIn('gc_dc_morning×2', self.logs[-1])
        self.assertIsNone(mw.job_finished('gc_dc_morning', now=60))
        line = mw.job_finished('gc_dc_morning', now=90)
        self.assertIn('最大 350MB', line)

    def test_知らないジョブの終了は無視する(self):
        self._rss(200.0)
        self.assertIsNone(mw.job_finished('unknown'))

    def test_測れない環境では何も書かない(self):
        self._rss(None)
        self.assertIsNone(mw.sample())
        self.assertEqual([], self.logs)


class ログの出し方(unittest.TestCase):

    def test_すぐ書き出す(self):
        """⚠️ stdout はまとめて書き出される。落ちると溜まった行ごと消えるので、
        毎行 flush する。"""
        with mock.patch('builtins.print') as p:
            mw._log('x')
        self.assertEqual(('[mem] x',), p.call_args.args)
        self.assertIs(True, p.call_args.kwargs.get('flush'))


class スケジューラへの取り付け(unittest.TestCase):

    def test_開始と終了と失敗を拾う(self):
        from apscheduler.events import (EVENT_JOB_ERROR, EVENT_JOB_EXECUTED,
                                        EVENT_JOB_SUBMITTED)
        from apscheduler.schedulers.background import BackgroundScheduler
        s = BackgroundScheduler()
        listener = mw.attach(s)
        masks = [mask for cb, mask in s._listeners if cb is listener]
        self.assertEqual(1, len(masks))
        for code in (EVENT_JOB_SUBMITTED, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR):
            self.assertTrue(masks[0] & code)

    def test_作り直したスケジューラにも付く(self):
        """⚠️ 起動時の1か所だけで付けると、止まったスケジューラを入れ替えたあとの
        個体では記録が消える。build_scheduler が付けていること。"""
        import app
        built = app.build_scheduler()
        names = [getattr(cb, '__qualname__', '') for cb, _ in built._listeners]
        self.assertIn('attach.<locals>.listener', names)

    def test_測れない環境では記録用スレッドを立てない(self):
        with mock.patch.object(mw, 'rss_mb', return_value=None):
            self.assertFalse(mw.ensure_sampler())


if __name__ == '__main__':
    unittest.main()
