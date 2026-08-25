"""銘柄ページの「この会社のノート」と、事業概要の手直し（2026-08-25）。

置き場所を決めた経緯:
  腰を据えた企業分析をどこに置くか。事業概要（business_summary_jp）は
  **一行紹介の器**で、3か所に出る——スクリーナーのカード（2行でクランプ）、
  テーマ・業種ページ（全文）、銘柄ページの meta description（全文を striptags）。
  実データは中央値85文字・最長184文字。長文を入れる場所ではない。

  コミュニティ（community_questions）は title / content / answer_count /
  is_resolved という**「問い」の器**。分析レポートを置くと、誰も答えない
  質問が並び is_resolved が意味を失う。

  notes は company_code / title / content / is_public / poster_name / tags を
  持つ**「記事」の器**で、書く画面も既にある。ここに置く。

⚠️ 「運営の見解」という見出しにしない。**書いた人の名前を出す。**
   一意見であることが伝わる形にしておく。
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


class BusinessSummaryEditTest(unittest.TestCase):

    def setUp(self):
        source = read('app.py')
        marker = "@app.route('/api/watchlist/update', methods=['POST'])"
        self.body = source.split(marker, 1)[1].split('@app.route', 1)[0]
        self.source = source

    def test_事業概要を直せる(self):
        self.assertIn("'business_summary_jp' in edited_data", self.body)

    def test_長さに上限がある(self):
        """一覧のカードとSEOの説明文にも出る。長文を入れる場所ではない。"""
        self.assertIn('BUSINESS_SUMMARY_MAX', self.body)
        self.assertIn('BUSINESS_SUMMARY_MAX = ', self.source)
        self.assertIn('400', self.body)

    def test_上限は実データより広いが長文は入らない(self):
        import re
        limit = int(re.search(r'BUSINESS_SUMMARY_MAX = (\d+)', self.source).group(1))
        self.assertGreater(limit, 184, '実データの最長(184文字)より狭い')
        self.assertLess(limit, 1000, '長文が入ってしまう')

    def test_画面が文字数を出す(self):
        html = read('templates', 'admin_stock_data.html')
        self.assertIn('asdSummaryCount', html)
        self.assertIn('SUMMARY_MAX', html)

    def test_長い分析はノートへと案内している(self):
        html = read('templates', 'admin_stock_data.html')
        self.assertIn('銘柄ノート', html)


class StockNotesSectionTest(unittest.TestCase):

    def setUp(self):
        self.html = read('templates', 'stock_detail.html')

    def test_銘柄コードで引く(self):
        self.assertIn("'/api/notes?company_code=' + encodeURIComponent(code)", self.html)

    def test_公開ノートだけが出る(self):
        """サーバー側が is_public で絞っていること。銘柄ページは公開なので、
        非公開のノートを混ぜない。"""
        client = read('supabase_client.py')
        block = client.split('def get_notes_by_company', 1)[1].split('\ndef ', 1)[0]
        self.assertIn("eq('is_public', True)", block)

    def test_本文をエスケープして入れる(self):
        """書き手は会員。HTMLをそのまま流し込むと公開ページに他人のタグが載る。"""
        self.assertIn('function escapeText(', self.html)
        self.assertIn('escapeText(n.content', self.html)
        self.assertIn('escapeText(n.title', self.html)

    def test_無いときは見出しごと出さない(self):
        self.assertIn("if (!notes.length) return;", self.html)
        self.assertIn('id="stock-notes-card" style="grid-column: 1 / -1; display: none;"',
                      self.html)

    def test_書いた人の名前を出す(self):
        """「運営の見解」ではなく一意見として見せる。"""
        self.assertIn('n.poster_name', self.html)
        self.assertIn('is_anonymous', self.html)

    def test_売買の勧めではないと書いてある(self):
        self.assertIn('売買の勧めでもありません', self.html)

    def test_改行が潰れない(self):
        """長文を1段落に潰さない。"""
        self.assertIn('white-space: pre-wrap', self.html)


if __name__ == '__main__':
    unittest.main()
