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
import re
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import data_freshness as df

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def body_of(src, header):
    """関数の本文だけを切り出す。次のトップレベル def で打ち切る。

    ⚠️ **文字数で窓を切らないこと。** 短い関数だと窓が次の関数まで食い込み、
       隣の関数のガードを拾って合格してしまう（2026-08-30 に実際に起きた形）。
    """
    body = src.split(header, 1)[1]
    cut = re.search(r'\n(?=(def |@app\.route|class ))', body)
    return body[:cut.start()] if cut else body


def code_of(src, header):
    # 関数の本文から docstring とコメントを落とし、コードだけを返す。
    #
    # ⚠️ **本文をそのまま検索すると、注意書きに書いた語を実装と取り違える。**
    #    『summary() を呼ばないこと』という docstring が assertNotIn('summary(')
    #    を落とした。禁止事項をコメントに書いた関数ほどこの形にはまるので、
    #    禁止を確かめるテストは必ずコードだけを見ること。
    body = body_of(src, header)
    q = chr(34) * 3
    body = ''.join(body.split(q)[::2])          # docstring を落とす
    nl = chr(10)
    return nl.join(ln for ln in body.split(nl)
                   if not ln.strip().startswith('#'))

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

    def test_asofとageは同じ行から取る(self):
        """as_of に「いちばん新しい更新」、age に「いちばん古い銘柄」を
        入れていたため、画面に「2026-08-31（5営業日前）」という、
        日付とかっこの中が別の銘柄を指す表示が出ていた。
        画面は as_of と age を並べて出すので、必ず同じ値から導くこと。"""
        block = self.src.split("'key': 'price'", 1)[1][:900]
        self.assertIn("'age': business_days_since(newest, now)", block)
        self.assertNotIn("'age': oldest", block)

    def test_古い件数の文言と計算が同じ定数から来ている(self):
        """⚠️ 注記は「4営業日を超えるもの」、計算は「4営業日以上」だった。
        16件と37件という2つの数が並存し、しきい値が36.7件だったため、
        この差だけで赤と黄が入れ替わっていた（2026-09-03）。

        数字を文言に直接書かせないことで、ズレようが無いようにする。
        """
        block = body_of(read('data_freshness.py'), 'def summary(')
        self.assertIn('count_behind(ages)', block)
        self.assertIn('PRICE_STALE_DAYS, behind', block)
        self.assertNotIn('4営業日', block)

    def test_境界はちょうどの日数も含む(self):
        """「%d営業日以上」と書いてあるので、ちょうどの日も入る。"""
        self.assertEqual(1, df.count_behind([df.PRICE_STALE_DAYS]))
        self.assertEqual(0, df.count_behind([df.PRICE_STALE_DAYS - 1]))
        self.assertEqual(0, df.count_behind([None]))

    def test_値が動いていないだけの銘柄も入ることを書いてある(self):
        """⚠️ price_updated_at は株価が変わったときしか動かない。
        ここに入る＝取得できていない、ではない。実測で、値が取れる15件は
        全件がYahooとぴったり一致していた（表示は正しい）。"""
        src = read('data_freshness.py')
        self.assertIn('変わったとき', src)

    def test_株価は件数で判定する(self):
        """いちばん古い1件で判定すると、廃止手前の銘柄が1つあるだけで
        常に警告になる。"""
        block = self.src.split("'key': 'price'", 1)[0][-900:]
        self.assertIn('behind', block)


