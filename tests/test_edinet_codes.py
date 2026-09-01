# -*- coding: utf-8 -*-
"""EDINETの提出者一覧の取り込み（2026-09-01）。

なぜ作ったか:
  いま設立日・本社・社長・従業員数・業績予想は Yahoo日本版のHTMLに乗っており、
  1晩60件ずつしか進まないうえ、構造が変われば一斉に落ちる（充足率86.8%で頭打ち）。
  EDINETの提出者一覧は**APIキー無しで1リクエスト全件**取れる公式データで、
  証券コード→EDINETコードの対応表（第2段で有報を引くのに必須）も同時に手に入る。

⚠️ **所在地を screened_latest.headquarters に流し込まないこと。**
   あちらは Yahoo日本版の「本社」、こちらは有報の「登記上の本店」で別物。
   6498キッツは 本社=東京都港区東新橋 / 登記上の本店=千葉市美浜区中瀬。
"""

import io
import os
import re
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import edinet_codes as ec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HEADER = ('ＥＤＩＮＥＴコード,提出者種別,上場区分,連結の有無,資本金,決算日,'
          '提出者名,提出者名（英字）,提出者名（ヨミ）,所在地,提出者業種,'
          '証券コード,提出者法人番号')


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def body_of(src, header):
    body = src.split(header, 1)[1]
    cut = re.search(r'\n(?=(def |@app\.route|class ))', body)
    return body[:cut.start()] if cut else body


def code_of(src, header=None):
    # docstring とコメントを落として、コードだけを返す。
    #
    # ⚠️ **注意書きに書いた語を実装と取り違えないため。**
    #    『headquarters に書かないこと』という警告文が、
    #    「headquarters が無いこと」を確かめるテストを落とした（4回目）。
    body = body_of(src, header) if header else src
    q = chr(34) * 3
    body = ''.join(body.split(q)[::2])
    nl = chr(10)
    return nl.join(ln for ln in body.split(nl) if not ln.strip().startswith('#'))

