-- =============================================
-- 業績予想データカラム追加マイグレーション
-- =============================================

-- 今期予想売上高（億円）
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS forecast_revenue NUMERIC;

-- 今期予想営業利益（億円）
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS forecast_op_income NUMERIC;

-- 今期予想経常利益（億円）
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS forecast_ordinary_income NUMERIC;

-- 今期予想純利益（億円）
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS forecast_net_income NUMERIC;

-- 今期予想の決算期
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS forecast_year TEXT;

-- コメント追加
COMMENT ON COLUMN screened_latest.forecast_revenue IS '今期予想売上高（億円）- Yahoo Finance Japan業績ページより';
COMMENT ON COLUMN screened_latest.forecast_op_income IS '今期予想営業利益（億円）- Yahoo Finance Japan業績ページより';
COMMENT ON COLUMN screened_latest.forecast_ordinary_income IS '今期予想経常利益（億円）- Yahoo Finance Japan業績ページより';
COMMENT ON COLUMN screened_latest.forecast_net_income IS '今期予想純利益（億円）- Yahoo Finance Japan業績ページより';
COMMENT ON COLUMN screened_latest.forecast_year IS '今期予想の決算期（例: 2025-12-31）';
