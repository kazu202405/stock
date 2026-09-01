# -*- coding: utf-8 -*-
"""有価証券報告書からの役員・大株主の取り出し（2026-09-01）。

なぜ作ったか:
  役員52.1% / 英語の大株主0.6%。取得元の yahooquery・J-LiC・Strainer が
  日本の中小型株を収録していない。有報には構造化されて入っており、
  247A Ａｉロボティクス（従業員12名）でも取れることを実データで確認した。

⚠️ **「（議案）」の行を混ぜないこと。**
   有報には「現在の役員」と「株主総会に諮る予定の役員（議案）」が両方載る。
   しかも**同じコンテキストIDを共有する**のでIDでは分けられない。
   混ぜると全員が二重になり、まだ就任していない人が現任として出る
   （トヨタは議案側にだけ次期社長が居た）。
"""

import io
import os
import re
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import edinet_report as er

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COLUMNS = ['要素ID', '項目名', 'コンテキストID', '相対年度', '連結・個別',
           '期間・時点', 'ユニットID', '単位', '値']


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def body_of(src, header):
    body = src.split(header, 1)[1]
    cut = re.search(r'\n(?=(def |@app\.route|class ))', body)
    return body[:cut.start()] if cut else body


def code_of(src, header=None):
    # docstring とコメントを落とす。注意書きの語を実装と取り違えないため。
    body = body_of(src, header) if header else src
    q = chr(34) * 3
    body = ''.join(body.split(q)[::2])
    nl = chr(10)
    return nl.join(ln for ln in body.split(nl) if not ln.strip().startswith('#'))


def make_zip(rows):
    """本物と同じ形（UTF-16・タブ区切り・jpcrp本表）のZIPを作る。"""
    lines = ['\t'.join('"%s"' % c for c in COLUMNS)]
    for r in rows:
        lines.append('\t'.join('"%s"' % str(x) for x in r))
    body = '\r\n'.join(lines).encode('utf-16')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('XBRL_TO_CSV/jpaud-aai-cc-001_E00000-000.csv', b'\xff\xfe')
        z.writestr('XBRL_TO_CSV/jpcrp030000-asr-001_E00000-000.csv', body)
    return buf.getvalue()


def officer_rows(ctx, title, name, born, proposal=False):
    """役員1人分の行。proposal=True で「（議案）」側にする。"""
    mark = '（議案）' if proposal else ''
    base = '役員の状況（取締役（及び監査役））' + mark
    return [
        ['x', '役職名、' + base, ctx, '', '', '', '', '', title],
        ['x', '氏名、' + base, ctx, '', '', '', '', '', name],
        ['x', '生年月日、' + base, ctx, '', '', '', '', '', born],
    ]


class ProposalTest(unittest.TestCase):
    """⚠️ ここが本丸。現任と議案は同じコンテキストIDを共有する。"""

    def test_昇任予定の役職で現任を上書きしない(self):
        """同じ人が現任と議案の両方に載り、**役職だけが違う**ことがある
        （昇任の議案）。コンテキストが同じなので、素直に読むと後から来た
        議案の役職で上書きされ、まだ就いていない役職が現任として出る。"""
        rows = (officer_rows('CtxA', '取締役副社長', '豊  田  章  男', '1956-05-03')
                + officer_rows('CtxA', '取締役社長（代表取締役）', '豊  田  章  男',
                               '1956-05-03', proposal=True))
        got = er.extract_officers(er.read_csv(make_zip(rows)))
        self.assertEqual(len(got), 1, '同じ人が二重になっている')
        self.assertEqual(got[0]['title_jp'], '取締役副社長',
                         '議案の役職で現任が上書きされている')

    def test_議案にしか居ない人は出さない(self):
        """まだ就任していない人を現任として出さない
        （トヨタは議案側にだけ次期社長が居た）。"""
        rows = (officer_rows('CtxA', '取締役会長', '豊  田  章  男', '1956-05-03')
                + officer_rows('CtxB', '取締役社長', '近　　　健  太', '1968-08-02',
                               proposal=True))
        got = er.extract_officers(er.read_csv(make_zip(rows)))
        self.assertEqual([o['name_jp'] for o in got], ['豊田章男'])

    def test_判定は項目名で行う(self):
        """コンテキストIDで分けようとすると必ず失敗する（共有されているため）。"""
        block = code_of(read('edinet_report.py'), 'def _is_current(')
        self.assertIn('PROPOSAL_MARK', block)


