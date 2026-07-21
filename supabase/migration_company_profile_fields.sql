-- =============================================
-- 企業プロフィール項目の追加
--
-- 背景:
--   Yahoo!ファイナンス日本版の /profile から代表者名・設立年月日・業種分類・
--   従業員数が取得できているのに、保存先のカラムが無く捨てられていた。
--   企業分析レポート（代表者名・設立日を表示）に必要なため追加する。
--
--   industry_jp は sector より細かい業種分類（例: sector=「一般消費財」に対し
--   industry_jp=「小売業」）。既存の sector カラムは残したまま併存させる。
-- =============================================

ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS ceo_name          TEXT;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS established       TEXT;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS industry_jp       TEXT;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS employees         TEXT;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS headquarters      TEXT;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS profile_updated_at TIMESTAMPTZ;

COMMENT ON COLUMN screened_latest.ceo_name           IS '代表者名（Yahoo!ファイナンス日本版 /profile より）';
COMMENT ON COLUMN screened_latest.established        IS '設立年月日';
COMMENT ON COLUMN screened_latest.industry_jp        IS '業種分類（sectorより細かい粒度）';
COMMENT ON COLUMN screened_latest.employees          IS '従業員数';
COMMENT ON COLUMN screened_latest.headquarters       IS '本社所在地';
COMMENT ON COLUMN screened_latest.profile_updated_at IS 'Yahoo!JP由来のプロフィール項目の最終取得日時';

-- 未取得の銘柄を拾う穴埋めパスで使う
CREATE INDEX IF NOT EXISTS idx_screened_profile_updated
    ON screened_latest(profile_updated_at);
