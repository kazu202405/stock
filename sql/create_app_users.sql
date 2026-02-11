-- app_usersテーブル作成
-- Supabaseダッシュボード > SQL Editor で実行してください

CREATE TABLE IF NOT EXISTS app_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'agent', 'admin')),
    referral_code TEXT UNIQUE,
    referred_by UUID REFERENCES app_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_app_users_email ON app_users(email);
CREATE INDEX IF NOT EXISTS idx_app_users_referral_code ON app_users(referral_code);
CREATE INDEX IF NOT EXISTS idx_app_users_referred_by ON app_users(referred_by);
CREATE INDEX IF NOT EXISTS idx_app_users_role ON app_users(role);

-- updated_at自動更新トリガー
CREATE OR REPLACE FUNCTION update_app_users_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_app_users_updated_at ON app_users;
CREATE TRIGGER trigger_app_users_updated_at
    BEFORE UPDATE ON app_users
    FOR EACH ROW
    EXECUTE FUNCTION update_app_users_updated_at();

-- RLS（行レベルセキュリティ）は無効のまま（service_role_keyで操作するため）
