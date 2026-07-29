"""
移動平均のクロス（ゴールデンクロス／デッドクロス）を株価履歴から計算する。

背景:
  従来は kabutan のGC銘柄一覧をスクレイピングし、gc_date に「取得した時刻」を
  全銘柄一律で入れていた。そのため「いつGCしたか」が分からず、毎回上書きされて
  履歴も残らなかった。

  stock_price_history に日足1年分があるので、5日線と25日線の交差を自前で
  計算すれば発生日を正確に出せる。過去分も遡れるうえ、外部サイトの構造変更に
  影響されない。

定義:
  ゴールデンクロス … 短期線が長期線を下から上へ抜けた日
  デッドクロス     … 短期線が長期線を上から下へ抜けた日
"""

from datetime import datetime, timezone

SHORT_WINDOW = 5
LONG_WINDOW = 25

# 取引所ローカルの日付に正規化するオフセット（price_history と同じ考え方）
_LOCAL_DATE_OFFSET = 12 * 3600


def _to_date(unix_sec):
    return datetime.fromtimestamp(unix_sec + _LOCAL_DATE_OFFSET, tz=timezone.utc).date().isoformat()


def _sma(values, window):
    """単純移動平均。window未満の位置は None を入れて長さを揃える。"""
    out = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        out.append(total / window if i >= window - 1 else None)
    return out


def detect_crosses(rows, short_window=SHORT_WINDOW, long_window=LONG_WINDOW):
    """日足から交差を検出する。

    Args:
        rows: [{time: UNIX秒, close: 終値}, ...] を古い順で
    Returns:
        {'crosses': [{'date': 'YYYY-MM-DD', 'type': 'gc'|'dc'}, ...],
         'latest_gc_date': str|None, 'latest_dc_date': str|None, 'cross_count': int}
    """
    empty = {'crosses': [], 'latest_gc_date': None, 'latest_dc_date': None, 'cross_count': 0}
    if not rows:
        return empty

    data = [r for r in rows if r and r.get('close') is not None and r.get('time') is not None]
    data.sort(key=lambda r: r['time'])
    if len(data) < long_window + 1:
        # 長期線が引けるだけの本数が無い（新規上場銘柄など）
        return empty

    closes = [float(r['close']) for r in data]
    short_ma = _sma(closes, short_window)
    long_ma = _sma(closes, long_window)

    crosses = []
    prev_diff = None
    for i in range(len(data)):
        s, l = short_ma[i], long_ma[i]
        if s is None or l is None:
            continue
        diff = s - l
        if prev_diff is not None and diff != 0:
            # 符号が入れ替わった日を交差日とする
            if prev_diff <= 0 < diff:
                crosses.append({'date': _to_date(data[i]['time']), 'type': 'gc'})
            elif prev_diff >= 0 > diff:
                crosses.append({'date': _to_date(data[i]['time']), 'type': 'dc'})
        if diff != 0:
            prev_diff = diff

    latest_gc = next((c['date'] for c in reversed(crosses) if c['type'] == 'gc'), None)
    latest_dc = next((c['date'] for c in reversed(crosses) if c['type'] == 'dc'), None)
    return {
        'crosses': crosses,
        'latest_gc_date': latest_gc,
        'latest_dc_date': latest_dc,
        'cross_count': len(crosses),
    }


def calculate_for_code(company_code, short_window=SHORT_WINDOW, long_window=LONG_WINDOW):
    """1銘柄だけ、保存済みの日足から交差を計算して保存する。

    銘柄ページを開いたときに呼ぶ。日足は閲覧時に自動で取り直される
    （price_history.get_daily）のに対し、GC/DCは一括再計算でしか更新されないため、
    放っておくとチャートには出ているクロスがトレンド表示に反映されない。

    ネットワークには触らない（DBの日足を読むだけ）。
    Returns: {'latest_gc_date', 'latest_dc_date', 'changed'} / 日足が無ければ None
    """
    from supabase_client import get_supabase_client
    client = get_supabase_client()

    res = (client.table('stock_price_history')
           .select('daily_1y')
           .eq('company_code', company_code)
           .execute())
    if not res.data:
        return None

    daily = res.data[0].get('daily_1y')
    if isinstance(daily, str):
        import json
        try:
            daily = json.loads(daily)
        except Exception:
            daily = None
    if not daily:
        return None

    result = detect_crosses(daily, short_window, long_window)

    stored = (client.table('ma_crosses')
              .select('latest_gc_date, latest_dc_date')
              .eq('company_code', company_code)
              .execute())
    prev = stored.data[0] if stored.data else {}
    changed = (str(prev.get('latest_gc_date') or '')[:10] != (result['latest_gc_date'] or '') or
               str(prev.get('latest_dc_date') or '')[:10] != (result['latest_dc_date'] or ''))

    # 変わっていなければ書かない（ページを開くたびに2テーブル更新するのは無駄）
    if changed:
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            'company_code': company_code,
            'latest_gc_date': result['latest_gc_date'],
            'latest_dc_date': result['latest_dc_date'],
            'cross_count': result['cross_count'],
            'crosses': result['crosses'],
            'short_window': short_window,
            'long_window': long_window,
            'calculated_at': now,
        }
        try:
            client.table('ma_crosses').upsert(payload).execute()
            sync_gc_to_screened(client, [payload])
        except Exception as e:
            print(f'GC/DCの保存エラー ({company_code}): {e}')

    return {
        'latest_gc_date': result['latest_gc_date'],
        'latest_dc_date': result['latest_dc_date'],
        'changed': changed,
    }


