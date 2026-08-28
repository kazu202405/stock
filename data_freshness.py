# -*- coding: utf-8 -*-
"""定期実行が「ちゃんと動いているか」をデータ側から測る。

なぜ要るか:
  定期実行は14本あるが、**通知も警告も1つも無い**。
  `/api/scheduler/status` はあるが、出るのは「次にいつ動くか」だけで、
  **前回ちゃんと動いたかは分からない**うえ、どの画面にも出ていなかった。

  ∴ ジョブが今夜から静かに止まっても誰も気づけない。データが古くなるだけで、
    画面はエラーを出さず普通に表示され続ける。「壊れる」のではなく
    「更新が止まる」ので手がかりが出ない。

⚠️ **「次回実行時刻」ではなく「最後に実際に値が動いた実績」を見ること。**
   予定は、ジョブが空振りしていても正常に見える。
   （過去に、サーキットブレーカーが開いたまま skip が正常系として記録され、
     「全件成功なのに中身が空」になった実例がある）

⚠️ **何も無いのが正常なジョブで警告を出さないこと。**
   決算の再分析は、決算が無い時期に何も処理しないのが正しい。
   そこを「古い」と赤くすると、パネル全体が信用されなくなる。
   ここでは「積み残し（処理待ちの件数）」と「データの古さ」を分けて扱う。

⚠️ 母数に PRO Market・ETF・REIT・上場廃止を入れないこと。
   PRO Market は売買が成立しない日が続くのが正常なので、
   株価が古くて当たり前。混ぜると常に赤くなる。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

CORE_SEGMENTS = ('プライム', 'スタンダード', 'グロース')


def _client():
    from supabase_client import get_supabase_client
    return get_supabase_client()


def _now():
    return datetime.now(JST)


def _parse(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(
            str(value).replace('Z', '+00:00')).astimezone(JST)
    except (TypeError, ValueError):
        return None


def business_days_since(dt, now=None):
    """土日を除いた経過日数。祝日は見ない（そこまでの精度は要らない）。

    ⚠️ 暦日で数えると、月曜の朝に「金曜から3日経った」となって常に赤くなる。
       必ず営業日で数えること。
    """
    if dt is None:
        return None
    now = now or _now()
    days = 0
    cur = dt.date()
    end = now.date()
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def _core_rows():
    """内国普通株・上場中の行だけを返す（鮮度の母数）。"""
    client = _client()
    rows, start = [], 0
    while True:
        page = (client.table('screened_latest')
                .select('company_code, market_segment, delisted_at, '
                        'price_updated_at, profile_updated_at, analyzed_at, '
                        'source_status')
                .range(start, start + 999).execute().data or [])
        if not page:
            break
        rows.extend(page)
        if len(page) < 1000:
            break
        start += 1000
    return [r for r in rows
            if (r.get('market_segment') or '') in CORE_SEGMENTS
            and not r.get('delisted_at')]


def _status(age, warn, bad):
    """古さから状態を決める。age が None（測れない）は警告扱い。"""
    if age is None:
        return 'warn'
    if age >= bad:
        return 'bad'
    if age >= warn:
        return 'warn'
    return 'ok'


def _margin_as_of(rows):
    """信用残の基準日（JPXの週末残高の日付）のうち最も新しいもの。"""
    found = []
    for r in rows:
        status = r.get('source_status')
        if isinstance(status, str):
            try:
                status = json.loads(status)
            except (ValueError, TypeError):
                continue
        value = ((status or {}).get('margin_trading') or {}).get('as_of')
        if value:
            found.append(str(value))
    return (max(found) if found else None), len(found)


def summary():
    """画面に出す鮮度の一覧を返す。

    各項目:
        key / label / schedule / as_of / detail / age / status / note
        as_of  … 最後に実際に値が動いた日時（次回予定ではない）
        age    … その時点からの営業日数
        status … ok / warn / bad
    """
    now = _now()
    rows = _core_rows()
    total = len(rows) or 1
    client = _client()
    items = []

    # ── 株価（平日 9:25 / 11:45 / 15:20）────────────────────────────
    stamps = [_parse(r.get('price_updated_at')) for r in rows]
    ages = [business_days_since(s, now) for s in stamps]
    fresh = sum(1 for a in ages if a is not None and a <= 1)
    oldest = max([a for a in ages if a is not None] or [None])
    newest = max([s for s in stamps if s], default=None)
    # ⚠️ 状態は「いちばん古い1件」ではなく「古い銘柄が何件あるか」で決める。
    #    上場廃止の手前で取引が止まった銘柄が1つあるだけで常に警告になり、
    #    パネルが信用されなくなるため。
    behind = sum(1 for a in ages if a is not None and a > 3)
    items.append({
        'key': 'price',
        'label': '株価',
        'schedule': '平日 9:25 / 11:45 / 15:20',
        'as_of': newest.isoformat() if newest else None,
        'detail': '%d / %d 件が1営業日以内（%.1f%%）' % (
            fresh, total, fresh / total * 100),
        'age': oldest,
        'status': ('bad' if behind > total * 0.01
                   else 'warn' if behind > total * 0.002 else 'ok'),
        'note': '一括取得は回によって数銘柄取りこぼすが、次の実行で拾い直す。'
                '4営業日を超えるものが増えたら止まっている疑い'
                '（いまは%d件）。' % behind,
    })

    # ── 会社概要・株主・設立日（毎日 2:00）──────────────────────────
    profs = [_parse(r.get('profile_updated_at')) for r in rows]
    newest_p = max([p for p in profs if p], default=None)
    age_p = business_days_since(newest_p, now)
    filled = sum(1 for p in profs if p)
    items.append({
        'key': 'profile',
        'label': '会社概要・株主・設立日',
        'schedule': '毎日 2:00',
        'as_of': newest_p.isoformat() if newest_p else None,
        'detail': '取得済み %d / %d 件（%.1f%%）' % (
            filled, total, filled / total * 100),
        'age': age_p,
        'status': _status(age_p, 3, 7),
        'note': 'Yahoo!ファイナンス日本版。1晩あたり数十件ずつ増える。'
                '数日まったく動かなければ、遮断されたまま戻っていない疑い。',
    })

    # ── 決算の再分析（21:00 検知 → 22:00 処理）──────────────────────
    pending, last_proc = None, None
    try:
        pending = (client.table('earnings_queue')
                   .select('company_code', count='exact')
                   .is_('processed_at', 'null').limit(1).execute().count or 0)
        done = (client.table('earnings_queue').select('processed_at')
                .not_.is_('processed_at', 'null')
                .order('processed_at', desc=True).limit(1).execute().data or [])
        last_proc = _parse(done[0]['processed_at']) if done else None
    except Exception as e:
        print('決算キューの取得に失敗: %s' % e)
    # ⚠️ 決算が無い時期は処理も無いのが正常。**古さでは赤くしない。**
    #    積み残し（未処理の件数）だけを見る。
    items.append({
        'key': 'earnings',
        'label': '決算の再分析',
        'schedule': '毎日 21:00 検知 / 22:00 処理',
        'as_of': last_proc.isoformat() if last_proc else None,
        'detail': ('未処理 %d件' % pending) if pending is not None else '取得できず',
        'age': business_days_since(last_proc, now),
        'status': ('warn' if pending is None
                   else 'bad' if pending > 300
                   else 'warn' if pending > 50 else 'ok'),
        'note': '決算が無い時期は何も処理しないのが正常なので、'
                '古さでは判断せず、積み残しの件数だけを見る。',
    })

    # ── 信用残（毎週木 4:10 / JPXは週次公表）────────────────────────
    as_of_m, count_m = _margin_as_of(rows)
    dt_m = _parse(as_of_m + 'T15:00:00+09:00') if as_of_m else None
    age_m = business_days_since(dt_m, now)
    items.append({
        'key': 'margin',
        'label': '信用残（信用倍率）',
        'schedule': '毎週木 4:10',
        'as_of': as_of_m,
        'detail': '%d件に取り込み済み' % count_m,
        'age': age_m,
        'status': _status(age_m, 8, 12),
        'note': 'JPXが週1で出す「銘柄別信用取引週末残高」。'
                '公表が1週間ほど遅れるので、8営業日程度までは正常。',
    })

    # ── テクニカル（GC/DC。毎日 9:15 / 17:15）──────────────────────
    # ⚠️ 見るのは `ma_crosses.calculated_at`。**`signal_stocks` ではない。**
    #    signal_stocks は kabutan をスクレイプした旧経路で、いまテクニカル
    #    一覧が読んでいるのは日足から計算した ma_crosses のほう
    #    （/api/technical-stocks が "source":"ma_crosses" を返す）。
    #    最初 signal_stocks を見て「22営業日前」と誤検知した。
    newest_s, count_s = None, 0
    try:
        res = (client.table('ma_crosses').select('calculated_at', count='exact')
               .not_.is_('calculated_at', 'null')
               .order('calculated_at', desc=True).limit(1).execute())
        newest_s = _parse(res.data[0]['calculated_at']) if res.data else None
        count_s = res.count or 0
    except Exception as e:
        print('GC/DCの取得に失敗: %s' % e)
    age_s = business_days_since(newest_s, now)
    items.append({
        'key': 'signals',
        'label': 'テクニカル（GC/DC）',
        'schedule': '毎日 3:30 に日足から再計算',
        'as_of': newest_s.isoformat() if newest_s else None,
        'detail': '%d件を計算済み' % count_s,
        'age': age_s,
        'status': _status(age_s, 3, 6),
        'note': '日足から5日線と25日線の交差を計算した結果。'
                'kabutan由来の signal_stocks とは別物。',
    })

    worst = 'ok'
    for item in items:
        if item['status'] == 'bad':
            worst = 'bad'
            break
        if item['status'] == 'warn':
            worst = 'warn'
    return {
        'generated_at': now.isoformat(),
        'total': len(rows),
        'overall': worst,
        'items': items,
    }
