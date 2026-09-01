# -*- coding: utf-8 -*-
"""お気に入りのフォルダ分けと一括登録（2026-09-01）。

なぜ作ったか:
  好調企業・高配当企業・テクニカル分析の一覧から、チェックした銘柄を
  まとめてお気に入りに入れたい。さらに「ウォッチ銘柄」「自分で見つけた
  高配当銘柄」のように自分の分類で束ねたい。

⚠️ **1銘柄は複数のフォルダに入る。** 好調企業でありながら高配当、という
   銘柄が実際にあるため、単一所属にすると片方を選ばせることになる。
   ∴ favorite_stocks に列を足さず、favorite_folder_items で結ぶ。

⚠️ **アプリは service_role で接続する＝RLSがバイパスされる。**
   user_id の絞り込みはアプリ側の責任。folder_id はクライアントから来るので、
   本人のものかを確かめずに使うと、他人のフォルダを触れてしまう。
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import supabase_client as sc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# folder_id を受け取って書き込む関数。**すべてが所有者確認を通ること。**
FOLDER_WRITERS = (
    'def rename_favorite_folder(',
    'def delete_favorite_folder(',
    'def add_favorite_stocks(',
    'def add_to_favorite_folder(',
    'def remove_from_favorite_folder(',
    'def set_favorite_folders(',
)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def body_of(src, header):
    """関数の本文だけを切り出す。次のトップレベル def で打ち切る。

    ⚠️ 文字数で窓を切ると隣の関数まで食い込み、隣のガードを拾って合格する。
    """
    body = src.split(header, 1)[1]
    cut = re.search(r'\n(?=(def |@app\.route|class ))', body)
    return body[:cut.start()] if cut else body


def sql_code(text):
    # SQLからコメント（-- 以降）を落とす。
    #
    # ⚠️ **注意書きに書いた語を実装と取り違えないため。**
    #    『ここの cascade は札を外す意味』という注意書きが、
    #    「cascade が無いこと」を確かめるテストを落とした。
    #    禁止を確かめるテストは必ずコードだけを見ること（3回目）。
    out = []
    for line in text.split(chr(10)):
        out.append(line.split('--', 1)[0])
    return chr(10).join(out)

class OwnershipGateTest(unittest.TestCase):
    """他人のフォルダを触らせない。

    ⚠️ RLSは service_role でバイパスされるので、**ここが唯一の門**。
       確認を1か所でも飛ばすと、folder_id を差し替えるだけで他人のフォルダに
       自分の銘柄を入れられる（画面には何も出ない）。
    """

    def setUp(self):
        self.src = read('supabase_client.py')

    def test_folder_idを書く関数はすべて所有者を確かめる(self):
        for fn in FOLDER_WRITERS:
            with self.subTest(fn=fn):
                self.assertIn('owns_favorite_folder', body_of(self.src, fn))

    def test_確認は本人のuser_idで引く(self):
        block = body_of(self.src, 'def owns_favorite_folder(')
        self.assertIn("eq('user_id', user_id)", block)
        self.assertIn("eq('id', folder_id)", block)

    def test_銘柄の特定も本人で絞る(self):
        """user_id で絞らず company_code だけで引くと、同じ銘柄を
        お気に入りにしている他人の行を掴む。"""
        self.assertIn("eq('user_id', user_id)", body_of(self.src, 'def _favorite_ids('))
        self.assertIn("eq('user_id', user_id)", body_of(self.src, 'def _all_favorite_ids('))

    def test_札は本人のお気に入りIDを経由して触る(self):
        """中間表は user_id を持たない。**本人のお気に入りIDに変換してから**
        触ること。folder_id だけで消すと、他人の札まで巻き込む。"""
        for fn in ('def add_to_favorite_folder(', 'def remove_from_favorite_folder('):
            with self.subTest(fn=fn):
                self.assertIn('_favorite_ids(', body_of(self.src, fn))


class FolderDeleteTest(unittest.TestCase):

    def test_フォルダを消しても銘柄は消さない(self):
        """⚠️ フォルダは札であって銘柄そのものではない。銘柄まで消すと、
        名前を付け替えるつもりだった人がお気に入りごと失う。"""
        block = body_of(read('supabase_client.py'), 'def delete_favorite_folder(')
        self.assertIn("table('favorite_folder_items').delete()", block)
        self.assertNotIn("table('favorite_stocks').delete()", block)

    def test_札を外しても銘柄は消さない(self):
        block = body_of(read('supabase_client.py'), 'def remove_from_favorite_folder(')
        self.assertIn("table('favorite_folder_items').delete()", block)
        self.assertNotIn("table('favorite_stocks').delete()", block)

    def test_cascadeがかかるのは中間表だけ(self):
        """⚠️ cascade は「札が消える」意味に限ること。favorite_stocks 側に
        かけると、フォルダを消した瞬間にお気に入りが道連れになる。"""
        sql = sql_code(read('supabase', 'migration_favorite_folders.sql'))
        head, items = sql.split('create table if not exists favorite_folder_items', 1)
        self.assertEqual(items.count('on delete cascade'), 2)   # favorite_id / folder_id
        self.assertNotIn('cascade', head)
        # favorite_stocks に列を足す旧設計（単一所属）に戻っていないこと
        self.assertNotIn('alter table favorite_stocks', sql)


class MultiFolderTest(unittest.TestCase):
    """1銘柄が複数のフォルダに入れること。"""

    def test_中間表で結ぶ(self):
        sql = read('supabase', 'migration_favorite_folders.sql')
        self.assertIn('favorite_folder_items', sql)
        self.assertIn('primary key (favorite_id, folder_id)', sql)

    def test_一覧はfolder_idsを返す(self):
        """画面でフォルダごとに絞るために要る。単数ではなく複数。"""
        block = body_of(read('supabase_client.py'), 'def get_favorite_stocks(')
        self.assertIn("'folder_ids'", block)
        self.assertNotIn("'folder_id':", block)

    def test_folder_idsはdetailの後に置く(self):
        """screened_latest 側に同名の列ができたとき、
        お気に入りのフォルダが黙って上書きされる。"""
        block = body_of(read('supabase_client.py'), 'def get_favorite_stocks(')
        self.assertLess(block.index('**detail'), block.rindex("'folder_ids'"))

    def test_札は足すだけで奪わない(self):
        """⚠️ 「移動」ではなく「追加」。すでに付いている他の札を外すと、
        一括登録のたびに手で付けた分類が黙って消える。"""
        block = body_of(read('supabase_client.py'), 'def add_to_favorite_folder(')
        self.assertIn('upsert', block)
        self.assertNotIn('.delete()', block)

    def test_すでにお気に入りでもフォルダには入れる(self):
        """既存を飛ばすと「入れたのに入っていない」が起きる。"""
        block = body_of(read('supabase_client.py'), 'def add_favorite_stocks(')
        self.assertIn('add_to_favorite_folder(user_id, codes, folder_id)', block)


class BulkAddTest(unittest.TestCase):

    def test_件数に上限がある(self):
        """上限が無いと、一覧を全選択して数千件を1リクエストで投げられる。"""
        for fn in ('def api_add_favorite_stocks_bulk():',
                   'def api_add_to_favorite_folder():'):
            with self.subTest(fn=fn):
                self.assertIn('BULK_FAVORITE_MAX', body_of(read('app.py'), fn))

    def test_重複を落としてから数える(self):
        block = body_of(read('supabase_client.py'), 'def add_favorite_stocks(')
        self.assertIn('dict.fromkeys', block)

    def test_空なら何もしない(self):
        self.assertEqual(sc.add_favorite_stocks('u', []),
                         {'added': 0, 'already': 0, 'filed': 0})
        self.assertEqual(sc.add_to_favorite_folder('u', [], 'f'), 0)
        self.assertEqual(sc.remove_from_favorite_folder('u', [], 'f'), 0)


class FolderNameTest(unittest.TestCase):

    def test_同名は作らせない(self):
        """どちらに入れたか分からなくなる。"""
        for fn in ('def create_favorite_folder(', 'def rename_favorite_folder('):
            with self.subTest(fn=fn):
                self.assertIn('すでにあります', body_of(read('supabase_client.py'), fn))

    def test_空の名前を弾く(self):
        block = body_of(read('supabase_client.py'), 'def create_favorite_folder(')
        self.assertIn('フォルダ名が空です', block)

    def test_長さを切り詰める(self):
        block = body_of(read('supabase_client.py'), 'def create_favorite_folder(')
        self.assertIn('FOLDER_NAME_MAX', block)


class ApiShapeTest(unittest.TestCase):

    def setUp(self):
        import app as app_module
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.src = read('app.py')

    def test_口が揃っている(self):
        paths = {str(r) for r in self.app.url_map.iter_rules()}
        for p in ('/api/favorite-folders', '/api/favorite-folders/<folder_id>',
                  '/api/favorite-stocks/bulk', '/api/favorite-stocks/folder',
                  '/api/favorite-stocks/folders'):
            self.assertIn(p, paths)

    def test_他人のフォルダは404にする(self):
        """存在しないのか権限が無いのかを外から区別させない。"""
        for fn in ('def api_add_favorite_stocks_bulk():',
                   'def api_add_to_favorite_folder():',
                   'def api_remove_from_favorite_folder():',
                   'def api_set_favorite_folders():',
                   'def api_rename_favorite_folder(folder_id):',
                   'def api_delete_favorite_folder(folder_id):'):
            with self.subTest(fn=fn):
                block = body_of(self.src, fn)
                self.assertIn('except PermissionError', block)
                self.assertIn('404', block)

    def test_中身の詳細を返さない(self):
        """例外文をそのまま返すと、内部の作りが漏れる。"""
        block = body_of(self.src, 'def api_add_favorite_stocks_bulk():')
        tail = block.rsplit('except Exception as e:', 1)[1]
        self.assertNotIn('str(e)', tail)


class BulkSelectUiTest(unittest.TestCase):
    """一覧の複数選択（共通部品 bulk-select.js）。"""

    def setUp(self):
        self.js = read('static', 'js', 'bulk-select.js')

    def test_表とカードの両方に出せる(self):
        """⚠️ スマホの既定はカード表示。表だけに付けると外では使えない。"""
        self.assertIn('function cell(', self.js)
        self.assertIn('function cardCheck(', self.js)
        self.assertIn('config.select', read('static', 'js', 'table-view.js'))

    def test_行のクリックを飲み込む(self):
        """一覧の行は onclick で銘柄ページを開く。止めないとチェックした
        瞬間に別タブが開く。"""
        self.assertIn("event.stopPropagation()", self.js)

    def test_選択は描画で消えない(self):
        """並べ替え・絞り込みで表は何度も描き直される。"""
        self.assertIn('var sets = {}', self.js)

    def test_全選択は表示中だけを対象にする(self):
        """絞り込みで隠れているものまで選ぶと、何を選んだか分からなくなる。"""
        block = self.js.split('function toggleAll(', 1)[1][:400]
        self.assertIn('visible', block)

    def test_0件のときバーを出さない(self):
        """常に出ていると一覧の下端が隠れて邪魔になる。"""
        block = self.js.split('function paintBar(', 1)[1][:400]
        self.assertIn("bar.style.display = 'none'", block)


class DashboardRenderTest(unittest.TestCase):
    """ダッシュボードに実際に差し込まれているか（描画して確かめる）。

    ⚠️ ソースを grep するだけだと、テンプレートの分岐で出ない場所に
       書いてあっても合格する。ここは render_template を通す。
    """

    @classmethod
    def setUpClass(cls):
        import app as app_module
        from flask import render_template
        with app_module.app.test_request_context('/dashboard/admin'):
            cls.html = render_template('stock.html', is_admin=True)

    def test_4つのタブすべてにチェック列がある(self):
        for key in ('watchlist', 'dividend', 'technical', 'favorite'):
            with self.subTest(key=key):
                self.assertIn("BulkSelect.headCell('" + key + "'", self.html)

    def test_カードにもチェックがある(self):
        """スマホの既定はカード表示。表だけだと外で使えない。"""
        for key in ('watchlist', 'dividend', 'technical', 'favorite'):
            with self.subTest(key=key):
                self.assertIn("BulkSelect.cardCheck('" + key + "'", self.html)

    def test_フォルダの帯とスクリプトが載っている(self):
        self.assertIn('id="favFolderBar"', self.html)
        self.assertIn('js/bulk-select.js', self.html)
        self.assertIn('#bulkBar', self.html)

    def test_削除の確認文に銘柄が残ると書く(self):
        """フォルダ＝銘柄だと思っている人が、消えたと誤解する。"""
        self.assertIn('お気に入りに残ります', self.html)


if __name__ == '__main__':
    unittest.main()
