-- =============================================
-- Stock Screener Database Schema
-- =============================================

-- 登録銘柄セット（表示制御用）
CREATE TABLE IF NOT EXISTS watched_tickers (
  company_code VARCHAR(10) PRIMARY KEY,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_watched_created ON watched_tickers(created_at);

-- RLS設定
ALTER TABLE watched_tickers ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON watched_tickers FOR SELECT USING (true);
CREATE POLICY "Public insert access" ON watched_tickers FOR INSERT WITH CHECK (true);
CREATE POLICY "Public delete access" ON watched_tickers FOR DELETE USING (true);

-- =============================================
-- スクリーニング結果テーブル（表示用集約）
-- =============================================
CREATE TABLE IF NOT EXISTS screened_latest (
  -- 基本情報
  company_code      VARCHAR(10) PRIMARY KEY,
  company_name      VARCHAR(200) NOT NULL,
  sector            VARCHAR(100),
  market            VARCHAR(50),
  listing_date      DATE,

  -- 時価総額・株価
  market_cap        DECIMAL(15,2),
  stock_price       DECIMAL(15,2),

  -- 売上高（生値：億円）
  revenue_2y        DECIMAL(15,2),
  revenue_1y        DECIMAL(15,2),
  revenue_cy        DECIMAL(15,2),
  revenue_ny        DECIMAL(15,2),

  -- 営業利益（生値：億円）
  op_2y             DECIMAL(15,2),
  op_1y             DECIMAL(15,2),
  op_cy             DECIMAL(15,2),
  op_ny             DECIMAL(15,2),

  -- 財務（生値）
  total_assets      DECIMAL(15,2),
  equity            DECIMAL(15,2),
  net_income        DECIMAL(15,2),
  operating_cf      DECIMAL(15,2),
  investing_cf      DECIMAL(15,2),
  free_cf           DECIMAL(15,2),

  -- スクリーニング指標（計算値）
  tk_deviation_revenue    DECIMAL(10,4),
  tk_deviation_op         DECIMAL(10,4),
  equity_ratio            DECIMAL(10,4),
  revenue_growth_2y_1y    DECIMAL(10,4),
  revenue_growth_1y_cy    DECIMAL(10,4),
  revenue_growth_cy_ny    DECIMAL(10,4),
  operating_margin        DECIMAL(10,4),
  op_growth_2y_1y         DECIMAL(10,4),
  op_growth_1y_cy         DECIMAL(10,4),
  op_growth_cy_ny         DECIMAL(10,4),
  roa                     DECIMAL(10,4),
  per_forward             DECIMAL(10,4),
  pbr                     DECIMAL(10,4),
  dividend_yield          DECIMAL(10,4),

  -- 判定結果
  status            VARCHAR(10) NOT NULL DEFAULT 'REVIEW',
  review_reasons    JSONB DEFAULT '[]'::jsonb,
  failed_reasons    JSONB DEFAULT '[]'::jsonb,

  -- 更新管理
  updated_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  price_updated_at  TIMESTAMP WITH TIME ZONE,
  data_status       VARCHAR(20) DEFAULT 'fresh',
  data_source       VARCHAR(50) DEFAULT 'yfinance',

  -- 制約
  CONSTRAINT chk_status CHECK (status IN ('PASS', 'FAIL', 'REVIEW')),
  CONSTRAINT chk_data_status CHECK (data_status IN ('fresh', 'stale'))
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_screened_status ON screened_latest(status);
CREATE INDEX IF NOT EXISTS idx_screened_sector ON screened_latest(sector);
CREATE INDEX IF NOT EXISTS idx_screened_market_cap ON screened_latest(market_cap);
CREATE INDEX IF NOT EXISTS idx_screened_updated ON screened_latest(updated_at);
CREATE INDEX IF NOT EXISTS idx_screened_roa ON screened_latest(roa);

-- RLS設定
ALTER TABLE screened_latest ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON screened_latest FOR SELECT USING (true);
CREATE POLICY "Service role write access" ON screened_latest FOR ALL USING (true);

-- =============================================
-- 四季報CSVインポート用テーブル（将来対応）
-- =============================================
CREATE TABLE IF NOT EXISTS shikiho_estimates (
  company_code      VARCHAR(10) PRIMARY KEY,
  shikiho_revenue   DECIMAL(15,2),
  shikiho_op        DECIMAL(15,2),
  fiscal_period     VARCHAR(20),
  imported_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  source_file       VARCHAR(200)
);

-- RLS設定
ALTER TABLE shikiho_estimates ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON shikiho_estimates FOR SELECT USING (true);
