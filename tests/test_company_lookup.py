"""会社名 → 証券コードの解決。

`/stock/キオクシア` が空のページになった件の回帰テスト。
検索欄が会社名を受けるので、サジェストを選ばずにEnterを押すと
名前がそのままURLになる。名前で来ても正しい銘柄に着けること。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import company_lookup as cl


class TestLooksLikeName(unittest.TestCase):
    def test_ascii_is_treated_as_code(self):
        # 日本株コードも米国株ティッカーもASCIIしか使わない
        for code in ['7203', '285A', '164A', 'AAPL', 'BRK.B']:
            self.assertFalse(cl.looks_like_name(code), code)

    def test_non_ascii_is_treated_as_name(self):
        for name in ['キオクシア', 'トヨタ自動車', '味の素']:
            self.assertTrue(cl.looks_like_name(name), name)


class TestResolve(unittest.TestCase):
    def test_unique_prefix_resolves(self):
        # 報告された不具合そのもの
        self.assertEqual(cl.resolve('キオクシア'), '285A')

    def test_exact_name_resolves(self):
        self.assertEqual(cl.resolve('トヨタ自動車'), '7203')

    def test_ambiguous_name_returns_none(self):
        # トヨタ自動車 と トヨタ紡織。勝手にどちらかへ飛ばさない
        self.assertIsNone(cl.resolve('トヨタ'))

    def test_unknown_name_returns_none(self):
        self.assertIsNone(cl.resolve('存在しない会社名です'))

    def test_code_passes_through_uppercased(self):
        self.assertEqual(cl.resolve('285a'), '285A')
        self.assertEqual(cl.resolve('7203'), '7203')

    def test_empty(self):
        self.assertIsNone(cl.resolve(''))
        self.assertIsNone(cl.resolve(None))


class TestListed(unittest.TestCase):
    def test_new_format_code_is_listed(self):
        # 英数字まじりの新形式コードを落とさないこと
        self.assertTrue(cl.is_listed_code('285A'))
        self.assertEqual(cl.name_of('285A'), 'キオクシアホールディングス')

    def test_unlisted_code(self):
        self.assertFalse(cl.is_listed_code('9999'))
        self.assertIsNone(cl.name_of('9999'))


class TestSuggest(unittest.TestCase):
    def test_ambiguous_name_offers_candidates(self):
        cands = cl.suggest('トヨタ')
        self.assertGreaterEqual(len(cands), 2)
        self.assertIn('7203', [c['c'] for c in cands])

    def test_unknown_name_offers_nothing(self):
        self.assertEqual(cl.suggest('存在しない会社名です'), [])

    def test_limit_is_respected(self):
        self.assertLessEqual(len(cl.suggest('ホールディングス', limit=5)), 5)


if __name__ == '__main__':
    unittest.main()
