# -*- coding: utf-8 -*-
"""全ての定期実行が実行記録を残す（2026-09-04）。

## なぜ要るか

gc_dc_morning（9:15）が走ったのかどうかを**確かめる手段が無かった**。
18本のうち9本が job_runs に何も残しておらず、さらに signal_stocks には
取得時刻の列も無い。∴「動いていないのか、記録が無いだけなのか」が
区別できなかった。

⚠️ **記録が無いことは「異常なし」と区別がつかない。** このリポジトリで
   繰り返し踏んでいる形（株価バッチが2回とも記録を残さず気づけなかった、
   日足ジョブがどの経路を通っても記録を残すよう直した、など）。

⚠️ **個々の関数に書き足さない。** 書き忘れが生まれる（実際9本が漏れていた）。
   登録の1か所で包む。
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


SRC = read('app.py')


def registrations():
    """[(job_id, 中身)] を返す。add_job の呼び出しを拾う。"""
    out = []
    for m in re.finditer(r"scheduler\.add_job\((.*?)id='([^']+)'", SRC, re.S):
        out.append((m.group(2), m.group(1)))
    return out


def body_of(name):
    i = SRC.index('def %s(' % name)
    body = SRC[i:]
    cut = re.search(r'\n(?=(def |@app\.route|class ))', body[1:])
    return body[:cut.start() + 1] if cut else body


class 全ジョブが記録を残す(unittest.TestCase):

    def test_登録が拾えている(self):
        """走査できていないと、何も見ずに合格してしまう。"""
        self.assertGreaterEqual(len(registrations()), 15)

    def test_記録の無いジョブが無い(self):
        """包まれているか、関数の中で自分で記録しているかのどちらか。"""
        missing = []
        for job_id, call in registrations():
            if 'recorded(' in call:
                continue                      # 登録で包んでいる
            m = re.search(r'scheduler\.add_job\(\s*(\w+)', 'scheduler.add_job(' + call)
            if not m:
                missing.append(job_id)
                continue
            if 'record_job_run' not in body_of(m.group(1)):
                missing.append(job_id)
        self.assertEqual([], missing,
                         '実行記録を残さないジョブ: %s' % missing)

    def test_二重に記録しない(self):
        """⚠️ 自分で record_job_run を呼ぶ関数を包むと、1回の実行で2行残る。"""
        for job_id, call in registrations():
            if 'recorded(' not in call:
                continue
            m = re.search(r"recorded\('[^']+',\s*(\w+)\)", call)
            self.assertIsNotNone(m, job_id)
            self.assertNotIn('record_job_run', body_of(m.group(1)),
                             '%s は自分でも記録している（二重になる）' % job_id)


class 包み方(unittest.TestCase):

    def setUp(self):
        self.block = body_of('recorded')

    def test_失敗も記録する(self):
        """成功しか残さないと、落ちたことが記録から消える。"""
        self.assertIn('ok=False', self.block)
        self.assertIn('ok=True', self.block)

    def test_例外は投げ直す(self):
        """握ると APScheduler のログにも残らない。"""
        self.assertIn('raise', self.block)

    def test_元の関数名を保つ(self):
        """APScheduler がジョブ名に使う。"""
        self.assertIn('functools.wraps', self.block)


class 実際に包まれている(unittest.TestCase):
    """⚠️ コードを読むだけで満足しない。実際に登録して数える。"""

    def test_17本すべて登録されている(self):
        os.environ['ENABLE_SCHEDULER'] = 'true'
        import app
        self.assertGreaterEqual(len(app.scheduler.get_jobs()), 15)

    def test_包んだジョブが動く(self):
        """包んだ関数を呼んで、記録が試みられること。"""
        import app
        calls = []
        original = app.record_job_run
        app.record_job_run = lambda job_id, ok, detail='': calls.append((job_id, ok))
        try:
            app.recorded('test_job', lambda: None)()
            self.assertEqual([('test_job', True)], calls)
            calls.clear()

            def boom():
                raise RuntimeError('x')
            with self.assertRaises(RuntimeError):
                app.recorded('test_job', boom)()
            self.assertEqual([('test_job', False)], calls)
        finally:
            app.record_job_run = original


if __name__ == '__main__':
    unittest.main()
