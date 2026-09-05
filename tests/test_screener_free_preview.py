"""非会員に見せるスクリーナーの上位3件（2026-09-06 追加）。

ヘッダーの「スクリーニング」は全員に出しているのに、押すと
「この機能は会員限定です」で終わっていた。上位3件だけ実物を見せる。

⚠️ **件数はサーバーで切る。** テンプレートで隠しても、APIを直に叩けば
   中身が読める。ここで見張るのは「レスポンスに何行載っているか」であって
   画面の見た目ではない。

⚠️ **パラメータで広げられないこと。** 並べ替えや絞り込みを受け付けると、
   条件を変えながら叩くだけで全件を集められる（3件 × 条件の数だけ漏れる）。
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

FREE_ROWS = 3


class ScreenerFreePreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module

        cls.app_module = app_module
        app_module.app.config['TESTING'] = True

    def _client_as(self, member):
        """ログイン済みの利用者。member=False なら無料会員。"""
        client = self.app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 'test-user'
            sess['user_name'] = 'テスト'
            sess['user_role'] = 'user'
        patcher = patch.object(self.app_module, 'is_member_session', return_value=member)
        patcher.start()
        self.addCleanup(patcher.stop)
        return client

    def test_the_constant_is_three(self):
        self.assertEqual(self.app_module.FREE_SCREEN_ROWS, FREE_ROWS)

    def test_free_user_gets_only_the_preview_rows(self):
        response = self._client_as(member=False).get('/api/stocks/screen')

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body.get('preview'), '見本であることを画面に伝えていない')
        self.assertLessEqual(len(body.get('rows') or []), FREE_ROWS)

    def test_query_parameters_cannot_widen_the_preview(self):
        """並べ替え・ページング・件数指定で中身を集められないこと。"""
        client = self._client_as(member=False)
        attempts = [
            '?per_page=200',
            '?page=2',
            '?sort=roe&order=asc',
            '?per_page=200&sort=per&order=asc&page=3',
            '?score_complete=1&per_page=100',
            '?industry=' + '情報・通信業' + '&per_page=100',
        ]
        for query in attempts:
            with self.subTest(query=query):
                body = client.get('/api/stocks/screen' + query).get_json()
                rows = body.get('rows') or []
                self.assertLessEqual(len(rows), FREE_ROWS,
                                     f'{query} で {len(rows)} 行返っている')
                # 常に同じ並び。指定した sort が効いていたら漏れが増やせる
                self.assertEqual(body.get('sort'), 'match_rate')
                self.assertEqual(body.get('page'), 1)

    def test_the_page_is_open_to_free_users(self):
        """会員限定のままだと、ヘッダーのメニューが行き止まりに戻る。"""
        response = self._client_as(member=False).get('/screener')
        self.assertEqual(response.status_code, 200)

    def test_logged_out_still_has_to_sign_in(self):
        anonymous = self.app_module.app.test_client()
        self.assertEqual(anonymous.get('/api/stocks/screen').status_code, 401)


if __name__ == '__main__':
    unittest.main()