class OfficerTest(unittest.TestCase):

    def test_画面が読むキーで返す(self):
        """updateCompanyOfficers は name_jp / title_jp / age を読む。"""
        rows = officer_rows('CtxA', '取締役会長（代表取締役）', '豊  田  章  男', '1956-05-03')
        got = er.extract_officers(er.read_csv(make_zip(rows)))[0]
        self.assertIn('name_jp', got)
        self.assertIn('title_jp', got)
        self.assertIn('age', got)
        self.assertEqual(got['yearBorn'], 1956)

    def test_字間の空白を詰める(self):
        """有報は「豊  田  章  男」のように字間を空けて書く。
        そのままだと検索にも表示にも噛み合わない。"""
        self.assertEqual(er._clean_name('豊  田  章  男'), '豊田章男')
        self.assertEqual(er._clean_name('近　　　健  太  '), '近健太')

    def test_英字の氏名は詰めない(self):
        """1文字ずつではないので、語の区切りを潰してはいけない。"""
        self.assertEqual(er._clean_name('George  Olcott'), 'George Olcott')

    def test_生年月日が無くても落ちない(self):
        rows = [['x', '役職名、役員の状況（取締役（及び監査役））', 'C', '', '', '', '', '', '取締役'],
                ['x', '氏名、役員の状況（取締役（及び監査役））', 'C', '', '', '', '', '', '山田太郎']]
        got = er.extract_officers(er.read_csv(make_zip(rows)))
        self.assertEqual(len(got), 1)
        self.assertNotIn('age', got[0])


class HolderTest(unittest.TestCase):

    def setUp(self):
        self.rows = [
            ['x', '氏名又は名称、大株主の状況', 'CurrentYearInstant_No1Major',
             '', '', '', '', '', '日本マスタートラスト信託銀行㈱'],
            ['x', '所有株式数', 'CurrentYearInstant_No1Major',
             '', '', '', '', '', '1667971000'],
            ['x', '発行済株式（自己株式を除く。）の総数に対する所有株式数の割合',
             'CurrentYearInstant_No1Major', '', '', '', '', '', '0.1280'],
        ]

    def test_既存の列と同じ形で返す(self):
        """major_shareholders_jp は [{name, shares, ratio}]。"""
        got = er.extract_major_holders(er.read_csv(make_zip(self.rows)))[0]
        self.assertEqual(set(got), {'name', 'shares', 'ratio'})
        self.assertEqual(got['name'], '日本マスタートラスト信託銀行㈱')
        self.assertEqual(got['shares'], 1667971000)

    def test_割合は百分率にする(self):
        """⚠️ 有報は 0.1280 のような小数で持っている。そのまま入れると
        0.128% になり、桁が2つずれる。"""
        got = er.extract_major_holders(er.read_csv(make_zip(self.rows)))[0]
        self.assertEqual(got['ratio'], 12.8)

    def test_自己株式は大株主に混ぜない(self):
        """「所有者の氏名又は名称、自己株式等」は別物。
        混ぜるとグループ会社が大株主として並ぶ。"""
        rows = self.rows + [
            ['x', '所有者の氏名又は名称、自己株式等', 'CurrentYearInstant_Row1Member',
             '', '', '', '', '', 'トヨタ自動車㈱'],
        ]
        got = er.extract_major_holders(er.read_csv(make_zip(rows)))
        self.assertEqual([h['name'] for h in got], ['日本マスタートラスト信託銀行㈱'])


class CsvTest(unittest.TestCase):

    def test_本表を読む(self):
        """⚠️ 監査報告書（jpaud-）ではなく本表（jpcrp-）を読むこと。"""
        rows = er.read_csv(make_zip(officer_rows('C', '取締役', '山田太郎', '1970-01-01')))
        self.assertTrue(rows)

    def test_UTF16で読む(self):
        """cp932 で読むと全滅する。"""
        self.assertIn("decode('utf-16')", read('edinet_report.py'))

    def test_従業員数(self):
        rows = [['x', '従業員数', 'C', '', '', '', '', '', '70710']]
        self.assertEqual(er.extract_employees(er.read_csv(make_zip(rows))), 70710)


class BackfillSafetyTest(unittest.TestCase):

    def test_取れなかった項目は触らない(self):
        """⚠️ 空で上書きすると、別経路で入っていた正常値が消える。"""
        import backfill_edinet_reports as bf
        self.assertEqual(bf.updates_for({}, {'company_officers': [],
                                             'major_shareholders_jp': [],
                                             'employees': None}), {})

    def test_取れたものだけ入る(self):
        import backfill_edinet_reports as bf
        got = bf.updates_for({}, {'company_officers': [{'name_jp': 'x'}],
                                  'major_shareholders_jp': [],
                                  'employees': 12})
        self.assertEqual(set(got), {'company_officers', 'employees'})

    def test_認証エラーを成功として扱わない(self):
        """⚠️ 公式APIは認証エラーでもHTTP 200を返し、本文のStatusCodeが401。
        status_code だけ見ると「全件成功なのに中身が空」になる。"""
        block = code_of(read('backfill_edinet_reports.py'), 'def _get_json(')
        self.assertIn("status != '200'", block)
        self.assertIn('raise RuntimeError', block)

    def test_ZIP以外が返ったら止まる(self):
        block = code_of(read('backfill_edinet_reports.py'), 'def fetch_report(')
        self.assertIn("!= b'PK'", block)


if __name__ == '__main__':
    unittest.main()
