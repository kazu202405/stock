# -*- coding: utf-8 -*-
"""履歴から作るスカラー列を、履歴と一緒に動かす（2026-08-26）。

何が起きていたか:
  `eps` 列が履歴より**1期古い**銘柄が 1,263件（34.5%）あった。
  4288 アズジェントは列が -115.44、履歴の最新期は +44.04。
  **黒字の会社が赤字として企業比較ページに出ていた。**

  分析した瞬間は合っている（日付で最新を選んでいる）。その後に決算で
  履歴だけが新しくなり、列が取り残されて起きる。
  ＝「派生値は元の値と一緒に動かす」を守れていなかった。

⚠️ 列ごとにルールが違う。ここを揃えると別の場所が壊れる:
     eps          … 実績しか入らないので、そのまま最新を取る
     dps/payout   … **進行中の年度を除く**。期末を待たずに合計した中間配当を
                    「年間配当」として拾い、357銘柄の1株配当が半分になった
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import supabase_client as sc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class PickTest(unittest.TestCase):
    """並び順に依存せず、日付で最新を選ぶ。"""

    ASC = [{'date': '2024-03-31', 'value': -117.43},
           {'date': '2025-03-31', 'value': -115.44},
           {'date': '2026-03-31', 'value': 44.04}]
    DESC = list(reversed(ASC))

    def test_古い順でも新しい順でも同じ答え(self):
        """⚠️ financial_history の並びは銘柄ごとに違う
        （実測で新しい順56.8% / 古い順43.2%）。[0] や [-1] で取ると半分外す。
        実際にこの間違いをして、876件が壊れていると誤判定した。"""
        self.assertEqual(sc._pick_from_history(self.ASC, False), 44.04)
        self.assertEqual(sc._pick_from_history(self.DESC, False), 44.04)

    def test_進行中の年度を除ける(self):
        rows = [{'date': '2025-03-28', 'value': 105.0},
                {'date': '2099-03-28', 'value': 60.0}]
        self.assertEqual(sc._pick_from_history(rows, True), 105.0)
        self.assertEqual(sc._pick_from_history(rows, False), 60.0)

    def test_空やおかしな形で落ちない(self):
        for v in (None, [], {}, 'なにか', [{'value': 1}], [{'date': '2026-01-01'}]):
            self.assertIsNone(sc._pick_from_history(v, False))


class SyncTest(unittest.TestCase):

    def test_履歴があれば列を作り直す(self):
        data = {'eps': -115.44,
                'financial_history': {'eps': PickTest.ASC}}
        self.assertEqual(sc.sync_history_scalars(data)['eps'], 44.04)

    def test_配当は進行中を除くルールで作る(self):
        """eps と同じルールにすると、中間配当を年間配当として拾う。"""
        data = {'dps': 0, 'financial_history': {'dps': [
            {'date': '2025-03-28', 'value': 105.0},
            {'date': '2099-03-28', 'value': 60.0}]}}
        self.assertEqual(sc.sync_history_scalars(data)['dps'], 105.0)

    def test_履歴を触らない更新には何もしない(self):
        """株価の同期など、履歴を送らない更新で列を消さない。"""
        data = {'company_code': '7203', 'stock_price': 3075}
        out = sc.sync_history_scalars(dict(data))
        self.assertEqual(out, data)

    def test_文字列で入っていても読める(self):
        import json
        data = {'eps': 1,
                'financial_history': json.dumps({'eps': PickTest.ASC})}
        self.assertEqual(sc.sync_history_scalars(data)['eps'], 44.04)

    def test_辞書でなくても落ちない(self):
        self.assertEqual(sc.sync_history_scalars(None), None)


class WiredInTest(unittest.TestCase):
    """保存の経路で必ず通ること。片方だけだと、そちらから漏れる。"""

    def setUp(self):
        self.src = read('supabase_client.py')

    def test_upsertで通る(self):
        block = self.src.split('def upsert_screened_data(', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('sync_history_scalars(data)', block)

    def test_updateでも通る(self):
        block = self.src.split('def update_screened_data(', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('sync_history_scalars(data)', block)

    def test_列ごとのルールが表になっている(self):
        self.assertIn('_HISTORY_SCALARS', self.src)
        for col in ('eps', 'dps', 'payout_ratio'):
            self.assertIn("'%s'" % col, self.src)


if __name__ == '__main__':
    unittest.main()
