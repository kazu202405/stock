-- =============================================
-- screened_latest に直近のGC/DC日を持たせる
--
-- 背景:
--   ゴールデンクロス日は ma_crosses テーブルにあるが、スクリーナーは
--   screened_latest を並べ替え・ページングしている。別テーブルの列では
--   並べ替えできず、無理に結合するとページングが崩れる。
--
--   「最近GCした銘柄を上に出す」を他の指標（PER・ROE等）と同じ操作で
--   できるようにするため、GC/DC日をこちらへ複製する。
--   実データの更新は ma_cross.calculate_for_all が担う。
-- =============================================

ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS gc_date DATE;
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS dc_date DATE;

COMMENT ON COLUMN screened_latest.gc_date IS '直近のゴールデンクロス発生日（ma_crossesから同期）';
COMMENT ON COLUMN screened_latest.dc_date IS '直近のデッドクロス発生日（ma_crossesから同期）';

-- GC日での並べ替えを速くする
CREATE INDEX IF NOT EXISTS idx_screened_gc_date ON screened_latest(gc_date DESC NULLS LAST);
