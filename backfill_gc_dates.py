"""
ma_crosses の GC/DC日を screened_latest に流し込む（一度きり）。

スクリーナーでGC日を並べ替えられるよう、既存の計算結果を複製する。
以後はGC再計算（ma_cross.calculate_for_all）の中で自動同期される。

使い方:
    python backfill_gc_dates.py
"""

import os
os.environ.setdefault('ENABLE_SCHEDULER', 'false')


def main():
    from supabase_client import get_supabase_client
    import ma_cross

    client = get_supabase_client()

    print('ma_crosses を読み込んでいます...')
    rows = []
    page = 0
    while page < 20:
        res = (client.table('ma_crosses')
               .select('company_code, latest_gc_date, latest_dc_date')
               .range(page * 1000, page * 1000 + 999).execute())
        chunk = res.data or []
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        page += 1

    print(f'対象 {len(rows)}件を screened_latest に反映します...')
    synced = ma_cross.sync_gc_to_screened(client, rows)
    print(f'完了: {synced}件を同期しました')


if __name__ == '__main__':
    main()
