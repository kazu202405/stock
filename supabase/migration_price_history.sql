-- =============================================
-- 株価履歴テーブル
--
-- 目的:
--   1. 株価履歴を揮発するローカルファイル(output/snapshot_*.json)から
--      DBへ移し、本番でチャートが表示されない問題を解消する
--   2. 長期チャート(2/3/5/10年)を、間引いた足で軽く持つ
--
-- 設計方針:
--   screened_latest には入れない。あちらは一覧・スクリーナーがSELECTするため、
--   1行に数十KBの履歴をぶら下げると一覧を開くたびに巨大なデータを引きずる。
--
--   daily_1y   … 全銘柄に事前投入する（チャートの既定表示）
--   weekly_10y … 2〜5年表示用。オンデマンドで取得しキャッシュ
--   monthly_10y… 10年表示用。オンデマンドで取得しキャッシュ
-- =============================================

CREATE TABLE IF NOT EXISTS stock_price_history (
    company_code VARCHAR(10) PRIMARY KEY,

    -- 直近1年の日足 [{t: UNIX秒, o, h, l, c}, ...]
    daily_1y JSONB,
    daily_updated_at TIMESTAMPTZ,

    -- 10年分を間引いた足（長期表示用・オンデマンドで埋まる）
    weekly_10y JSONB,
    monthly_10y JSONB,
    long_term_updated_at TIMESTAMPTZ,

    updated_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE  stock_price_history            IS '銘柄ごとの株価履歴（チャート表示用）';
COMMENT ON COLUMN stock_price_history.daily_1y   IS '直近1年の日足OHLC。全銘柄に事前投入する';
COMMENT ON COLUMN stock_price_history.weekly_10y IS '10年分の週足OHLC。2〜5年表示用、オンデマンド取得';
COMMENT ON COLUMN stock_price_history.monthly_10y IS '10年分の月足OHLC。10年表示用、オンデマンド取得';
COMMENT ON COLUMN stock_price_history.daily_updated_at     IS '日足の最終取得日時（鮮度判定に使用）';
COMMENT ON COLUMN stock_price_history.long_term_updated_at IS '週足・月足の最終取得日時（鮮度判定に使用）';

-- 鮮度の古い順に拾うローリング更新で使う
CREATE INDEX IF NOT EXISTS idx_price_history_daily_updated
    ON stock_price_history(daily_updated_at);

-- =============================================
-- RLS
--
-- このアプリのDBアクセスは全てFlask経由（supabase_client.py が
-- SUPABASE_SERVICE_ROLE_KEY を使用）。サービスロールキーはRLSをバイパスするため、
-- ポリシーを定義しなくてもアプリは正常に動作する。
--
-- RLSを有効にする目的は、anon/authenticated キーからの
-- 書き込み（データ破壊）を防ぐこと。株価は公開情報なので読まれること自体は
-- 問題ないが、書き換えられると困るため。
--
-- ⚠️ 将来フロントエンドから直接Supabaseを参照する場合は、
--    ここに SELECT 用のポリシーを追加すること（無いと全拒否になる）。
-- =============================================
ALTER TABLE stock_price_history ENABLE ROW LEVEL SECURITY;
