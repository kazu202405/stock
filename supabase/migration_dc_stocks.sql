-- =============================================
-- デッドクロス銘柄テーブル
-- =============================================

CREATE TABLE IF NOT EXISTS dc_stocks (
  company_code   VARCHAR(10) PRIMARY KEY,
  company_name   VARCHAR(200) NOT NULL,
  market         VARCHAR(50),
  stock_price    DECIMAL(15,2),
  per            DECIMAL(10,4),
  pbr            DECIMAL(10,4),
  scraped_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dc_stocks_scraped ON dc_stocks(scraped_at);

ALTER TABLE dc_stocks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON dc_stocks FOR SELECT USING (true);
CREATE POLICY "Service role write access" ON dc_stocks FOR ALL USING (true);