def make_zip(rows):
    """本物と同じ形（1行目は見出しでない・cp932）のZIPを作る。"""
    lines = ['ダウンロード実行日,2026年09月01日現在,件数,%d件' % len(rows), HEADER]
    lines.extend(rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('EdinetcodeDlInfo.csv', '\r\n'.join(lines).encode('cp932'))
    return buf.getvalue()


TOYOTA = ('E02144,内国法人・組合,上場,有,635401,3月31日,トヨタ自動車株式会社,'
          'TOYOTA MOTOR CORPORATION,トヨタジドウシャカブシキカイシャ,'
          '豊田市トヨタ町１番地,輸送用機器,72030,1180301018771')
NEWCODE = ('E12345,内国法人・組合,上場,無,100,12月31日,新規上場株式会社,'
           'NEW INC.,シンキジョウジョウ,港区赤坂一丁目１番１号,情報・通信業,'
           '409A0,9999999999999')
NOCODE = ('E99999,内国法人・組合,非上場,無,50,3月31日,非上場ファンド,'
          'FUND,ファンド,千代田区,その他,,8888888888888')


class CodeConversionTest(unittest.TestCase):
    """EDINETは5桁・末尾0で持っている（実測3,821件すべて）。"""

    def test_数字コード(self):
        self.assertEqual(ec.to_company_code('72030'), '7203')

    def test_英字を含む新形式(self):
        """2024年以降の新形式（409A など）。数字だけを前提にすると落とす。"""
        self.assertEqual(ec.to_company_code('409A0'), '409A')

    def test_5桁でないものは受け取らない(self):
        for bad in ('7203', '720300', '', None, '  '):
            with self.subTest(bad=bad):
                self.assertIsNone(ec.to_company_code(bad))

    def test_末尾が0でなければ受け取らない(self):
        """形が変わったら黙って変換せず、気づけるようにする。"""
        self.assertIsNone(ec.to_company_code('72031'))


class FiscalMonthTest(unittest.TestCase):

    def test_月を取り出す(self):
        self.assertEqual(ec.to_fiscal_month('3月31日'), 3)
        self.assertEqual(ec.to_fiscal_month('12月末日'), 12)

    def test_読めなければNone(self):
        for bad in ('', None, '不明', '31日'):
            with self.subTest(bad=bad):
                self.assertIsNone(ec.to_fiscal_month(bad))

    def test_ありえない月は捨てる(self):
        self.assertIsNone(ec.to_fiscal_month('13月1日'))


class ParseTest(unittest.TestCase):

    def test_1行目を読み飛ばす(self):
        """1行目は「ダウンロード実行日,…」で見出しではない。
        そのまま DictReader に渡すと全件おかしくなる。"""
        rows = ec.parse(make_zip([TOYOTA]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['ＥＤＩＮＥＴコード'], 'E02144')

    def test_cp932を読める(self):
        rows = ec.parse(make_zip([TOYOTA]))
        self.assertEqual(rows[0]['提出者名'], 'トヨタ自動車株式会社')

    def test_証券コードが無い提出者は落とす(self):
        """非上場のファンド等。うちの銘柄と結べない。"""
        rows = ec.to_rows(ec.parse(make_zip([TOYOTA, NOCODE])))
        self.assertEqual([r['company_code'] for r in rows], ['7203'])

    def test_行の中身(self):
        row = ec.to_rows(ec.parse(make_zip([TOYOTA])))[0]
        self.assertEqual(row['edinet_code'], 'E02144')
        self.assertEqual(row['registered_address'], '豊田市トヨタ町１番地')
        self.assertEqual(row['capital'], 635401)
        self.assertEqual(row['fiscal_month'], 3)
        self.assertEqual(row['corporate_number'], '1180301018771')

    def test_新形式コードも取り込める(self):
        rows = ec.to_rows(ec.parse(make_zip([TOYOTA, NEWCODE])))
        self.assertIn('409A', [r['company_code'] for r in rows])


class SyncSafetyTest(unittest.TestCase):
    """壊れた一覧で対応表を潰さないこと。"""

    def test_件数が少なすぎたら書き込まない(self):
        """⚠️ 空やエラーページが返ってきたときに対応表を消さないため。
        「空データで正常値を消さない」はこのリポジトリで何度も踏んでいる。"""
        calls = []

        class Client:
            def table(self, name):
                calls.append(name)
                return self

        with self.assertRaises(ValueError):
            ec.sync(client=Client(), rows=[{'company_code': '7203'}])
        self.assertEqual(calls, [], '書き込みを試みてはいけない')

    def test_下限が実測より十分低い(self):
        """実測3,821件。下限を上げすぎると、正常な回まで弾く。"""
        self.assertLess(ec.MIN_EXPECTED_ROWS, 3000)
        self.assertGreater(ec.MIN_EXPECTED_ROWS, 100)


class DoNotOverwriteTest(unittest.TestCase):
    """本社と登記上の本店を混ぜないこと。"""

    def test_headquartersに書かない(self):
        """⚠️ 6498キッツ: 本社=東京都港区東新橋 / 登記上の本店=千葉市美浜区中瀬。
        混ぜると1つの列に2つの意味が入り、どちらか見分けられなくなる。
        さらにEDINET側は99%が都道府県から始まらない。"""
        src = read('edinet_codes.py')
        self.assertNotIn('headquarters', code_of(src))
        self.assertIn('registered_address', src)

    def test_決算月を上書きしない(self):
        """yfinance由来の値と44件食い違う。どちらが正しいかは
        有報を見るまで決められない。"""
        block = code_of(read('edinet_codes.py'), 'def fiscal_month_mismatches(')
        self.assertNotIn('.update(', block)
        self.assertNotIn('.upsert(', block)


class SchedulerTest(unittest.TestCase):

    def test_週1回で登録されている(self):
        src = read('app.py')
        self.assertIn("id='edinet_codes'", src)
        block = src.split('scheduled_sync_edinet_codes,', 1)[1][:120]
        self.assertIn('day_of_week', block)

    def test_二重起動の門を通る(self):
        block = body_of(read('app.py'), 'def scheduled_sync_edinet_codes():')
        self.assertIn("claim_job('edinet_codes')", block)

    def test_成否を記録する(self):
        block = body_of(read('app.py'), 'def scheduled_sync_edinet_codes():')
        self.assertIn("record_job_run('edinet_codes', ok=False", block)


class MigrationTest(unittest.TestCase):

    def test_対応表の要点(self):
        sql = read('supabase', 'migration_edinet_codes.sql')
        for needed in ('edinet_codes', 'company_code', 'edinet_code',
                       'registered_address', 'corporate_number'):
            with self.subTest(needed=needed):
                self.assertIn(needed, sql)

    def test_RLSを有効にする(self):
        sql = read('supabase', 'migration_edinet_codes.sql')
        self.assertIn('enable row level security', sql)


if __name__ == '__main__':
    unittest.main()