class SchedulerLivenessTest(unittest.TestCase):
    """定期実行そのものが生きているかを見る行（2026-08-31）。

    ⚠️ `scheduler.running` は start() したかのフラグで、ループが死んでいても
       true のまま返る。実際に見るのは**次回実行時刻が進んでいるか**。
       APScheduler はジョブを投げた時点で予定を先に進めるので、予定が
       過去のまま残っている＝発火していない。

    実例: 2026-08-31、17:15以降の5本が今日の予定のまま止まっていたが、
    running は true、ログにも画面にも何も出ていなかった。
    """

    def setUp(self):
        self.now = datetime(2026, 8, 31, 23, 35, tzinfo=df.JST)

    def item(self, jobs):
        return df.scheduler_item(jobs, self.now)

    def test_予定を大きく過ぎたジョブがあれば赤(self):
        i = self.item([
            {'id': 'gc_dc_evening', 'next_run_time': '2026-08-31T17:15:00+09:00'},
            {'id': 'price_update_morning', 'next_run_time': '2026-09-01T09:25:00+09:00'},
        ])
        self.assertEqual(i['status'], 'bad')
        self.assertIn('gc_dc_evening', i['when_text'])

    def test_全部先の予定なら正常(self):
        i = self.item([
            {'id': 'a', 'next_run_time': '2026-09-01T02:00:00+09:00'},
            {'id': 'b', 'next_run_time': '2026-09-01T09:25:00+09:00'},
        ])
        self.assertEqual(i['status'], 'ok')

    def test_少しの遅れでは赤にしない(self):
        """毎回のtickに数分のずれは出る。そこで赤くすると誰も見なくなる。"""
        late = '2026-08-31T23:25:00+09:00'          # 10分前
        self.assertEqual(self.item([{'id': 'a', 'next_run_time': late}])['status'], 'ok')

    def test_起動していなければ赤(self):
        """ジョブは登録されているのに次回予定が1つも無い＝start()されていない。"""
        i = self.item([{'id': 'a', 'next_run_time': None}])
        self.assertEqual(i['status'], 'bad')

    def test_取得できなければ正常に倒さない(self):
        """ここを ok にすると、状態が読めない時にパネルが緑になる。"""
        self.assertEqual(self.item(None)['status'], 'warn')

    def test_ジョブが1本も無ければ赤(self):
        self.assertEqual(self.item([])['status'], 'bad')

    def test_summaryは渡し忘れを正常に倒さない(self):
        """summary(jobs=...) の渡し忘れが素通りすると、ジョブが死んでも
        画面が緑のままになる。"""
        src = read('data_freshness.py')
        self.assertIn('items = [scheduler_item(jobs, now)]', src)
        self.assertIn("jobs=jobs", read('app.py'))


class JobRunRecordTest(unittest.TestCase):
    """「取れなかった」と「変わらなかった」を区別する（2026-08-31）。

    株価の price_updated_at は**株価が変わったときしか動かない**。
    3回の定期実行がすべて取得0件で終わり、スクリーナーが丸1日前営業日の
    終値を出していたのに、パネルは「98.3%が1営業日以内」で緑寄りだった。
    データの新しさだけを見ていては、この形は永久に見つからない。
    """

    def test_失敗はNoneでなくFalseで返す(self):
        """記録が無い(None)と失敗(False)を同じ扱いにすると、
        テーブルを作った直後に全部赤くなるか、失敗を見逃すかのどちらかになる。"""
        text, ok = df._run_suffix({'ran_at': '2026-08-31T15:20:00+09:00',
                                   'ok': False, 'detail': '取得0/3669件'})
        self.assertIs(ok, False)
        self.assertIn('取得0/3669件', text)

    def test_成功はTrue(self):
        _, ok = df._run_suffix({'ran_at': '2026-08-31T15:20:00+09:00',
                                'ok': True, 'detail': ''})
        self.assertIs(ok, True)

    def test_記録が無ければ何も足さない(self):
        text, ok = df._run_suffix(None)
        self.assertEqual(text, '')
        self.assertIsNone(ok)

    def test_直近の実行が失敗なら株価は赤(self):
        """データが新しく見えても、取れていないなら赤にする。"""
        src = read('data_freshness.py')
        block = src.split("'key': 'price'", 1)[1][:1200]
        self.assertIn("'bad' if price_run_ok is False", block)

    def test_取得0件は例外にする(self):
        """0件を「変化なし」として正常に通すと、誰も気づけない。"""
        src = read('app.py')
        block = body_of(src, 'def scheduled_update_stock_prices')
        self.assertIn('if not prices:', block)
        self.assertIn('raise RuntimeError', block)

    def test_失敗しても実行記録は残す(self):
        src = read('app.py')
        block = body_of(src, 'def scheduled_update_stock_prices')
        self.assertIn("record_job_run('price_update', ok=False", block)

    def test_日足はどの経路でも記録を残す(self):
        """⚠️ 「最後まで通ったとき」だけ書いていたため、2026-09-01 の3:30は
        発火したのに記録が1行も残らなかった。記録が無いことは「異常なし」と
        区別がつかないので、**黙って何もしない回がいちばん見えなくなる**。"""
        block = body_of(read('app.py'), 'def scheduled_update_daily_and_crosses():')
        # スキップして return する経路
        head = block.split('return', 1)[0]
        self.assertIn("record_job_run('daily_and_crosses'", head)
        # 本体が落ちても書く
        self.assertIn('finally:', block)
        self.assertLess(block.index('try:'), block.index('_update_daily_and_recalc'))

    def test_記録の失敗でジョブを止めない(self):
        """見張りが本体を殺すのは本末転倒。テーブル未作成でも動くこと。"""
        src = read('app.py')
        block = body_of(src, 'def record_job_run')
        self.assertIn('except Exception', block)
        self.assertNotIn('raise', block)


