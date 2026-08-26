# -*- coding: utf-8 -*-
"""上場廃止の判定にJPXの公式一覧を足す（2026-08-26）。

なぜ足したか:
  「日足が30日以上止まっている」だけで絞ると **PRO Market を巻き込む**。
  TOKYO PRO Market はプロ投資家向けで、売買が成立しない日が続くのが正常。
  実測では止まっている52件が**全件 PRO Market**で、上場廃止は1件も無かった。

  逆に `probe_is_alive`（yfinanceに値があるか）は**単独では信用できない**。
  1年ぶんの足を見るので、**廃止から1年経つまで「上場中」を返し続ける**。
  2026年6月に廃止された18件を「上場中」と誤判定していた。

  ∴ 「足が止まっている」×「JPXの一覧に無い」の2つが揃ったときだけ印を付ける。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class DetectorTest(unittest.TestCase):

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

    def test_一覧に載っている銘柄は候補から外す(self):
        block = self.src.split('def main(', 1)[1]
        self.assertIn('c[0] not in listed', block)

    def test_一覧を取れなければこの条件を使わない(self):
        """取得に失敗しただけで全銘柄を廃止扱いにしない。"""
        block = self.src.split('def listed_codes', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('return None', block)
        main = self.src.split('def main(', 1)[1]
        self.assertIn('if listed:', main)

    def test_印を外すときもJPXを見る(self):
        """⚠️ probe_is_alive は廃止から1年は True を返す。これだけで
        外すと、正しく付いた印を全部外してしまう。"""
        main = self.src.split('def main(', 1)[1]
        self.assertIn('recheck', main)
        self.assertIn('if c in listed', main)

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
