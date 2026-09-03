# -*- coding: utf-8 -*-
"""有報の取り込みを毎晩続ける仕組み（2026-09-02）。

なぜ作ったか:
  2026-09-01 に一括で入れて役員99.7%まで埋めたが、**有報は年に1回出る**。
  放っておくと来年の有報で古くなるし、新規上場も拾えない。

⚠️ **判定は対象決算期（periodEnd）で行う。** 提出日ではない。訂正報告書や
   再提出で提出日は動くが、対象の決算期は変わらない。提出日で見ると、
   訂正が出るたびに全社を取り直すことになる。
"""

import os
import re
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import edinet_api
import edinet_sync

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def body_of(src, header):
    body = src.split(header, 1)[1]
    cut = re.search(r'\n(?=(def |@app\.route|class ))', body)
    return body[:cut.start()] if cut else body


def code_of(src, header=None):
    # docstring とコメントを落とす（注意書きの語を実装と取り違えないため）
    body = body_of(src, header) if header else src
    q = chr(34) * 3
    body = ''.join(body.split(q)[::2])
    nl = chr(10)
    return nl.join(ln for ln in body.split(nl) if not ln.strip().startswith('#'))


def doc(code, period_end, doc_id='S1'):
    return {'secCode': code + '0', 'docID': doc_id, 'periodEnd': period_end,
            'docTypeCode': '120'}


class FindNewReportsTest(unittest.TestCase):
    """どれを取り直すかの判定。"""

    def setUp(self):
        self.today = date(2026, 9, 2)
        self.calls = []

        def fake(day, key=None):
            self.calls.append(day)
            return dict(self.docs)
        self._real = edinet_api.annual_reports
        edinet_api.annual_reports = fake
        self.docs = {}

    def tearDown(self):
        edinet_api.annual_reports = self._real

    def find(self, state):
        return edinet_sync.find_new_reports(state, key='x', days=1,
                                            today=self.today, log=lambda *a: None)

    def test_未取得なら取り込む(self):
        self.docs = {'7203': doc('7203', '2026-03-31')}
        self.assertIn('7203', self.find({}))

    def test_同じ決算期なら取り直さない(self):
        """⚠️ 訂正報告書や再提出で提出日は動くが、対象の決算期は変わらない。
        提出日で見ると、訂正が出るたびに取り直すことになる。"""
        self.docs = {'7203': doc('7203', '2026-03-31', 'S2')}
        state = {'7203': {'doc_id': 'S1', 'period_end': date(2026, 3, 31)}}
        self.assertEqual(self.find(state), {})

    def test_新しい決算期なら取り直す(self):
        self.docs = {'7203': doc('7203', '2027-03-31')}
        state = {'7203': {'doc_id': 'S1', 'period_end': date(2026, 3, 31)}}
        self.assertIn('7203', self.find(state))

    def test_土日は叩かない(self):
        """提出は平日にしか無い。休日に一覧を引くのは相手への無駄な負荷。"""
        self.docs = {'7203': doc('7203', '2026-03-31')}
        edinet_sync.find_new_reports({}, key='x', days=3,
                                     today=date(2026, 9, 7),   # 月曜
                                     log=lambda *a: None)
        # 9/4(金) 9/5(土) 9/6(日) → 土日は飛ばして金曜だけ
        self.assertEqual(self.calls, ['2026-09-04'])

    def test_一覧が取れなくても他の日を続ける(self):
        """1日ぶん取れないだけで、その晩まるごと諦めない。"""
        def boom(day, key=None):
            raise RuntimeError('だめ')
        edinet_api.annual_reports = boom
        self.assertEqual(self.find({}), {})     # 例外を外に出さない


class SaveTest(unittest.TestCase):

    class FakeClient:
        def __init__(self):
            self.writes = []
            self._table = None
            self._payload = None

        def table(self, name):
            self._table = name
            return self

        def update(self, payload):
            self._payload = payload
            return self

        def eq(self, *a):
            return self

        def execute(self):
            self.writes.append((self._table, self._payload))
            class R:
                data = []
            return R()

    def test_中身が取れたら本体と印を両方書く(self):
        c = self.FakeClient()
        ok = edinet_sync.save_company(c, '7203', {'docID': 'S1', 'periodEnd': '2026-03-31'},
                                      {'company_officers': [{'name_jp': 'x'}],
                                       'major_shareholders_jp': [], 'employees': 10})
        self.assertTrue(ok)
        self.assertEqual([t for t, _ in c.writes], ['screened_latest', 'edinet_codes'])

    def test_取れなかったら印を付けない(self):
        """⚠️ 中身が無いのに印を付けると、その会社は次の有報まで
        二度と試されない。"""
        c = self.FakeClient()
        ok = edinet_sync.save_company(c, '7203', {'docID': 'S1', 'periodEnd': '2026-03-31'},
                                      {'company_officers': [],
                                       'major_shareholders_jp': [], 'employees': None})
        self.assertFalse(ok)
        self.assertEqual(c.writes, [])

    def test_取れなかった項目は触らない(self):
        c = self.FakeClient()
        edinet_sync.save_company(c, '7203', {'docID': 'S1', 'periodEnd': '2026-03-31'},
                                 {'company_officers': [{'name_jp': 'x'}],
                                  'major_shareholders_jp': [], 'employees': None})
        payload = dict(c.writes)['screened_latest']
        self.assertEqual(set(payload), {'company_officers'})


