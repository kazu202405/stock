-- =============================================
-- 企業分析レポート：生成した文章のキャッシュ
--
-- レポートの数値部分はDBから都度組み立てるが、文章部分（ひとことで言うと／
-- 勝ち筋／リスク／学べること／まとめ）はLLMで生成するため、毎回叩くと
-- 遅くコストもかかる。生成結果をここに保存して再利用する。
--
-- 設計方針:
--   将来「経営者が自社の数字を入れてレポートを出す」を足せるよう、
--   上場銘柄コードに紐づけない。source で источник を切り替える。
--     source='listed' … 上場企業（source_key = 銘柄コード）
--     source='own'    … 自社決算（source_key = settlements 側のID）
-- =============================================

CREATE TABLE IF NOT EXISTS stock_reports (
    source       VARCHAR(16)  NOT NULL DEFAULT 'listed',
    source_key   VARCHAR(64)  NOT NULL,

    -- 生成した文章（{one_line, strengths[], risks[], learnings[], closing}）
    narrative    JSONB,

    -- 生成の再現性・鮮度判定用
    model        VARCHAR(64),
    input_hash   VARCHAR(64),   -- 元データが変わったら作り直すための指紋
    generated_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (source, source_key)
);

COMMENT ON TABLE  stock_reports            IS '企業分析レポートの生成文章キャッシュ';
COMMENT ON COLUMN stock_reports.source     IS 'データ源: listed=上場企業 / own=自社決算';
COMMENT ON COLUMN stock_reports.source_key IS 'listedなら銘柄コード、ownなら自社決算のID';
COMMENT ON COLUMN stock_reports.input_hash IS '生成元データの指紋。変化したら再生成する';

CREATE INDEX IF NOT EXISTS idx_stock_reports_generated ON stock_reports(generated_at);

-- RLS: アクセスは全てFlask経由（サービスロールキー）のためポリシーは不要。
-- anon/authenticated からの書き込みを塞ぐ目的で有効化する。
ALTER TABLE stock_reports ENABLE ROW LEVEL SECURITY;
