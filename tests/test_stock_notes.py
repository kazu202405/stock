# -*- coding: utf-8 -*-
"""銘柄メモ（2026-09-06）。

運営が「この会社は勉強になる」と思った銘柄に一言を残し、一覧に印を出す。

⚠️ **書けるのは管理者だけ。** 画面のボタンを隠すだけでは、APIを直接叩ける。
⚠️ **migration が未適用でも読みは落ちない。** 適用は運用側が手で行うので、
   コードが先行する期間がある（CLAUDE.md）。ただし書き込みは黙って成功しない。
⚠️ **一覧の印はサーバーが付ける。** 画面から後で取りに行くと描画に間に合わず、
   印が付かない行が残る。
"""

import os
import re
import unittest
from unittest.mock import patch

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


class NormalizeTest(unittest.TestCase):
    def test_code_forms_are_the_same_stock(self):
        import stock_notes
        for raw in ('7203', '7203.T', ' 7203 ', '7203.t'):
            self.assertEqual(stock_notes.normalize_code(raw), '7203', raw)


class ApiPermissionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module

        cls.app_module = app_module
        app_module.app.config['TESTING'] = True

    def _client(self, role=None):
        client = self.app_module.app.test_client()
        if role:
            with client.session_transaction() as sess:
                sess['user_id'] = 'test-user'
                sess['user_role'] = role
        return client

    def test_reading_is_open(self):
        """銘柄ページは公開なので、そこに出るメモも読める。"""
        self.assertEqual(self._client().get('/api/stock-notes/7203').status_code, 200)
        self.assertEqual(self._client().get('/api/stock-notes/codes').status_code, 200)

    def test_writing_needs_an_admin(self):
        """⚠️ 画面でボタンを隠すだけにしない。"""
        for client, expected in ((self._client(), 401),
                                 (self._client('user'), 403)):
            for method in ('put', 'delete'):
                with self.subTest(method=method, expected=expected):
                    call = getattr(client, method)
                    res = call('/api/stock-notes/7203', json={'body': 'x'})
                    self.assertEqual(res.status_code, expected)

    def test_an_empty_note_is_refused(self):
        """空のメモを許すと、一覧に印だけ出て開いても何も無い。"""
        with patch('stock_notes.save', side_effect=ValueError('メモが空です')):
            res = self._client('admin').put('/api/stock-notes/7203', json={'body': '   '})
        self.assertEqual(res.status_code, 400)

    def test_a_failed_save_is_reported(self):
        """⚠️ 保存できていないのに成功を返すと、書いた本人が気づけない。"""
        with patch('stock_notes.save', side_effect=RuntimeError('テーブルがありません')):
            res = self._client('admin').put('/api/stock-notes/7203', json={'body': 'あ'})
        self.assertEqual(res.status_code, 500)
        self.assertIn('error', res.get_json())


class MissingTableTest(unittest.TestCase):
    """migration 未適用でも、読みは落ちない。"""

    def test_reads_fall_back_to_empty(self):
        import stock_notes

        class Missing:
            def table(self, name):
                raise RuntimeError(
                    "relation \"public.stock_notes\" does not exist")

        with patch.object(stock_notes, '_client', return_value=Missing()):
            self.assertIsNone(stock_notes.get('7203'))
            self.assertEqual(stock_notes.marked_codes(), set())
            self.assertEqual(stock_notes.listing(), [])
            self.assertFalse(stock_notes.table_ready())


class AuthorFieldTest(unittest.TestCase):
    """書いた人の記録欄で、本文を落とさない。"""

    def test_a_malformed_user_id_does_not_lose_the_note(self):
        """⚠️ updated_by は UUID 列。UUID でない値が来ると挿入ごと失敗する。
        書いた人が分からないより、書いた文章が消えるほうが痛い。
        """
        import stock_notes

        captured = {}

        class Table:
            def upsert(self, payload, **kw):
                captured.update(payload)
                return self

            def execute(self):
                return type('R', (), {'data': [captured]})()

        class Client:
            def table(self, name):
                return Table()

        with patch.object(stock_notes, '_client', return_value=Client()):
            stock_notes.save('7203', 'メモ', user_id='local')
            self.assertNotIn('updated_by', captured, 'UUIDでない値を送っている')
            self.assertEqual(captured['body'], 'メモ')

            captured.clear()
            uuid = '01d13939-3f29-4f1e-a5d2-a7186a0a3bb0'
            stock_notes.save('7203', 'メモ', user_id=uuid)
            self.assertEqual(captured.get('updated_by'), uuid)


