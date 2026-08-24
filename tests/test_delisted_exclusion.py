"""上場廃止を一覧から外す（読み取り時フィルタ）。

行は消さない。消すと元に戻せず、過去に見た人のURLも404になる。
印を付けて読み取り時に外せば、判定を間違えても印を消すだけで戻る
（ETFの EXCLUDED_CODES と同じ考え方）。

⚠️ migration は運用側が手で当てるため、**列が無い期間がある**。
その間に条件を足すとクエリが400で落ち、一覧が丸ごと表示されなくなる。
「列が無ければ何もしない」をここで固定する。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import security_filter
import supabase_client as sc


class TestExcludeDelisted(unittest.TestCase):
    def setUp(self):
        sc._column_cache.clear()

    def tearDown(self):
        sc._column_cache.clear()

    def test_adds_the_condition_when_the_column_exists(self):
        query = MagicMock()
        with patch('supabase_client.has_column', return_value=True):
            out = security_filter.exclude_delisted(query)
        query.is_.assert_called_once_with('delisted_at', 'null')
        self.assertIs(out, query.is_.return_value)

    def test_does_nothing_when_the_column_is_missing(self):
        """migration 未適用の間に条件を足すと一覧が丸ごと落ちる。"""
        query = MagicMock()
        with patch('supabase_client.has_column', return_value=False):
            out = security_filter.exclude_delisted(query)
        query.is_.assert_not_called()
        self.assertIs(out, query)


class TestHasColumn(unittest.TestCase):
    def setUp(self):
        sc._column_cache.clear()

    def tearDown(self):
        sc._column_cache.clear()

    def test_true_when_the_query_succeeds(self):
        with patch('supabase_client.get_supabase_client'):
            self.assertTrue(sc.has_column('screened_latest', 'anything'))

    def test_false_when_the_query_fails(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError('column does not exist')
        with patch('supabase_client.get_supabase_client', return_value=client):
            self.assertFalse(sc.has_column('screened_latest', 'nope'))

    def test_the_answer_is_remembered(self):
        """一覧を出すたびに聞きに行くと、1リクエストが2往復になる。"""
        client = MagicMock()
        with patch('supabase_client.get_supabase_client', return_value=client):
            sc.has_column('screened_latest', 'x')
            sc.has_column('screened_latest', 'x')
        self.assertEqual(client.table.call_count, 1)


class TestScreenerExcludesDelisted(unittest.TestCase):
    def test_the_screener_calls_the_filter(self):
        """スクリーナーから条件が外れていないこと。"""
        import re
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, 'app.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('query = exclude_delisted(query)', src,
                      'スクリーナーが上場廃止を外していない')
        # 決算一覧・テーマ一覧でも使っていること
        self.assertGreaterEqual(len(re.findall(r'exclude_delisted\(', src)), 2)
        with open(os.path.join(here, 'models', 'root.py'), encoding='utf-8') as f:
            self.assertIn('exclude_delisted', f.read())


class TestDetailPageTellsTheReader(unittest.TestCase):
    """一覧から外すだけでは足りない。URLを直接開けば見えてしまう。"""

    def test_the_template_has_the_notice(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, 'templates', 'stock_detail.html'),
                  encoding='utf-8') as f:
            html = f.read()
        # 日付が分からない銘柄でもバナーは出すこと。印の有無と日付は別物
        # （分けていなかったため 2692 伊藤忠食品でバナーが丸ごと消えた）
        self.assertIn('{% if is_delisted %}', html)
        notice = html.index('この会社は上場廃止になっています')
        guard = html.rindex('{% if is_delisted %}', 0, notice)
        self.assertNotIn('{% if delisted_on %}', html[guard:notice],
                         'バナー全体が日付の有無で消える作りになっている')
        self.assertIn('上場廃止', html)

    def test_the_route_passes_the_flag(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, 'models', 'root.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('delisted_on=delisted_on', src)
        self.assertIn('is_delisted=is_delisted', src)


if __name__ == '__main__':
    unittest.main()
