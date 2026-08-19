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
    python backfill_yahoo_fields.py --code 7089      # 1銘柄だけ再取得
    python backfill_yahoo_fields.py --code 7089 --code 164A
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
import json
from datetime import datetime, timezone

os.environ['ENABLE_SCHEDULER'] = 'false'

CONSECUTIVE_FAIL_ABORT = 15

# 遮断されて待つ時間の合計がこれを超えたら打ち切る。
# 夜間に流しっぱなしにするが、相手がずっと閉じているのに何時間も居座らない。
MAX_TOTAL_WAIT_SECONDS = 4 * 3600


def normalize_target_codes(values):
    """--code の値をDBで使う銘柄コードへ正規化し、入力順で重複除去する。"""
    codes = []
    seen = set()
    for value in values or []:
        for raw in str(value).split(','):
            code = raw.strip().upper()
            if code.endswith('.T'):
                code = code[:-2]
            if not code or code in seen:
                continue
            if len(code) != 4 or not code.isalnum():
                raise ValueError(f'銘柄コードの形式が不正です: {raw}')
            seen.add(code)
            codes.append(code)
    return codes


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
               .select('company_code, business_summary_jp, forecast_year, '
                       'forecast_revenue, forecast_op_income, profile_updated_at, '
                       'source_status')
               .range(page * 1000, page * 1000 + 999)
               .execute())
        rows = res.data or []
        if not rows:
            break
        for r in rows:
            if not only_missing:
                targets.append(r['company_code'])
                continue
            source_status = r.get('source_status') or {}
            if isinstance(source_status, str):
                try:
                    source_status = json.loads(source_status)
                except (TypeError, ValueError):
                    source_status = {}
            forecast_not_disclosed = (
                (source_status.get('forecast') or {}).get('status') == 'not_disclosed'
            )
            forecast_ready = forecast_not_disclosed or (
                bool(r.get('forecast_year'))
                and r.get('forecast_revenue') is not None
                and r.get('forecast_op_income') is not None
            )
            # どれか欠けていれば対象。会社予想非開示は再取得を繰り返さない。
            if (not r.get('business_summary_jp')
                    or not forecast_ready
                    or not r.get('profile_updated_at')):
                targets.append(r['company_code'])
        if len(rows) < 1000:
            break
        page += 1
    return targets


def fill_one(code, analyzer, use_edinet_forecasts=False):
    """1銘柄のYahoo由来項目を取得して保存する。保存した項目数を返す。"""
    # 対象は screened_latest から抽出した既存行なので UPDATE を使う。
    # upsert は INSERT ... ON CONFLICT として実行されるため、
    # 部分的な項目だけを渡すと INSERT 側でNOT NULL制約に引っかかる（23502）。
    from jp_company_scraper import get_yahoo_japan_profile
    from supabase_client import (
        get_screened_data, merge_source_status, update_screened_data,
    )

    symbol = code if code.endswith('.T') else f'{code}.T'
    data = {'company_code': code}
    existing = get_screened_data(code) or {}

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
    if forecast.get('source_status'):
        data['source_status'] = merge_source_status(
            existing.get('source_status'), forecast['source_status'])

    # Yahooが遮断・構造変更で空のときだけ、予想エンドポイントに限定してEDINET DBを使う。
    # 「会社予想非開示」は取得元を変えても数値が存在しないため、枠を消費しない。
    forecast_status = (forecast.get('source_status', {}).get('forecast', {}).get('status'))
    if (use_edinet_forecasts
            and forecast_status != 'not_disclosed'
            and (data.get('forecast_revenue') is None
                 or data.get('forecast_op_income') is None)):
        from edinet_db_client import apply_edinet_db_fallback
        seed = {
            key: data.get(key, existing.get(key))
            for key in ('forecast_revenue', 'forecast_op_income',
                        'forecast_ordinary_income', 'forecast_net_income', 'forecast_year')
        }
        seed['source_status'] = forecast.get('source_status', {})
        apply_edinet_db_fallback(symbol, seed, categories={'earnings'})
        for key in ('forecast_revenue', 'forecast_op_income', 'forecast_ordinary_income',
                    'forecast_net_income', 'forecast_year', 'source_status'):
            if seed.get(key) is not None:
                data[key] = seed[key]

    if len(data) <= 1:
        return 0

    data['profile_updated_at'] = datetime.now(timezone.utc).isoformat()
    payload = {k: v for k, v in data.items() if k != 'company_code'}
    update_screened_data(code, payload)
    return len(payload) - 1  # profile_updated_at を除いた実項目数


