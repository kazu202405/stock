# -*- coding: utf-8 -*-
"""セッションの署名鍵を既定値のまま動かさない（2026-08-26）。

何が問題だったか:
  config.py が鍵を**2か所**で設定していた。

      app.secret_key           = os.getenv('APP_SECRET_KEY', 'your_secret_key')
      app.config['SECRET_KEY'] = os.getenv('SECRET_KEY',     'secret-key')

  Flask では app.secret_key は app.config['SECRET_KEY'] の別名なので、
  **後に実行されるほうが勝つ**。つまり APP_SECRET_KEY を設定しても使われず、
  コードを読んでもどちらが効くのか分からなかった。実際に取り違えた。

  さらに両方とも既定値を持っていた。未設定のまま起動すると鍵が
  'secret-key' という**公開されている文字列**になり、
  **誰でも管理者のセッションを偽造できる**。
  診断中に実際にこの鍵でセッションを作り、管理者として全APIを通せた。

  ⚠️ 例外は出ず、ログイン画面も一覧も普通に動く。
     「壊れる」のではなく「静かに全開になる」ので画面に手がかりが出ない。
     ＝気づける唯一の場所が起動時。だから必ず落とす。
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# .env を読ませずに config を読み込む。既定値のまま起動しないことを確かめるため。
_CODE = ("import dotenv;dotenv.load_dotenv=lambda *a, **k: None;"
         "import os;os.environ['ENABLE_SCHEDULER']='false';"
         "import config;print(config.SECRET_KEY)")


def boot(**env_over):
    env = dict(os.environ)
    env['ENABLE_SCHEDULER'] = 'false'
    # ⚠️ Windows の既定（cp932）だと、例外の日本語が化けて照合できない。
    #    テストが「落ちなかった」ように見えるので、必ず utf-8 に固定する。
    env['PYTHONIOENCODING'] = 'utf-8'
    env.update({k: v for k, v in env_over.items()})
    return subprocess.run([sys.executable, '-c', _CODE], cwd=ROOT,
                          capture_output=True, text=True, env=env,
                          encoding='utf-8', errors='replace')


def read_config():
    with open(os.path.join(ROOT, 'config.py'), encoding='utf-8') as f:
        return f.read()


class SingleSourceTest(unittest.TestCase):

    def test_鍵の設定は1か所だけ(self):
        """2か所あると後勝ちになり、どちらが効くのか読めなくなる。"""
        src = read_config()
        self.assertEqual(src.count("app.secret_key = "), 0)
        self.assertEqual(src.count("app.config['SECRET_KEY'] = "), 1)

    def test_既定値を持たない(self):
        src = read_config()
        self.assertNotIn("os.getenv('SECRET_KEY', 'secret-key')", src)
        self.assertNotIn("os.getenv('APP_SECRET_KEY', 'your_secret_key')", src)

    def test_app_secret_keyとconfigが一致する(self):
        import config
        self.assertEqual(config.app.secret_key, config.SECRET_KEY)


class RefuseUnsafeTest(unittest.TestCase):
    """危ない鍵では起動させない。"""

    def test_未設定なら落ちる(self):
        r = boot(SECRET_KEY='', APP_SECRET_KEY='')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('SECRET_KEY', r.stderr)

    def test_既定値なら落ちる(self):
        for bad in ('secret-key', 'your_secret_key', 'change-me', 'dev'):
            r = boot(SECRET_KEY=bad, APP_SECRET_KEY='')
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn('既定値', r.stderr, bad)

    def test_大文字小文字を問わず弾く(self):
        r = boot(SECRET_KEY='SECRET-KEY', APP_SECRET_KEY='')
        self.assertNotEqual(r.returncode, 0)

    def test_空白だけでも落ちる(self):
        r = boot(SECRET_KEY='   ', APP_SECRET_KEY='')
        self.assertNotEqual(r.returncode, 0)


class AcceptValidTest(unittest.TestCase):

    def test_まともな鍵なら起動する(self):
        r = boot(SECRET_KEY='k9d2mQ7xLp4vRt8wZ3nB', APP_SECRET_KEY='')
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn('k9d2mQ7xLp4vRt8wZ3nB', r.stdout)

    def test_APP_SECRET_KEYでも受ける(self):
        """既存の環境変数名を壊さない。SECRET_KEY が正だが、
        こちらしか無い環境でも動くようにしてある。"""
        r = boot(SECRET_KEY='', APP_SECRET_KEY='k9d2mQ7xLp4vRt8wZ3nB')
        self.assertEqual(r.returncode, 0, r.stderr[-400:])

    def test_SECRET_KEYが優先される(self):
        r = boot(SECRET_KEY='aaaaaaaaaaaaaaaa', APP_SECRET_KEY='bbbbbbbbbbbbbbbb')
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn('aaaaaaaaaaaaaaaa', r.stdout)


if __name__ == '__main__':
    unittest.main()
