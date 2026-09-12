# -*- coding: utf-8 -*-
"""株価の一括取得が、待ちスレッドで膨らまないこと（2026-09-12）。

本番ログ（11:45の回）:
    [mem] price_update_midday 開始: 216MB
    [mem] 254MB 実行中: price_update_midday（スレッド97本）
    [mem] 412MB 実行中: price_update_midday（スレッド20本）
    ==> Running 'gunicorn app:app ...'      ← プロセスが入れ替わった

⚠️ **`threads=4` は効かない。** yfinance の threads は「銘柄ごとの並列取得」の
   上限であって、待っているスレッドを減らすものではない。銘柄をまとめて1つの
   文字列で渡すと、内部で銘柄ごとにスレッドが立つ。Yahooが遅いと、返らない
   スレッドが積み上がってメモリが増える。

   → 1回に渡す銘柄数を減らし、待ち時間の上限を明示する。
"""

import os
import re
import unittest
from unittest.mock import patch

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


class BatchLimitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.app = app_module

    def test_a_batch_is_small_enough(self):
        """1回に渡す銘柄数が多いほど、待ちスレッドの上限が上がる。"""
        self.assertLessEqual(self.app.YFINANCE_BATCH_SIZE, 50)
        self.assertGreaterEqual(self.app.YFINANCE_BATCH_SIZE, 10,
                                '小さすぎるとリクエスト数が増えて制限に当たる')

    def test_the_default_batch_size_is_the_constant(self):
        """⚠️ 既定値を別に書くと、定数を直しても呼び出し側が古いままになる。"""
        import inspect
        default = inspect.signature(self.app.fetch_prices_batch).parameters['chunk_size'].default
        self.assertEqual(default, self.app.YFINANCE_BATCH_SIZE)

    def test_it_waits_with_a_limit(self):
        """返らない銘柄を待ち続けない（本番では60〜115秒待っていた）。"""
        self.assertLessEqual(self.app.YFINANCE_TIMEOUT_SECONDS, 30)

    def test_the_limits_reach_yfinance(self):
        """⚠️ 定数を決めても渡していなければ意味が無い。"""
        # ⚠️ 最後の呼び出しだけを見ない。端数の回（120銘柄なら最後の20件）を
        #    拾って「定数と違う」と誤判定する。全部の呼び出しを記録して見る。
        calls = []
        captured = {}

        def fake_download(symbols, **kwargs):
            calls.append(symbols)
            if not captured:
                captured['symbols'] = symbols
                captured.update(kwargs)
            raise RuntimeError('ここでは取得しない')

        import sys
        import types
        fake = types.ModuleType('yfinance')
        fake.download = fake_download
        with patch.dict(sys.modules, {'yfinance': fake}):
            stats = {}
            codes = ['%04d' % n for n in range(1, 121)]
            self.app.fetch_prices_batch(codes, stats=stats)

        self.assertEqual(captured.get('timeout'), self.app.YFINANCE_TIMEOUT_SECONDS)
        # どの回も定数を超えない（端数の回だけ少ないのは正常）
        biggest = max(len(symbols.split()) for symbols in calls)
        self.assertEqual(biggest, self.app.YFINANCE_BATCH_SIZE,
                         '1回に渡す銘柄数が定数と違う')
        # 120銘柄なら 50+50+20 の3回（1回目は取り直すので呼び出し自体は増える）
        self.assertEqual(stats['chunks'], 3)

    def test_the_reason_is_written_down(self):
        """⚠️ 「threads=4 で足りる」と誤解して戻さないよう、理由を残す。"""
        source = read('app.py')
        start = source.index('YFINANCE_BATCH_SIZE = ')
        block = source[max(0, start - 700):start]
        self.assertIn('threads=4', block.replace(' ', ''))


if __name__ == '__main__':
    unittest.main()