def compare_with_signals(client=None, recent_days=10, limit_samples=10):
    """かぶたん(signal_stocks)と自前計算(ma_crosses)を突き合わせる。

    ⚠️ 日付の一致は見ない。かぶたんの日付は「一覧に載っているのを取得した日」で
    あって発生日ではないため、金曜に発生したクロスが月曜の日付で入る。
    発生日の正本はあくまで自前計算（日足から算出）。

    ここで見たいのは「かぶたんが最近クロスを検知しているのに、自前側が古いまま」
    という取りこぼし。これは日足の更新が止まっているサインになる。
    """
    from datetime import date, timedelta
    if client is None:
        from supabase_client import get_supabase_client
        client = get_supabase_client()

    cutoff = (date.today() - timedelta(days=recent_days)).isoformat()

    def _fetch_all(table, cols):
        out, page = [], 0
        while page < 10:
            res = client.table(table).select(cols).range(page * 1000, page * 1000 + 999).execute()
            chunk = res.data or []
            out.extend(chunk)
            if len(chunk) < 1000:
                break
            page += 1
        return out

    signals = {r['company_code']: r for r in _fetch_all('signal_stocks', 'company_code, gc_date, dc_date')}
    mine = {r['company_code']: r for r in _fetch_all('ma_crosses', 'company_code, latest_gc_date, latest_dc_date')}

    stale = []
    for code, s in signals.items():
        m = mine.get(code)
        if not m:
            continue
        for sig_key, my_key in (('gc_date', 'latest_gc_date'), ('dc_date', 'latest_dc_date')):
            sig_date = str(s.get(sig_key) or '')[:10]
            my_date = str(m.get(my_key) or '')[:10]
            if sig_date and sig_date >= cutoff and my_date < sig_date:
                stale.append({'company_code': code, 'kind': sig_key[:2],
                              'kabutan': sig_date, 'mine': my_date or None})
                break

    return {
        'compared': len(set(signals) & set(mine)),
        'stale_count': len(stale),
        'samples': stale[:limit_samples],
    }


def calculate_for_all(progress=None, should_stop=None,
                      short_window=SHORT_WINDOW, long_window=LONG_WINDOW):
    """保存済みの日足から全銘柄の交差を計算し ma_crosses に保存する。

    ネットワークアクセスは一切しない（DBに入っている日足だけを使う）ため、
    外部サイトのレート制限とは無関係に何度でも実行できる。

    Args:
        progress: done, total, saved を受け取るコールバック
        should_stop: True を返すと中断する
    """
    from supabase_client import get_supabase_client
    client = get_supabase_client()

    # 日足を一括で読む
    rows = []
    page = 0
    while page < 20:
        res = (client.table('stock_price_history')
               .select('company_code, daily_1y')
               .range(page * 500, page * 500 + 499)
               .execute())
        chunk = res.data or []
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 500:
            break
        page += 1

    total = len(rows)
    now = datetime.now(timezone.utc).isoformat()
    payloads = []
    skipped = 0

    for i, r in enumerate(rows):
        if should_stop and should_stop():
            break
        daily = r.get('daily_1y')
        if isinstance(daily, str):
            import json
            try:
                daily = json.loads(daily)
            except Exception:
                daily = None
        if not daily:
            skipped += 1
        else:
            result = detect_crosses(daily, short_window, long_window)
            if result['cross_count'] or result['latest_gc_date'] or result['latest_dc_date']:
                payloads.append({
                    'company_code': r['company_code'],
                    'latest_gc_date': result['latest_gc_date'],
                    'latest_dc_date': result['latest_dc_date'],
                    'cross_count': result['cross_count'],
                    'crosses': result['crosses'],
                    'short_window': short_window,
                    'long_window': long_window,
                    'calculated_at': now,
                })
            else:
                skipped += 1

        if progress and (i % 50 == 0 or i == total - 1):
            progress(done=i + 1, total=total, saved=len(payloads))

    # まとめて保存（1件ずつupsertすると件数分の往復が発生して遅い）
    saved = 0
    first_error = None
    for i in range(0, len(payloads), 200):
        batch = payloads[i:i + 200]
        try:
            client.table('ma_crosses').upsert(batch).execute()
            saved += len(batch)
        except Exception as e:
            print(f'ma_crosses 保存エラー: {e}')
            if first_error is None:
                first_error = str(e)
        if progress:
            progress(done=total, total=total, saved=saved)

    # 保存が1件も通らなかった場合は失敗として扱う。
    # ログに出すだけだと画面上は「完了」と表示され、
    # テーブル未作成などの原因に気づけないため。
    if payloads and saved == 0:
        raise RuntimeError(
            f'計算は{len(payloads)}件成功しましたが、保存が1件も通りませんでした。'
            f'migration_ma_crosses.sql を適用済みか確認してください。原因: {first_error}'
        )

    # スクリーナーで並べ替えられるよう、GC/DC日を screened_latest にも複製する
    synced = sync_gc_to_screened(client, payloads)

    return {'total': total, 'saved': saved, 'skipped': skipped, 'synced': synced}


def sync_gc_to_screened(client, payloads):
    """ma_crosses の結果を screened_latest.gc_date / dc_date に反映する。

    スクリーナーは screened_latest を並べ替えるため、GC日をこちらへ持たせる。
    日付は銘柄ごとにばらばらなので、同じ値でまとめる余地が少なく、
    1件ずつ更新する。定期的に走る計算処理の一部なので許容範囲。
    """
    synced = 0
    for p in payloads:
        try:
            client.table('screened_latest').update({
                'gc_date': p.get('latest_gc_date'),
                'dc_date': p.get('latest_dc_date'),
            }).eq('company_code', p['company_code']).execute()
            synced += 1
        except Exception as e:
            print(f"GC同期エラー ({p['company_code']}): {e}")
    return synced
