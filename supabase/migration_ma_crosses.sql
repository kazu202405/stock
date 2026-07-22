-- =============================================
-- 移動平均のクロス（ゴールデンクロス／デッドクロス）発生日
--
-- 背景:
--   従来 signal_stocks.gc_date には「kabutanをスクレイピングした時刻」を
--   全銘柄一律で入れていた。GCがいつ起きたかは記録されておらず、
--   毎回上書きされるため履歴も残らなかった。
--   （実データでも数千件が同一タイムスタンプになっていた）
--
--   stock_price_history に日足1年分があるので、5日線と25日線の交差を
--   自前で計算すれば、発生日を正確に、しかも過去分まで復元できる。
--   外部サイトの構造変更にも影響されない。
--
-- 使い方:
--   直近のGC/DCを見るなら latest_gc_date / latest_dc_date を参照。
--   過去の発生履歴は crosses（[{date, type}] の配列）に入れる。
-- =============================================

CREATE TABLE IF NOT EXISTS ma_crosses (
    company_code    VARCHAR(10) PRIMARY KEY,

    latest_gc_date  DATE,     -- 直近でゴールデンクロスした日
    latest_dc_date  DATE,     -- 直近でデッドクロスした日
    cross_count     INTEGER,  -- 期間内の交差回数（多いほど方向感が乏しい）

    -- 期間内の全交差 [{"date": "2026-05-01", "type": "gc"}, ...]
    crosses         JSONB,

    -- 計算に使った移動平均の日数（後で変えた場合に区別できるように）
    short_window    INTEGER DEFAULT 5,
    long_window     INTEGER DEFAULT 25,

    calculated_at   TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE  ma_crosses                IS '移動平均のクロス発生日（株価履歴から自前計算）';
COMMENT ON COLUMN ma_crosses.latest_gc_date IS '直近のゴールデンクロス発生日。並べ替えの主キー';
COMMENT ON COLUMN ma_crosses.crosses        IS '期間内の全交差。[{date, type}] の配列';
COMMENT ON COLUMN ma_crosses.cross_count    IS '交差回数。多い銘柄はだましが多く方向感に乏しい';

-- 「最近GCした順」に並べるための索引
CREATE INDEX IF NOT EXISTS idx_ma_crosses_gc ON ma_crosses(latest_gc_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_ma_crosses_dc ON ma_crosses(latest_dc_date DESC NULLS LAST);

-- RLS: アクセスは全てFlask経由（サービスロールキー）のためポリシーは不要。
ALTER TABLE ma_crosses ENABLE ROW LEVEL SECURITY;
