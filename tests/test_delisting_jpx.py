# -*- coding: utf-8 -*-
"""上場廃止の判定にJPXの公式一覧を使う（2026-08-26 導入 / 2026-09-03 作り直し）。

## 決まり

    候補      = JPXの一覧に無い内国株  または  日足が30日以上止まっている
    印を付ける = 候補 かつ JPXの一覧に無い かつ 直近5営業日に値が付かない
    印を外す   = 印つき かつ JPXの一覧に載っている
    一覧が取れない → 何もしない（fail-closed）

## なぜこの形か（全部、実際に踏んだ）

- **日足だけで絞ると PRO Market を巻き込む。** 売買が成立しない日が続くのが
  正常な市場で、実測では止まっている52件が全件 PRO Market だった。
  2026-07-17 には約40社を一斉に「上場廃止」と誤判定している。
- **1年ぶんの足で生死を見ると、廃止から1年は「上場中」を返し続ける。**
  そのせいで廃止直後の8社に永遠に印が付かなかった。∴ 生死は直近5営業日で見る。
  PRO Market を巻き込まないのは、**先にJPXの一覧で落としているから**（順番が要る）。
- **一覧が取れないときに条件を落とすと、正しく付いた印を全部外す。**
  2026-09-03、JPXが .xls → .xlsx に変えて一覧が404になり、40件を外す判定に
  なっていた。⚠️ 以前のテストはこの fail-open を**通していた**
  （`if listed:` という文字列があることしか見ていなかった）。
  ∴ ここでは文字列ではなく**実際に呼んで結果を見る**。
"""

import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import delisting  # noqa: E402
import detect_delisted  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def bar(day, volume=100):
    """日足1本。day は 'YYYY-MM-DD'。"""
    y, m, d = (int(x) for x in day.split('-'))
    from datetime import datetime
    return {'time': int(datetime(y, m, d, 15, 0, tzinfo=delisting.JST).timestamp()),
            'volume': volume, 'close': 100.0}


class FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self._range = (0, len(rows))

    def select(self, *_a, **_k):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        start, end = self._range
        return SimpleNamespace(data=self.rows[start:end + 1])


class FakeClient:
    """screened_latest と stock_price_history だけを持つ最小のクライアント。"""

    def __init__(self, screened, history):
        self._tables = {'screened_latest': screened,
                        'stock_price_history': history}

    def table(self, name):
        return FakeTable(self._tables[name])


def plan(screened, history, listed, alive=(), today=date(2026, 9, 3)):
    """listed を差し替えて plan_changes を呼ぶ。"""
    original = detect_delisted.listed_codes
    detect_delisted.listed_codes = lambda: listed
    try:
        return detect_delisted.plan_changes(
            FakeClient(screened, history), today=today, verbose=False,
            probe=lambda codes: {c for c in codes if c in alive})
    finally:
        detect_delisted.listed_codes = original


def row(code, name='テスト', segment='スタンダード', delisted_at=None):
    return {'company_code': code, 'company_name': name,
            'market_segment': segment, 'delisted_at': delisted_at}


def hist(code, days):
    return {'company_code': code, 'daily_1y': [bar(d) for d in days]}


class 一覧が取れないとき(unittest.TestCase):
    """⚠️ ここが fail-open だと、正しく付いた印を全部外す。"""

    def test_Noneなら判定を中止する(self):
        with self.assertRaises(detect_delisted.ListingUnavailable):
            plan([row('7203')], [hist('7203', ['2026-09-02'])], listed=None)

    def test_空の一覧でも中止する(self):
        """空を「全銘柄が載っていない」と読むと、全部を廃止にする。"""
        with self.assertRaises(detect_delisted.ListingUnavailable):
            plan([row('7203')], [hist('7203', ['2026-09-02'])], listed=set())


class 印を付ける条件(unittest.TestCase):

    def test_一覧に無く値も付かなければ印を付ける(self):
        to_mark, _, held = plan(
            [row('8283', 'ＰＡＬＴＡＣ')],
            [hist('8283', ['2026-08-07'])],
            listed={'7203'}, alive=())
        self.assertEqual(['8283'], [c for c, _n, _s in to_mark])
        self.assertEqual([], held)

    def test_一覧に載っていれば印を付けない(self):
        """PRO Market は売買が無い日が続くのが正常。"""
        to_mark, _, held = plan(
            [row('1432', '動力', segment='スタンダード')],
            [hist('1432', ['2026-06-01'])],
            listed={'1432'}, alive=())
        self.assertEqual([], to_mark)
        self.assertEqual([], held)

    def test_一覧に無くても値が付くなら保留する(self):
        """コード変更や廃止直前の可能性がある。自動では触らない。"""
        to_mark, _, held = plan(
            [row('2162', 'ｎｍｓ')],
            [hist('2162', ['2026-08-27'])],
            listed={'7203'}, alive={'2162'})
        self.assertEqual([], to_mark)
        self.assertEqual(['2162'], [c for c, _n in held])

    def test_内国株以外は対象にしない(self):
        to_mark, _, _ = plan(
            [row('1306', 'ＴＯＰＩＸ連動型', segment='ETF・ETN')],
            [hist('1306', ['2026-06-01'])],
            listed={'7203'}, alive=())
        self.assertEqual([], to_mark)

    def test_廃止直後でも拾える(self):
        """⚠️ 日足30日の条件だけだと、廃止から30日は候補にすらならない。
        JPXの一覧に無いことを候補の条件に入れてあるので、当日から拾える。"""
        to_mark, _, _ = plan(
            [row('6197', 'ソラスト')],
            [hist('6197', ['2026-09-02'])],   # 昨日まで足がある
            listed={'7203'}, alive=())
        self.assertEqual(['6197'], [c for c, _n, _s in to_mark])


