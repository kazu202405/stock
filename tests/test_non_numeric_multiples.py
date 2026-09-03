# -*- coding: utf-8 -*-
"""外部値が数値でないときに分析ごと落ちないこと（2026-09-03）。

## 何が起きていたか

407A（ＵＮＩＣＯＮホールディングス）は EPS が 0.0 のため、Yahoo が
`trailingPE` を **文字列 `'Infinity'`** で返す。`_fill_missing_multiples` の
`external <= 0` がそこで TypeError になり、`analyze()` が例外で終わっていた。

⚠️ **この壊れ方はDBに痕跡を残さない。** 例外は握られて `result["error"]` に
   入るだけで、保存まで到達しないので `failed_reasons` にも何も書かれない。
   実測で failed_reasons が入っている銘柄は0件だった。
   つまり**「この銘柄だけ何度更新しても変わらない」という形でしか気づけない**。

## 決まり

- 数値として読めない外部値は「無い」として扱う（捨てる）
- ⚠️ **NaN も弾く。** `nan <= 0` も `nan > limit` も False なので、範囲チェックを
  素通りして「正しいPER」として保存されてしまう
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import stock_analyzer as sa  # noqa: E402


class 数値として読めるものだけ通す(unittest.TestCase):

    def test_Infinityの文字列は捨てる(self):
        """⚠️ EPSが0の銘柄でYahooが実際に返す値。これで分析が落ちていた。"""
        self.assertIsNone(sa._finite_number('Infinity'))
        self.assertIsNone(sa._finite_number('-Infinity'))

    def test_無限大の数値も捨てる(self):
        self.assertIsNone(sa._finite_number(float('inf')))
        self.assertIsNone(sa._finite_number(float('-inf')))

    def test_NaNも捨てる(self):
        """⚠️ 範囲チェックを素通りするので、明示的に弾く必要がある。"""
        self.assertIsNone(sa._finite_number(float('nan')))
        # 素通りしてしまうことの確認（なぜ明示的に弾くのかの根拠）
        nan = float('nan')
        self.assertFalse(nan <= 0)
        self.assertFalse(nan > 100)

    def test_数字でない文字列は捨てる(self):
        for value in ('', '-', 'N/A', 'なし', None):
            self.assertIsNone(sa._finite_number(value))

    def test_真偽値は数値として扱わない(self):
        self.assertIsNone(sa._finite_number(True))
        self.assertIsNone(sa._finite_number(False))

    def test_ふつうの数値は通す(self):
        self.assertEqual(12.5, sa._finite_number(12.5))
        self.assertEqual(12.5, sa._finite_number('12.5'))
        self.assertEqual(1234.0, sa._finite_number('1,234'))
        self.assertEqual(0.0, sa._finite_number(0))
        self.assertEqual(-3.0, sa._finite_number(-3))


class 分析が落ちないこと(unittest.TestCase):
    """`_fill_missing_multiples` を直接呼んで、例外が出ないことを見る。"""

    def _run(self, result):
        analyzer = sa.StockAnalyzer.__new__(sa.StockAnalyzer)
        analyzer.MAX_PER = sa.StockAnalyzer.MAX_PER
        analyzer.MAX_PBR = sa.StockAnalyzer.MAX_PBR
        sa.StockAnalyzer._fill_missing_multiples(analyzer, result)
        return result

    def test_PERが文字列Infinityでも落ちない(self):
        result = {'last_price': 933.0, 'per': 'Infinity', 'pbr': 2.37,
                  'eps': [{'date': '2025-06-30', 'value': 0.0}],
                  'bps': [{'date': '2025-06-30', 'value': 393.6}]}
        self._run(result)
        self.assertIsNone(result['per'], '読めない外部値を残している')

    def test_株価が文字列でも落ちない(self):
        result = {'last_price': 'N/A', 'per': 10.0, 'pbr': 1.0}
        self._run(result)          # 例外が出ないこと

    def test_EPSが文字列でも落ちない(self):
        result = {'last_price': 100.0, 'per': None, 'pbr': None,
                  'eps': [{'date': '2025-06-30', 'value': 'なし'}],
                  'bps': [{'date': '2025-06-30', 'value': 50.0}]}
        self._run(result)
        self.assertEqual(2.0, result['pbr'], 'BPSからPBRを出せていない')

    def test_ふつうの銘柄はこれまでどおり(self):
        result = {'last_price': 100.0, 'per': None, 'pbr': None,
                  'eps': [{'date': '2025-03-31', 'value': 10.0}],
                  'bps': [{'date': '2025-03-31', 'value': 50.0}]}
        self._run(result)
        self.assertEqual(10.0, result['per'])
        self.assertEqual(2.0, result['pbr'])


if __name__ == '__main__':
    unittest.main()
