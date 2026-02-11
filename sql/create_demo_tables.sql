-- デモ売買機能用テーブル

-- デモ口座残高
CREATE TABLE IF NOT EXISTS demo_account (
    user_id TEXT PRIMARY KEY,
    cash_balance DECIMAL DEFAULT 1000000,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- デモポートフォリオ（保有銘柄）
CREATE TABLE IF NOT EXISTS demo_portfolio (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id TEXT NOT NULL,
    company_code VARCHAR(10) NOT NULL,
    company_name VARCHAR(200),
    shares INTEGER NOT NULL,
    avg_cost DECIMAL NOT NULL,
    buy_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- デモ売買履歴
CREATE TABLE IF NOT EXISTS demo_trades (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id TEXT NOT NULL,
    company_code VARCHAR(10) NOT NULL,
    company_name VARCHAR(200),
    trade_type VARCHAR(4) NOT NULL,
    shares INTEGER NOT NULL,
    price DECIMAL NOT NULL,
    total_amount DECIMAL NOT NULL,
    reason TEXT,
    traded_at TIMESTAMPTZ DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_demo_portfolio_user ON demo_portfolio(user_id);
CREATE INDEX IF NOT EXISTS idx_demo_trades_user ON demo_trades(user_id);
CREATE INDEX IF NOT EXISTS idx_demo_portfolio_user_code ON demo_portfolio(user_id, company_code);
