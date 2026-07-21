"""
Yahoo!ファイナンス日本版由来の項目だけを埋める穴埋めパス。

対象項目:
  - 事業概要（日本語）business_summary_jp
  - 業績予想（今期）forecast_revenue / op_income / ordinary_income / net_income / year
  - 代表者名・設立年月日・業種分類・従業員数・本社所在地・市場名

背景:
  全銘柄バックフィル中にYahoo!JPから一時ブロックされたため、本体のバッチでは
  SKIP_YAHOO_JP=true にしてYahooを完全に切って回した。その分をここで埋める。

⚠️ ブロックを再発させないための設計:
  - 既定の待機を 1.5秒 と長めに取る（ブロック時は約23回/分だった）
  - 1銘柄あたり最大2リクエスト（/profile と /performance）
  - サーキットブレーカー内蔵。5回連続失敗で自動停止する
  - 中断・再開可能なので、数日に分けて流してよい

使い方:
    python backfill_yahoo_fields.py --limit 20      # まず20銘柄で試す
    python backfill_yahoo_fields.py                 # 未取得を全部
    python backfill_yahoo_fields.py --sleep 2.0     # さらに安全側
    python backfill_yahoo_fields.py --dry-run

⚠️ SKIP_YAHOO_JP が立っているウィンドウでは何も取得できない。
   本体バッチを流したウィンドウとは別のウィンドウで実行すること。
"""

import os
import time
import argparse
from datetime import datetime, timezone

os.environ['ENABLE_SCHEDULER'] = 'false'

CONSECUTIVE_FAIL_ABORT = 15


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}時間{m}分' if h else (f'{m}分{s}秒' if m else f'{s}秒')


def load_targets(only_missing=True):
    """穴埋めが必要な銘柄コードを返す"""
    from supabase_client import get_supabase_client
    client = get_supabase_client()
    targets = []
    page = 0
    while True:
        res = (client.table('screened_latest')
               .select('company_code, business_summary_jp, forecast_year, profile_updated_at')
               .range(page * 1000, page * 1000 + 999)
               .execute())
        rows = res.data or []
        if not rows:
            break
        for r in rows:
            if not only_missing:
                targets.append(r['company_code'])
                continue
            # どれか欠けていれば対象
            if (not r.get('business_summary_jp')
                    or not r.get('forecast_year')
                    or not r.get('profile_updated_at')):
                targets.append(r['company_code'])
        if len(rows) < 1000:
            break
        page += 1
    return targets


def fill_one(code, analyzer):
    """1銘柄のYahoo由来項目を取得して保存する。保存した項目数を返す。"""
    # 対象は screened_latest から抽出した既存行なので UPDATE を使う。
    # upsert は INSERT ... ON CONFLICT として実行されるため、
    # 部分的な項目だけを渡すと INSERT 側でNOT NULL制約に引っかかる（23502）。
    from jp_company_scraper import get_yahoo_japan_profile
    from supabase_client import update_screened_data

    symbol = code if code.endswith('.T') else f'{code}.T'
    data = {'company_code': code}

    # 1) /profile 由来（事業概要・代表者名・設立・業種・従業員・本社・市場）
    profile = get_yahoo_japan_profile(code)
    summary = profile.get('business_summary_jp')
    segments = profile.get('business_segments')
    if summary and segments:
        summary = f'{summary}<br>【連結事業】{segments}'
    elif segments and not summary:
        summary = f'【連結事業】{segments}'
    if summary:
        data['business_summary_jp'] = summary

    for src, dest in (('ceo_name', 'ceo_name'),
                      ('established', 'established'),
                      ('industry', 'industry_jp'),
                      ('employees', 'employees'),
                      ('headquarters', 'headquarters'),
                      ('market', 'market')):
        if profile.get(src):
            data[dest] = profile[src]

    # 2) /performance 由来（業績予想）
    forecast = {}
    try:
        analyzer._get_forecast_data(symbol, forecast)
    except Exception as e:
        print(f'  業績予想の取得エラー {code}: {e}')
    for key in ('forecast_revenue', 'forecast_op_income', 'forecast_ordinary_income',
                'forecast_net_income', 'forecast_year'):
        if forecast.get(key) is not None:
            data[key] = forecast[key]

    if len(data) <= 1:
        return 0

    data['profile_updated_at'] = datetime.now(timezone.utc).isoformat()
    payload = {k: v for k, v in data.items() if k != 'company_code'}
    update_screened_data(code, payload)
    return len(payload) - 1  # profile_updated_at を除いた実項目数


