# -*- coding: utf-8 -*-
"""市場区分による銘柄の切り分け（2026-08-26）。

きっかけ:
  `market_segment` 列がほぼ空だったため、PRO Market（プロ投資家向け市場）を
  見分けられず、**「出来高ゼロが1年続く103銘柄＝上場廃止」と誤診した**。
  実際は103件すべてPRO Marketで、売買が成立しない日が続くのが正常。
  Yahoo・kabutan が404を返すのも、両社が扱っていないだけだった。

  ⚠️ 教訓は「市場区分を持たずに異常を判定しない」。
     区分はJPXが無料で公開している（jpx_master.py）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import jpx_master
import security_filter as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class SegmentLabelTest(unittest.TestCase):
    """保存するラベルと、判定に使う文字列を食い違わせない。"""

    def test_短いラベルでも非事業会社と判定できる(self):
        """sync_market_segments.py は 'REIT等' で保存する。
        判定側がJPXの生の区分名しか見ていないと、ここですり抜ける。
        実際に一度この形で壊した。"""
        self.assertTrue(sf.is_non_operating_segment('REIT等'))
        self.assertTrue(sf.is_non_operating_segment(
            'REIT・ベンチャーファンド・カントリーファンド・インフラファンド'))
        self.assertTrue(sf.is_non_operating_segment('ETF・ETN'))

    def test_PRO_Marketと外国株は事業会社(self):
        """会社としては実在するので除外しない（既存の方針）。"""
        for seg in ('PRO Market', '外国株', '出資証券',
                    'プライム', 'スタンダード', 'グロース'):
            self.assertFalse(sf.is_non_operating_segment(seg), seg)

    def test_JPXの区分名をすべて知っている(self):
        """知らない区分が来たら気づけること。fetch_all は未知の区分を
        'その他' にまとめず、素の名前をそのまま返す。"""
        src = read('jpx_master.py')
        self.assertIn("or OTHER_SEGMENTS.get(segment) or segment", src)
        self.assertIn('REIT・ベンチャーファンド・カントリーファンド・インフラファンド',
                      src)


class ClassShareTest(unittest.TestCase):
    """種類株式（優先株・社債型）は会社ではない。"""

    def test_5桁の数字は種類株式(self):
        for code in ('75505', '92025', '25935', '94345'):
            self.assertTrue(sf.is_class_share(code), code)

    def test_普通株を巻き込まない(self):
        for code in ('7203', '9432', '164A', '136A', '1305'):
            self.assertFalse(sf.is_class_share(code), code)

    def test_空やNoneで落ちない(self):
        for v in (None, '', '   '):
            self.assertFalse(sf.is_class_share(v))

    def test_取り込みの入口で弾く(self):
        """EXCLUDED_CODES は入ってしまったぶんの手当て。
        入口を塞がないと同じものがまた増える。"""
        src = read('fetch_companies.py')
        self.assertIn('is_class_share(code)', src)

    def test_サジェストに種類株式が無い(self):
        """companies.json は検索の候補。ここに残ると
        「ソフトバンク」で社債型種類株式が出る。"""
        import json
        with open(os.path.join(ROOT, 'static', 'companies.json'),
                  encoding='utf-8') as f:
            data = json.load(f)
        five = [x for x in data
                if sf.is_class_share(str(x.get('c', '')))]
        self.assertEqual(five, [])


class ExcludeQueryTest(unittest.TestCase):
    """除外はDB側でやる。Python側で絞ると件数とページングが狂う。"""

    def test_区分でも外す(self):
        """コードの列挙は手で足すので必ず取りこぼす。実際
        8963 インヴィンシブル投資法人と 8987 Japan Excellent, Inc. が
        漏れていた（後者は英語名なのでキーワードにも掛からない）。"""
        src = read('security_filter.py')
        block = src.split('def exclude_non_operating', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('NON_OPERATING_SEGMENTS', block)

    def test_区分が空の行を巻き込まない(self):
        """⚠️ SQLでは `NULL NOT IN (...)` が真にならない。
        `not_.in_()` だけで書くと、区分がまだ入っていない行が
        まとめて消える。実測で37件が巻き込まれた。"""
        src = read('security_filter.py')
        block = src.split('def exclude_non_operating', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('market_segment.is.null', block)
        self.assertIn('.or_(', block)


class ProMarketNoticeTest(unittest.TestCase):
    """PRO Market であることを画面に出す。"""

    def test_銘柄ページに注記がある(self):
        html = read('templates', 'stock_detail.html')
        self.assertIn("market_segment == 'PRO Market'", html)
        self.assertIn('TOKYO PRO Market', html)
        self.assertIn('データの欠落ではありません', html)

    def test_注記の色が定義されている(self):
        html = read('templates', 'stock_detail.html')
        self.assertIn('.pro-market-note', html)

    def test_区分がテンプレートに渡っている(self):
        src = read('models', 'root.py')
        self.assertIn("market_segment=company.get('market_segment')", src)


if __name__ == '__main__':
    unittest.main()
