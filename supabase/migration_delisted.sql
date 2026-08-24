-- 上場廃止銘柄の印（2026-08-24）
--
-- 2026年のTOB・MBOの波で、5〜7月だけで22社が上場廃止になっていた。
-- アプリはそれらを生きた銘柄として表示し続け、株価は最終売買日で凍結されたまま
-- 検索にもスクリーナーにも出ていた。上場廃止だとはどこにも書かれていなかった。
--
-- 行は消さない。消すと元に戻せないうえ、過去に見た人のURLが404になる。
-- 印を付けて「読み取り時に外す」方針にすれば、判定を間違えても1列消せば戻る。

ALTER TABLE screened_latest
    ADD COLUMN IF NOT EXISTS delisted_at timestamptz;

COMMENT ON COLUMN screened_latest.delisted_at IS
    '上場廃止と判定した日（最終売買日）。NULLなら上場中。detect_delisted.py が入れる';

-- スクリーナー・一覧は「上場中だけ」で毎回絞るので、NULLを速く引けるようにする
CREATE INDEX IF NOT EXISTS idx_screened_latest_delisted_at
    ON screened_latest (delisted_at)
    WHERE delisted_at IS NULL;