class FakeRunClient:
    """job_runs の直近1行だけを返す最小のスタブ。"""

    def __init__(self, run=None):
        self.run = run

    def table(self, _name):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = [self.run] if self.run else []
        return r


class FakeJobsClient:
    """job_id ごとに直近1行を返すスタブ。"""

    def __init__(self, rows):
        self.rows = rows
        self.job = None

    def table(self, _name):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, col, value):
        if col == 'job_id':
            self.job = value
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = [self.rows[self.job]] if self.job in self.rows else []
        return r


class JobStateTest(unittest.TestCase):
    """始まったのに終わらない回を見つける（2026-09-01）。

    ⚠️ **終わりの印だけでは「死んだ」が見えない。** 株価バッチが 9:25 と
    11:45 の2回とも記録を1行も残さなかった。成功でも失敗でもなく、最後まで
    到達しなかったため。記録が無いことは「まだ何もしていない」と区別が
    つかないので、始まりの印を先に置いて、終わりが来ないことを証拠にする。
    """

    def setUp(self):
        self.now = datetime(2026, 9, 1, 14, 30, tzinfo=df.JST)

    def at(self, hour, minute):
        return datetime(2026, 9, 1, hour, minute, tzinfo=df.JST).isoformat()

    def state(self, rows):
        return df.job_state(FakeJobsClient(rows), 'price_update', self.now)[0]

    def test_始まったまま終わらなければ死んだとみなす(self):
        self.assertEqual(self.state(
            {'price_update:start': {'ran_at': self.at(11, 45), 'ok': True}}), 'hung')

    def test_走っている最中は正常(self):
        """実行中を赤くすると、毎回の実行で赤くなって誰も見なくなる。"""
        self.assertEqual(self.state(
            {'price_update:start': {'ran_at': self.at(14, 25), 'ok': True}}), 'running')

    def test_終わっていれば結果で判断する(self):
        base = {'price_update:start': {'ran_at': self.at(11, 45), 'ok': True}}
        self.assertEqual(self.state(
            dict(base, **{'price_update': {'ran_at': self.at(11, 58), 'ok': True}})), 'ok')
        self.assertEqual(self.state(
            dict(base, **{'price_update': {'ran_at': self.at(11, 47), 'ok': False}})), 'failed')

    def test_記録が無ければ何も言わない(self):
        self.assertEqual(self.state({}), 'none')

    def test_死んだ回は503にする(self):
        rows = {'price_update:start': {'ran_at': self.at(11, 45), 'ok': True}}
        jobs = [{'id': 'a', 'next_run_time': '2026-09-01T15:20:00+09:00'}]
        self.assertEqual(
            df.health(jobs, client=FakeJobsClient(rows), now=self.now), (False, 'price'))

    def test_開始の印を別のjob_idで残す(self):
        """終わりの印と同じ job_id にすると「直近の実行」の意味が変わる。

        印は claim_job が置く（二重起動の門と同じ場所にまとめてある）。
        置く位置は ClaimJobTest が見ている。"""
        block = body_of(read('app.py'), 'def claim_job(job_id):')
        self.assertIn("record_job_run(job_id + ':start'", block)
        # 印を置いてから判定すること。逆だと自分の印を数えられない。
        self.assertLess(block.index("':start'"), block.index('JOB_CLAIM_WAIT_SECONDS'))

    def test_門を通るジョブは開始の印が必ず残る(self):
        """claim_job を呼べば印は自動で残る。呼び忘れたジョブは
        「途中で死んだ」が見えないままになる。"""
        src = read('app.py')
        for fn in ('def scheduled_update_stock_prices(',
                   'def scheduled_update_daily_and_crosses():'):
            with self.subTest(fn=fn):
                self.assertIn('claim_job(', body_of(src, fn))


