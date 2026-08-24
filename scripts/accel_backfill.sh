#!/bin/bash
# 400件×2回 → score_complete 再計算。
# 1回の実行は待ち時間合計4時間で自動停止するので、区切って2回まわす。
# スクリプトは未取得だけを拾うので、続けて実行すれば続きから進む。
cd "$(dirname "$0")"
for i in 1 2; do
  echo "===== ラウンド $i 開始: $(date) ====="
  python backfill_yahoo_fields.py --max-per-run 400 --sleep 5.0
  echo "===== ラウンド $i 終了: $(date) ====="
done
echo "===== score_complete 再計算: $(date) ====="
python backfill_score_complete.py --apply
echo "===== 全部おわり: $(date) ====="
