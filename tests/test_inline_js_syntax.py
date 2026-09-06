# -*- coding: utf-8 -*-
"""テンプレートに書いたJSが、そもそも構文として通るか（2026-09-06）。

何が起きたか:
  `'\\n\\n'` と書いたつもりが**本物の改行**になり、JSの文字列が閉じないまま
  <script> 全体が構文エラーになった。結果、その中の関数が1つも定義されず、
  「メモを書く」ボタンを押しても何も起きなくなった。

  ⚠️ **Pythonのテストは1,071件すべて緑だった。** JSを読む見張りが無かったので、
     壊れたものが本番へ出た。画面を開いて押すまで誰にも分からない。

判定は自前でやらない。**Node の構文チェック（node --check）に投げる。**
最初は引用符の数を数える方式で書いたが、正規表現リテラル（/[&<>"']/）や
複数行のテンプレートリテラルを誤検知した。誤検知を出す見張りは誰も見なく
なるので、本物のパーサに任せる。

Node が無い環境ではスキップする（CIに入れるときは Node を用意すること）。
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, 'templates')
STATIC_JS = os.path.join(ROOT, 'static', 'js')

NODE = shutil.which('node')

# JSON-LD など、JSではない <script> は見ない
_SCRIPT = re.compile(
    r'<script(?![^>]*\btype\s*=\s*"(?:application|text)/(?!javascript))[^>]*>(.*?)</script>',
    re.S | re.I)

_JINJA_EXPR = re.compile(r'\{\{.*?\}\}', re.S)
_JINJA_STMT = re.compile(r'\{%.*?%\}', re.S)
_JINJA_COMMENT = re.compile(r'\{#.*?#\}', re.S)


def _as_plain_js(code: str) -> str:
    """Jinja の式を、JSとして無害な字句に置き換える。

    `{{ x }}` は文字列の中にも外にも入りうるので、どちらでも壊れない `0` にする。
    """
    code = _JINJA_COMMENT.sub('', code)
    return _JINJA_EXPR.sub('0', code)


def _has_branch(code: str) -> bool:
    """Jinja の分岐（{% if %} 等）を含むか。

    ⚠️ 分岐は「どれか1つが出る」ので、印を外して並べるとJSとしては読めない
       （report_view.html のグラフ定義が実際に誤検知になった）。
       **誤検知を出す見張りは誰も見なくなる**ので、分岐を含むブロックは
       対象から外し、その数を数えて穴を隠さないようにする。
    """
    return bool(_JINJA_STMT.search(code))


def _syntax_error(code: str):
    """構文が通らなければ Node のメッセージを返す。通れば None。"""
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False,
                                     encoding='utf-8') as f:
        f.write(code)
        path = f.name
    try:
        result = subprocess.run([NODE, '--check', path],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return None
        return (result.stderr or result.stdout).strip()[:400]
    finally:
        os.unlink(path)


@unittest.skipIf(NODE is None, 'Node が無いので構文チェックできません')
class InlineJsSyntaxTest(unittest.TestCase):
    def test_inline_scripts_parse(self):
        checked = 0
        skipped = 0
        for name in sorted(os.listdir(TEMPLATES)):
            if not name.endswith('.html'):
                continue
            with open(os.path.join(TEMPLATES, name), encoding='utf-8') as f:
                html = f.read()
            for index, block in enumerate(_SCRIPT.findall(html)):
                if not block.strip():
                    continue
                if _has_branch(block):
                    skipped += 1          # Jinja の分岐入り。上の注記を参照
                    continue
                checked += 1
                error = _syntax_error(_as_plain_js(block))
                self.assertIsNone(
                    error,
                    '%s の %d 番目の <script> が構文エラー:%s%s'
                    % (name, index + 1, os.linesep, error))
        # ⚠️ 0件でも通る形にしない。抽出が壊れたら落とす。
        self.assertGreater(checked, 20, '<script> を拾えていない')
        # ⚠️ 見張りの穴を隠さない。見ていない範囲が見ている範囲より広がったら、
        #    そのときは別の手を考える合図にする。
        self.assertLess(skipped, checked,
                        '構文を見ていない <script> のほうが多い（%d / %d）'
                        % (skipped, checked))

    def test_shared_js_files_parse(self):
        checked = 0
        for name in sorted(os.listdir(STATIC_JS)):
            if not name.endswith('.js'):
                continue
            checked += 1
            with open(os.path.join(STATIC_JS, name), encoding='utf-8') as f:
                error = _syntax_error(f.read())
            self.assertIsNone(error, '%s が構文エラー:%s%s'
                                     % (name, os.linesep, error))
        self.assertGreater(checked, 3, 'jsファイルを拾えていない')

    def test_the_note_editor_block_parses(self):
        """実際に壊れた場所を名指しで見る。"""
        with open(os.path.join(TEMPLATES, 'stock_detail.html'),
                  encoding='utf-8') as f:
            html = f.read()
        blocks = [b for b in _SCRIPT.findall(html)
                  if 'function editStockNote' in b]
        self.assertEqual(len(blocks), 1,
                         'editStockNote が入った <script> が見つからない')
        self.assertIsNone(_syntax_error(_as_plain_js(blocks[0])))

    def test_the_checker_actually_catches_a_broken_string(self):
        """⚠️ 見張り自身が素通ししていないか。実際に起きた壊れ方で確かめる。"""
        broken = "const message = 'これは\n閉じていない';"
        self.assertIsNotNone(_syntax_error(broken),
                             '壊れたJSを通してしまう見張りになっている')
        self.assertIsNone(_syntax_error("const message = 'これは閉じている';"))


if __name__ == '__main__':
    unittest.main()