class BacklogTest(unittest.TestCase):

    def test_積み残しは処理せず数えるだけ(self):
        """⚠️ 拾うふりをして黙って飛ばすと、いつまでも埋まらないのに
        「毎晩動いている」ように見える。docIDを引くには325日ぶんの走査が
        要るので、初回の穴埋めは一括スクリプトの担当。"""
        block = code_of(read('edinet_sync.py'), 'def run(')
        self.assertIn('count_backlog(', block)
        self.assertNotIn('pick_backlog(', block)
        # 積み残しの件数は外へ返して、見えるようにする
        self.assertIn("'backlog': backlog", block)


class 積み残しが減る条件(unittest.TestCase):
    """⚠️ 2026-09-03 まで、積み残しは何をしても19社のまま動かなかった。

    毎晩の edinet_sync は `edinet_codes.report_doc_id` の有無で数える。
    一括スクリプト（backfill_edinet_reports.py）は screened_latest に中身だけ
    書いて、この印を書いていなかった。∴ 何社取り込んでも件数が動かない。

    担当が2つに分かれているとき、**進捗の印は片方だけが書けばよいのではなく、
    数える側が見る場所に書く**こと。
    """

    def test_一括スクリプトも取り込み済みの印を書く(self):
        src = read('backfill_edinet_reports.py')
        self.assertIn('def mark_ingested(', src)
        block = code_of(src, 'def mark_ingested(')
        # 数える側が見るテーブルと列に書くこと
        self.assertIn("table('edinet_codes')", block)
        self.assertIn('report_doc_id', block)

    def test_中身を書いたら印も書く(self):
        """本体を書いた直後に印を書く。片方だけ通る道を作らない。

        ⚠️ 「どこかに mark_ingested がある」だけを見ると、別の分岐にだけ
           残っていても合格してしまう。本体を書く行より後ろに限って見る。
        """
        block = code_of(read('backfill_edinet_reports.py'), 'def main(')
        after = block.split("table('screened_latest').update(updates)", 1)[1]
        after = after.split('ok += 1', 1)[0]
        self.assertIn('mark_ingested(', after)

    def test_取れる項目が無くても印は書く(self):
        """有報は読めている（既に埋まっているだけ）。ここで印を書かないと
        永久に積み残しに残り、毎晩同じ会社を数え続ける。"""
        block = code_of(read('backfill_edinet_reports.py'), 'def main(')
        head = block.split('if not updates:', 1)[1].split('continue', 1)[0]
        self.assertIn('mark_ingested(', head)

    def test_印が書けなくても取り込みは落とさない(self):
        block = code_of(read('backfill_edinet_reports.py'), 'def mark_ingested(')
        self.assertIn('except Exception', block)
        # ただし黙って飛ばさない（同じことが起きる）
        self.assertIn('print(', block)


class 積み残しの数え方(unittest.TestCase):

    def test_種類株式は数えない(self):
        """⚠️ 5桁コードは種類株式（伊藤園第1種優先株式・ソフトバンク第1回
        社債型種類株式など）で、会社ではないので有報を出すことがない。
        数え続けると「毎晩やっているのに永久に減らない件数」になる。
        2026-09-03 時点で、積み残し10件のうち6件がこれだった。"""
        block = code_of(read('edinet_sync.py'), 'def count_backlog(')
        self.assertIn('is_class_share', block)

    def test_種類株式の判定が生きている(self):
        import security_filter as sf
        self.assertTrue(sf.is_class_share('25935'))
        self.assertTrue(sf.is_class_share('94345'))
        self.assertFalse(sf.is_class_share('7203'))
        self.assertFalse(sf.is_class_share('407A'))

    def test_上場廃止も数えない(self):
        block = code_of(read('edinet_sync.py'), 'def count_backlog(')
        self.assertIn('delisted_at', block)


class SchedulerTest(unittest.TestCase):

    def setUp(self):
        self.src = read('app.py')

    def test_毎晩登録されている(self):
        self.assertIn("id='edinet_reports'", self.src)

    def test_二重起動の門を通る(self):
        block = body_of(self.src, 'def scheduled_sync_edinet_reports():')
        self.assertIn("claim_job('edinet_reports')", block)

    def test_新しい有報が無い日を失敗にしない(self):
        """⚠️ ふだんは0社が正常。そこを赤くするとパネルが信用されなくなる。"""
        block = body_of(self.src, 'def scheduled_sync_edinet_reports():')
        self.assertIn("ok=(r['failed'] == 0)", block)

    def test_他のジョブと時間が重ならない(self):
        """同じ時刻に置くと、無料枠のプロセスで外部取得がかち合う。"""
        times = re.findall(r"hour=(\d+), minute=(\d+), id='([a-z_]+)'", self.src)
        slots = [(h, m) for h, m, _ in times]
        self.assertEqual(len(slots), len(set(slots)), '同じ時刻のジョブがある: %s' % times)


class ApiGuardTest(unittest.TestCase):

    def test_本文のStatusCodeを見る(self):
        """⚠️ 認証エラーでもHTTP 200が返る。status_code で判定すると
        「全件成功なのに中身が空」になる。"""
        block = code_of(read('edinet_api.py'), 'def _check(')
        self.assertIn("status != '200'", block)
        self.assertIn('raise EdinetError', block)

    def test_ZIP以外が返ったら止まる(self):
        block = code_of(read('edinet_api.py'), 'def fetch_report(')
        self.assertIn("!= b'PK'", block)

    def test_第三者サービスの鍵と取り違えない(self):
        src = read('edinet_api.py')
        self.assertIn('EDINET_SUBSCRIPTION_KEY', src)
        self.assertNotIn("getenv('EDINET_API_KEY')", src)


if __name__ == '__main__':
    unittest.main()
