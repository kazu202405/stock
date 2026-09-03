# -*- coding: utf-8 -*-
"""TDnetの決算短信から取った通期予想を screened_latest に入れる。

## なぜ入れ替えるか

業績予想の取得元が Yahoo!ファイナンス日本版のHTMLだけで、いちばん脆い経路に
乗っていた（充足率83.6%）。決算短信は**会社が自分で出した一次情報**。

2026-09-03 に実物60本で突き合わせた結果:
    通期予想が取れた 31本（52%。四半期短信は予想を再掲しない会社があるため）
    DBの値と比較できた 27本のうち **25本が完全一致**
    ずれた2本は**どちらもTDnetが正しく、DBが古かった**
        内田洋行 … DBは2026年7月期のまま、短信は2027年7月期を出していた
        G-Enjin  … DBは0、短信は35.1億

⚠️ **52%は「取れない」ではない。** 会社は通期予想を本決算で出し、修正が
   あったときだけ出し直す。四半期短信に予想が載らないのは正常。
   毎日拾い続ければ、1年でほぼ全社の予想を会社の言葉で持てる。

⚠️ **TDnetは直近31日ぶんしか公開されていない。** 取りこぼした日は取り返せない。
   だから毎日走らせる。過去に遡って一気に埋めることはできない。

## 期をまたぐときの決まり

⚠️ **同じ期の値でそろえること。** 短信で売上だけ取れて営業益が取れなかったとき、
   古い期の営業益を残すと「売上は今期・営業益は前期」が並ぶ。実際に内田洋行が
   この形だった（短信は売上だけ、DBには前期の営業益）。
   ∴ **決算期が変わるなら、取れなかった項目は消す。** 同じ期なら足すだけ。
"""

from __future__ import annotations

from datetime import datetime, timezone

FORECAST_FIELDS = ('forecast_revenue', 'forecast_op_income',
                   'forecast_ordinary_income', 'forecast_net_income')

# source_status に残す印。分析側はこれを見て上書きを控える。
SOURCE_KEY = 'forecast'
SOURCE_NAME = 'tdnet'


def build_update(existing: dict, forecast: dict) -> dict:
    """1銘柄ぶんの更新内容を作る。変えるものが無ければ {}。

    Args:
        existing: いまDBにある行（forecast_* と forecast_year を見る）
        forecast: tdnet.extract_forecast() の戻り
    """
    if not forecast:
        return {}
    year = forecast.get('forecast_year')
    old_year = (existing or {}).get('forecast_year')

    update = {}
    same_year = bool(year and old_year and str(year)[:10] == str(old_year)[:10])
    for field in FORECAST_FIELDS:
        if field in forecast:
            update[field] = forecast[field]
        elif not same_year:
            # ⚠️ 期が変わるのに前期の値を残すと、期の違う数字が並ぶ
            if (existing or {}).get(field) is not None:
                update[field] = None
    if year:
        update['forecast_year'] = year

    # 中身が今と同じなら書かない（更新日時だけ動かさない）
    if all((existing or {}).get(k) == v for k, v in update.items()):
        return {}
    return update


def mark(existing_status, year=None) -> dict:
    """source_status に「この予想は短信から来た」と残す。"""
    from supabase_client import merge_source_status

    return merge_source_status(existing_status, {
        SOURCE_KEY: {
            'status': 'success',
            'source': SOURCE_NAME,
            'year': year,
            'at': datetime.now(timezone.utc).isoformat(),
        }
    })


def apply(client, rows, dry_run=False, log=print) -> dict:
    """collect() の結果を書き込む。

    ⚠️ 上場廃止・screened_latest に無い銘柄は触らない。
    """
    stats = {'seen': 0, 'updated': 0, 'skipped': 0, 'unknown': 0, 'failed': 0}
    targets = [r for r in rows if r.get('forecast')]
    if not targets:
        return stats

    codes = sorted({r['company_code'] for r in targets})
    existing = {}
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        try:
            got = (client.table('screened_latest')
                   .select('company_code, forecast_revenue, forecast_op_income, '
                           'forecast_ordinary_income, forecast_net_income, '
                           'forecast_year, delisted_at, source_status')
                   .in_('company_code', chunk).execute()).data or []
        except Exception as e:
            log('既存の取得に失敗: %s' % str(e)[:120])
            stats['failed'] += len(chunk)
            continue
        for row in got:
            existing[row['company_code']] = row

    for r in targets:
        stats['seen'] += 1
        code = r['company_code']
        row = existing.get(code)
        if row is None:
            stats['unknown'] += 1          # 新規上場などでまだ銘柄が無い
            continue
        if row.get('delisted_at'):
            stats['skipped'] += 1
            continue
        update = build_update(row, r['forecast'])
        if not update:
            stats['skipped'] += 1
            continue
        update['source_status'] = mark(row.get('source_status'),
                                       r['forecast'].get('forecast_year'))
        if dry_run:
            log('  [dry-run] %s %s %s' % (code, r['company_name'], update))
            stats['updated'] += 1
            continue
        try:
            (client.table('screened_latest').update(update)
             .eq('company_code', code).execute())
            stats['updated'] += 1
        except Exception as e:
            stats['failed'] += 1
            log('  %s の書き込みに失敗: %s' % (code, str(e)[:120]))
    return stats


def run(client, day=None, session=None, dry_run=False, log=print) -> dict:
    """その日の決算短信を拾って書き込む。"""
    from datetime import date

    import tdnet

    day = day or date.today()
    rows = tdnet.collect(day, session=session, log=log)
    stats = apply(client, rows, dry_run=dry_run, log=log)
    stats['reports'] = len(rows)
    stats['with_forecast'] = len([r for r in rows if r.get('forecast')])
    stats['day'] = day.isoformat()
    return stats
