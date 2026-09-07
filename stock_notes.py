"""銘柄メモ（2026-09-06）。

運営が「この会社は勉強になる」と思った銘柄に一言を残す。

なぜ要るか:
  12項目の適合度で緑の100点を取れるのは 3,886社中185社。数としては多くない
  が、一覧では全部同じ顔をしていて、どれから見ればいいか分からない。しかも
  実測では 1000億円を超える会社に100点は**1社も無く**、点数は身軽な会社に
  寄る。点数は機械の答えで、「見る価値があるか」の答えではない。

⚠️ **screened_latest に列を足さない。** あの表は分析のたびに書き戻される。
   人が書いた文章を、機械が上書きしうる場所に置かない。

⚠️ **migration が未適用でも落ちない。** 適用は運用側が手で行うので、コードが
   先行する期間がある（CLAUDE.md）。表が無い間は「メモが1件も無い」として
   振る舞う。⚠️ ただし**書き込みでは黙って成功しない**。書けなかったのに
   保存できたように見せると、書いた本人が消えたことに気づけない。
"""

import re

MAX_BODY_CHARS = 2000       # 数行のメモ。長文の解説はレポート側の仕事

# メモは会社の見方なので、株価ほど頻繁には古くならない。一方、四半期決算を
# 1回またぐと前提が変わりうる。90日で見直し候補、さらに1か月そのままなら
# 要確認にする。決算処理がメモより後なら、日数を待たず要確認。
REVIEW_AFTER_DAYS = 90
REVIEW_OVERDUE_DAYS = 120

# updated_by は UUID 列。app_users.id（＝auth.users.id）が入る前提。
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
                   re.I)


def _client():
    from supabase_client import get_supabase_client
    return get_supabase_client()


def _table_missing(error) -> bool:
    """migration 未適用のときだけ静かに空を返すための判定。"""
    text = str(error).lower()
    return ('stock_notes' in text
            and ('does not exist' in text or 'pgrst205' in text
                 or 'schema cache' in text))


def normalize_code(code) -> str:
    """`7203.T` でも `7203` でも同じ銘柄として扱う。"""
    return (str(code or '').strip().upper().replace('.T', ''))


def get(company_code: str):
    """1銘柄のメモ。無ければ None。"""
    code = normalize_code(company_code)
    if not code:
        return None
    try:
        rows = (_client().table('stock_notes')
                .select('company_code, body, updated_at')
                .eq('company_code', code).limit(1).execute().data or [])
        return rows[0] if rows else None
    except Exception as e:
        if _table_missing(e):
            return None
        print('銘柄メモの取得に失敗 (%s): %s' % (code, str(e)[:150]))
        return None


def marked_codes() -> set:
    """メモが付いている銘柄コードの集合。一覧に印を出すために使う。

    ⚠️ 件数は運営が書いた分だけなので小さい。全件を1回で引いてよい。
       銘柄ごとに問い合わせると一覧の描画で数十本のリクエストになる。
    """
    try:
        rows = (_client().table('stock_notes')
                .select('company_code').limit(2000).execute().data or [])
        return {r['company_code'] for r in rows if r.get('company_code')}
    except Exception as e:
        if _table_missing(e):
            return set()
        print('銘柄メモの一覧取得に失敗: %s' % str(e)[:150])
        return set()


def listing(limit: int = 200) -> list:
    """メモの付いた銘柄を新しい順に。「取り上げた会社」ページで使う。"""
    try:
        return (_client().table('stock_notes')
                .select('company_code, body, updated_at')
                .order('updated_at', desc=True)
                .limit(limit).execute().data or [])
    except Exception as e:
        if _table_missing(e):
            return []
        print('銘柄メモの一覧取得に失敗: %s' % str(e)[:150])
        return []


