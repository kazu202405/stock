"""決算の取りこぼしを見つける（2026-08-25 追加）。

決算の検知は kabutan のスクレイピング頼み（1日2回）。サイトの構造が変わる・
遮断される・その銘柄が載らない、のどれかが起きると**その銘柄は決算が出ても
古い財務のまま残る**。しかもエラーは出ない。「検知しなかった」だけで処理は
正常に終わるので、気づく手段が無かった。

決算月は98%の銘柄で分かっているので、「期末から猶予を過ぎたのに最終分析日が
その期末より前」を数えれば漏れが見える。

⚠️ 主眼は「まだ発表されていないだけの銘柄を漏れと数えないこと」。
   誤検知が多いと、翌日のキューが無駄な再分析で埋まる。
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import earnings_freshness as ef

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def row(fiscal_month=3, analyzed='2026-06-01T00:00:00+00:00', **kw):
    base = {'company_code': '9999', 'company_name': 'テスト',
            'fiscal_month': fiscal_month, 'analyzed_at': analyzed}
    base.update(kw)
    return base


class FiscalEndTest(unittest.TestCase):

    def test_今年の決算期がもう終わっている(self):
        self.assertEqual(ef.last_fiscal_end(3, date(2026, 8, 25)), date(2026, 3, 31))

    def test_今年の決算期がまだ来ていないなら前年(self):
        """8月決算で今日が8/25なら、まだ8月は終わっていない。"""
        self.assertEqual(ef.last_fiscal_end(8, date(2026, 8, 25)), date(2025, 8, 31))

    def test_月末の日数を取り違えない(self):
        self.assertEqual(ef.last_fiscal_end(2, date(2026, 8, 25)), date(2026, 2, 28))
        self.assertEqual(ef.last_fiscal_end(2, date(2024, 8, 25)), date(2024, 2, 29))

    def test_決算月が無ければ判定しない(self):
        self.assertIsNone(ef.last_fiscal_end(None, date(2026, 8, 25)))
        self.assertIsNone(ef.last_fiscal_end(13, date(2026, 8, 25)))


class StaleDetectionTest(unittest.TestCase):

    TODAY = date(2026, 8, 25)

    def test_期末より前の分析は取りこぼし(self):
        """3月期末なのに、分析が2月で止まっている。"""
        self.assertTrue(ef.is_stale(row(3, '2026-02-10T00:00:00+00:00'), self.TODAY))

    def test_期末より後に分析していれば健全(self):
        self.assertFalse(ef.is_stale(row(3, '2026-05-15T00:00:00+00:00'), self.TODAY))

    def test_まだ発表の期限が来ていない銘柄は数えない(self):
        """6月決算は8/25時点で期末から56日。まだ出ていないだけかもしれない。
        誤検知すると翌日のキューが無駄な再分析で埋まる。"""
        self.assertFalse(ef.is_stale(row(6, '2026-03-01T00:00:00+00:00'), self.TODAY))

    def test_猶予を過ぎたら数える(self):
        """3月決算は8/25時点で期末から147日。とっくに出ている。"""
        self.assertTrue(ef.is_stale(row(3, '2026-01-01T00:00:00+00:00'), self.TODAY))

    def test_上場廃止は数えない(self):
        r = row(3, '2026-02-10T00:00:00+00:00', delisted_at='2026-07-01')
        self.assertFalse(ef.is_stale(r, self.TODAY))

    def test_決算月が無ければ数えない(self):
        self.assertFalse(ef.is_stale(row(None, '2026-02-10T00:00:00+00:00'), self.TODAY))

    def test_一度も分析していない銘柄は別の話(self):
        """バックフィルの領分。ここで数えると毎晩同じ銘柄を積み続ける。"""
        self.assertFalse(ef.is_stale(row(3, None), self.TODAY))

    def test_古い順に返す(self):
        rows = [row(3, '2026-02-20T00:00:00+00:00', company_code='B'),
                row(3, '2026-01-05T00:00:00+00:00', company_code='A')]
        found = ef.find_stale(rows, self.TODAY)
        self.assertEqual([f['company_code'] for f in found], ['A', 'B'])


class WiringTest(unittest.TestCase):

    def setUp(self):
        self.source = read('app.py')

    def test_毎晩動く(self):
        self.assertIn('scheduled_check_earnings_freshness', self.source)
        self.assertIn("id='earnings_freshness'", self.source)

    def test_決算の再分析より後に走る(self):
        """22:00の再分析で拾えたものを、取りこぼしと数えないため。"""
        import re
        # ⚠️ 2026-09-04: 実行記録を残すため recorded(...) で包んだので、
        #    関数名の直後に 'cron' が来ない。ジョブIDから時刻を引く。
        def at(job_id):
            return re.search(
                r"hour=(\d+), minute=(\d+),?\s*id='%s'" % job_id,
                self.source, re.S)
        proc = at('earnings_process_queue')
        check = at('earnings_freshness')
        self.assertIsNotNone(proc)
        self.assertIsNotNone(check)
        self.assertGreater((int(check.group(1)), int(check.group(2))),
                           (int(proc.group(1)), int(proc.group(2))))

    def test_見つけたら積み直す(self):
        """数えるだけでは直らない。同じキューに積んで翌日拾わせる。"""
        block = self.source.split('def scheduled_check_earnings_freshness', 1)[1] \
                           .split(chr(10) + 'def ', 1)[0]
        self.assertIn("table('earnings_queue')", block)
        self.assertIn('STALE_ENQUEUE_LIMIT', block)

    def test_一晩に積む数に上限がある(self):
        """全部いっぺんに積むと翌日の再分析が何百件も走って外部を叩きすぎる。"""
        import re
        limit = int(re.search(r'STALE_ENQUEUE_LIMIT = (\d+)', self.source).group(1))
        self.assertLessEqual(limit, 50)

    def test_落ちても他の処理を巻き込まない(self):
        block = self.source.split('def scheduled_check_earnings_freshness', 1)[1] \
                           .split(chr(10) + 'def ', 1)[0]
        self.assertIn('except Exception', block)


class UsesAnalyzedAtTest(unittest.TestCase):
    """鮮度は analyzed_at で見る。updated_at は一部の保存経路でしか
    書かれておらず、中身が新しくても2月のまま止まっている行がある。"""

    def test_updated_atを見ていない(self):
        source = read('earnings_freshness.py')
        self.assertIn("row.get('analyzed_at')", source)
        code = source.split('"""', 2)[2]      # 説明文を除いた実コード
        self.assertNotIn("get('updated_at')", code)


if __name__ == '__main__':
    unittest.main()
