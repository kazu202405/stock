-- =============================================
-- 合致度カラム追加マイグレーション
-- =============================================

-- 合致度（0-100のパーセンテージ）
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS match_rate INTEGER;

-- 合致度の説明用コメント
COMMENT ON COLUMN screened_latest.match_rate IS '財務指標の投資基準への合致度（0-100%）';

  -- supabase/migration_add_match_rate.sql の内容を実行
  ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS match_rate INTEGER;

       -- =============================================
     -- 業績予想データカラム追加マイグレーション
     -- =============================================
     -- 今期予想売上高（億円）
     ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS forecast_revenue NUMERIC;
     -- 今期予想営業利益（億円）
     ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS forecast_op_income NUMERIC;