class 印を外す条件(unittest.TestCase):

    def test_一覧に載っていれば印を外す(self):
        _, to_clear, _ = plan(
            [row('1432', '動力', delisted_at='2026-07-17T06:00:00')],
            [hist('1432', ['2026-06-01'])],
            listed={'1432'}, alive=())
        self.assertEqual(['1432'], to_clear)

    def test_外すときに値が付くかは見ない(self):
        """⚠️ PRO Market は上場中でも値が付かない日が続く。
        値で判断すると、正しく外すべき印が残る。"""
        _, to_clear, _ = plan(
            [row('5135', 'ＡＩＲ－Ｕ', delisted_at='2026-07-17T06:00:00')],
            [hist('5135', ['2026-06-01'])],
            listed={'5135'}, alive=())      # 値は付かない
        self.assertEqual(['5135'], to_clear)

    def test_一覧に無いままなら印は残す(self):
        _, to_clear, _ = plan(
            [row('6670', 'ＭＣＪ', delisted_at='2026-06-15T06:00:00')],
            [hist('6670', ['2026-06-15'])],
            listed={'7203'}, alive=())
        self.assertEqual([], to_clear)


class 判定は1か所(unittest.TestCase):
    """⚠️ スケジューラが独自の条件を持っていたせいで、手動スクリプトと
    違う判定をしていた（JPXの一覧を見ておらず、8社を取りこぼしていた）。"""

    def test_スケジューラはplan_changesを呼ぶ(self):
        block = read('app.py').split('def scheduled_detect_delisted(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn('detect_delisted.plan_changes(', block)
        self.assertNotIn('probe_is_alive', block)

    def test_一覧が取れなければスケジューラも中止する(self):
        block = read('app.py').split('def scheduled_detect_delisted(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn('ListingUnavailable', block)

    def test_実行を記録する(self):
        """記録が無いと、動いたのか落ちたのかが外から分からない。"""
        block = read('app.py').split('def scheduled_detect_delisted(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn("record_job_run('detect_delisted'", block)


class JPXの一覧の取り方(unittest.TestCase):

    def setUp(self):
        self.src = read('detect_delisted.py')

    def test_JPXの一覧を見る(self):
        self.assertIn('def listed_codes(', self.src)
        self.assertIn('jpx_master.fetch_all()', self.src)

    def test_companies_jsonを使わない(self):
        """あれはこちらが取ってきたスナップショットで、ETFを意図的に
        外しているので「載っていない＝廃止」にならない。"""
        block = self.src.split('def listed_codes', 1)[1].split('\ndef ', 1)[0]
        self.assertNotIn('companies.json', block)

    def test_ファイル名が変わっても探し直す(self):
        """⚠️ JPXは 2026-09 に data_j.xls を data_j.xlsx に差し替えた。
        固定URLだけだと404で、判定が丸ごと効かなくなる。"""
        src = read('jpx_master.py')
        self.assertIn('data_j.xlsx', src)
        self.assertIn('JPX_INDEX_URL', src)
        self.assertIn('def _download(', src)

    def test_取得失敗を空の一覧にしない(self):
        """空を返すと「全銘柄が一覧に無い」になり、全部を廃止にする。"""
        block = read('jpx_master.py').split('def _download(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn('raise', block)

    def test_誤検出の経緯が残っている(self):
        self.assertIn('PRO Market', self.src)
        self.assertIn('1年', self.src)


class ProMarketIsNotDelistedTest(unittest.TestCase):
    """PRO Market を「売買が無いから廃止」と判定しない。"""

    def test_区分の判定が生きている(self):
        import security_filter as sf
        self.assertFalse(sf.is_non_operating_segment('PRO Market'))

    def test_画面にも説明がある(self):
        html = read('templates', 'stock_detail.html')
        self.assertIn('TOKYO PRO Market', html)


if __name__ == '__main__':
    unittest.main()