def _parse_datetime(value):
    if not value:
        return None
    from datetime import datetime, timezone
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def build_review_items(notes, company_names=None, earnings=None, now=None):
    """見直しが必要なメモだけを、緊急度の高い順に返す純粋関数。"""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    company_names = company_names or {}
    earnings = earnings or {}
    items = []

    for note in notes or []:
        code = normalize_code(note.get('company_code'))
        updated = _parse_datetime(note.get('updated_at'))
        if not code or not updated:
            continue
        age_days = max(0, int((now - updated).total_seconds() // 86400))
        processed = _parse_datetime(earnings.get(code))

        if processed and processed > updated:
            status, reason = 'bad', 'earnings'
            reason_label = '決算更新後に未確認'
        elif age_days >= REVIEW_OVERDUE_DAYS:
            status, reason = 'bad', 'overdue'
            reason_label = '%d日以上未更新' % REVIEW_OVERDUE_DAYS
        elif age_days >= REVIEW_AFTER_DAYS:
            status, reason = 'warn', 'age'
            reason_label = '前回の見直しから%d日' % age_days
        else:
            continue

        items.append({
            'company_code': code,
            'company_name': company_names.get(code) or '',
            'updated_at': note.get('updated_at'),
            'age_days': age_days,
            'status': status,
            'reason': reason,
            'reason_label': reason_label,
        })

    priority = {'earnings': 0, 'overdue': 1, 'age': 2}
    items.sort(key=lambda item: (
        priority.get(item['reason'], 9), -item['age_days'], item['company_code']))
    return items


def review_summary(limit: int = 20) -> dict:
    """管理画面用。期限超過または決算後未確認のメモをまとめる。"""
    client = _client()
    try:
        notes = (client.table('stock_notes')
                 .select('company_code, updated_at')
                 .order('updated_at').limit(2000).execute().data or [])
    except Exception as e:
        if _table_missing(e):
            return {'total': 0, 'due_count': 0, 'items': []}
        raise

    codes = [normalize_code(n.get('company_code')) for n in notes]
    codes = [c for c in dict.fromkeys(codes) if c]
    company_names = {}
    earnings = {}
    if codes:
        for start in range(0, len(codes), 500):
            part = codes[start:start + 500]
            companies = (client.table('screened_latest')
                         .select('company_code, company_name')
                         .in_('company_code', part).execute().data or [])
            company_names.update({
                normalize_code(r.get('company_code')): r.get('company_name') or ''
                for r in companies
            })
            try:
                processed = (client.table('earnings_queue')
                             .select('company_code, processed_at')
                             .in_('company_code', part)
                             .not_.is_('processed_at', 'null')
                             .execute().data or [])
                earnings.update({
                    normalize_code(r.get('company_code')): r.get('processed_at')
                    for r in processed
                })
            except Exception as e:
                # 時間経過の判定だけでも使える。決算キューの一時的な読み失敗で
                # 見直しカード全体を消さない。
                print('メモ見直し用の決算履歴を取得できませんでした: %s'
                      % str(e)[:150])

    items = build_review_items(notes, company_names, earnings)
    return {
        'total': len(notes),
        'due_count': len(items),
        'bad_count': sum(1 for item in items if item['status'] == 'bad'),
        'warn_count': sum(1 for item in items if item['status'] == 'warn'),
        'review_after_days': REVIEW_AFTER_DAYS,
        'overdue_days': REVIEW_OVERDUE_DAYS,
        'items': items[:limit],
    }


def save(company_code: str, body: str, user_id=None) -> dict:
    """メモを書く（上書き）。書けなければ例外を投げる。

    ⚠️ ここは握りつぶさない。表が無い・権限が無いのに「保存しました」と
       返すと、書いた本人が消えたことに気づけない。
    """
    code = normalize_code(company_code)
    if not code:
        raise ValueError('銘柄コードがありません')
    text = (body or '').strip()
    if not text:
        raise ValueError('メモが空です')
    if len(text) > MAX_BODY_CHARS:
        raise ValueError('メモは%d文字までです' % MAX_BODY_CHARS)

    payload = {'company_code': code, 'body': text}
    # ⚠️ **本文を、記録欄のせいで落とさない。** updated_by は UUID の列なので、
    #    セッションに UUID でない値が入っていると挿入ごと失敗する（開発用の
    #    偽ログインで実際に 22P02 になった）。書いた人が分からないのは痛手が
    #    小さいが、書いた文章が消えるのは取り返せない。
    #    ⚠️ ただし黙って捨てない。理由をログに残す。
    if user_id:
        if _UUID.fullmatch(str(user_id)):
            payload['updated_by'] = str(user_id)
        else:
            print('銘柄メモ: updated_by が UUID ではないので記録しません (%r)'
                  % (user_id,))
    # updated_at は既定値では更新されない（DEFAULT は INSERT のときだけ）
    from datetime import datetime, timezone
    payload['updated_at'] = datetime.now(timezone.utc).isoformat()

    rows = (_client().table('stock_notes')
            .upsert(payload, on_conflict='company_code').execute().data or [])
    return rows[0] if rows else payload


def delete(company_code: str) -> bool:
    code = normalize_code(company_code)
    if not code:
        return False
    (_client().table('stock_notes')
     .delete().eq('company_code', code).execute())
    return True


def table_ready() -> bool:
    """migration が適用済みか。管理者に案内を出すために使う。"""
    try:
        _client().table('stock_notes').select('company_code').limit(1).execute()
        return True
    except Exception as e:
        if _table_missing(e):
            return False
        raise
