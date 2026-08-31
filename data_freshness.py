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

# 予定時刻を過ぎたまま次回実行時刻が進まないジョブを「止まっている」と見なすまでの猶予。
#
# ⚠️ APScheduler は**ジョブを投げた時点**で次回実行時刻を進める。処理が長引いても
#    予定は先に進むので、過去のまま止まっているのは「発火していない」ことを意味する。
#    2026-08-31、17:15以降の5本が今日の予定のまま進まなくなっていた
#    （scheduler.running は true のままで、画面にもログにも何も出なかった）。
SCHEDULER_OVERDUE_MINUTES = 15


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


def last_run(client, job_id):
    """job_runs から、そのジョブの直近1回を返す（無ければ None）。

    ⚠️ **データの新しさとは別の情報。** データは「取れなかった」と
       「変わらなかった」を区別できない。株価の price_updated_at は
       株価が変わったときしか動かないので、取得0件で終わった日も
       前回の更新日時が残り、新しく見えてしまう。
    """
    try:
        rows = (client.table('job_runs')
                .select('ran_at, ok, detail')
                .eq('job_id', job_id)
                .order('ran_at', desc=True).limit(1).execute().data or [])
    except Exception as e:
        print('実行記録の取得に失敗 (%s): %s' % (job_id, str(e)[:120]))
        return None
    return rows[0] if rows else None


def _run_suffix(run):
    """直近の実行結果を detail の末尾に足す文字列にする。

    記録がまだ無いときは何も足さない。テーブルを作った直後は1回も
    走っていないのが正常で、そこを警告にするとパネルが信用されなくなる。
    """
    if not run:
        return '', None
    when = _parse(run.get('ran_at'))
    stamp = when.strftime('%m-%d %H:%M') if when else '?'
    if run.get('ok'):
        return '　直近の実行 %s %s' % (stamp, run.get('detail') or ''), True
    return '　⚠️ 直近の実行が失敗 %s %s' % (stamp, run.get('detail') or ''), False