def main():
    parser = argparse.ArgumentParser(description='Yahoo!JP由来項目の穴埋め')
    parser.add_argument('--code', action='append', default=[],
                        help='指定銘柄だけ再取得する。複数回指定またはカンマ区切り可')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--sleep', type=float, default=5.0,
                        help='銘柄間の待機秒数。1銘柄あたりYahooに2回リクエストするため、'
                             '5秒で約20回/分になる')
    parser.add_argument('--max-per-run', type=int, default=400,
                        help='1回の実行で処理する上限。数日に分けて流すための安全弁')
    parser.add_argument('--all', action='store_true',
                        help='欠けているものだけでなく全銘柄を対象にする')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--no-wait', action='store_true',
                        help='遮断されたら待たずに中断する（従来の挙動）')
    parser.add_argument('--edinet-forecasts', action='store_true',
                        help='Yahooで取得できない業績予想だけEDINET DBで補完する。'
                             '会社予想非開示では呼ばない')
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
        targets = (normalize_target_codes(args.code) if args.code
                   else load_targets(only_missing=not args.all))
    except ValueError as e:
        print(f'[エラー] {e}')
        return
    except Exception as e:
        print(f'[エラー] 対象の抽出に失敗しました: {e}')
        print('  migration_company_profile_fields.sql を適用済みか確認してください')
        return

    remaining_total = len(targets)
    cap = args.limit or args.max_per_run
    if cap and len(targets) > cap:
        targets = targets[:cap]

    rate = 60.0 / (args.sleep + 1.5) * 2   # 1銘柄あたりYahooへ2リクエスト
    print(f'未処理: {remaining_total}件 / 今回処理: {len(targets)}件')
    print(f'推定所要時間: {fmt_duration(len(targets) * (1.5 + args.sleep))}')
    print(f'待機: {args.sleep}秒/銘柄（Yahooへ約{rate:.0f}回/分）')
    if remaining_total > len(targets):
        print(f'※ 1回の上限{cap}件で区切っています。'
              f'完了まで約{-(-remaining_total // len(targets))}回に分けて実行してください')

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
    waited_total = 0        # 遮断で待った合計秒数
    ok = fail = 0
    consecutive_fail = 0

    print('\n開始します（Ctrl+C で安全に中断できます）\n')

    try:
        for i, code in enumerate(targets, 1):
            try:
                filled = fill_one(code, analyzer, use_edinet_forecasts=args.edinet_forecasts)
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

            # ガードが落ちたら、冷却が明けるまで待ってから続ける。
            # 2026-08-19にブレーカーへ半開放を入れるまでは、一度落ちると
            # プロセスを作り直すまで戻らなかったので中断するしかなかった。
            # いまは待てば1本試して復帰できるので、夜間に流しっぱなしにできる。
            # --no-wait を付けると従来どおり即中断する。
            snap = yahoo_jp_guard.status_snapshot()
            if snap['tripped']:
                if args.no_wait:
                    print('\n[中断] Yahoo!JPへのアクセスが遮断されました（連続失敗）。')
                    print('       時間を置いてから再実行してください。済んだ分はスキップされます。')
                    break
                waited_total += snap['retry_in_seconds']
                if waited_total > MAX_TOTAL_WAIT_SECONDS:
                    print(f'\n[中断] 待ち時間の合計が {fmt_duration(waited_total)} を超えました。'
                          f'時間を置いて再実行してください。済んだ分はスキップされます。')
                    break
                wait = snap['retry_in_seconds'] + 2
                print(f'  … 遮断されました。{fmt_duration(wait)} 待って再開します'
                      f'（{snap["trip_count"]}回目／待ち合計 {fmt_duration(waited_total)}）',
                      flush=True)
                time.sleep(wait)

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
