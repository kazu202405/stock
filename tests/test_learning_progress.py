"""学習の進捗記録のリグレッション。

設計の要点:
  - 解説文は learning.html が持ち、DBには持たない（ユーザーごとに変わらないため）
  - サーバーが持つのは項目IDだけ。進捗APIはそのIDで検証する
  - migrationは運用側が手で適用するため、テーブルが無くても学習ノートは開けること
"""

import os
import re
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from learning_terms import LEARNING_TERMS, TERM_IDS, is_valid_term, total_terms
from supabase_client import LearningProgressUnavailable


class TermCatalogTest(unittest.TestCase):
    """learning.html と learning_terms.py がズレたら気づけるようにする"""

    def setUp(self):
        self.page = Path('templates/learning.html').read_text(encoding='utf-8')

    def _ids_in_page(self, kind):
        pattern = (r"id: '([a-z_]+)', category: '"
                   if kind == 'term' else r"id: '([a-z_]+)', name: '")
        return [m for m in re.findall(pattern, self.page)]

    def test_term_ids_match_the_page(self):
        self.assertEqual(sorted(self._ids_in_page('term')), sorted(TERM_IDS))

    def test_category_of_each_term_matches_the_page(self):
        pairs = re.findall(r"id: '([a-z_]+)', category: '([a-z_]+)'", self.page)
        self.assertEqual(sorted(pairs), sorted(LEARNING_TERMS))

    def test_no_duplicate_ids(self):
        ids = [t for t, _ in LEARNING_TERMS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_total_matches_catalog(self):
        self.assertEqual(total_terms(), len(LEARNING_TERMS))

    def test_validation_rejects_unknown_ids(self):
        self.assertTrue(is_valid_term('per'))
        for bad in ('', 'not_a_term', 'PER', 'per; drop table', None):
            self.assertFalse(is_valid_term(bad), bad)


class LearningProgressApiTest(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def _login(self):
        with self.client.session_transaction() as s:
            s['user_id'] = 'user-1'

    def test_requires_login(self):
        self.assertEqual(self.client.get('/api/learning/progress').status_code, 401)
        self.assertEqual(
            self.client.put('/api/learning/progress/per').status_code, 401)

    def test_rejects_unknown_term(self):
        """任意の文字列で理解済み件数を水増しできないこと"""
        self._login()
        res = self.client.put('/api/learning/progress/fake_term')
        self.assertEqual(res.status_code, 404)

    def test_returns_understood_terms_and_total(self):
        self._login()
        rows = [{'term_id': 'per', 'understood_at': '2026-08-08T00:00:00+00:00'},
                {'term_id': 'pbr', 'understood_at': '2026-08-08T00:00:00+00:00'}]
        with unittest.mock.patch.object(
                self.app_module, 'get_learning_progress', return_value=rows):
            body = self.client.get('/api/learning/progress').get_json()

        self.assertTrue(body['available'])
        self.assertEqual(sorted(body['understood']), ['pbr', 'per'])
        self.assertEqual(body['total_terms'], total_terms())

    def test_drops_records_for_removed_terms(self):
        """項目を廃止したあと、消えたIDが件数に残らないこと"""
        self._login()
        rows = [{'term_id': 'per', 'understood_at': '2026-08-08T00:00:00+00:00'},
                {'term_id': 'retired_term', 'understood_at': '2026-01-01T00:00:00+00:00'}]
        with unittest.mock.patch.object(
                self.app_module, 'get_learning_progress', return_value=rows):
            body = self.client.get('/api/learning/progress').get_json()

        self.assertEqual(body['understood'], ['per'])

    def test_missing_table_does_not_break_the_page(self):
        """migration未適用でも200で返し、記録機能だけ無効にする"""
        self._login()
        with unittest.mock.patch.object(
                self.app_module, 'get_learning_progress',
                side_effect=LearningProgressUnavailable()):
            res = self.client.get('/api/learning/progress')

        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertFalse(body['available'])
        self.assertEqual(body['understood'], [])
        self.assertIn('migration_learning_progress.sql', body['reason'])

    def test_marking_when_table_missing_tells_the_operator(self):
        self._login()
        with unittest.mock.patch.object(
                self.app_module, 'mark_learning_understood',
                side_effect=LearningProgressUnavailable()):
            res = self.client.put('/api/learning/progress/per')

        self.assertEqual(res.status_code, 503)
        self.assertTrue(res.get_json()['migration_required'])

    def test_mark_and_unmark(self):
        self._login()
        with unittest.mock.patch.object(
                self.app_module, 'mark_learning_understood',
                return_value={'understood_at': '2026-08-08T00:00:00+00:00'}) as mark:
            body = self.client.put('/api/learning/progress/roa').get_json()
        self.assertTrue(body['understood'])
        mark.assert_called_once_with('user-1', 'roa')

        with unittest.mock.patch.object(
                self.app_module, 'unmark_learning_understood') as unmark:
            body = self.client.delete('/api/learning/progress/roa').get_json()
        self.assertFalse(body['understood'])
        unmark.assert_called_once_with('user-1', 'roa')


class LearningPageTest(unittest.TestCase):
    def setUp(self):
        self.page = Path('templates/learning.html').read_text(encoding='utf-8')
        self.mypage = Path('templates/mypage.html').read_text(encoding='utf-8')

    def test_page_has_check_and_summary(self):
        for token in ('toggleUnderstood', 'loadProgress', 'lp-summary', 'lp-check-btn'):
            self.assertIn(token, self.page)

    def test_progress_ui_hidden_until_migration_applied(self):
        """テーブルが無い間は記録UIを出さない（押せないボタンを見せない）"""
        self.assertIn('x-show="progressAvailable"', self.page)

    def test_mypage_shows_the_count(self):
        self.assertIn('loadLearningProgress', self.mypage)
        self.assertIn('learningUnderstood', self.mypage)

    def test_no_urgency_wording(self):
        """煽り表現を入れない（アプリの禁止パターン）。

        テンプレート内のコメント（{# #} と //）は説明文なので対象外にする。
        """
        markup = self.page.split('function learningApp')[0]
        markup = re.sub(r'\{#.*?#\}', '', markup, flags=re.S)
        markup = re.sub(r'^\s*//.*$', '', markup, flags=re.M)
        for banned in ('今すぐ', '急いで', 'あと少しで'):
            self.assertNotIn(banned, markup)


if __name__ == '__main__':
    unittest.main()
