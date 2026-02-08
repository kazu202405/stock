-- =============================================
-- analyzed_atカラム追加マイグレーション
-- 分析実行日時を記録し、日付ベースのスキップ判定に使用
-- =============================================

-- screened_latestテーブル（ウォッチリスト・GC銘柄の詳細分析結果）
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN screened_latest.analyzed_at IS '詳細分析の実行日時（当日分析済みはスキップ判定に使用）';

-- gc_stocksテーブル（GC銘柄の分析日時）
ALTER TABLE gc_stocks ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN gc_stocks.analyzed_at IS '詳細分析の実行日時';
