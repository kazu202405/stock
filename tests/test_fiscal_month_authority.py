# -*- coding: utf-8 -*-
"""決算月は有報の対象決算期から決める（2026-09-03）。

## なぜ変えたか

これまでは yfinance の決算日の**最頻値**だけで決めていた。1年だけの不規則な
日付に引きずられないための作りだが、**決算期を変更した会社では古い月を返し続ける**。
履歴には古い決算月が4〜5年ぶん、新しい月は1年ぶんしか無いためで、最頻値は
構造的に新しいほうを選べない。

実測（2026-09-03）で44社が該当した:
    ダイフク    3月 → 12月
    ツルハHD    5月 →  2月
    タダノ      3月 → 12月（この社は逆に、こちらが正しく提出者一覧が古かった）

決算月が違うと「決算情報ページの銘柄一覧」がまるごと間違う。

## 決め方

    有報の対象決算期（会社が自分で出した一次情報）> 提出者一覧の決算日 > 最頻値

⚠️ 最頻値は**有報が取れないときの保険**に下げた。消してはいない。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import analysis_quality as aq  # noqa: E402
import edinet_codes  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def history(*dates):
    return {'revenue': [{'date': d, 'value': 1} for d in dates]}


class 決め方の優先順位(unittest.TestCase):

    def test_有報があればそれを使う(self):
        # 履歴は3月ばかりだが、有報が12月なら12月
        h = history('2024-03-31', '2025-03-31', '2026-03-31')
        self.assertEqual(12, aq.derive_fiscal_month(h, None, authoritative=12))

    def test_有報が無ければ最頻値(self):
        h = history('2024-03-31', '2025-03-31', '2026-03-31')
        self.assertEqual(3, aq.derive_fiscal_month(h, None, authoritative=None))

    def test_決算期変更を最頻値では追えない(self):
        """⚠️ これがバグの正体。4年ぶんの3月に対し12月は1年ぶんしか無い。"""
        h = history('2022-03-31', '2023-03-31', '2024-03-31', '2025-03-31',
                    '2025-12-31')
        self.assertEqual(3, aq.derive_fiscal_month(h))          # 追えない
        self.assertEqual(12, aq.derive_fiscal_month(h, None, authoritative=12))

    def test_ありえない月は無視する(self):
        h = history('2026-03-31')
        self.assertEqual(3, aq.derive_fiscal_month(h, None, authoritative=0))
        self.assertEqual(3, aq.derive_fiscal_month(h, None, authoritative=13))


class 引き当ては分析を止めない(unittest.TestCase):
    """⚠️ 決算月が引けないだけで分析全体を落とさない。最頻値に落ちればよい。"""

    def test_失敗してもNoneを返す(self):
        block = read('app.py').split('def _authoritative_fiscal_month(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn('except Exception', block)
        self.assertIn('return None', block)

    def test_引き当て表が作れなくても落ちない(self):
        src = read('edinet_codes.py')
        block = src.split('def authoritative_fiscal_months(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn('except Exception', block)

    def test_銘柄ごとに問い合わせない(self):
        """⚠️ 一括分析は200銘柄。1件ずつ引くと200回問い合わせることになる。"""
        src = read('edinet_codes.py')
        block = src.split('def authoritative_fiscal_months(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn('_FISCAL_CACHE', block)


class 食い違いの数え方(unittest.TestCase):
    """決着がついたものを数え続けない。"""

    def test_有報がこちらを支持していれば数えない(self):
        block = read('edinet_codes.py').split('def fiscal_month_mismatches(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn('report.get(code)', block)
        self.assertIn('continue', block)

    def test_どちらが正しいか返す(self):
        """件数だけだと、見た人が次に何をすればいいか分からない。"""
        block = read('edinet_codes.py').split('def fiscal_month_mismatches(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn("'report'", block)

    def test_上書きはしない(self):
        """判定して返すだけ。書き込むかは人が決める。"""
        block = read('edinet_codes.py').split('def fiscal_month_mismatches(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertNotIn('.update(', block)


if __name__ == '__main__':
    unittest.main()
