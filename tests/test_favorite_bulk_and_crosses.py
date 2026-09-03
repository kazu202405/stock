# -*- coding: utf-8 -*-
"""お気に入りの一括解除とGC/DC列（2026-09-03）。

## 変えたこと

- 解除の入口を**表の右端の列から一括バーへ移した**。横に長い表のいちばん端は
  押しづらく、スマホでは横スクロールしないと見えない。チェックボックスは
  既にあるので、選んでから外す形に寄せる。
- GC/DCの発生日を列に足した。

## いちばん間違えやすいところ

⚠️ **GC/DCに `screened_latest.gc_date` を使わない。** あれは kabutan を
   スクレイピングした時刻が全銘柄一律で入っており、「いつGCしたか」を
   表していない。テクニカル一覧は `ma_crosses`（保存済みの日足から自前計算）を
   見ているので、ここで別の値を出すと**同じアプリの中で日付が食い違う**。
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def body_of(src, header):
    """関数の本文だけを切り出す。

    ⚠️ **Python用の打ち切り方をJSに使わない。** stock.html の関数はインデント
       された JS なので `def `/`class ` では切れず、窓がファイル末尾まで伸びて
       **隣の関数を拾ってしまう**（実際にこのテストがそれで落ちた）。
       Python は行頭の def、JS はインデント付きの function で打ち切る。
    """
    body = src.split(header, 1)[1]
    cut = re.search(r'\n(?=(def |@app\.route|class |\s+function |\s+async function ))',
                    body)
    return body[:cut.start()] if cut else body


class GCDCの出どころ(unittest.TestCase):

    def test_ma_crossesから取る(self):
        block = body_of(read('app.py'), 'def _attach_ma_crosses(')
        self.assertIn("table('ma_crosses')", block)
        self.assertIn('latest_gc_date', block)

    def test_お気に入り一覧で入れ直している(self):
        block = body_of(read('app.py'), 'def api_get_favorite_stocks():')
        self.assertIn('_attach_ma_crosses(stocks)', block)

    def test_取れなければ空にする(self):
        """⚠️ 取れなかったときに screened_latest の値が残ると、
        テクニカル一覧と違う日付が出る。黙って古い値を見せない。"""
        block = body_of(read('app.py'), 'def _attach_ma_crosses(')
        tail = block.split('except Exception', 1)[1]
        self.assertIn("row['gc_date'] = None", tail)

    def test_1000行の上限に当たらない(self):
        """⚠️ Supabaseは1リクエスト既定1000行。まとめて引くときは区切る。"""
        block = body_of(read('app.py'), 'def _attach_ma_crosses(')
        self.assertIn('range(0, len(codes), 200)', block)


class 一括解除(unittest.TestCase):

    def test_まとめて受けるAPIがある(self):
        """⚠️ 1件ずつ叩くと、20件選んだだけで20往復になる。"""
        src = read('app.py')
        self.assertIn("@app.route('/api/favorite-stocks/bulk', methods=['DELETE'])", src)

    def test_空の指定を弾く(self):
        block = body_of(read('app.py'), 'def api_bulk_remove_favorite_stocks():')
        self.assertIn('銘柄が選ばれていません', block)

    def test_一括バーに解除が出る(self):
        js = read('static', 'js', 'bulk-select.js')
        self.assertIn('onRemove', js)
        self.assertIn('bulk-danger', js)

    def test_お気に入りタブだけに出す(self):
        """他のタブでの「解除」は別の意味になるので混ぜない。"""
        block = read('templates/stock.html').split('BulkSelect.mountBar(', 1)[1][:600]
        self.assertIn("tabName === 'favorite'", block)

    def test_取り消せないので確かめる(self):
        block = body_of(read('templates/stock.html'), 'async function bulkRemoveFavorites(')
        self.assertIn('showConfirmModal', block)
        self.assertIn('danger: true', block)

    def test_選択解除と文言で区別する(self):
        """⚠️ 「解除」が2つ並ぶと、消すつもりのない人が押す。"""
        js = read('static', 'js', 'bulk-select.js')
        self.assertIn('選択をやめる', js)
        self.assertNotIn('>選択解除<', js)


class 削除の入口は1つ(unittest.TestCase):
    """⚠️ 同じことをする場所が2つあると、片方だけ直る事故が起きる。"""

    def test_表に解除ボタンを置かない(self):
        block = body_of(read('templates/stock.html'), 'function renderFavoriteStocks(')
        self.assertNotIn("removeFavoriteStock('${code}')", block)

    def test_カードにも置かない(self):
        block = body_of(read('templates/stock.html'), 'function renderFavoriteStocks(')
        self.assertNotIn('removeFavoriteStock', block)


if __name__ == '__main__':
    unittest.main()
