"""企業比較を /search から切り出した（2026-08-25）。

/search は検索窓のほかに事業概要・財務データ5年・CF・財務健全性・
主要株主/役員を持っていたが、**その5つは /stock/<code> と同じもの**だった。
「検索するためだけに、同じ内容のページをもう1つ開く」形になっていた。

移した先:
  銘柄検索        → ヘッダーの検索窓（layout.html / company-search.js）
  5つの重複       → /stock/<code> に元からある
  管理者の手入力  → /admin/stock-data

⚠️ 消す前に移し終えていること。逆順にすると、移すまでのあいだ機能が無くなる。
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


class RoutingTest(unittest.TestCase):

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 'test-user'
            sess['user_role'] = 'user'

    def test_旧URLは新URLへ転送する(self):
        """外から貼られたリンクとブックマークを404にしない。"""
        res = self.client.get('/search')
        self.assertEqual(res.status_code, 301)
        self.assertTrue(res.headers.get('Location', '').endswith('/compare'))

    def test_比較ページが開く(self):
        res = self.client.get('/compare')
        self.assertEqual(res.status_code, 200)

    def test_未ログインは開けない(self):
        res = self.app_module.app.test_client().get('/compare')
        self.assertNotEqual(res.status_code, 200)


class ContentTest(unittest.TestCase):

    def setUp(self):
        self.html = read('templates', 'compare.html')

    def test_比較に要るものが揃っている(self):
        for piece in ('compareApp()', 'radarChart', '/api/compare',
                      '/static/companies.json'):
            self.assertIn(piece, self.html, piece)

    def test_1社を見るためのものは持たない(self):
        """銘柄ページと同じ中身を二重に持たない。"""
        for piece in ('事業概要', '主要株主', '役員構成', '財務健全性'):
            self.assertNotIn('detail-header">' + piece, self.html, piece)

    def test_管理者の編集欄を持たない(self):
        """手入力は /admin/stock-data へ移した。"""
        for piece in ('editable-field', 'editable-cell', 'saveEditedData',
                      '/api/watchlist/update'):
            self.assertNotIn(piece, self.html, piece)

    def test_使っているCSS変数が全部定義されている(self):
        """切り出しのときに :root を取りこぼし、枠線も文字色も既定値に落ちて
        入力欄が読めなくなっていた。変数はページ内で完結させる。"""
        import re
        used = set(re.findall(r'var\((--[a-z-]+)\)', self.html))
        self.assertTrue(used, '変数を1つも使っていない（抽出が壊れた？）')
        for name in sorted(used):
            self.assertIn(name + ':', self.html, name + ' が未定義')

    def test_失敗をその場に出す(self):
        """モーダルは比較の邪魔になる。"""
        self.assertIn('id="compareError"', self.html)
        self.assertIn('function showErrorModal', self.html)


class NoDanglingLinksTest(unittest.TestCase):

    def test_旧テンプレートを参照していない(self):
        self.assertFalse(
            os.path.exists(os.path.join(ROOT, 'templates', 'search.html')),
            'search.html が残っている')

    def test_画面のリンクが新URLを指す(self):
        import glob
        for path in glob.glob(os.path.join(ROOT, 'templates', '*.html')):
            html = read('templates', os.path.basename(path))
            self.assertNotIn('href="/search"', html, os.path.basename(path))

    def test_現在地の判定も新URL(self):
        layout = read('templates', 'layout.html')
        self.assertIn("'/compare'", layout)
        self.assertNotIn("'/mypage', '/search'", layout)


if __name__ == '__main__':
    unittest.main()
