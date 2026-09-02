# -*- coding: utf-8 -*-
"""ブラウザ標準ダイアログ（alert / confirm / prompt）を使わせないための見張り。

なぜ禁止か:
    見た人が「エラーが出た」と読む。OSごとに見た目が違って安っぽく、
    文言の調整もできない。ブロックされる環境もある。

代わりに使うもの（すべて layout.html にある。全ページで使える）:
    showErrorModal(msg)                     失敗を伝えて止める
    showSuccessModal(msg)                   完了を伝えて止める
    await showConfirmModal({...})           はい/いいえを聞く（danger:true で赤ボタン）
    await showPromptModal({...})            文字を入力してもらう
    showToast(msg, 'error'|'info')          止めずに short く知らせる

⚠️ この見張り自身がフェイルオープンしていないかを test_scanner_* で確かめている。
   注意書きのコメントを読んで合格してしまう作りにはしないこと。
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 呼び出しだけを拾う。`showAlert(` や `this.confirmDelete(` のような別物は拾わない。
# ⚠️ `window.prompt(` を取りこぼしていた（直前が `.` なので除外されていた）。
#    自前の接頭辞だけを許し、`window.` は明示的に拾う。
CALL = re.compile(r'(?<![\w.$])(?:window\.)?(alert|confirm|prompt)\s*\(')

BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
HTML_COMMENT = re.compile(r'<!--.*?-->', re.S)
# `//` から行末まで。`https://` を消さないよう、直前が `:` のものは対象外。
LINE_COMMENT = re.compile(r'(?<!:)//[^\n]*')


def strip_comments(text):
    """コメントを空白に置き換える（行番号は保つ）。"""
    def blank(m):
        return re.sub(r'[^\n]', ' ', m.group(0))
    text = HTML_COMMENT.sub(blank, text)
    text = BLOCK_COMMENT.sub(blank, text)
    text = LINE_COMMENT.sub(blank, text)
    return text


def find_violations(text):
    """(行番号, 行の中身) の一覧を返す。"""
    stripped = strip_comments(text)
    out = []
    for i, line in enumerate(stripped.split('\n'), 1):
        if CALL.search(line):
            out.append((i, line.strip()))
    return out


class ScannerSelfTest(unittest.TestCase):
    """見張りが本当に見張れているかを先に確かめる。"""

    def test_detects_a_real_call(self):
        self.assertTrue(find_violations("if (!confirm('x')) return;"))
        self.assertTrue(find_violations("alert('保存できません');"))
        self.assertTrue(find_violations("const n = window.prompt('name');"))

    def test_ignores_comments(self):
        # ⚠️ 注意書きに書いた `confirm(` を拾って落ちると、本物を直しても赤いまま
        #    になり、やがて誰も見なくなる。
        self.assertFalse(find_violations("// confirm() は使わない"))
        self.assertFalse(find_violations("<!-- alert('x') は禁止 -->"))
        self.assertFalse(find_violations("/*\n * prompt('x') の置き換え\n */"))

    def test_does_not_hide_code_after_a_url(self):
        # URL を消すために `//` を落とす実装だと、同じ行の後ろにある本物を
        # 見逃す（フェイルオープン）。
        self.assertTrue(find_violations("fetch('https://example.com'); alert('ng');"))

    def test_ignores_similar_names(self):
        self.assertFalse(find_violations("showAlert('x'); this.confirmDelete(1);"))


class NoNativeDialogsInTemplates(unittest.TestCase):

    def _targets(self):
        for folder, exts in (('templates', ('.html',)), ('static/js', ('.js',))):
            base = os.path.join(ROOT, *folder.split('/'))
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                if name.endswith(exts):
                    yield os.path.join(base, name)

    def test_no_native_dialogs(self):
        found = []
        checked = 0
        for path in self._targets():
            checked += 1
            with open(path, encoding='utf-8') as f:
                text = f.read()
            for line_no, line in find_violations(text):
                rel = os.path.relpath(path, ROOT).replace('\\', '/')
                found.append('%s:%d  %s' % (rel, line_no, line[:110]))

        # 走査対象が0件だと、何も見ずに合格してしまう。
        self.assertGreater(checked, 20, 'テンプレートを走査できていない')

        self.assertEqual([], found, '\n\nブラウザ標準ダイアログが残っています。\n'
                         'showErrorModal / showConfirmModal / showPromptModal / showToast '
                         'に置き換えてください（layout.html）。\n\n  '
                         + '\n  '.join(found) + '\n')


if __name__ == '__main__':
    unittest.main()
