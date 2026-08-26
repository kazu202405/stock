# -*- coding: utf-8 -*-
"""「取得に失敗した」と「権限が無い」を分けて伝える（2026-08-26）。

何が起きていたか:
  非会員でダッシュボードを開くと「好調企業」「テクニカル分析」が
  回り続けたあと、こう出ていた:

      データの読み込みに失敗しました。ページを再読み込みしてお試しください。

  実際は 403（会員限定）で、**再読み込みしても直らない**。
  直し方が書いていないうえに、直らない方法を案内していた。

  ⚠️ 401（ログイン切れ）と 403（会員でない）と通信エラーは
     利用者がやることが全部違う。同じ文言にしてはいけない。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class MessageTest(unittest.TestCase):

    def setUp(self):
        self.html = read('templates', 'stock.html')

    def test_状態ごとに文言を分ける(self):
        self.assertIn('function loadErrorHtml(', self.html)
        self.assertIn('status === 401', self.html)
        self.assertIn('status === 403', self.html)

    def test_会員限定には案内先がある(self):
        """「見られません」だけでは、どうすれば見られるか分からない。"""
        block = self.html.split('function loadErrorHtml(', 1)[1][:1400]
        self.assertIn('会員向け', block)
        self.assertIn('gia2018.com/upgrade', block)

    def test_ログイン切れにはログイン先がある(self):
        block = self.html.split('function loadErrorHtml(', 1)[1][:1400]
        self.assertIn('/login', block)

    def test_全部の一覧で使う(self):
        """好調企業・お気に入り・高配当・GC・DC・テクニカルの6つ。
        1つ忘れると、そのタブだけ古い文言のまま残る。"""
        self.assertEqual(self.html.count('loadErrorHtml(response)'), 7)  # 定義1＋呼び出し6

    def test_通信エラーは従来のまま(self):
        """catch 側は本当に「再読み込み」で直ることがある。
        ここまで会員向けの文言にすると、今度は逆に嘘になる。"""
        self.assertIn('データの読み込みに失敗しました', self.html)


class ApiStatusTest(unittest.TestCase):
    """APIが実際に401/403を返し分けていること。"""

    def setUp(self):
        import app as app_module
        self.app = app_module.app
        self.app.config['TESTING'] = True

    def test_未ログインは401(self):
        c = self.app.test_client()
        for path in ('/api/watchlist', '/api/technical-stocks'):
            self.assertEqual(c.get(path).status_code, 401, path)

    def test_ログイン済みで非会員は403(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = '11111111-1111-1111-1111-111111111111'
        for path in ('/api/watchlist', '/api/technical-stocks'):
            self.assertEqual(c.get(path).status_code, 403, path)


if __name__ == '__main__':
    unittest.main()
