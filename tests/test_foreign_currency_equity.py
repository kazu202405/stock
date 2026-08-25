"""円建てでない純資産を億円として保存しない（2026-08-25）。

総資産・純資産を埋めたら、3銘柄で「時価総額 ÷ 純資産」が150〜460倍という
あり得ない値になった。純資産が外国通貨のまま億円として入っていたため。

  6269 三井海洋開発        PBR 2.73 / 時価÷純資産 463.2
  4875 メディシノバ        PBR 1.84 / 280.1
  7699 オムニ・プラス      PBR 0.97 / 167.3

⚠️ **通貨のメタ情報（financialCurrency）は信じない。** 6269 は JPY と
   申告しているのに実際はドル建てだった（EPSでも同じ罠を踏んでいる）。
   「時価総額 ÷ 純資産」と保存済みPBRを突き合わせる——同じものを別の道筋で
   出しているので、通貨が違えば100倍以上ずれる。

⚠️ 一番たちが悪いのは、**自己資本比率とは整合してしまう**こと
   （純資産÷総資産は通貨に関係なく正しい）。片方が正しいので壊れて見えない。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import backfill_balance_debt as bbd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class DetectionTest(unittest.TestCase):

    def test_実際にずれていた3銘柄を捕まえる(self):
        # (純資産・億円, 時価総額・億円, 保存PBR)
        for equity, cap, pbr, name in (
                (14.53, 6730.2, 2.73, '6269 三井海洋開発'),
                (41.59 / 1.0, 11648.0, 1.84, '4875 メディシノバ相当'),
                (86.94, 14540.0, 0.97, '7699 オムニ・プラス相当')):
            self.assertTrue(bbd.looks_foreign_currency(equity, cap, pbr), name)

    def test_普通の銘柄は捕まえない(self):
        """トヨタ: 純資産39.9兆・時価総額36.4兆・PBR1.00。
        時価÷純資産は0.91倍で、PBRとほぼ同じ。"""
        self.assertFalse(bbd.looks_foreign_currency(399188.54, 364112.37, 1.004))

    def test_数倍のずれでは消さない(self):
        """通貨は正しいのに決算が特殊な会社を巻き込まない。
        2026-08-25 の実測では 1.5〜10倍が46件あり、これらは本物の食い違い。"""
        self.assertFalse(bbd.looks_foreign_currency(100.0, 400.0, 1.0))   # 4倍
        self.assertFalse(bbd.looks_foreign_currency(100.0, 900.0, 1.0))   # 9倍

    def test_境目は谷にある(self):
        """実測で 10〜50倍が0件、50倍以上が3件。谷で切れば巻き込まない。"""
        self.assertGreaterEqual(bbd.CURRENCY_MISMATCH_RATIO, 20)
        self.assertLessEqual(bbd.CURRENCY_MISMATCH_RATIO, 100)

    def test_材料が無ければ判定しない(self):
        """疑わしきは残す。消しすぎるほうが害が大きい。"""
        self.assertFalse(bbd.looks_foreign_currency(None, 1000.0, 1.0))
        self.assertFalse(bbd.looks_foreign_currency(100.0, None, 1.0))
        self.assertFalse(bbd.looks_foreign_currency(100.0, 1000.0, None))
        self.assertFalse(bbd.looks_foreign_currency(0, 1000.0, 1.0))
        self.assertFalse(bbd.looks_foreign_currency(100.0, 1000.0, 0))


class PolicyTest(unittest.TestCase):

    def setUp(self):
        self.source = read('backfill_balance_debt.py')

    def test_通貨のメタ情報に頼らない(self):
        """6269 は JPY と申告しているのに実際はドル建てだった。

        コメントで経緯として触れるのはよい。**判定に使っていない**ことを見る。"""
        for line in self.source.splitlines():
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith("'"):
                continue
            self.assertNotIn('financialCurrency', line, line)
        self.assertNotIn("ticker.info", self.source)

    def test_疑わしい行は入れない(self):
        """決められないものを決めたふりで出さない。"""
        self.assertIn('looks_foreign_currency(', self.source)
        self.assertIn('scalars = {}', self.source)

    def test_理由を書き残している(self):
        self.assertIn('三井海洋開発', self.source)
        self.assertIn('検算', self.source)


class CheckerTest(unittest.TestCase):
    """検算の道具そのもの。"""

    def setUp(self):
        self.source = read('check_pbr_consistency.py')

    def test_書き込まない(self):
        for word in ('.update(', '.insert(', '.delete(', '.upsert('):
            self.assertNotIn(word, self.source, word)

    def test_スコアの合格ラインで分ける(self):
        """減点されすぎか、甘く見えているか。実害はここに出る。"""
        self.assertIn('PBR_SCORE_LINE', self.source)
        self.assertIn('wrong_fail', self.source)
        self.assertIn('wrong_pass', self.source)

    def test_少しの差は問題にしない(self):
        """非支配株主持分の扱いで数%は動く。"""
        self.assertIn('GAP_THRESHOLD', self.source)


if __name__ == '__main__':
    unittest.main()
