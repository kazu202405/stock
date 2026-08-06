-- 決算月（何月期の会社か）を銘柄ごとに持つ。
--
-- 背景:
--   決算月は financial_history の決算日から導出できるが、その都度3,879銘柄分の
--   JSONを引くと重く、Supabaseのデフォルト1000行上限にも当たる。
--   決算月は年に一度も変わらない値なので、列として持って集計はDB側でやる。
--
-- 値: 1〜12。決算発表"予定日"ではなく決算"期"の月であることに注意。
--     発表予定日は取得元が未整理のため、この列では扱わない。
ALTER TABLE screened_latest
    ADD COLUMN IF NOT EXISTS fiscal_month SMALLINT;

ALTER TABLE screened_latest
    DROP CONSTRAINT IF EXISTS screened_latest_fiscal_month_check;
ALTER TABLE screened_latest
    ADD CONSTRAINT screened_latest_fiscal_month_check
    CHECK (fiscal_month IS NULL OR (fiscal_month BETWEEN 1 AND 12));

-- 決算月ページは「その月の銘柄を新しい順に並べる」ので複合indexにする。
CREATE INDEX IF NOT EXISTS idx_screened_latest_fiscal_month
    ON screened_latest (fiscal_month, company_code);

COMMENT ON COLUMN screened_latest.fiscal_month IS
    '決算期の月(1-12)。financial_historyの決算日から導出。決算発表予定日ではない';
