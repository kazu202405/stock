-- =============================================
-- 詳細データ保存用カラム追加マイグレーション
-- =============================================

-- 事業概要
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS business_summary TEXT;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS business_summary_jp TEXT;

-- 主要株主・役員情報（JSON形式）
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS major_holders JSONB;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS institutional_holders JSONB;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS company_officers JSONB;

-- 追加財務指標
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS roe DECIMAL(10,4);
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS eps DECIMAL(15,4);
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS dps DECIMAL(15,4);
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS payout_ratio DECIMAL(10,4);

-- 財務健全性
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS cash DECIMAL(15,2);
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS current_liabilities DECIMAL(15,2);
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS current_assets DECIMAL(15,2);
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS current_ratio DECIMAL(10,4);

-- 信用取引
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS margin_trading_ratio DECIMAL(10,4);
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS margin_trading_buy BIGINT;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS margin_trading_sell BIGINT;

-- 年度別財務データ（JSONB形式で柔軟に保存）
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS financial_history JSONB;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS cf_history JSONB;
