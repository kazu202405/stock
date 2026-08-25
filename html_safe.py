"""外から来た文章を、画面にそのまま流し込める形にする。

なぜ要るか:
  事業概要（business_summary_jp）は
    ① Yahoo!ファイナンス日本版のページから取ってくる
    ② 英語の概要を OpenAI で日本語にする
    ③ 管理画面から手で直す
  の3経路で入ってくる。①と②は**こちらが中身を決められない**。

  そして画面側は
    stock_detail.html   summaryContent.innerHTML = `...${data.business_summary_jp}...`
    _report_body.html   {{ report.business_summary|safe }}
  と、**HTMLとして解釈する**形で出している。改行を <br> で入れている都合で
  そうなっていた。

  つまり「取得元のページ」や「LLMの出力」に <script> や
  <img onerror=...> が混じれば、そのまま公開ページで動く。
  2026-08-25 時点の実データに危険なタグは1つも無い（<br> が2,497件だけ）が、
  **入る経路は開いている**。

方針:
  改行のための <br> だけを残し、他のタグは文字として見せる。
  タグを丸ごと消すのではなく**エスケープ**するのは、消すと
  「<社名>」のような普通の文章まで欠けるため。
"""

from __future__ import annotations

import html
import re

# 残すタグ。ここを増やすときは、属性を書けないことを必ず確かめること
# （<a href> を許すと javascript: が書ける）。
_BR = re.compile(r'<\s*br\s*/?\s*>', re.I)
_PLACEHOLDER = '\x00BR\x00'


def sanitize_rich_text(text):
    """<br> だけを残してエスケープする。None はそのまま返す。"""
    if text is None:
        return None
    if not isinstance(text, str):
        return text
    kept = _BR.sub(_PLACEHOLDER, text)
    escaped = html.escape(kept, quote=False)
    return escaped.replace(_PLACEHOLDER, '<br>')


def strip_tags(text):
    """タグを落として素の文章にする。meta description のように
    HTMLを置けない場所で使う。"""
    if not text:
        return text
    return re.sub(r'<[^>]*>', '', text)
