"""外から来た文章を、そのまま画面に流し込まない（2026-08-25）。

事業概要（business_summary_jp）は3経路で入る:
  ① Yahoo!ファイナンス日本版のページから取ってくる
  ② 英語の概要を OpenAI で日本語にする
  ③ 管理画面から手で直す
①と②は**こちらが中身を決められない**。

そして画面は
  stock_detail.html   summaryContent.innerHTML = `...${...}...`
  _report_body.html   {{ report.business_summary|safe }}
と **HTMLとして解釈する**形で出していた（改行を <br> で入れている都合）。

つまり取得元のページやLLMの出力に <script> や <img onerror=...> が混じれば、
そのまま**公開ページで動く**。2026-08-25 時点の実データに危険なタグは1つも
無かった（<br> が2,497件だけ）が、入る経路は開いていた。

⚠️ 保存の入口と表示の直前の**両方**で通す。片方だけに置くと、片方を直した
   ときに穴が開く。過去に保存された行にも表示側が効く。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from html_safe import sanitize_rich_text, strip_tags

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class SanitizeTest(unittest.TestCase):

    def test_改行のBRは残す(self):
        """事業概要は「本文<br>【連結事業】…」の形で保存されている。
        全部エスケープすると 2,497件の表示が壊れる。"""
        self.assertEqual(sanitize_rich_text('建材を製造<br>【連結事業】建材70%'),
                         '建材を製造<br>【連結事業】建材70%')
        for variant in ('<br>', '<br/>', '<BR />', '< br >'):
            self.assertIn('<br>', sanitize_rich_text('a' + variant + 'b'))

    def test_スクリプトは文字にする(self):
        out = sanitize_rich_text('<script>alert(1)</script>')
        self.assertNotIn('<script', out)
        self.assertIn('&lt;script&gt;', out)

    def test_属性つきのタグも文字にする(self):
        for payload in ('<img src=x onerror=alert(1)>',
                        '<svg onload=alert(1)>',
                        '<iframe src="javascript:alert(1)"></iframe>',
                        '<a href="javascript:alert(1)">押して</a>'):
            out = sanitize_rich_text(payload)
            self.assertNotIn('<' + payload.lstrip('<').split()[0].rstrip('>'), out, payload)
            self.assertIn('&lt;', out, payload)

    def test_タグを消さずにエスケープする(self):
        """消すと「<社名>」のような普通の文章まで欠ける。"""
        out = sanitize_rich_text('「<社名>」のような普通の文章')
        self.assertIn('社名', out)
        self.assertIn('&lt;社名&gt;', out)

    def test_空やNoneで落ちない(self):
        self.assertIsNone(sanitize_rich_text(None))
        self.assertEqual(sanitize_rich_text(''), '')

    def test_文字列でないものはそのまま(self):
        self.assertEqual(sanitize_rich_text(123), 123)

    def test_二度通しても壊れない(self):
        """保存時と表示時の両方で通るので、冪等でないと <br> が消える。"""
        once = sanitize_rich_text('a<br>b')
        self.assertEqual(sanitize_rich_text(once), once)

    def test_strip_tagsは素の文章にする(self):
        """meta description のようにHTMLを置けない場所で使う。"""
        self.assertEqual(strip_tags('建材を製造<br>【連結事業】70%'),
                         '建材を製造【連結事業】70%')


class SavePathTest(unittest.TestCase):
    """保存の入口で通す。"""

    def setUp(self):
        self.source = read('app.py')

    def test_3つの保存パス全部で通す(self):
        """どれか1つを忘れると、その経路を通った銘柄だけ素通しになる。"""
        self.assertEqual(
            self.source.count("sanitize_rich_text(stock_data.get('business_summary_jp'))"), 3)

    def test_手入力でも通す(self):
        """管理者でも、貼り付けた文章にタグが混じることがある。"""
        block = self.source.split("'business_summary_jp' in edited_data", 1)[1][:600]
        self.assertIn('sanitize_rich_text(summary)', block)

    def test_取得元から直で取る経路でも通す(self):
        block = self.source.split('def api_retry_summary_jp', 1)[1][:900]
        self.assertIn("sanitize_rich_text(yahoo_data.get('business_summary_jp'))", block)

    def test_自分で足すBRはエスケープの後(self):
        """先に足すと、こちらが意図して入れた <br> まで文字になる。"""
        block = self.source.split('def api_retry_summary_jp', 1)[1][:900]
        self.assertLess(block.index('sanitize_rich_text(yahoo_data'),
                        block.index('【連結事業】'))


class DisplayPathTest(unittest.TestCase):
    """表示の直前でも通す。過去に保存された行に効かせるため。"""

    def test_銘柄ページが通す(self):
        html = read('templates', 'stock_detail.html')
        self.assertIn('function safeSummary(', html)
        self.assertIn('safeSummary(data.business_summary_jp)', html)

    def test_レポートがsafeを使っていない(self):
        """|safe はエスケープを外す。中身を決められない文章に使わない。"""
        html = read('templates', '_report_body.html')
        self.assertNotIn('business_summary|safe }}', html)
        self.assertIn('business_summary|safe_summary', html)

    def test_フィルタが登録されている(self):
        import app as app_module
        self.assertIn('safe_summary', app_module.app.jinja_env.filters)
        rendered = str(app_module.app.jinja_env.filters['safe_summary'](
            '<script>alert(1)</script>a<br>b'))
        self.assertNotIn('<script', rendered)
        self.assertIn('<br>', rendered)


class OtherFieldsTest(unittest.TestCase):
    """会員が書くものは別経路で守っている（こちらは全部エスケープ）。"""

    def test_ノートはHTMLを許さない(self):
        html = read('templates', 'stock_detail.html')
        self.assertIn('escapeText(n.content', html)

    def test_勉強会の説明もエスケープする(self):
        html = read('templates', 'learning.html')
        self.assertIn('studyEscape(item.description)', html)


if __name__ == '__main__':
    unittest.main()