def main():
    parser = argparse.ArgumentParser(description='Yahoo!JP由来項目の穴埋め')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--sleep', type=float, default=1.5,
                        help='銘柄間の待機秒数。短くするとブロックされやすい')
    parser.add_argument('--all', action='store_true',
                        help='欠けているものだけでなく全銘柄を対象にする')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    print('=' * 60)
    print('Yahoo!JP由来項目の穴埋め')
    print('=' * 60)

    if os.getenv('SKIP_YAHOO_JP', '').lower() in ('true', '1', 'yes'):
        print('\n[中止] SKIP_YAHOO_JP が有効です。このままでは何も取得できません。')
        print('       別のウィンドウで実行するか、次を実行して解除してください:')
        print('         Remove-Item Env:\\SKIP_YAHOO_JP')
        return

    print('対象を抽出中...')
    try:
        targets = load_targets(only_missing=not args.all)
    except Exception as e:
        print(f'[エラー] 対象の抽出に失敗しました: {e}')
        print('  migration_company_profile_fields.sql を適用済みか確認してください')
        return

    if args.limit:
        targets = targets[:args.limit]

    print(f'処理対象: {len(targets)}件')
    print(f'推定所要時間: {fmt_duration(len(targets) * (1.5 + args.sleep))}')
    print(f'待機: {args.sleep}秒/銘柄（ブロック回避のため長めに設定）')

    if args.dry_run:
        print('\n--dry-run のため実行せず終了します')
        return
    if not targets:
        print('\n対象がありません。完了しています。')
        return

    from stock_analyzer import StockAnalyzer
    import yahoo_jp_guard

    analyzer = StockAnalyzer()
    started = time.time()
    ok = fail = 0
    consecutive_fail = 0

    print('\n開始します（Ctrl+C で安全に中断できます）\n')

    try:
        for i, code in enumerate(targets, 1):
            try:
                filled = fill_one(code, analyzer)
                if filled > 0:
                    ok += 1
                    consecutive_fail = 0
                    status = f'OK ({filled}項目)'
                else:
                    fail += 1
                    consecutive_fail += 1
                    status = '取得できず'
            except Exception as e:
                fail += 1
                consecutive_fail += 1
                status = f'エラー: {str(e)[:60]}'

            remain = ((time.time() - started) / i) * (len(targets) - i)
            print(f'[{i}/{len(targets)}] {code} {status} | 成功{ok} 失敗{fail} | 残り約{fmt_duration(remain)}',
                  flush=True)

            # ガードが落ちたら即座に中断する（叩き続けない）
            if not yahoo_jp_guard.is_available():
                print('\n[中断] Yahoo!JPへのアクセスが遮断されました（連続失敗）。')
                print('       時間を置いてから再実行してください。済んだ分はスキップされます。')
                break

            if consecutive_fail >= CONSECUTIVE_FAIL_ABORT:
                print(f'\n[中断] {consecutive_fail}件連続で失敗しました。時間を置いて再実行してください。')
                break

            time.sleep(args.sleep)

    except KeyboardInterrupt:
        print('\n\n[中断] Ctrl+C を検知しました。')

    print('\n' + '=' * 60)
    print(f'完了: 成功 {ok}件 / 失敗 {fail}件 / 所要 {fmt_duration(time.time() - started)}')
    print('=' * 60)
    print('\n再実行すれば、埋まった分をスキップして続きから処理します。')


if __name__ == '__main__':
    main()