class ClaimJobTest(unittest.TestCase):
    """同じジョブが同時に何本も走らないこと（2026-09-01）。

    15:20の株価バッチで開始の印が**3秒以内に3つ**残った。同じ処理が3本
    同時に走り、Yahooへ3倍のリクエストを投げていた（その日のレート制限の
    原因として有力）。gunicorn は --workers の指定が無いと Render の
    WEB_CONCURRENCY を見るため、worker が増えるとスケジューラも増える。

    ⚠️ 起動コマンドは直したが、**設定は人が変えられる**のでDB側にも門を置く。
    """

    def setUp(self):
        self.src = read('app.py')

    def test_株価バッチは門を通る(self):
        block = body_of(self.src, 'def scheduled_update_stock_prices(')
        self.assertIn("claim_job('price_update')", block)
        # 門は取得を始める前にあること
        self.assertLess(block.index('claim_job('), block.index('fetch_prices_batch('))

    def test_日足も門を通る(self):
        block = body_of(self.src, 'def scheduled_update_daily_and_crosses():')
        self.assertIn("claim_job('daily_and_crosses')", block)
        self.assertLess(block.index('claim_job('),
                        block.index('_update_daily_and_recalc_background()'))

    def test_降りるときは自分の印を消す(self):
        """⚠️ 残すと「始まったのに終わらない」に見えて hung と誤検知される。
        実際には走っていないので印も残さない。"""
        block = body_of(self.src, 'def claim_job(job_id):')
        tail = block.split('降ります', 1)[1]
        self.assertIn(".delete()", tail)
        self.assertIn("eq('id', mine['id'])", tail)

    def test_記録できないときは本体を止めない(self):
        """見張りが本体を殺すのは本末転倒。"""
        block = body_of(self.src, 'def claim_job(job_id):')
        self.assertIn('if not mine:', block)
        self.assertIn('return True', block.split('if not mine:', 1)[1][:120])

    def test_前回の実行の印を数えない(self):
        """窓の中に前回の開始が残っていると、毎回「他で実行中」になって
        いつまでも動かなくなる。"""
        block = body_of(self.src, 'def claim_job(job_id):')
        self.assertIn('last_job_finish(job_id)', block)

    def test_順番が確定するまで待つ(self):
        """同時に立ち上がった印が出そろう前に判定すると、全員が勝つ。"""
        block = body_of(self.src, 'def claim_job(job_id):')
        self.assertIn('JOB_CLAIM_WAIT_SECONDS', block)


