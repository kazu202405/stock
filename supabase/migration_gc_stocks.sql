-- =============================================
-- ゴールデンクロス銘柄テーブル
-- =============================================

CREATE TABLE IF NOT EXISTS gc_stocks (
  company_code   VARCHAR(10) PRIMARY KEY,
  company_name   VARCHAR(200) NOT NULL,
  market         VARCHAR(50),
  stock_price    DECIMAL(15,2),
  per            DECIMAL(10,4),
  pbr            DECIMAL(10,4),
  scraped_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gc_stocks_scraped ON gc_stocks(scraped_at);

ALTER TABLE gc_stocks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON gc_stocks FOR SELECT USING (true);
CREATE POLICY "Service role write access" ON gc_stocks FOR ALL USING (true);
