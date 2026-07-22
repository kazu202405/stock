-- =============================================
-- 決算発表キュー
--
-- 背景:
--   決算発表のあった銘柄だけ財務データを更新する運用にしたが、
--   「今日発表した銘柄」をその場で取得して処理する作りだと、
--   ボタンを押し忘れた日の分がそのまま消えてしまう。
--
--   発表を検知した時点でキューに記録し、処理済みフラグで管理すれば、
--   数日空けても未処理分がすべて残る。
-- =============================================

CREATE TABLE IF NOT EXISTS earnings_queue (
    company_code   VARCHAR(10) PRIMARY KEY,

    company_name   VARCHAR(200),
    announced_date DATE,          -- 発表を検知した日
    source         VARCHAR(20),   -- intraday / afterhours / upcoming

    processed      BOOLEAN DEFAULT FALSE,
    processed_at   TIMESTAMPTZ,

    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE  earnings_queue                IS '決算発表・業績修正のあった銘柄の処理待ちキュー';
COMMENT ON COLUMN earnings_queue.processed      IS '財務データの更新が済んだか。押し忘れた日の分を残すために使う';
COMMENT ON COLUMN earnings_queue.announced_date IS '発表を検知した日';

-- 未処理を古い順に拾うための索引
CREATE INDEX IF NOT EXISTS idx_earnings_queue_pending
    ON earnings_queue(processed, announced_date);

ALTER TABLE earnings_queue ENABLE ROW LEVEL SECURITY;