class HealthEndpointTest(unittest.TestCase):
    """止まったら503を返す口（2026-09-01）。

    鮮度パネルは管理画面を**開かないと見えない**。2026-08-31、株価が丸1日
    止まっていたのに気づいたのは翌日だった。UptimeRobot が5分おきに
    /health/db を叩いているので、そこに相乗りする。
    """

    def setUp(self):
        self.now = datetime(2026, 9, 1, 8, 0, tzinfo=df.JST)
        self.healthy = [{'id': 'a', 'next_run_time': '2026-09-01T09:25:00+09:00'}]

    def call(self, jobs, run):
        return df.health(jobs, client=FakeRunClient(run), now=self.now)

    def test_スケジューラが止まっていたら異常(self):
        stalled = [{'id': 'a', 'next_run_time': '2026-08-31T17:15:00+09:00'}]
        self.assertEqual(self.call(stalled, None), (False, 'scheduler'))

    def test_直近の取得が失敗していたら異常(self):
        run = {'ran_at': '2026-09-01T09:25:00+09:00', 'ok': False}
        self.assertEqual(self.call(self.healthy, run), (False, 'price'))

    def test_取得が何日も成功していなければ異常(self):
        run = {'ran_at': '2026-08-26T09:25:00+09:00', 'ok': True}
        self.assertEqual(self.call(self.healthy, run), (False, 'price'))

    def test_正常なら正常(self):
        run = {'ran_at': '2026-09-01T09:25:00+09:00', 'ok': True}
        self.assertEqual(self.call(self.healthy, run), (True, None))

    def test_状態が読めないときも鳴らす(self):
        """⚠️ 「読めなかった」を200で返すと、読めなくなった時点で監視が
        黙って無効になる。最初これを bad だけで判定していて素通りしていた。"""
        self.assertEqual(self.call(None, None), (False, 'scheduler'))

    def test_未起動でも鳴らす(self):
        """start() していないジョブには next_run_time が無い。属性で直に
        読むと例外→「読めなかった」に化けて、未起動が分からなくなる。"""
        self.assertEqual(self.call([{'id': 'a', 'next_run_time': None}], None),
                         (False, 'scheduler'))
        block = body_of(read('app.py'), 'def _jobs_health():')
        self.assertIn("getattr(j, 'next_run_time', None)", block)

    def test_実行記録がまだ無いだけでは鳴らさない(self):
        """テーブルを作った直後・初回実行前がこれ。ここで鳴らすと
        「いつも赤い監視」になり、誰も見なくなる。"""
        self.assertEqual(self.call(self.healthy, None), (True, None))

    def test_重い集計を呼ばない(self):
        """5分おきに叩かれる口。summary() は screened_latest を3,669行読む。"""
        block = code_of(read('data_freshness.py'), 'def health(jobs')
        self.assertNotIn('summary(', block)
        self.assertNotIn('_core_rows(', block)

    def test_判定できなければ503(self):
        """読めないことを ok として返すと、監視そのものが黙って無効になる。"""
        block = body_of(read('app.py'), 'def health_jobs():')
        self.assertIn('return jsonify({"status": "error"}), 503', block)

    def test_件数を漏らさない(self):
        """未ログインで叩ける口。別アプリで /api/health/db が誰でも
        会員数を返していた例がある。"""
        block = code_of(read('app.py'), 'def health_jobs():')
        self.assertNotIn('count', block)
        self.assertNotIn('total', block)

    def test_未ログインで叩ける(self):
        src = read('app.py')
        head = src.split("@app.route('/health/jobs'", 1)[1][:200]
        self.assertNotIn('@admin_required', head)
        self.assertNotIn('@login_required', head)


class HealthDbCarriesJobsTest(unittest.TestCase):
    """外形監視の枠が1つしか無いので /health/db に相乗りさせる（2026-09-01）。

    UptimeRobot の無料枠で監視を1本しか置けないため、既に5分おきに叩かれて
    いる /health/db が定期実行の異常も知らせる。監視が赤い間も UptimeRobot は
    叩き続けるので、Supabase のキープアライブは効き続ける。
    """

    def setUp(self):
        self.src = read('app.py')
        self.block = body_of(self.src, 'def health_db():')

    def test_定期実行の異常でも503(self):
        self.assertIn('_jobs_health()', self.block)
        self.assertIn('"status": "stale"', self.block)

    def test_先にDBへ触ってからにする(self):
        """⚠️ 順番が要。キープアライブが本来の目的なので、
        定期実行の判定で先に return すると DB に触らない回ができる。"""
        db_at = self.block.index("table('watched_tickers')")
        jobs_at = self.block.index('_jobs_health()')
        self.assertLess(db_at, jobs_at)

    def test_判定できなければ503(self):
        """読めないことを ok として返すと、監視そのものが黙って無効になる。"""
        tail = self.block.rsplit('except Exception as e:', 1)[1]
        self.assertIn('503', tail)

    def test_どちらが原因か本文で分かる(self):
        """このURLが赤いとき、DB不達と定期実行停止のどちらもありうる。"""
        self.assertIn('"problem": "db"', self.block)
        self.assertIn('"problem": problem', self.block)

    def test_例外文を外に出さない(self):
        """⚠️ 未ログインで叩ける口。接続エラーの本文には接続先ホストや
        ライブラリ名が出るので、内部構成が読める。原因はサーバー側の
        ログに残し、外へ出すのは「届かない」だけでよい
        （監視は status と problem しか見ていない）。"""
        # 返す本文に例外を混ぜていないこと
        for chunk in self.block.split('return jsonify(')[1:]:
            body = chunk.split(')', 1)[0]
            self.assertNotIn('str(e)', body)
            self.assertNotIn('detail', body)
        # ただし原因は必ずログに残す（黙って捨てない）
        self.assertIn('print(', self.block)

    def test_判定は1か所にまとめる(self):
        """2つの口が別々に判定していると、片方だけ直る事故が起きる。"""
        self.assertEqual(self.src.count('def _jobs_health():'), 1)
        self.assertIn('_jobs_health()', body_of(self.src, 'def health_jobs():'))


