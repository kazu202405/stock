"""JPXの週次信用残高を全銘柄に流し込む（2026-08-25）。

信用倍率は 3,859銘柄のうち **22件（0.6%）** しか入っていなかった。銘柄ページを
開いたときの後追い取得しか経路が無く、開かれた銘柄だけが埋まる形だったため。
回転日数（信用買残 ÷ 平均出来高）は出来高が99%埋まったので、分子さえあれば
全銘柄で出せる。

⚠️ 主眼は2つ。
  1. **外部へのリクエストを1回に保つ。** PDFに全銘柄（4,221件）が載っており、
     jpx_margin がキャッシュする。銘柄ごとに叩くと4,000回になる。
  2. **基準日を必ず残す。** 週次データなので、いつ時点の残高かが分からない
     数字を並べない。
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


class OneRequestTest(unittest.TestCase):

    def setUp(self):
        self.source = read('backfill_margin.py')

    def test_取得は1回だけ(self):
        """銘柄ごとに get_margin_balance を呼ぶと4,000回叩くことになる。"""
        calls = re.findall(r'get_margin_balance\(', self.source)
        self.assertEqual(len(calls), 1, '取得の呼び出しが1回でない')

    def test_残りはキャッシュから読む(self):
        self.assertIn("jpx_margin._cache.get('rows')", self.source)

    def test_銘柄ごとのループの中で取りに行かない(self):
        loop = self.source.split('for row in rows:', 1)[1]
        self.assertNotIn('get_margin_balance', loop)
        self.assertNotIn('requests', loop)


class AsOfTest(unittest.TestCase):

    def setUp(self):
        self.source = read('backfill_margin.py')

    def test_基準日を残す(self):
        """週次データ。いつ時点の残高かが分からない数字は並べない。"""
        self.assertIn("'as_of': as_of", self.source)
        self.assertIn("'frequency': 'weekly'", self.source)

    def test_診断は上書きせずマージする(self):
        """他の取得元の診断（成功済み）を消さない。"""
        self.assertIn('merge_source_status(', self.source)


class SkipsTest(unittest.TestCase):

    def setUp(self):
        self.source = read('backfill_margin.py')

    def test_信用取引の対象外は失敗にしない(self):
        """PDFに載らない銘柄がある。取得失敗と区別する。"""
        self.assertIn('missing += 1', self.source)
        self.assertIn('信用取引の対象でない', self.source)

    def test_上場廃止は触らない(self):
        self.assertIn("not r.get('delisted_at')", self.source)

    def test_変わらない行は書かない(self):
        """毎週走るので、丸め誤差だけの書き換えを積み上げない。"""
        self.assertIn('unchanged += 1', self.source)


class ScheduleTest(unittest.TestCase):

    def setUp(self):
        self.source = read('app.py')

    def test_毎週動く(self):
        self.assertIn('scheduled_update_margin_balances', self.source)
        self.assertIn("day_of_week='thu'", self.source)

    def test_失敗しても他の処理を巻き込まない(self):
        block = self.source.split('def scheduled_update_margin_balances', 1)[1] \
                           .split('\ndef ', 1)[0]
        self.assertIn('except Exception', block)


if __name__ == '__main__':
    unittest.main()
