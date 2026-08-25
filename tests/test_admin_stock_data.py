"""財務データの手入力（管理者専用ページ）。2026-08-25 に /search から移した。

移した理由:
  /search は is_admin=True を固定で渡しており、ログインしていれば誰にでも
  編集欄と「変更を保存」ボタンが出ていた。管理の機能は管理のメニューに置く。
  公開・会員向けの画面に管理UIを混ぜない。

⚠️ 一番の主眼は「保存でデータが消えないこと」。
   編集画面の表に出ているのは financial_history の7キーだけで、丸ごと
   置き換えると bps が毎回消えていた。エラーは出ず「保存しました」と
   表示されるので気づけない。
"""

import os
import re
import sys
import json
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class PagePlacementTest(unittest.TestCase):

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config['TESTING'] = True

    def _client(self, role):
        client = self.app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 'test-user'
            sess['user_role'] = role
        return client

    def test_管理者は開ける(self):
        res = self._client('admin').get('/admin/stock-data')
        self.assertEqual(res.status_code, 200)

    def test_一般ユーザーは開けない(self):
        res = self._client('user').get('/admin/stock-data')
        self.assertNotEqual(res.status_code, 200)

    def test_未ログインは開けない(self):
        res = self.app_module.app.test_client().get('/admin/stock-data')
        self.assertNotEqual(res.status_code, 200)

    def test_管理メニューから行ける(self):
        layout = read('templates', 'layout.html')
        self.assertEqual(layout.count('href="/admin/stock-data"'), 2,
                         'ヘッダーのドロップダウンとスライドメニューの両方に置く')


class SaveMergesHistoryTest(unittest.TestCase):
    """履歴はキー単位でマージする。丸ごと置き換えない。"""

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 'admin-test'
            sess['user_role'] = 'admin'

        self.existing = {
            'company_code': '367A',
            'financial_history': json.dumps({
                'revenue': [{'date': '2025-08-31', 'value': 28000000000.0}],
                'bps': [{'date': '2025-08-31', 'value': 2323.0}],
            }),
            'cf_history': json.dumps({
                'cash': [{'date': '2025-08-31', 'value': 3740000000.0}],
                'interest_bearing_debt': [{'date': '2025-08-31', 'value': 18072000000.0}],
            }),
        }
        self.written = {}

    def _post(self, edited):
        def fake_get(code):
            return dict(self.existing)

        def fake_update(code, data):
            self.written = data
            return data

        with patch.object(self.app_module, 'get_screened_data', side_effect=fake_get), \
             patch('supabase_client.get_screened_data', side_effect=fake_get), \
             patch.object(self.app_module, 'update_screened_data', side_effect=fake_update), \
             patch.object(self.app_module, 'calculate_match_rate', return_value=90):
            return self.client.post('/api/watchlist/update', json={
                'company_code': '367A', 'edited_data': edited})

    def test_表に無いキーが消えない(self):
        """financial_history の bps は編集画面の表に無い。"""
        res = self._post({'financial_history': {
            'dps': [{'date': '2024-08-31', 'value': 80.0}]}})
        self.assertEqual(res.status_code, 200)

        saved = json.loads(self.written['financial_history'])
        self.assertIn('bps', saved, '表に無いキー(bps)が保存で消えている')
        self.assertIn('revenue', saved, '触っていないキーが消えている')
        self.assertEqual(saved['dps'][0]['value'], 80.0)

    def test_CF履歴も同じ(self):
        """cf_history には有利子負債・利益剰余金が入っている。"""
        res = self._post({'cf_history': {
            'cash': [{'date': '2025-08-31', 'value': 4000000000.0}]}})
        self.assertEqual(res.status_code, 200)

        saved = json.loads(self.written['cf_history'])
        self.assertIn('interest_bearing_debt', saved)
        self.assertEqual(saved['cash'][0]['value'], 4000000000.0)

    def test_管理者以外は保存できない(self):
        client = self.app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 'u1'
            sess['user_role'] = 'user'
        res = client.post('/api/watchlist/update', json={
            'company_code': '367A', 'edited_data': {'pbr': 1.0}})
        self.assertEqual(res.status_code, 403)


