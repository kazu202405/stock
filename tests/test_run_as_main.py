# -*- coding: utf-8 -*-
"""`python app.py` で起動しても、トップページが開けること（2026-09-11）。

何が起きていたか:
  `python app.py` で起動すると、app.py は `__main__` として読まれる。トップページの
  処理が関数の中で `from app import membership_tier_for` すると、app.py が `app` と
  してもう一度読み込まれ、`@app.after_request` の登録で Flask に止められていた
  （AssertionError: The setup method 'after_request' can no longer be called…）。
  本番の gunicorn は最初から `app` として読むので起きず、手元の確認でだけ出る。

見張り方:
  別プロセスで app.py を `__main__` として実行し（app.run は止める）、そのまま
  トップページを開く。テストの中で import すると `app` として読まれてしまい、
  再現しないので、必ず別プロセスで走らせる。
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SCRIPT = r"""
import os, runpy, sys
import flask
flask.Flask.run = lambda *a, **k: None      # 開発サーバーは立てない
sys.path.insert(0, os.getcwd())
ns = runpy.run_path('app.py', run_name='__main__')
client = ns['app'].test_client()
res = client.get('/')
print('STATUS', res.status_code)
"""


class 手元で起動したとき(unittest.TestCase):

    def test_トップページが開ける(self):
        env = dict(os.environ, ENABLE_SCHEDULER='false', PYTHONIOENCODING='utf-8')
        result = subprocess.run([sys.executable, '-c', _SCRIPT], cwd=ROOT, env=env,
                                capture_output=True, text=True, encoding='utf-8',
                                timeout=180)
        tail = (result.stdout + result.stderr)[-1500:]
        self.assertIn('STATUS 200', result.stdout, tail)


if __name__ == '__main__':
    unittest.main()
