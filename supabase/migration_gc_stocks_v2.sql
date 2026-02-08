-- =============================================
-- gc_stocksテーブルに詳細分析用カラムを追加
-- =============================================

ALTER TABLE gc_stocks ADD COLUMN IF NOT EXISTS sector VARCHAR(100);
ALTER TABLE gc_stocks ADD COLUMN IF NOT EXISTS market_cap DECIMAL(15,1);
ALTER TABLE gc_stocks ADD COLUMN IF NOT EXISTS dividend_yield DECIMAL(10,4);
ALTER TABLE gc_stocks ADD COLUMN IF NOT EXISTS match_rate INTEGER;
