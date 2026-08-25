"""財務データの手入力は管理者だけ（2026-08-25 に塞いだ）。

発見:
  /search が is_admin=True を固定で渡しており、ログインしていれば
  誰にでも数値の編集欄と「変更を保存」ボタンが出ていた。
  さらに保存先の /api/watchlist/update には認証が一切無く、
  **未ログインでも POST するだけで**任意の銘柄の自己資本比率・PER・PBR・
  配当利回り・時価総額・財務履歴を上書きできた。
  書き換えると match_rate も再計算されるので、スコアごと汚染される。

⚠️ 画面を隠すだけでは塞がらない。編集欄を消してもAPIは生きている。
   守るのはサーバー側。
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


class UpdateEndpointGuardTest(unittest.TestCase):

    def setUp(self):
        source = read('app.py')
        marker = "@app.route('/api/watchlist/update', methods=['POST'])"
        self.assertIn(marker, source)
        # 次のルート定義までを関数の本体とみなす
        body = source.split(marker, 1)[1]
        self.body = body.split('@app.route', 1)[0]

    def test_管理者以外は403(self):
        self.assertIn("session.get('user_role') != 'admin'", self.body)
        self.assertIn('403', self.body)

    def test_ガードが書き込みより前にある(self):
        """try の中に入れると、例外時に素通りする書き方になりやすい。"""
        self.assertLess(self.body.index("403"), self.body.index('update_screened_data'))

    def test_未ログインも弾く(self):
        """user_role だけ見るとセッションが空のときに落ちる／通る。"""
        self.assertIn("session.get('user_id')", self.body)


class SearchPageAdminFlagTest(unittest.TestCase):
    """2026-08-25: /search は /compare へのリダイレクトになり、編集UIは
    /admin/stock-data（管理者専用）へ移した。"""

    def test_旧ページは管理者UIを持たない(self):
        source = read('models', 'root.py')
        block = source.split("@app.route('/search')", 1)[1].split('@app.route', 1)[0]
        code = block.split('"""')[-1]
        self.assertNotIn('is_admin', code)
        self.assertIn("redirect('/compare'", code)

    def test_編集画面は管理者だけが開ける(self):
        source = read('models', 'root.py')
        block = source.split("@app.route('/admin/stock-data')", 1)[1].split('@app.route', 1)[0]
        self.assertIn('_require_admin()', block)


if __name__ == '__main__':
    unittest.main()
