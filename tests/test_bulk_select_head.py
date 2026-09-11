# -*- coding: utf-8 -*-
"""一覧の見出しの「全選択」が、押して効くか（2026-09-11）。

何が起きていたか:
  見出しのチェックボックスは onclick に表示中の銘柄をJSONで埋めていた。
    onclick="BulkSelect.toggleAll('watchlist', ["7203","6758"], this.checked, event)"
  esc() が " を変換していなかったため、最初の " で属性が閉じ、onclick は
  「BulkSelect.toggleAll('watchlist', [」で途切れた。押すと構文エラーで
  **1件も選ばれない**。見出しの✔だけは付くので、効いたように見えていた。

見張り方:
  bulk-select.js を Node で動かし、実際に出てくるHTMLから onclick を取り出して
  node --check にかける。偽の document は、ブラウザと同じく < > & だけを
  変換する（この性質が不具合の原因だったので、そこを再現しないと意味が無い）。
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BULK_JS = os.path.join(ROOT, 'static', 'js', 'bulk-select.js')
NODE = shutil.which('node')

# ブラウザの innerHTML と同じ変換（テキストの中身として < > & と nbsp だけ）
_HARNESS = r"""
global.window = global;
global.document = {
  createElement: function () {
    var t = '';
    return {
      set textContent(v) { t = String(v); },
      get innerHTML() {
        return t.replace(/&/g, '&amp;').replace(/</g, '&lt;')
                .replace(/>/g, '&gt;').replace(/ /g, '&nbsp;');
      }
    };
  },
  querySelectorAll: function () { return []; },
  querySelector: function () { return null; },
  getElementById: function () { return null; }
};
require(process.argv[2]);
var codes = JSON.parse(process.argv[3]);
process.stdout.write(BulkSelect.headCell('watchlist', codes));
"""


class _Onclick(HTMLParser):
    """HTMLとして読み、onclick 属性をブラウザと同じように取り出す。"""

    def __init__(self):
        super().__init__()
        self.onclick = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == 'onclick':
                self.onclick.append(value)


def _onclicks(html):
    parser = _Onclick()
    parser.feed(html)
    return parser.onclick


def _js_error(code):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False,
                                     encoding='utf-8') as f:
        f.write(code)
        path = f.name
    try:
        result = subprocess.run([NODE, '--check', path], capture_output=True,
                                text=True, encoding='utf-8', timeout=30)
        return None if result.returncode == 0 else (result.stderr or 'error')
    finally:
        os.remove(path)


@unittest.skipUnless(NODE, 'Node が無い環境ではスキップ')
class 見出しの全選択(unittest.TestCase):

    def _head_cell(self, codes):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False,
                                         encoding='utf-8') as f:
            f.write(_HARNESS)
            harness = f.name
        try:
            result = subprocess.run(
                [NODE, harness, BULK_JS, json.dumps(codes)],
                capture_output=True, text=True, encoding='utf-8', timeout=30)
        finally:
            os.remove(harness)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def test_onclickが途中で切れず_JSとして読める(self):
        html = self._head_cell(['7203', '6758', '285A'])
        onclick = _onclicks(html)
        self.assertEqual(1, len(onclick), html)
        self.assertIn('["7203","6758","285A"]', onclick[0])
        self.assertIsNone(_js_error(onclick[0]), onclick[0])

    def test_表示中が0件でも壊れない(self):
        onclick = _onclicks(self._head_cell([]))
        self.assertIsNone(_js_error(onclick[0]), onclick[0])

    def test_見張りが途切れたonclickを捕まえる(self):
        """⚠️ 見張り自身が素通ししていないか。実際に起きた壊れ方で確かめる。"""
        broken = ('<th><input type="checkbox" onclick="BulkSelect.toggleAll('
                  '\'watchlist\', ["7203","6758"], this.checked, event)"></th>')
        onclick = _onclicks(broken)
        self.assertEqual(["BulkSelect.toggleAll('watchlist', ["], onclick)
        self.assertIsNotNone(_js_error(onclick[0]))


class 最初のタブにも操作バーが付く(unittest.TestCase):
    """チェックが入っても操作バーが出なければ、選んでも何もできない（2026-09-11）。

    操作バーを付ける mountBulkBar は、タブを切り替えたときと、フォルダ一覧を
    読み込めたときにしか呼ばれていなかった。フォルダ一覧はお気に入りを開くまで
    読まれないので、最初に開いたタブではバーが出なかった。
    """

    def setUp(self):
        with open(os.path.join(ROOT, 'templates', 'stock.html'), encoding='utf-8') as f:
            self.html = f.read()

    def _function(self, signature):
        start = self.html.index(signature)
        # 次の関数定義までを本文とみなす（関数の中に関数は無い）
        end = self.html.index('\n    function ', start + len(signature))
        return self.html[start:end]

    def test_ページを開いたときにフォルダ一覧を読む(self):
        start = self.html.index("document.addEventListener('DOMContentLoaded', function() {")
        block = self.html[start:self.html.index('});', start)]
        self.assertIn('loadWatchlist();', block)
        self.assertIn('loadFavoriteFolders();', block)

    def test_フォルダが取れなくてもバーは付ける(self):
        body = self._function('async function loadFavoriteFolders()')
        self.assertIn('finally {', body)
        self.assertGreater(body.index('mountBulkBar('), body.index('finally {'),
                           'mountBulkBar が finally の外にある（取れたときだけ付く）')


if __name__ == '__main__':
    unittest.main()
