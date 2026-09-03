# -*- coding: utf-8 -*-
"""英語列（business_summary / major_holders / institutional_holders）の扱い。

**0.6% しか埋まっていないのは正常。壊れていない。**
2026-09-03 実測（内国株・上場中 3,658件）:

    business_summary       0.6%  ←→  business_summary_jp    100.0%
    major_holders          0.6%  ←→  major_shareholders_jp   99.8%
    institutional_holders  0.3%

これらは日本語版が入る前の取得元（yahooquery / yfinance）の名残で、
取得経路はもう動いていない。**消さずに残してある**のは、画面に
「日本語が取れなかったときだけ出す」フォールバックが実際にあるため
（`_report_body.html` の事業内容、`stock_detail.html` の創業家・機関投資家の比率）。
新規上場直後など、日本語概要が一瞬入っていない銘柄で効く。

⚠️ **この低い数字を「欠損」として画面に出さないこと。** 出すと 99.4% の銘柄で
   「事業概要を取得できていません」と表示されるのに、実際には日本語で
   ちゃんと出ている、という嘘になる。
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class 欠損として報告しない(unittest.TestCase):

    def _omission_fields(self):
        src = read('app.py')
        block = src.split('OMISSION_FIELDS = (', 1)[1].split(')', 1)[0]
        return set(re.findall(r"'([^']+)'", block))

    def test_英語の概要は欠損に数えない(self):
        """⚠️ 数えると 99.4% の銘柄で「事業概要を取得できていません」と出る。
        実際には日本語概要が100%入っていて、画面にはちゃんと出ている。"""
        self.assertNotIn('business_summary', self._omission_fields())

    def test_英語の大株主も欠損に数えない(self):
        fields = self._omission_fields()
        self.assertNotIn('major_holders', fields)
        self.assertNotIn('institutional_holders', fields)

    def test_日本語側は欠損に数える(self):
        """こちらは本当に埋まっているべき列なので、欠けたら見えるようにする。"""
        fields = self._omission_fields()
        self.assertIn('major_shareholders_jp', fields)
        self.assertIn('company_officers', fields)


class フォールバックは残す(unittest.TestCase):
    """消さない理由。ここが消えたら英語列も一緒に片付けてよい。"""

    def test_レポートは日本語が無いときだけ英語を出す(self):
        html = read('templates', '_report_body.html')
        self.assertIn('report.business_summary', html)
        # 英語を出すときは、その旨を画面に書く
        self.assertIn('英語原文', html)

    def test_銘柄ページは日本語を先に見る(self):
        html = read('templates', 'stock_detail.html')
        jp = html.index('data.business_summary_jp')
        en = html.index('data.business_summary)')
        self.assertLess(jp, en, '英語を先に見ている')


if __name__ == '__main__':
    unittest.main()