class TableFitsTest(unittest.TestCase):
    """2つの表が、ページの幅に収まること。

    最初 max-width を 1000px にしていたため、7列の表がカードと外周の余白を
    足すと必ずはみ出し、横スクロールバーが出ていた。この画面は「表を横に
    見る」のが仕事なので、幅は表に合わせる。
    """

    def setUp(self):
        self.html = read('templates', 'admin_stock_data.html')

    def _num(self, pattern, flags=0):
        found = re.findall(pattern, self.html, flags)
        self.assertTrue(found, '見つからない: ' + pattern)
        return int(found[0])

    def _count_cols(self, const_name):
        block = self.html.split('const ' + const_name + ' = [', 1)[1].split('];', 1)[0]
        return len(re.findall(r"\{ *key: '", block))

    def test_列の数を固定する(self):
        """列が増えたら必要な幅も変わる。数を固定して気づけるようにする。"""
        self.assertEqual(self._count_cols('YEAR_COLS'), 7)
        self.assertEqual(self._count_cols('CF_COLS'), 11)

    def test_どちらの表も横スクロールせずに収まる(self):
        wrap = self._num(r'\.asd-wrap \{ max-width: (\d+)px')
        wrap_pad = self._num(r'\.asd-wrap \{ padding-left: (\d+)px')
        cell_pad = self._num(r'\.asd-table th, \.asd-table td \{[^}]*?padding: \d+px (\d+)px', re.S)
        first_w = self._num(r'\.asd-table th:first-child[^}]*?width: (\d+)px', re.S)
        card_pad = self._num(r'\.asd-card \{[^}]*?padding: \d+px (\d+)px', re.S)
        narrow = self._num(r'\.asd-table\.is-wide td input \{ width: (\d+)px')
        normal = self._num(r'\.asd-table td input \{[^}]*?width: (\d+)px', re.S)

        outer = card_pad * 2 + wrap_pad * 2
        for name, cols, input_w in (('財務データ', self._count_cols('YEAR_COLS'), normal),
                                    ('CF・財務指標', self._count_cols('CF_COLS'), narrow)):
            needed = first_w + cols * (input_w + cell_pad * 2) + outer
            self.assertLessEqual(
                needed, wrap,
                '%s の表に %dpx 要るのにページは %dpx しかない' % (name, needed, wrap))


class CfHistoryKeyTest(unittest.TestCase):
    """CF表のキーは cf_history の実際のキー名を使う。

    銘柄ページの data-type は current_liabilities_list / equity_ratio_list と
    いう別名だが、保存先のキーは _list の付かない方。以前の編集画面は
    data-type をそのままキーにして書き込んでいたため、流動負債と
    自己資本比率を直しても**画面に反映されなかった**。
    """

    def setUp(self):
        self.html = read('templates', 'admin_stock_data.html')
        self.block = self.html.split('const CF_COLS = [', 1)[1].split('];', 1)[0]

    def test_別名を使っていない(self):
        for alias in ('current_liabilities_list', 'equity_ratio_list'):
            self.assertNotIn(alias, self.block, alias + ' は cf_history のキーではない')

    def test_実際のキーを使っている(self):
        for key in ('operating_cf', 'investing_cf', 'financing_cf', 'cash',
                    'current_liabilities', 'equity_ratio', 'roe', 'roa',
                    'current_assets', 'interest_bearing_debt', 'retained_earnings'):
            self.assertIn("key: '%s'" % key, self.block, key)

    def test_保存先を取り違えない(self):
        """表が2つあるので、どちらの履歴かを持たせて送る。
        混ぜると片方が相手のキーを上書きする。"""
        self.assertIn('data-source="${source}"', self.html)
        self.assertIn('const source = input.dataset.source;', self.html)
        self.assertIn('payload[source] = built;', self.html)


class EditorSendsOnlyChangesTest(unittest.TestCase):
    """触っていない欄は送らない。

    全部送ると、表示のときの丸め（億円で小数第1位）がそのまま保存され、
    直していない数字が少しずつ削れていく。
    """

    def setUp(self):
        self.html = read('templates', 'admin_stock_data.html')

    def test_変わっていない欄は送らない(self):
        self.assertIn("if (String(input.value) === String(input.dataset.original)) return;",
                      self.html)

    def test_触った欄に印を付ける(self):
        self.assertIn("classList.toggle('is-changed'", self.html)

    def test_並びが銘柄ページと同じ(self):
        """行が決算期、列が項目。銘柄ページの「財務データ（直近5年）」と
        向きが違うと、見比べながら直すときに目が滑る。"""
        detail = read('templates', 'stock_detail.html')
        self.assertIn('<th style="text-align: left; width: 100px;">決算期</th>', detail)

        self.assertIn("'<th>決算期</th>'", self.html)
        self.assertIn('const YEAR_COLS', self.html)
        # 列の見出しは銘柄ページと同じ文字にそろえる
        for label in ('売上高(億円)', '営業利益(億円)', '経常利益(億円)',
                      '純利益(億円)', '1株益(円)', '1株配(円)', '配当性向(%)'):
            self.assertIn(label, self.html, label)
            self.assertIn(label, detail, label + ' が銘柄ページ側に無い')

    def test_同じキーの他の期を消さない(self):
        """サーバーはキー単位で差し替えるので、触った期だけ送ると
        同じキーの他の期が消える。表が2つあるので履歴ごとに組み立てる。"""
        self.assertIn('const stored = asObject(loadedRow[source]);', self.html)
        self.assertIn('merged.push(d)', self.html)


if __name__ == '__main__':
    unittest.main()
