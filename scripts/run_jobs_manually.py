"""Render の定期実行が止まったときに、手元の PC から同じ処理を回す。

使い方（stock フォルダで）:
    py -3 scripts/run_jobs_manually.py            株価 → 日足＋GC/DC（両方・約10分）
    py -3 scripts/run_jobs_manually.py price      株価の更新だけ（約4分）
    py -3 scripts/run_jobs_manually.py daily      日足＋GC/DC の再計算だけ（約6分）

いつ使うか:
  - /dashboard/admin のデータ鮮度パネルで「株価」「テクニカル（GC/DC）」が赤い
  - Render の Events に "Ran out of memory" が出ている

何をするか:
  - 本番（.env の Supabase）に、定期実行と**まったく同じ書き込み**をする。
      price … 9:25 / 11:45 / 15:20 の株価更新（scheduled_update_stock_prices）
              株価と一緒に PER・PBR・時価総額・配当利回りも動かす
      daily … 3:30 の日足＋GC/DC 再計算（scheduled_update_daily_and_crosses）
              続けてスコアと増減率も計算し直す
  - 実行記録（job_runs）も残るので、鮮度パネルも元に戻る。
  - スケジューラは起動しない。AI は呼ばない（Yahoo と DB と計算だけ）。

⚠️ Render 側と同時に走っても、後から始まった方が claim_job で降りる。
   二重に書き込まれることはないが、降りた側は何もせずに終わる。
⚠️ 取れない銘柄が約100件出るのは正常（Yahoo がその期間の値を返さない銘柄）。

2026-09-11 の実績: 株価 3,789/3,888件（215秒）→ 日足 3,848件（371秒）。
"""

import argparse
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JOBS = {
    'price': ('株価の更新', 'scheduled_update_stock_prices'),
    'daily': ('日足＋GC/DC の再計算', 'scheduled_update_daily_and_crosses'),
}


def main():
    parser = argparse.ArgumentParser(description='定期実行を手元から回す')
    parser.add_argument('job', nargs='?', default='all',
                        choices=['all', 'price', 'daily'],
                        help='all=株価→日足（既定） / price=株価だけ / daily=日足だけ')
    args = parser.parse_args()

    # ⚠️ app を読み込む前に止めること。読み込んだ時点でスケジューラが起動し、
    #    手元の PC でも 9:25 などの定期実行が走り出す。
    os.environ['ENABLE_SCHEDULER'] = 'false'
    os.chdir(ROOT)              # companies.json などを相対パスで読むため
    sys.path.insert(0, ROOT)
    import app

    names = ['price', 'daily'] if args.job == 'all' else [args.job]
    for name in names:
        label, func = JOBS[name]
        started = time.time()
        print(f'=== {label} 開始 {datetime.now():%H:%M:%S}', flush=True)
        getattr(app, func)()
        print(f'=== {label} 終了 {datetime.now():%H:%M:%S}'
              f'（{time.time() - started:.0f}秒）', flush=True)
    print('結果は /dashboard/admin のデータ鮮度パネルで確認してください。', flush=True)


if __name__ == '__main__':
    main()
