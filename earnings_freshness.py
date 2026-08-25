"""決算の取りこぼしを見つける。

なぜ要るか:
  決算の検知は kabutan のスクレイピング頼み（1日2回）。サイトの構造が変わる・
  遮断される・その銘柄が載らない、のどれかが起きると、**その銘柄は決算が
  出ても古い財務のまま残る**。しかもエラーは出ない。「検知しなかった」だけで、
  処理としては正常に終わる。気づく手段が無いのが問題だった。

  一方、決算月（fiscal_month）は98%の銘柄で分かっている。日本の決算発表は
  期末から45日以内が原則なので、

      直近の決算期末から猶予を過ぎたのに、最終分析日がその期末より前

  の銘柄を数えれば、漏れが数字で見える。

⚠️ 鮮度は `analyzed_at` で見る。`updated_at` は一部の保存経路でしか
   書かれておらず、2月のまま止まっている行がある（中身は古くない）。
   `updated_at` を信じると誤診する。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone

# 決算発表の期限。東証は期末から45日以内が原則だが、遅れる会社もあるので
# 少し余裕を見る。短くすると「まだ出ていないだけ」を漏れと数えてしまう。
DISCLOSURE_GRACE_DAYS = 75


def _as_date(value):
    if value is None or value == '':
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        text = str(value).replace('Z', '+00:00')
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date()
    except (TypeError, ValueError):
        return None


def last_fiscal_end(fiscal_month, today):
    """直近に終わった決算期末を返す。

    8月決算で今日が8/25なら、まだ8月は終わっていないので前年の8/31。
    """
    if not fiscal_month or not (1 <= int(fiscal_month) <= 12):
        return None
    month = int(fiscal_month)
    year = today.year
    end = date(year, month, calendar.monthrange(year, month)[1])
    if end > today:
        year -= 1
        end = date(year, month, calendar.monthrange(year, month)[1])
    return end


def is_stale(row, today):
    """直近の決算が反映されていない疑いがあるか。"""
    if row.get('delisted_at'):
        return False
    fiscal_end = last_fiscal_end(row.get('fiscal_month'), today)
    if fiscal_end is None:
        return False
    if (today - fiscal_end).days < DISCLOSURE_GRACE_DAYS:
        return False        # まだ発表の期限が来ていない
    analyzed = _as_date(row.get('analyzed_at'))
    if analyzed is None:
        return False        # 一度も分析していない銘柄は別の話（バックフィルの領分）
    return analyzed < fiscal_end


def find_stale(rows, today=None):
    """取りこぼしの疑いがある銘柄を、古い順に返す。"""
    today = today or datetime.now(timezone.utc).date()
    found = []
    for row in rows:
        if is_stale(row, today):
            found.append({
                'company_code': row.get('company_code'),
                'company_name': row.get('company_name'),
                'fiscal_month': row.get('fiscal_month'),
                'fiscal_end': last_fiscal_end(row.get('fiscal_month'), today).isoformat(),
                'analyzed_at': row.get('analyzed_at'),
            })
    found.sort(key=lambda r: str(r.get('analyzed_at') or ''))
    return found