def _late_text(minutes):
    """遅れを人が読める形にする。"""
    minutes = int(minutes)
    if minutes < 60:
        return '%d分' % minutes
    return '%d時間%d分' % (minutes // 60, minutes % 60)


def scheduler_item(jobs, now=None):
    """定期実行そのものが生きているかを見る。

    ⚠️ **`scheduler.running` を当てにしないこと。** あれは start() したかの
       フラグで、ループが死んでいても true のまま返る。実際に見るのは
       **次回実行時刻が進んでいるか**。APScheduler はジョブを投げた時点で
       予定を先に進めるので、予定が過去のまま残っている＝発火していない。

    ⚠️ ここは「データが古いか」ではなく「仕組みが動いているか」を見る唯一の行。
       他の行はデータを見るので、手で流し直せば緑に戻ってしまい、
       ジョブが死んでいることを隠せてしまう
       （2026-08-31、決算の行は手でボタンを押したせいで緑のままだった）。

    Args:
        jobs: [{'id': str, 'next_run_time': iso文字列 or None}, ...]
              取得できなかったときは None を渡す（正常に倒さない）。
    """
    now = now or _now()
    item = {
        'key': 'scheduler',
        'label': '定期実行そのもの',
        'schedule': '全ジョブの次回予定を突き合わせ',
        'as_of': None,
        'when_text': None,
        'detail': '',
        'age': None,
        'note': '次回実行時刻が予定を過ぎたまま進まないジョブは発火していない。'
                'running フラグは死んでいても true のままなので当てにならない。',
    }

    if jobs is None:
        item['detail'] = 'スケジューラの状態を取得できませんでした'
        item['status'] = 'warn'
        return item
    if not jobs:
        item['detail'] = 'ジョブが1本も登録されていません'
        item['status'] = 'bad'
        return item

    scheduled, overdue = [], []
    for job in jobs:
        when = _parse(job.get('next_run_time'))
        if when is None:
            continue
        scheduled.append((when, job.get('id')))
        late = (now - when).total_seconds() / 60.0
        if late > SCHEDULER_OVERDUE_MINUTES:
            overdue.append((late, when, job.get('id')))

    # 全ジョブに次回予定が無い＝start() されていない（ENABLE_SCHEDULER=false 等）
    if not scheduled:
        item['detail'] = 'スケジューラが起動していません（%d本すべて予定なし）' % len(jobs)
        item['status'] = 'bad'
        return item

    if overdue:
        overdue.sort(reverse=True)
        late, when, job_id = overdue[0]
        item['as_of'] = when.isoformat()
        item['when_text'] = '%s が %s 遅れ' % (job_id, _late_text(late))
        item['detail'] = '%d / %d 本が予定時刻を過ぎたまま' % (len(overdue), len(jobs))
        item['status'] = 'bad'
        return item

    nxt, job_id = min(scheduled)
    item['as_of'] = nxt.isoformat()
    item['when_text'] = '次は %s %s' % (nxt.strftime('%m-%d %H:%M'), job_id)
    item['detail'] = '%d 本すべて予定どおり' % len(jobs)
    item['status'] = 'ok'
    return item


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


# 画面に「◯/◯時点」と出しはじめる古さ（営業日）。
# 1営業日は「昨日の終値が出ている」ふつうの状態なので出さない。
PRICE_BANNER_STALE_DAYS = 1

_price_as_of_cache = {'at': None, 'value': (None, None)}
_PRICE_AS_OF_CACHE_SECONDS = 300


def price_as_of(client=None, now=None, use_cache=True):
    """最後に株価取得が**成功した**時刻と、その営業日数を返す。

    なぜ画面に出すか:
      2026-08-31、取得が3回とも空振りして、スクリーナーが金曜の終値を
      **今日の株価の顔で**丸1日出していた。取得が失敗すること自体は
      外部次第で避けきれないが、「いつ時点の値か」を書いておけば
      **見る人の判断は狂わない**。外部が何をしようが必ず効く唯一の手当て。

    ⚠️ price_updated_at は使わない。あれは**株価が変わったとき**しか動かず、
       取得が0件で終わった日も新しく見える（それで今回見逃した）。
       見るのは job_runs に残った「取得できた実績」のほう。

    ⚠️ 全ページの描画で呼ぶので、失敗しても例外を出さないこと。
       出せないときは (None, None) を返し、画面は何も出さない。
    """
    import time as _time
    if use_cache and _price_as_of_cache['at'] is not None:
        if _time.time() - _price_as_of_cache['at'] < _PRICE_AS_OF_CACHE_SECONDS:
            return _price_as_of_cache['value']

    now = now or _now()
    value = (None, None)
    try:
        rows = ((client or _client()).table('job_runs')
                .select('ran_at')
                .eq('job_id', 'price_update').eq('ok', True)
                .order('ran_at', desc=True).limit(1).execute().data or [])
        if rows:
            when = _parse(rows[0]['ran_at'])
            value = (when, business_days_since(when, now))
    except Exception as e:
        print('株価の取得時点を読めませんでした: %s' % str(e)[:120])

    if use_cache:
        _price_as_of_cache.update({'at': _time.time(), 'value': value})
    return value


def price_fetch_failing(client=None):
    """直近の株価取得が失敗しているか。

    ⚠️ price_as_of() だけで帯を出すと、**成功した記録が1件も無いとき**に
       帯が永久に出ない。取得がずっと弾かれている最中に公開すると
       まさにこの形になり、古い株価が何の断りもなく出続ける。
       「いつ時点か」が言えなくても「取れていない」ことは言える。
    """
    try:
        run = last_run(client or _client(), 'price_update')
    except Exception:
        return False        # 読めないことを理由に帯を出さない（画面優先）
    return bool(run) and not run.get('ok')


# 直近の株価取得がこれ以上さかのぼると、実行記録の有無に関わらず異常とみなす。
HEALTH_PRICE_STALE_DAYS = 2


def health(jobs, client=None, now=None):
    """外形監視用の軽い死活判定。(ok, problem) を返す。

    なぜ別に作るか:
      鮮度パネルは管理画面を**開かないと見えない**。定期実行が止まっても、
      誰かがダッシュボードを開くまで誰も気づかない
      （2026-08-31、株価が丸1日止まっていたのに気づいたのは翌日だった）。
      UptimeRobot が5分おきに `/health/db` を叩いているので、
      そこに相乗りして「止まったら503」を返す口をつくる。

    ⚠️ **summary() を呼ばないこと。** あれは screened_latest を3,669行読む。
       5分おきに叩かれる口で回すには重すぎる。ここは
       スケジューラの予定（メモリ上）と job_runs 1行だけで判定する。

    ⚠️ **判定できないときは異常side（fail-closed）に倒す。** 読めないことを
       正常として返すと、監視そのものが黙って無効になる。
    """
    now = now or _now()
    if scheduler_item(jobs, now)['status'] == 'bad':
        return False, 'scheduler'

    run = last_run(client or _client(), 'price_update')
    # 記録がまだ無いのは正常。テーブルを作った直後や初回実行前がこれにあたる。
    if run is None:
        return True, None
    if not run.get('ok'):
        return False, 'price'
    age = business_days_since(_parse(run.get('ran_at')), now)
    if age is None or age > HEALTH_PRICE_STALE_DAYS:
        return False, 'price'
    return True, None


def summary(jobs=None):
    """画面に出す鮮度の一覧を返す。

    Args:
        jobs: スケジューラの [{'id', 'next_run_time'}, ...]。
              ⚠️ 省略すると「取得できず」の警告になる。**正常には倒さない。**
              渡し忘れが静かに素通りすると、ジョブが死んでも画面が緑のままになる。

    各項目:
        key / label / schedule / as_of / detail / age / status / note
        as_of  … 最後に実際に値が動いた日時（次回予定ではない）
        age    … その時点からの営業日数
        when_text … 日付＋営業日数では言い表せない行だけが持つ（定期実行の遅れ等）
        status … ok / warn / bad
    """
    now = _now()
    rows = _core_rows()
    total = len(rows) or 1
    client = _client()

    # ⚠️ 先頭は「定期実行そのもの」。ここが死んでいると、以降の行が緑でも
    #    それは手で流し直した結果かもしれない。仕組みの生死を最初に出す。
    items = [scheduler_item(jobs, now)]

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
    price_run_text, price_run_ok = _run_suffix(last_run(client, 'price_update'))
    items.append({
        'key': 'price',
        'label': '株価',
        'schedule': '平日 9:25 / 11:45 / 15:20',
        'as_of': newest.isoformat() if newest else None,
        'detail': '%d / %d 件が1営業日以内（%.1f%%）%s%s' % (
            fresh, total, fresh / total * 100,
            '　いちばん古い銘柄は%d営業日前' % oldest if oldest else '',
            price_run_text),
        # ⚠️ **as_of と age は同じ行から取ること。**
        #    以前は as_of に「いちばん新しい更新」、age に「いちばん古い銘柄」を
        #    入れていたため、画面に「2026-08-31（5営業日前）」という、日付と
        #    かっこの中が別の銘柄を指す表示が出ていた
        #    （今日更新されているのに「5営業日前」）。
        #    古い銘柄の話は detail に回す。
        'age': business_days_since(newest, now),
        # ⚠️ **直近の実行が失敗していたら、データが新しく見えても赤にする。**
        #    price_updated_at は株価が変わったときしか動かないので、取得が
        #    0件で終わってもこの列は前回のまま残り、古くならない。
        #    2026-08-31、3回の実行がすべて0件で終わり、スクリーナーが丸1日
        #    前営業日の終値を出していたのに、ここは98.3%で緑寄りだった。
        'status': ('bad' if price_run_ok is False
                   else 'bad' if behind > total * 0.01
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
    cross_run_text, cross_run_ok = _run_suffix(last_run(client, 'daily_and_crosses'))
    items.append({
        'key': 'signals',
        'label': 'テクニカル（GC/DC）',
        'schedule': '毎日 3:30 に日足から再計算',
        'as_of': newest_s.isoformat() if newest_s else None,
        'detail': '%d件を計算済み%s' % (count_s, cross_run_text),
        'age': age_s,
        'status': 'bad' if cross_run_ok is False else _status(age_s, 3, 6),
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