class ListMarkTest(unittest.TestCase):
    """一覧の印はサーバーが付ける。"""

    def test_the_flag_is_attached_on_the_server(self):
        import app as app_module

        with patch('stock_notes.marked_codes', return_value={'7203'}):
            rows = app_module._attach_notes([
                {'company_code': '7203'}, {'company_code': '6758'}])
        self.assertTrue(rows[0]['has_note'])
        self.assertFalse(rows[1]['has_note'])

    def test_every_list_marks_its_rows(self):
        """4つの一覧＋スクリーナーの全部に付けていること。"""
        source = read('app.py')
        self.assertGreaterEqual(source.count('_attach_notes('), 6,
                                '印を付けていない一覧が残っている')

    def test_the_screens_show_the_mark(self):
        self.assertIn('NoteMark.chip(', read('templates/stock.html'))
        self.assertIn('row.has_note', read('templates/screener.html'))

    def test_the_mark_is_not_coloured_like_the_score(self):
        """⚠️ 緑・赤はスコアとGC/DCで使っている。3つ目を載せない。"""
        layout = read('templates/layout.html')
        start = layout.find('.note-mark {')
        self.assertNotEqual(start, -1, '.note-mark のCSSが無い')
        block = layout[start:start + 400]
        for banned in ('#15803d', '#dc2626', '#16a34a'):
            self.assertNotIn(banned, block, banned)


