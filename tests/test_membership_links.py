"""Company Note から GIA の会員申込へ出す導線の行き先がそろっているか。

2026-09-05: 会員限定ゲートは5か所あるが、勉強会（learning.html）だけ
`gia2018.com/plans`（比較ページ）を指していて、他4か所の
`gia2018.com/upgrade`（申込ページ）と違っていた。ゲートに当たった人が
一番買う気になっている瞬間に、決済まで1クリック遠い状態だった。

⚠️ **コメントは走査から外す。** 「/plans は使わない」と注意書きを書いた
   だけでテストが落ちると、書いた本人が原因を探すことになる
   （feedback_test_reads_its_own_warning）。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 行き先はこの2つだけ。どちらも `from=note` が要る。
#   /upgrade?from=note        … 公開の申込ページ（¥4,980）。会員限定ゲートからはここへ
#   /upgrade/invite?from=note … 紹介限定の ¥11,000（models/root.py の INVITE_CHECKOUT_URL）
#
# ⚠️ **`from=note` を落とさないこと。** これが無いと、決済のあとGIA側のマイページに
#    着地して、買ったはずの機能に戻る道が示されない。GIA側は
#    gia-next の app/(form)/upgrade/ でこの値を受けている。
ALLOWED = {
    'https://gia2018.com/upgrade?from=note',
    'https://gia2018.com/upgrade/invite?from=note',
}

SCAN_DIRS = ('templates', 'models')
GIA_URL = re.compile(r"https://gia2018\.com[^\"'\s)]*")


def _strip_comments(text, path):
    """注意書きに引っかからないよう、コメントを落としてから走査する。"""
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    if path.endswith('.py'):
        text = re.sub(r'^\s*#.*$', '', text, flags=re.M)
    else:
        text = re.sub(r'^\s*(//|\*|#)\s.*$', '', text, flags=re.M)
    return text


class MembershipLinkTest(unittest.TestCase):
    def _found_links(self):
        found = []
        for directory in SCAN_DIRS:
            base = os.path.join(ROOT, directory)
            for name in sorted(os.listdir(base)):
                if not name.endswith(('.html', '.py')):
                    continue
                path = os.path.join(base, name)
                with open(path, encoding='utf-8') as f:
                    body = _strip_comments(f.read(), path)
                for url in GIA_URL.findall(body):
                    found.append((name, url))
        return found

    def test_every_membership_link_points_at_the_same_place(self):
        found = self._found_links()

        # ⚠️ 0件でも通る形にしない。走査が壊れたら落とす。
        self.assertGreaterEqual(len(found), 5,
                                'GIAへのリンクを拾えていない（走査が壊れている可能性）')

        stray = sorted({f'{name}: {url}' for name, url in found if url not in ALLOWED})
        self.assertEqual(stray, [],
                         '会員申込の行き先がそろっていない: ' + ', '.join(stray))


if __name__ == '__main__':
    unittest.main()
