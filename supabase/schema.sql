-- =============================================
-- Company Note (stock) Database Schema  ★完全版
-- =============================================
-- コードベース(supabase_client.py / app.py / models/*.py)が実際に参照する
-- 全テーブルを定義。新規Supabaseプロジェクトでこのファイルをまるごと実行する。
--
-- 認証は SUPABASE_SERVICE_ROLE_KEY 経由（service role は RLS をバイパスする）。
-- そのため各テーブルは RLS 有効＋明示ポリシー無し = サーバ(service role)からは全操作可、
-- 外部(anon)からは遮断、というセキュアな既定にしている。
-- anon キーで直接読ませたいテーブルがあれば末尾で個別に SELECT ポリシーを足す。
--
-- UUID 採番には pgcrypto の gen_random_uuid() を使用（Supabase は標準で有効）。
-- =============================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =============================================
-- 1. app_users : ユーザーマスター（ログイン認証の中核）
-- =============================================
CREATE TABLE IF NOT EXISTS app_users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            VARCHAR(200),
  email           VARCHAR(320) NOT NULL UNIQUE,
  password_hash   VARCHAR(255) NOT NULL,
  display_name    VARCHAR(200),
  role            VARCHAR(20) NOT NULL DEFAULT 'user'
                    CHECK (role IN ('user', 'agent', 'admin')),
  referral_code   VARCHAR(12) UNIQUE,
  referred_by     UUID,                      -- 紹介者 app_users.id（FK制約は付けない）
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_app_users_email       ON app_users(email);
CREATE INDEX IF NOT EXISTS idx_app_users_referral    ON app_users(referral_code);
CREATE INDEX IF NOT EXISTS idx_app_users_referred_by ON app_users(referred_by);
ALTER TABLE app_users ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 2. watched_tickers : ウォッチリスト（表示制御用）
-- =============================================
CREATE TABLE IF NOT EXISTS watched_tickers (
  company_code VARCHAR(10) PRIMARY KEY,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_watched_created ON watched_tickers(created_at);
ALTER TABLE watched_tickers ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 3. screened_latest : 分析結果キャッシュ（表示用集約）
-- =============================================
CREATE TABLE IF NOT EXISTS screened_latest (
  -- 基本情報
  company_code        VARCHAR(10) PRIMARY KEY,
  company_name        VARCHAR(200),
  sector              VARCHAR(100),
  market              VARCHAR(50),
  listing_date        DATE,

  -- 時価総額・株価（億円 / 円）
  market_cap          NUMERIC(18,2),
  stock_price         NUMERIC(15,2),

  -- 売上高（億円）：2期前 / 前期 / 今期予 / 来期予
  revenue_2y          NUMERIC(18,2),
  revenue_1y          NUMERIC(18,2),
  revenue_cy          NUMERIC(18,2),
  revenue_ny          NUMERIC(18,2),

  -- 営業利益（億円）
  op_2y               NUMERIC(18,2),
  op_1y               NUMERIC(18,2),
  op_cy               NUMERIC(18,2),
  op_ny               NUMERIC(18,2),

  -- 財務（億円）
  total_assets        NUMERIC(18,2),
  equity              NUMERIC(18,2),
  net_income          NUMERIC(18,2),
  operating_cf        NUMERIC(18,2),
  investing_cf        NUMERIC(18,2),
  free_cf             NUMERIC(18,2),

  -- 1株指標・収益性
  eps                 NUMERIC(15,2),
  dps                 NUMERIC(15,2),
  payout_ratio        NUMERIC(10,4),
  roe                 NUMERIC(10,4),
  roa                 NUMERIC(10,4),

  -- スクリーニング指標（計算値）
  equity_ratio        NUMERIC(10,4),
  operating_margin    NUMERIC(10,4),
  per_forward         NUMERIC(12,4),
  pbr                 NUMERIC(12,4),
  dividend_yield      NUMERIC(10,4),

  -- 旧schema互換（未使用でも残す）
  tk_deviation_revenue NUMERIC(12,4),
  tk_deviation_op      NUMERIC(12,4),
  revenue_growth_2y_1y NUMERIC(12,4),
  revenue_growth_1y_cy NUMERIC(12,4),
  revenue_growth_cy_ny NUMERIC(12,4),
  op_growth_2y_1y      NUMERIC(12,4),
  op_growth_1y_cy      NUMERIC(12,4),
  op_growth_cy_ny      NUMERIC(12,4),

  -- 信用取引
  margin_trading_ratio NUMERIC(12,4),
  margin_trading_buy   NUMERIC(18,2),
  margin_trading_sell  NUMERIC(18,2),

  -- 業績予想（Yahoo Finance Japan / 億円）
  forecast_revenue        NUMERIC(18,2),
  forecast_op_income      NUMERIC(18,2),
  forecast_ordinary_income NUMERIC(18,2),
  forecast_net_income     NUMERIC(18,2),
  forecast_year           VARCHAR(20),

  -- 事業説明
  business_summary        TEXT,
  business_summary_jp     TEXT,

  -- JSON保存データ
  major_holders           JSONB,
  institutional_holders   JSONB,
  company_officers        JSONB,
  major_shareholders_jp   JSONB,
  financial_history       JSONB,
  cf_history              JSONB,

  -- 合致度・判定
  match_rate          INTEGER,
  status              VARCHAR(10) DEFAULT 'REVIEW'
                        CHECK (status IN ('PASS', 'FAIL', 'REVIEW')),
  review_reasons      JSONB DEFAULT '[]'::jsonb,
  failed_reasons      JSONB DEFAULT '[]'::jsonb,

  -- テクニカルシグナル
  gc_date             TIMESTAMPTZ,
  dc_date             TIMESTAMPTZ,

  -- フラグ・管理
  is_dividend         BOOLEAN DEFAULT FALSE,
  updated_at          TIMESTAMPTZ DEFAULT NOW(),
  analyzed_at         TIMESTAMPTZ,
  price_updated_at    TIMESTAMPTZ,
  data_status         VARCHAR(20) DEFAULT 'fresh',
  data_source         VARCHAR(50) DEFAULT 'yfinance'
);
CREATE INDEX IF NOT EXISTS idx_screened_sector     ON screened_latest(sector);
CREATE INDEX IF NOT EXISTS idx_screened_market_cap ON screened_latest(market_cap);
CREATE INDEX IF NOT EXISTS idx_screened_updated    ON screened_latest(updated_at);
CREATE INDEX IF NOT EXISTS idx_screened_roa        ON screened_latest(roa);
CREATE INDEX IF NOT EXISTS idx_screened_dividend   ON screened_latest(is_dividend);
CREATE INDEX IF NOT EXISTS idx_screened_gc_date    ON screened_latest(gc_date);
CREATE INDEX IF NOT EXISTS idx_screened_dc_date    ON screened_latest(dc_date);
ALTER TABLE screened_latest ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 4. signal_stocks : GC/DCテクニカルシグナル集約（gc_stocks/dc_stocks統合先）
-- =============================================
CREATE TABLE IF NOT EXISTS signal_stocks (
  company_code    VARCHAR(10) PRIMARY KEY,
  company_name    VARCHAR(200),
  sector          VARCHAR(100),
  market_cap      NUMERIC(18,2),
  stock_price     NUMERIC(15,2),
  per             NUMERIC(12,4),
  pbr             NUMERIC(12,4),
  dividend_yield  NUMERIC(10,4),
  match_rate      INTEGER,
  gc_date         TIMESTAMPTZ,
  dc_date         TIMESTAMPTZ,
  analyzed_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_signal_gc ON signal_stocks(gc_date);
CREATE INDEX IF NOT EXISTS idx_signal_dc ON signal_stocks(dc_date);
ALTER TABLE signal_stocks ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 5. gc_stocks / 6. dc_stocks : 旧スナップショット（signal_stocksへ統合済だが互換保持）
-- =============================================
CREATE TABLE IF NOT EXISTS gc_stocks (
  company_code    VARCHAR(10) PRIMARY KEY,
  company_name    VARCHAR(200),
  sector          VARCHAR(100),
  market_cap      NUMERIC(18,2),
  stock_price     NUMERIC(15,2),
  match_rate      INTEGER,
  gc_date         TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE gc_stocks ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS dc_stocks (
  company_code    VARCHAR(10) PRIMARY KEY,
  company_name    VARCHAR(200),
  sector          VARCHAR(100),
  market_cap      NUMERIC(18,2),
  stock_price     NUMERIC(15,2),
  match_rate      INTEGER,
  dc_date         TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE dc_stocks ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 7. favorite_stocks : お気に入り銘柄（ユーザー単位）
-- =============================================
CREATE TABLE IF NOT EXISTS favorite_stocks (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL,
  company_code  VARCHAR(10) NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, company_code)
);
CREATE INDEX IF NOT EXISTS idx_fav_user ON favorite_stocks(user_id);
ALTER TABLE favorite_stocks ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 8. notes : 企業研究ノート（ユーザー単位 / ゲストIDも入りうる）
-- =============================================
CREATE TABLE IF NOT EXISTS notes (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL,             -- 認証ユーザー or ゲストUUID（FKは付けない）
  title         VARCHAR(300),
  content       TEXT,
  company_code  VARCHAR(10),
  company_name  VARCHAR(200),
  stars         INTEGER DEFAULT 0,
  tags          JSONB DEFAULT '[]'::jsonb,
  is_public     BOOLEAN DEFAULT FALSE,
  is_anonymous  BOOLEAN DEFAULT FALSE,
  poster_name   VARCHAR(200),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notes_user    ON notes(user_id);
CREATE INDEX IF NOT EXISTS idx_notes_public  ON notes(is_public);
CREATE INDEX IF NOT EXISTS idx_notes_company ON notes(company_code);
ALTER TABLE notes ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 9. community_questions : コミュニティ質問
-- =============================================
CREATE TABLE IF NOT EXISTS community_questions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL,
  title         VARCHAR(300),
  content       TEXT,
  company_code  VARCHAR(10),
  company_name  VARCHAR(200),
  tags          JSONB DEFAULT '[]'::jsonb,
  is_anonymous  BOOLEAN DEFAULT FALSE,
  poster_name   VARCHAR(200),
  answer_count  INTEGER DEFAULT 0,
  is_resolved   BOOLEAN DEFAULT FALSE,
  like_count    INTEGER DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cq_user     ON community_questions(user_id);
CREATE INDEX IF NOT EXISTS idx_cq_company  ON community_questions(company_code);
CREATE INDEX IF NOT EXISTS idx_cq_resolved ON community_questions(is_resolved);
ALTER TABLE community_questions ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 10. community_answers : コミュニティ回答
-- =============================================
CREATE TABLE IF NOT EXISTS community_answers (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question_id   UUID NOT NULL,
  user_id       UUID NOT NULL,
  content       TEXT,
  is_anonymous  BOOLEAN DEFAULT FALSE,
  poster_name   VARCHAR(200),
  is_best       BOOLEAN DEFAULT FALSE,
  like_count    INTEGER DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ca_question ON community_answers(question_id);
CREATE INDEX IF NOT EXISTS idx_ca_user     ON community_answers(user_id);
ALTER TABLE community_answers ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 11. community_likes : いいね（質問/回答）
-- =============================================
CREATE TABLE IF NOT EXISTS community_likes (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL,
  target_type   VARCHAR(20) NOT NULL CHECK (target_type IN ('question', 'answer')),
  target_id     UUID NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS idx_cl_target ON community_likes(target_type, target_id);
ALTER TABLE community_likes ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 12. demo_account : デモ口座（session単位）
-- =============================================
CREATE TABLE IF NOT EXISTS demo_account (
  user_id         UUID PRIMARY KEY,
  cash_balance    NUMERIC(15,2) DEFAULT 1000000,
  total_deposited NUMERIC(15,2) DEFAULT 1000000,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE demo_account ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 13. demo_portfolio : デモ保有銘柄
-- =============================================
CREATE TABLE IF NOT EXISTS demo_portfolio (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL,
  company_code  VARCHAR(10) NOT NULL,
  company_name  VARCHAR(200),
  shares        INTEGER DEFAULT 0,
  avg_cost      NUMERIC(15,2) DEFAULT 0,
  buy_reason    TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (user_id, company_code)
);
CREATE INDEX IF NOT EXISTS idx_demo_pf_user ON demo_portfolio(user_id);
ALTER TABLE demo_portfolio ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 14. demo_trades : デモ取引履歴
-- =============================================
CREATE TABLE IF NOT EXISTS demo_trades (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL,
  company_code  VARCHAR(10) NOT NULL,
  company_name  VARCHAR(200),
  trade_type    VARCHAR(10) NOT NULL CHECK (trade_type IN ('buy', 'sell')),
  shares        INTEGER NOT NULL,
  price         NUMERIC(15,2) NOT NULL,
  reason        TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_demo_trades_user ON demo_trades(user_id);
ALTER TABLE demo_trades ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 15. shikiho_estimates : 四季報CSVインポート用（将来対応）
-- =============================================
CREATE TABLE IF NOT EXISTS shikiho_estimates (
  company_code    VARCHAR(10) PRIMARY KEY,
  shikiho_revenue NUMERIC(18,2),
  shikiho_op      NUMERIC(18,2),
  fiscal_period   VARCHAR(20),
  imported_at     TIMESTAMPTZ DEFAULT NOW(),
  source_file     VARCHAR(200)
);
ALTER TABLE shikiho_estimates ENABLE ROW LEVEL SECURITY;

-- =============================================
-- 補足:
-- ・全テーブル RLS 有効。アプリは service_role キーで接続するため全操作可能。
--   anon キーから直接読ませたい場合のみ、対象テーブルに
--   CREATE POLICY "anon read" ON <table> FOR SELECT TO anon USING (true);
--   を個別に追加すること（書き込みは service role に限定推奨）。
-- ・テーブル間 FK 制約は意図的に付けていない（ゲストノート引き継ぎ等で
--   app_users に存在しない user_id を扱うため）。整合性はアプリ層で担保。
-- =============================================