class ScreenerOrderTest(unittest.TestCase):
    """メモのある銘柄を、同じスコアの中で先に出す。"""

    def setUp(self):
        import app as app_module

        self.app_module = app_module
        app_module._screen_source_cache.clear()

    def tearDown(self):
        self.app_module._screen_source_cache.clear()

    def test_it_uses_the_view_when_it_exists(self):
        class Ok:
            def table(self, name):
                return self

            def select(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def execute(self):
                return type('R', (), {'data': []})()

        name, can_sort = self.app_module.screen_source(Ok())
        self.assertEqual(name, self.app_module.SCREEN_VIEW)
        self.assertTrue(can_sort)

    def test_it_falls_back_when_the_view_is_missing(self):
        """⚠️ migration の適用は運用側が手で行う。無い間も今までどおり動く。"""
        class Missing:
            def table(self, name):
                raise RuntimeError(
                    'relation "public.screened_with_notes" does not exist')

        name, can_sort = self.app_module.screen_source(Missing())
        self.assertEqual(name, self.app_module.SCREEN_TABLE)
        self.assertFalse(can_sort, 'ビューが無いのにメモ順で並べようとしている')

    def test_a_missing_view_is_rechecked_later(self):
        """⚠️「無い」を永久に覚えない。

        migration の適用は運用側が手で行うので「デプロイ → SQL適用」の順に
        なる。永久に覚えると、SQLを流してもアプリを再起動するまで並びが
        変わらない。
        """
        import time

        calls = {'n': 0}

        class Appearing:
            """最初は無い。2回目以降は有る。"""
            def table(self, name):
                calls['n'] += 1
                if calls['n'] == 1:
                    raise RuntimeError('does not exist')
                return self

            def select(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def execute(self):
                return type('R', (), {'data': []})()

        client = Appearing()
        self.assertEqual(self.app_module.screen_source(client)[0],
                         self.app_module.SCREEN_TABLE)

        # 間隔のうちは確かめ直さない（毎回問い合わせると回数が増える）
        self.assertEqual(self.app_module.screen_source(client)[0],
                         self.app_module.SCREEN_TABLE)
        self.assertEqual(calls['n'], 1, '間隔を待たずに問い合わせている')

        # 時間が経てば確かめ直し、見つけたら切り替わる
        self.app_module._screen_source_cache['checked_at'] = (
            time.time() - self.app_module.SCREEN_VIEW_RECHECK_SECONDS - 1)
        name, can_sort = self.app_module.screen_source(client)
        self.assertEqual(name, self.app_module.SCREEN_VIEW)
        self.assertTrue(can_sort)

    def test_an_existing_view_is_not_rechecked(self):
        """有るものは消えないので、毎回確かめない。"""
        calls = {'n': 0}

        class Ok:
            def table(self, name):
                calls['n'] += 1
                return self

            def select(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def execute(self):
                return type('R', (), {'data': []})()

        client = Ok()
        for _ in range(3):
            self.assertEqual(self.app_module.screen_source(client)[0],
                             self.app_module.SCREEN_VIEW)
        self.assertEqual(calls['n'], 1, '有ると分かった後も問い合わせている')

    def test_the_order_is_decided_in_the_database(self):
        """⚠️ 取得後に並べ替えない。50件ずつ区切っているので順序が崩れる。"""
        source = read('app.py')
        self.assertIn("query.order('has_note', desc=True", source)
        # スコア→確かさ→メモ の順であること（緑の中でメモが上、が崩れない）
        i_score = source.find("query.order('score_complete'")
        i_note = source.find("query.order('has_note'")
        self.assertNotEqual(i_score, -1)
        self.assertNotEqual(i_note, -1)
        self.assertLess(i_score, i_note, 'メモが確かさより先に効いている')


class NotePlacementTest(unittest.TestCase):
    """メモの置き場所（2026-09-06 に事業概要と財務データの間へ移した）。"""

    def setUp(self):
        self.html = read('templates/stock_detail.html')

    def test_it_sits_between_the_summary_and_the_financials(self):
        """何の会社かを読んだ直後に、どこを見ると面白いかが来る。"""
        summary = self.html.find('<!-- 事業概要 -->')
        note = self.html.find('id="stockNoteBlock"')
        financials = self.html.find('<!-- 財務データテーブル -->')
        for name, pos in (('事業概要', summary), ('メモ', note),
                          ('財務データ', financials)):
            self.assertNotEqual(pos, -1, name)
        self.assertLess(summary, note, 'メモが事業概要より前にある')
        self.assertLess(note, financials, 'メモが財務データより後にある')


class WideModalTest(unittest.TestCase):
    """複数行のときだけモーダルを広げる。"""

    def setUp(self):
        self.layout = read('templates/layout.html')

    def test_the_wide_style_exists(self):
        self.assertIn('.ui-modal-card.is-wide', self.layout)

    def test_the_width_is_toggled_not_left_on(self):
        """⚠️ 同じモーダルを使い回しているので、付けっぱなしにすると
        次に開いた1行入力まで広いままになる。"""
        start = self.layout.find('function _promptField(')
        self.assertNotEqual(start, -1)
        block = self.layout[start:self.layout.find('function showPromptModal(', start)]
        self.assertIn("classList.toggle('is-wide'", block)

    def test_the_single_line_modal_is_not_widened(self):
        """40文字の1行入力が横に間延びしないこと。"""
        start = self.layout.find('.ui-modal-card {')
        block = self.layout[start:start + 220]
        self.assertIn('max-width: 420px', block)


class SharedDialogTest(unittest.TestCase):
    """入力はアプリ内モーダルで行う（全プロジェクト共通ルール）。"""

    def test_the_note_editor_does_not_use_a_browser_dialog(self):
        source = read('templates/stock_detail.html')
        block_start = source.find('function editStockNote(')
        self.assertNotEqual(block_start, -1)
        block = source[block_start:block_start + 1800]
        for banned in ('prompt(', 'alert(', 'confirm('):
            self.assertNotIn('window.' + banned, block, banned)
        self.assertIn('showPromptModal(', block)

    def test_the_shared_modal_gained_multiline_instead_of_a_new_one(self):
        """⚠️ 画面ごとに自作しない。共通部品のほうを複数行対応にする。"""
        layout = read('templates/layout.html')
        self.assertIn('promptModalTextarea', layout)
        self.assertIn('opts.multiline', layout)


class CuratedPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module

        cls.app_module = app_module
        app_module.app.config['TESTING'] = True

    def test_it_needs_a_login_but_not_a_membership(self):
        anonymous = self.app_module.app.test_client()
        self.assertEqual(anonymous.get('/curated').status_code, 302)

        client = self.app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 'test-user'
            sess['user_role'] = 'user'
        with patch.object(self.app_module, 'is_member_session', return_value=False):
            self.assertEqual(client.get('/curated').status_code, 200)

    def test_it_is_reachable_from_both_menus(self):
        """⚠️ 片方だけ足すと、スマホからは辿れない。"""
        layout = read('templates/layout.html')
        self.assertEqual(layout.count('href="/curated"'), 2)

    def test_the_size_threshold_is_not_repeated_in_the_page(self):
        body = read('templates/curated.html')
        body = re.sub(r'\{#.*?#\}', '', body, flags=re.S)
        for word in ('超小型', '中型'):
            self.assertNotIn(word, body, '規模の呼び名を自前で持っている')


if __name__ == '__main__':
    unittest.main()
