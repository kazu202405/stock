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

    def test_読むだけの人には空の見出しを出さない(self):
        """書ける人（ログイン中）には0件でもセクションを出す。
        「自分もノートを書く」の導線がここにしか無いため。"""
        self.assertIn('const canWrite = !!document.querySelector', self.html)
        self.assertIn('if (!canWrite) return;', self.html)
        self.assertIn('id="stock-notes-card" style="grid-column: 1 / -1; display: none;"',
                      self.html)

    def test_上のボタンに件数を出す(self):
        """ページ下部に置くと気づかれない。**下に何かあることを数字で伝える**
        のがその唯一の仕掛け。0件ならボタンごと隠す。"""
        self.assertIn('notesJumpCount', self.html)
        self.assertIn("jump.style.display = 'none'", self.html)

    def test_モーダルをやめて移動にする(self):
        """同じ中身をモーダルとページの両方に持っていて二重だった。
        ノートは長文なので、狭いモーダルより本文の流れで読むほうが読みやすい。"""
        self.assertNotIn('notesModal', self.html)
        self.assertNotIn('loadCommunityNotes', self.html)
        self.assertIn('function jumpToNotes()', self.html)
        self.assertIn("scrollIntoView({ behavior: 'smooth'", self.html)

    def test_書いた人の名前を出す(self):
        """「運営の見解」ではなく一意見として見せる。"""
        self.assertIn('n.poster_name', self.html)
        self.assertIn('is_anonymous', self.html)

    def test_売買の勧めではないと書いてある(self):
        self.assertIn('売買の勧めでもありません', self.html)

    def test_改行が潰れない(self):
        """長文を1段落に潰さない。"""
        self.assertIn('white-space: pre-wrap', self.html)


class PosterNameTest(unittest.TestCase):
    """投稿者名はアカウントの表示名に一本化する（2026-08-25）。

    以前は投稿した時点の名前を1件ずつ焼き付けていた（poster_name）。
    表示名を変えると過去のノートは古い名前のまま残り、**同じ人が何人も
    いるように見えた**。会員が5人しかいない場では、これは実態を誤って見せる。

    名前は読むときにアカウントから引く。変えれば過去の投稿も一緒に変わる。
    """

    def test_投稿ごとの名前を保存しない(self):
        source = read('app.py')
        block = source.split('def api_create_note', 1)[1].split(chr(10) + 'def ', 1)[0]
        self.assertIn("data.pop('poster_name', None)", block)

    def test_投稿名の入力欄を置かない(self):
        for name in ('mypage.html', 'stock_detail.html'):
            html = read('templates', name)
            self.assertNotIn('この投稿だけの表示名', html, name)
        self.assertNotIn('noteFormPosterName', read('templates', 'stock_detail.html'))

    def test_読むときにアカウントから引く(self):
        source = read('app.py')
        self.assertIn('_resolve_display_name', source)
        # ノートの一覧で解決していること
        block = source.split('def api_get_notes', 1)[1].split(chr(10) + 'def ', 1)[0]
        self.assertIn('_resolve_display_name(note, user_map)', block)


class CommunityPosterNameTest(unittest.TestCase):
    """コミュニティ側は、投稿名を入れると**投稿そのものが失敗していた**。

    community_questions / community_answers に poster_name という列が無いのに、
    画面には入力欄があり、コードは値があれば insert に混ぜていた。
    PostgREST は存在しない列で PGRST204 を返すので、投稿名を書いた人は
    質問も回答もできなかった（実際 質問は1件・回答は0件だった）。

    列を足すのではなく、経路ごと無くす。表示名はノートと同じく
    アカウントから読むときに引く。
    """

    def test_存在しない列を挿そうとしない(self):
        source = read('supabase_client.py')
        for table in ('community_questions', 'community_answers'):
            block = source.split("insert(", 1)  # 形だけの保険
        self.assertNotIn("q_data['poster_name']", source)
        self.assertNotIn("a_data['poster_name']", source)

    def test_理由を書き残している(self):
        source = read('supabase_client.py')
        self.assertIn('PGRST204', source)

    def test_画面から投稿名の欄を外した(self):
        html = read('templates', 'community.html')
        self.assertNotIn('poster_name', html)
        self.assertNotIn('この投稿だけの表示名', html)


class PublishConsentTest(unittest.TestCase):
    """公開の同意文が、実際に出る範囲と合っていること。

    2026-08-25、公開ノートは銘柄ページ（/stock/<code>）にも出るようになった。
    銘柄ページは**ログインしていない人にも見える公開ページ**で、検索エンジンにも
    載る。ところがマイノートの同意文は「他のユーザーが閲覧できます」のままで、
    書いた人が同意した範囲と実際に出る範囲がずれていた。

    ⚠️ 出る場所を増やしたら、同意文も一緒に直す。
    """

    def setUp(self):
        self.html = read('templates', 'mypage.html')

    def test_銘柄ページに出ることを書いてある(self):
        self.assertIn('銘柄ページ', self.html)

    def test_ログインしていない人にも見えると書いてある(self):
        self.assertIn('ログインしていない人にも見えます', self.html)

    def test_コミュニティだけと書いていない(self):
        self.assertNotIn('他のユーザーがあなたのノートを閲覧できます', self.html)


if __name__ == '__main__':
    unittest.main()
