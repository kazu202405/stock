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

# ⚠️ app.py も見る。2026-09-06、member_required_api の upgrade_url が
#    templates/models の外にあったため、この見張りをすり抜けていた。
SCAN_DIRS = ('templates', 'models')
SCAN_FILES = ('app.py',)
GIA_URL = re.compile(r"https://gia2018\.com[^\"'\s)]*")

# アプリ内の会員案内。2026-09-06、ログイン済みの人が使うゲート3か所
# （勉強会・アカウント設定・ウォッチリスト）を、別ドメインへ直接飛ばすのを
# やめてここへ通した。/membership に価格と申込ボタンと見本があるため。
INTERNAL_GATE = 'href="/membership"'


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
        paths = [os.path.join(ROOT, name) for name in SCAN_FILES]
        for directory in SCAN_DIRS:
            base = os.path.join(ROOT, directory)
            paths += [os.path.join(base, name) for name in sorted(os.listdir(base))
                      if name.endswith(('.html', '.py'))]
        for path in paths:
            with open(path, encoding='utf-8') as f:
                body = _strip_comments(f.read(), path)
            for url in GIA_URL.findall(body):
                found.append((os.path.basename(path), url))
        return found

    def _internal_gates(self):
        """アプリ内の /membership へのリンク数。"""
        count = 0
        for directory in SCAN_DIRS:
            base = os.path.join(ROOT, directory)
            for name in sorted(os.listdir(base)):
                if not name.endswith('.html'):
                    continue
                with open(os.path.join(base, name), encoding='utf-8') as f:
                    count += f.read().count(INTERNAL_GATE)
        return count

    def test_every_membership_link_points_at_the_same_place(self):
        found = self._found_links()

        # ⚠️ 0件でも通る形にしない。走査が壊れたら落とす。
        #    行き先を /membership に寄せると gia2018.com のリンク自体は減るので、
        #    「会員申込への導線の総数」で数える（減らすと落ちる、を保つ）。
        self.assertGreaterEqual(len(found) + self._internal_gates(), 5,
                                '会員申込への導線を拾えていない（走査が壊れている可能性）')

        stray = sorted({f'{name}: {url}' for name, url in found if url not in ALLOWED})
        self.assertEqual(stray, [],
                         '会員申込の行き先がそろっていない: ' + ', '.join(stray))


if __name__ == '__main__':
    unittest.main()
