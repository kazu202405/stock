# -*- coding: utf-8 -*-
"""大株主・役員の名前から有報の脚注記号を落とす（2026-09-03）。

画面に「株式会社We(注)２」と出ていた。有報の表には「（注）2」のような
脚注記号が名前の後ろに付くが、**脚注の本文は取り込んでいない**ので、
読む人には「(注)２ って何？」としか見えない。

実測で 3,658件中 124銘柄が該当した（大株主だけでなく役員名にも付く）。

⚠️ **括弧を丸ごと落とさないこと。** 「（信託口）」「（常任代理人 …）」は
   名前の一部で、消すと別の株主になる。落とすのは「注」「※」だけ。

⚠️ **経路ごとに実装しないこと。** 有報CSV（edinet_report）と
   EDINET DB API（edinet_db_client）の両方から名前が入る。別々に書くと、
   同じ株主が経路によって違う名前で入る。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from edinet_report import strip_footnote  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class 脚注記号を落とす(unittest.TestCase):

    def test_閉じ括弧の後ろの数字も落とす(self):
        """実際に画面へ出ていた形。"""
        self.assertEqual('株式会社We', strip_footnote('株式会社We(注)２'))

    def test_括弧の中に数字がある形(self):
        self.assertEqual('辻角 智之', strip_footnote('辻角 智之（注）１'))
        self.assertEqual('株式会社日本カストディ銀行（信託口）',
                         strip_footnote('株式会社日本カストディ銀行（信託口）（注）2'))

    def test_複数の脚注が続く形(self):
        self.assertEqual('榊 淳', strip_footnote('榊 淳（注）１（注）５'))
        self.assertEqual('株式会社チキンシープ',
                         strip_footnote('株式会社チキンシープ （注）１,２'))

    def test_米印(self):
        self.assertEqual('日本マスタートラスト信託銀行株式会社（信託口）',
                         strip_footnote('日本マスタートラスト信託銀行株式会社（信託口）※１'))

    def test_番号なしの注(self):
        self.assertEqual('芳村 美紀', strip_footnote('芳村 美紀 (注)'))


class 名前の一部は残す(unittest.TestCase):
    """⚠️ ここを消すと別の株主になる。括弧を一律に落としてはいけない。"""

    def test_信託口(self):
        for name in ('株式会社日本カストディ銀行(信託口)',
                     '日本マスタートラスト信託銀行株式会社（信託口）',
                     '野村信託銀行株式会社(投信口)'):
            self.assertEqual(name, strip_footnote(name))

    def test_常任代理人(self):
        name = '三井物産株式会社(常任代理人 日本カストディ銀行)'
        self.assertEqual(name, strip_footnote(name))

    def test_名前の途中の括弧は触らない(self):
        name = 'ナティクシス エスエイ (常任代理人 みずほ銀行) 701910'
        self.assertEqual(name, strip_footnote(name))

    def test_空とNone(self):
        self.assertEqual('', strip_footnote(''))
        self.assertEqual('', strip_footnote(None))


class 経路をそろえる(unittest.TestCase):
    """有報CSVと EDINET DB API の両方から名前が入る。"""

    def test_有報CSV側で通している(self):
        src = read('edinet_report.py')
        self.assertIn('def strip_footnote(', src)
        self.assertIn('text = strip_footnote(value)', src)

    def test_EDINET_DB側も同じものを使う(self):
        src = read('edinet_db_client.py')
        self.assertIn('from edinet_report import strip_footnote', src)
        # 大株主と役員の両方に通す
        self.assertEqual(2, src.count('_strip_footnote(str(name))'))

    def test_読み込めなくても落とさない(self):
        """整形が失敗しても、取り込み自体は続ける。"""
        block = read('edinet_db_client.py').split('def _strip_footnote(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn('except Exception', block)
        self.assertIn('return value', block)


if __name__ == '__main__':
    unittest.main()
