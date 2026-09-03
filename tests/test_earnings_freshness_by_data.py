# -*- coding: utf-8 -*-
"""決算の取りこぼしは「データが入っているか」で見る（2026-09-03）。

## 何が起きていたか

判定が `analyzed_at < 期末` だった。だが**決算の発表は期末の1〜2か月後**。
期末と発表の間に分析が走ると `analyzed_at > 期末` になり、その年度は
**二度と拾われない**。

実測（2026-09-03）:
    直近決算が入っている          3,149件
    入っていない & 網が拾えていた      0件   ← 網がまったく効いていない
    入っていない & 網が見逃していた   199件   ← 日清食品HD・東武鉄道・タマホーム等

どれも履歴の最新が1年前で止まっていた。エラーは出ず、画面も普通に表示される。

## 直し方

**見たいのは「直近の決算が入っているか」であって「いつ分析したか」ではない。**
財務履歴のいちばん新しい決算期を見る。同じ形の間違いを繰り返さないための行。
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import earnings_freshness as ef  # noqa: E402

TODAY = date(2026, 9, 3)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def row(fiscal_month, newest, analyzed='2026-07-21', **extra):
    r = {'fiscal_month': fiscal_month, 'analyzed_at': analyzed,
         'financial_history': {'revenue': (
             [{'date': newest, 'value': 1}] if newest else [])}}
    r.update(extra)
    return r


class データで判定する(unittest.TestCase):

    def test_直近決算が入っていなければ拾う(self):
        """⚠️ 分析日は期末より後（2026-07-21 > 2026-03-31）。
        以前の判定ではここが False になり、永久に拾われなかった。"""
        self.assertTrue(ef.is_stale(row(3, '2025-03-31'), TODAY))

    def test_入っていれば拾わない(self):
        self.assertFalse(ef.is_stale(row(3, '2026-03-31'), TODAY))

    def test_発表の期限内は拾わない(self):
        """6月期は9/3時点でまだ猶予内（65日）。まだ出ていないだけ。"""
        self.assertFalse(ef.is_stale(row(6, '2025-06-30'), TODAY))
        self.assertTrue(ef.is_stale(row(6, '2025-06-30'), date(2026, 9, 20)))

    def test_履歴が無い銘柄は対象外(self):
        """一度も取れていない銘柄はバックフィルの領分。ここで鳴らすと
        いつも赤い監視になり、誰も見なくなる。"""
        self.assertFalse(ef.is_stale(row(3, None), TODAY))

    def test_上場廃止は対象外(self):
        self.assertFalse(ef.is_stale(row(3, '2025-03-31', delisted_at='2026-05-01'),
                                     TODAY))


class 履歴が渡されないときの保険(unittest.TestCase):
    """呼び出し側が financial_history を渡し忘れても、黙って全部
    「問題なし」にはしない。従来どおり分析日で見る。"""

    def test_分析日で見る(self):
        self.assertTrue(ef.is_stale(
            {'fiscal_month': 3, 'analyzed_at': '2025-01-01'}, TODAY))
        self.assertFalse(ef.is_stale(
            {'fiscal_month': 3, 'analyzed_at': '2026-07-21'}, TODAY))


class 最新の決算期の取り方(unittest.TestCase):

    def test_複数の系列から一番新しいものを取る(self):
        r = {'financial_history': {
            'revenue': [{'date': '2025-03-31', 'value': 1}],
            'op_income': [{'date': '2026-03-31', 'value': 2}]}}
        self.assertEqual('2026-03-31', ef.newest_period(r))

    def test_値がNoneの行は数えない(self):
        r = {'financial_history': {
            'revenue': [{'date': '2026-03-31', 'value': None},
                        {'date': '2025-03-31', 'value': 1}]}}
        self.assertEqual('2025-03-31', ef.newest_period(r))

    def test_配当は見ない(self):
        """⚠️ dps は権利確定日ベースで決算期末とズレる。"""
        r = {'financial_history': {'dps': [{'date': '2026-06-30', 'value': 10}]}}
        self.assertIsNone(ef.newest_period(r))

    def test_文字列のJSONでも読める(self):
        import json
        r = {'financial_history': json.dumps(
            {'revenue': [{'date': '2026-03-31', 'value': 1}]})}
        self.assertEqual('2026-03-31', ef.newest_period(r))

    def test_壊れていても落ちない(self):
        self.assertIsNone(ef.newest_period({'financial_history': 'not json'}))
        self.assertIsNone(ef.newest_period({}))


class 呼び出し側が履歴を渡している(unittest.TestCase):

    def test_定期実行のselectに入っている(self):
        src = read('app.py')
        block = src.split('def scheduled_check_earnings_freshness():', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn('financial_history', block)


if __name__ == '__main__':
    unittest.main()
