# -*- coding: utf-8 -*-
"""定期実行が動いているかをデータ側から測る（2026-08-28）。

なぜ作ったか:
  定期実行は14本あるのに**通知も警告も1つも無かった**。
  `/api/scheduler/status` は「次にいつ動くか」しか出せず、どの画面にも
  出ていなかった。ジョブが今夜から静かに止まっても誰も気づけない。
  データが古くなるだけで、画面はエラーを出さず普通に表示され続ける。

⚠️ 見るのは**最後に実際に値が動いた実績**。次回実行時刻ではない。
   予定は、ジョブが空振りしていても正常に見える。
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import data_freshness as df

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class BusinessDaysTest(unittest.TestCase):
    """⚠️ 暦日で数えると月曜の朝に必ず警告が出る（金曜から3日経つため）。"""

    def test_土日をまたいでも営業日で数える(self):
        fri = datetime(2026, 8, 21, 15, 0, tzinfo=df.JST)   # 金
        mon = datetime(2026, 8, 24, 9, 0, tzinfo=df.JST)    # 月
        self.assertEqual(df.business_days_since(fri, mon), 1)

    def test_同じ日はゼロ(self):
        now = datetime(2026, 8, 28, 15, 0, tzinfo=df.JST)
        self.assertEqual(df.business_days_since(now, now), 0)

    def test_Noneで落ちない(self):
        self.assertIsNone(df.business_days_since(None))


class StatusRuleTest(unittest.TestCase):

    def test_測れないものは警告(self):
        """「古さが分からない」を正常に倒すと、止まっても気づけない。"""
        self.assertEqual(df._status(None, 3, 5), 'warn')

    def test_しきい値(self):
        self.assertEqual(df._status(0, 3, 5), 'ok')
        self.assertEqual(df._status(3, 3, 5), 'warn')
        self.assertEqual(df._status(5, 3, 5), 'bad')


class DesignTest(unittest.TestCase):
    """設計上の約束をコードに固定する。"""

    def setUp(self):
        self.src = read('data_freshness.py')

    def test_母数から対象外を外す(self):
        """PRO Marketは売買が成立しない日が続くのが正常。
        混ぜると株価が常に古く見えて、パネルが信用されなくなる。"""
        self.assertIn("CORE_SEGMENTS", self.src)
        self.assertIn("delisted_at", self.src)

    def test_決算は古さで判定しない(self):
        """決算が無い時期に何も処理しないのは正常。
        そこを赤くすると誰も見なくなる。積み残しの件数だけを見る。"""
        block = self.src.split("'key': 'earnings'", 1)[1][:700]
        self.assertIn('pending', block)
        self.assertNotIn("_status(", block.split("'note'")[0])

    def test_GCDCはma_crossesを見る(self):
        """⚠️ signal_stocks は kabutan 由来の旧経路。テクニカル一覧が
        読んでいるのは日足から計算した ma_crosses のほう。
        最初 signal_stocks を見て「22営業日前」と誤検知した。"""
        block = self.src.split("'key': 'signals'", 1)[0][-1200:]
        self.assertIn("table('ma_crosses')", block)

    def test_株価は件数で判定する(self):
        """いちばん古い1件で判定すると、廃止手前の銘柄が1つあるだけで
        常に警告になる。"""
        block = self.src.split("'key': 'price'", 1)[0][-900:]
        self.assertIn('behind', block)


class EndpointTest(unittest.TestCase):

    def setUp(self):
        import app as app_module
        self.app = app_module.app
        self.app.config['TESTING'] = True

    def test_管理者限定(self):
        src = read('app.py')
        block = src.split("@app.route('/api/admin/data-freshness'", 1)[1][:200]
        self.assertIn('@admin_required_api', block)

    def test_未ログインは401(self):
        c = self.app.test_client()
        self.assertEqual(c.get('/api/admin/data-freshness').status_code, 401)

    def test_非管理者は403(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = '11111111-1111-1111-1111-111111111111'
        self.assertEqual(c.get('/api/admin/data-freshness').status_code, 403)


class PanelTest(unittest.TestCase):

    def setUp(self):
        self.html = read('templates', 'stock.html')

    def test_管理者だけに出す(self):
        block = self.html.split('id="freshnessCard"', 1)[0][-400:]
        self.assertIn('{% if is_admin %}', block)

    def test_要素が無ければ何もしない(self):
        """管理者以外のページには要素自体が無い。確認せずに触ると
        描画が止まる（2026-08-26 に実際に起きた形）。"""
        block = self.html.split('async function loadDataFreshness(', 1)[1][:400]
        self.assertIn('if (!card) return;', block)

    def test_本文をエスケープする(self):
        self.assertIn('function freshEscape(', self.html)
        self.assertIn('freshEscape(i.label)', self.html)

    def test_鮮度が出せなくてもダッシュボードを壊さない(self):
        block = self.html.split('async function loadDataFreshness(', 1)[1][:1800]
        self.assertIn('catch', block)


if __name__ == '__main__':
    unittest.main()