class StalePriceBannerTest(unittest.TestCase):
    """古い株価を「今日の株価の顔」で出さない（2026-09-01）。

    取得が失敗すること自体は外部次第で避けきれない。いつ時点の値かを
    書いておけば、見る人の判断は狂わない。外部に依存しない唯一の手当て。
    """

    def test_見るのは取得できた実績(self):
        """⚠️ price_updated_at は**株価が変わったとき**しか動かない。
        取得0件で終わった日も新しく見えるので、帯の判定には使えない。"""
        src = read('data_freshness.py')
        self.assertIn("table('job_runs')", body_of(src, 'def price_as_of('))
        self.assertNotIn('price_updated_at', code_of(src, 'def price_as_of('))

    def test_成功した回だけを見る(self):
        block = body_of(read('data_freshness.py'), 'def price_as_of(')
        self.assertIn("eq('ok', True)", block)

    def test_成功した記録が無くても取得失敗なら出す(self):
        """⚠️ price_as_of だけで出すと、成功した記録が1件も無いときに
        帯が永久に出ない。取得がずっと弾かれている最中に公開すると
        まさにこの形になり、古い株価が何の断りもなく出続ける。"""
        block = code_of(read('models', 'root.py'), 'def inject_price_freshness():')
        self.assertIn('price_fetch_failing()', block)
        self.assertIn('price_fetch_failing', read('templates', 'layout.html'))

    def test_古いときだけ出す(self):
        """平常時にも出していると誰も読まなくなり、本当に古い日に効かない。"""
        self.assertIn('{% if price_stale_as_of %}', read('templates', 'layout.html'))
        block = body_of(read('models', 'root.py'), 'def inject_price_freshness():')
        self.assertIn('PRICE_BANNER_STALE_DAYS', block)

    def test_描画を壊さない(self):
        """全ページの描画を通る。出せないときは何も出さないほうを優先する。"""
        block = body_of(read('models', 'root.py'), 'def inject_price_freshness():')
        self.assertIn('except Exception', block)
        # 例外時は「何も出さない」を返すこと。ここで落ちると全ページが死ぬ。
        self.assertIn('return none', block.rsplit('except Exception', 1)[1])
        self.assertIn("'price_stale_as_of': None", block)


class PriceRetryTest(unittest.TestCase):
    """空振りしたら試し直す（2026-09-01）。

    定期実行は1日3回しかないので、1回落ちると次まで2時間以上開く。
    """

    def test_空振りしたら再試行を積む(self):
        """取得はできたが大半が欠けた回（例外は出ない）も積み直す。
        except 側だけ見ていると、この経路が抜けても気づけない。"""
        block = body_of(read('app.py'), 'def scheduled_update_stock_prices(')
        # ⚠️ 最初の except で切ると、保存の except に当たって窓が短すぎる。
        #    経路を確かめたいので、行そのものを見る。
        self.assertIn('if not ok:' + chr(10) + ' ' * 12
                      + '_schedule_price_retry(attempt)', block)

    def test_例外でも再試行を積む(self):
        """0件は例外にしてあるので、except 側にも無いと再試行が効かない。"""
        block = body_of(read('app.py'), 'def scheduled_update_stock_prices(')
        # 関数の中に except が複数ある。見たいのは一番外側なので後ろから切る。
        tail = block.rsplit('except Exception as e:', 1)[1]
        self.assertIn('_schedule_price_retry(attempt)', tail)

    def test_無限には試さない(self):
        """恒常的に弾かれているとき叩き続けても状況を悪くするだけ。"""
        block = body_of(read('app.py'), 'def _schedule_price_retry(attempt):')
        self.assertIn('if attempt >= PRICE_RETRY_MAX:', block)

    def test_再試行の登録失敗で本体を止めない(self):
        block = body_of(read('app.py'), 'def _schedule_price_retry(attempt):')
        self.assertIn('except Exception', block)

    def test_yfinanceの版を固定する(self):
        """無指定だと再デプロイのたびに別の版が入り、
        「昨日まで動いていたのに」が起きる。"""
        self.assertIn('yfinance==', read('requirements.txt'))


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
