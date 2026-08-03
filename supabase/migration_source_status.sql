-- 外部データ取得の成功/失敗理由を項目・取得元ごとに保持する。
-- data_source(VARCHAR(50)) は従来どおり主取得元の短い名称に使う。
ALTER TABLE screened_latest
    ADD COLUMN IF NOT EXISTS source_status JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN screened_latest.source_status IS
    '取得元別ステータス。success/no_data/rate_limited/timeout/parse_error/skipped等';
