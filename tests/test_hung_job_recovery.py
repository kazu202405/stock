# -*- coding: utf-8 -*-
"""途中で死んだ実行から自力で立ち直る（2026-09-04）。

## 何が起きたか

日足ジョブ（3:30）が開始の印だけ残して終わらなかった。`running` は false、
日足も1件も保存されていない＝**プロセスごと消えた**（Renderの再起動やOOM）。
`finally` があっても、プロセスが落ちれば終わりの印は書けない。

⚠️ **検知はできていたのに、自力で戻る道が無かった。** パネルは hung と出し
   `/health/db` も503を返していたが、**誰かが手で終端しない限りその状態が続く**。
   翌日の3:30が来ても開始の印が古いままなので、赤いままになる。

同じ形＝[[feedback_circuit_breaker_no_cooldown_silent_skip]]（自動復帰が無いと
一括処理を丸ごと空振りさせる）。**検知を足したら、戻る道も足す。**
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


def body_of(src, header):
    body = src.split(header, 1)[1]
    cut = re.search(r'\n(?=(def |@app\.route|class ))', body)
    return body[:cut.start()] if cut else body


def code_of(src, header):
    """docstring とコメントを落としてコードだけ返す。

    ⚠️ 禁止や注意を確かめるテストは、自分で書いた注意書きを拾って落ちる。
    """
    body = body_of(src, header)
    q = chr(34) * 3
    body = ''.join(body.split(q)[::2])
    nl = chr(10)
    return nl.join(l for l in body.split(nl) if not l.strip().startswith('#'))


class 死んだ実行を終端する(unittest.TestCase):

    def setUp(self):
        self.src = read('app.py')

    def test_終端する関数がある(self):
        self.assertIn('def close_hung_run(', self.src)

    def test_hungだけを対象にする(self):
        """⚠️ 実行中(running)を終端すると、本物の実行が終わったときに
        記録が二重になる。"""
        block = code_of(self.src, 'def close_hung_run(')
        self.assertIn("state != 'hung'", block)

    def test_失敗として残す(self):
        """成功で終端すると、落ちたことが記録から消える。"""
        block = code_of(self.src, 'def close_hung_run(')
        self.assertIn('ok=False', block)

    def test_次の実行が呼ぶ(self):
        """⚠️ 誰かが手で終端しないと戻らない、という状態を作らない。"""
        block = code_of(self.src, 'def claim_job(')
        self.assertIn('close_hung_run(job_id)', block)

    def test_開始の印より先に呼ぶ(self):
        """自分の開始を書いた後だと、それ自身を hung と誤認しうる。"""
        block = code_of(self.src, 'def claim_job(')
        self.assertLess(block.index('close_hung_run(job_id)'),
                        block.index("record_job_run(job_id + ':start'"))

    def test_失敗しても本体を止めない(self):
        """記録の掃除は本筋ではない。ここで例外を出して処理を止めない。"""
        block = code_of(self.src, 'def close_hung_run(')
        self.assertIn('except Exception', block)
        self.assertIn('return False', block)


class 検知の側は変えない(unittest.TestCase):
    """自動で終端するようにしても、失敗が見えなくなってはいけない。"""

    def test_失敗はパネルに出る(self):
        import data_freshness as df
        self.assertEqual('bad', df._status(9, 3, 5))

    def test_hungの判定が残っている(self):
        src = read('data_freshness.py')
        self.assertIn("return 'hung'", src)
        self.assertIn('JOB_HUNG_MINUTES', src)


if __name__ == '__main__':
    unittest.main()
