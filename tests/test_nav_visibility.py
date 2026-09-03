# -*- coding: utf-8 -*-
"""役割ごとに、ヘッダーと運用ボタンの見え方を固定する（2026-09-03）。

## 隠しているもの

企業比較・テーマ・業種・決算情報は、中身を確認してから公開する。それまでは
**管理者にだけ**出す。銘柄ページの「更新」も会員には出さない。

## なぜテストで固定するか

⚠️ **ヘッダーは2か所にある。** デスクトップのドロップダウンとスマホの
   スライドメニュー。片方だけ直すと、スマホからは見えたままになる
   （このリポジトリで実際に起きている形）。**両方を数える。**

⚠️ 条件を足したことを目で確かめて終わりにしない。**実際にレンダリングして、
   会員のHTMLに文字が1つも無いこと**を見る。
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from app import app  # noqa: E402
from flask import render_template  # noqa: E402

GUEST = {'is_logged_in': False, 'user_role': None,
         'is_member': False, 'is_admin': False}
MEMBER = {'is_logged_in': True, 'user_role': 'user',
          'is_member': True, 'is_admin': False}
ADMIN = {'is_logged_in': True, 'user_role': 'admin',
         'is_member': True, 'is_admin': True}

# 確認できるまで会員に出さないもの
HIDDEN = ['/compare', '/themes', '/earnings']
# 会員にも出したままのもの（隠しすぎていないことの確認）
VISIBLE = ['/screener', '/simulator', '/report', '/market']


def render(template, ctx, **extra):
    with app.test_request_context('/dashboard'):
        return render_template(template, **dict(ctx, **extra))


def nav_links(html, href):
    """**ヘッダーのメニューだけ**を数える。

    ⚠️ ページ本文にも同じ行き先へのリンクがある（ダッシュボードの「銘柄検索」、
       空のときの案内文、料金ページ、レポート選択、銘柄が無いときの案内）。
       それらまで数えると、ここで見たいこと（メニューを隠せているか）が
       分からなくなる。∴ ナビ専用のclassが付いたものだけを数える。

    ⚠️ **メニューを隠しても、それらの入口からは辿れる。** 本当に閉じたいなら
       ページ側のガードが要る。ただし `/themes` は SEO のために意図して
       公開しているページなので、閉じると検索からの入口を失う。
    """
    body = re.sub(r'\{#.*?#\}', '', html, flags=re.S)
    body = re.sub(r'<script.*?</script>', '', body, flags=re.S | re.I)
    pattern = (r'<a\s[^>]*href="%s"[^>]*class="(?:nav-dropdown-item|slide-menu-link)"'
               % re.escape(href))
    return len(re.findall(pattern, body))


class ヘッダーの見え方(unittest.TestCase):

    def test_会員には出さない(self):
        html = render('stock.html', MEMBER)
        for href in HIDDEN:
            self.assertEqual(0, nav_links(html, href),
                             '%s が会員に見えている' % href)

    def test_未ログインにも出さない(self):
        html = render('stock.html', GUEST)
        for href in HIDDEN:
            self.assertEqual(0, nav_links(html, href))

    def test_管理者には出す(self):
        """⚠️ デスクトップとスマホの2か所。片方だけだと、もう片方から漏れる。"""
        html = render('stock.html', ADMIN)
        for href in HIDDEN:
            self.assertEqual(2, nav_links(html, href),
                             '%s が2か所に出ていない（デスクトップとスマホ）' % href)

    def test_隠しすぎていない(self):
        """スクリーニング・過去シミュレーション・レポート・マーケットは会員にも出す。"""
        html = render('stock.html', MEMBER)
        for href in VISIBLE:
            self.assertGreater(nav_links(html, href), 0,
                               '%s まで消えている' % href)


class 運用ボタン(unittest.TestCase):
    """定期実行で置き換わったものは閉じてある。

    決算銘柄を更新 … 22:00 の再分析＋23:30 の取りこぼし拾いが同じ処理をする
    N件 詳細取得   … 財務は決算のときだけ変わり、そこは上の2本が見ている
    スコア再計算   … 保存のたびに match_rate は計算される。定義変更時は
                     recalc_match_rates.py（全銘柄）の仕事
    """

    def buttons(self, ctx):
        html = render('stock.html', ctx)
        body = re.sub(r'\{#.*?#\}', '', html, flags=re.S)
        return body

    def test_閉じた3つは管理者にも出ない(self):
        body = self.buttons(ADMIN)
        for el in ('id="earningsBtn"', 'id="wlAnalyzeBtn"', 'id="recalcBtn"'):
            self.assertNotIn(el, body, '%s が復活している' % el)

    def test_テクニカルの詳細取得は残す(self):
        """⚠️ GC/DC銘柄は毎日入れ替わるのに、scheduled_fetch_gc_dc は一覧を
        取るだけで分析しない。ここを消すと新しい銘柄が未分析のまま残る。"""
        self.assertIn('id="techAnalyzeBtn"', self.buttons(ADMIN))

    def test_会員には運用ボタンを出さない(self):
        body = self.buttons(MEMBER)
        for el in ('id="earningsBtn"', 'id="wlAnalyzeBtn"', 'id="recalcBtn"',
                   'id="techAnalyzeBtn"'):
            self.assertNotIn(el, body)


class 銘柄ページの更新ボタン(unittest.TestCase):
    """押すと外部APIを叩く。会員が連打するとレート制限を招く。"""

    def render_detail(self, ctx):
        html = render('stock_detail.html', ctx, stock_code='7203', company={})
        return re.sub(r'\{#.*?#\}', '', html, flags=re.S)

    def test_会員には出さない(self):
        self.assertNotIn('id="refreshBtn"', self.render_detail(MEMBER))

    def test_管理者には残す(self):
        """1銘柄だけ数字がおかしいときの手当てに要る。"""
        self.assertIn('id="refreshBtn"', self.render_detail(ADMIN))

    def test_低負荷更新は管理者だけ(self):
        self.assertIn('id="safeRefreshBtn"', self.render_detail(ADMIN))
        self.assertNotIn('id="safeRefreshBtn"', self.render_detail(MEMBER))


if __name__ == '__main__':
    unittest.main()
