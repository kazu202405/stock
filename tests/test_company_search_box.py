"""どのページからでも会社名で銘柄ページへ行けること（2026-08-25）。

きっかけ:
  検索窓は /search という専用ページにしか無かった。そのページは窓のほかに
  事業概要・財務データ5年・CF・財務健全性・主要株主/役員を持っており、
  **その5つは /stock/<code> と同じもの**だった。「検索するためだけに、
  同じ内容のページをもう1つ開く」形になっていた。

⚠️ コードか会社名かの判定を**ここ（ブラウザ側）で書かない**。
   サーバー（models/root.py の /stock/<code>）が持っていて、解決できなければ
   候補付きの stock_not_found を返す。2か所に書くと片方だけ直したときに
   食い違う（2026-08-19 にサジェスト未選択のEnterで同じ事故が起きている）。
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


class SearchBoxPlacementTest(unittest.TestCase):

    def setUp(self):
        self.layout = read('templates', 'layout.html')

    def test_共通レイアウトに窓がある(self):
        """layout.html に置いてあれば全ページで使える。"""
        self.assertIn('id="hdrCompanySearch"', self.layout)
        self.assertIn('js/company-search.js', self.layout)

    def test_スマホは全画面で開く(self):
        """ヘッダーに窓を置く幅が無いので下タブから開く。"""
        self.assertIn('id="sheetCompanySearch"', self.layout)
        self.assertIn('openCompanySearchSheet', self.layout)

    def test_窓はログイン不要(self):
        """/stock/<code> は公開ページ。検索で来た人がそのまま
        別の会社も見に行ける方が入口として素直。"""
        head = self.layout.split('id="hdrCompanySearch"')[0]
        # 直前に {% if is_logged_in %} が開いたままになっていないこと
        self.assertEqual(head.count('{% if is_logged_in %}'),
                         head.count('{% endif %}'))

    def test_両方の窓が同じ部品を使う(self):
        """判定を2か所に書くと、片方だけ直したときに食い違う。"""
        self.assertEqual(self.layout.count('CompanySearch.mount('), 2)


class SearchLogicTest(unittest.TestCase):

    def setUp(self):
        self.js = read('static', 'js', 'company-search.js')

    def test_コードか名前かの判定を持たない(self):
        """判定はサーバー側。ここでやると2か所になる。"""
        for forbidden in ('length === 4', 'length == 4', '/^[0-9A-Z]{4}$/'):
            self.assertNotIn(forbidden, self.js,
                             'コードか名前かをブラウザ側で判定している')

    def test_候補を選ばなくても飛べる(self):
        """サジェストは選んだ人しか救わない。打ったまま Enter でも飛ばす。"""
        self.assertIn("pick(open && active >= 0 ? els[active].dataset.code : input.value)",
                      self.js)

    def test_選んだあとの行き先を差し替えられる(self):
        """管理画面は「選んだらその場で読み込む」。候補の出し方は同じものを使い、
        行き先だけ差し替える（候補の絞り込みを2か所に書かない）。"""
        self.assertIn('var onSelect = opts.onSelect;', self.js)
        self.assertIn('if (onSelect) { onSelect(v); return; }', self.js)

    def test_企業リストは最初のフォーカスまで読まない(self):
        """全ページのヘッダーに置くので、毎回3,906件を読ませない。"""
        self.assertIn("input.addEventListener('focus'", self.js)
        # モジュールの評価時に取りに行っていないこと
        top = self.js.split('function load()')[0]
        self.assertNotIn('fetch(', top)

    def test_候補の文字列を素通しで埋め込まない(self):
        self.assertIn('escapeHtml', self.js)


if __name__ == '__main__':
    unittest.main()
