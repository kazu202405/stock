-- =============================================
-- JPX公式の上場銘柄情報を保持する列を足す
--
-- 背景:
--   業種をLLMに判定させると誤りが残った（家具小売に「専門商社」、
--   厨房機器メーカーに「建設」）。JPXが全銘柄の業種区分を公式に無料公開して
--   いるため、業種は事実データとして取り込み、LLMには細かいテーマ判定だけを
--   任せる。
--
--   industry_jp は既存列だが、Yahoo!ファイナンス日本版からの取得が
--   アクセス制限で完走できず、3,877件中76件（2%）しか埋まっていなかった。
--   JPXからの取り込みで100%になる。
--
--   market_segment があるとETF・REIT・PRO Marketを確実に除外できる。
--   従来は銘柄コードからの推測に頼っていた。
-- =============================================

ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS industry17_jp   VARCHAR(40);
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS market_segment  VARCHAR(20);
ALTER TABLE screened_latest ADD COLUMN IF NOT EXISTS size_category   VARCHAR(20);

COMMENT ON COLUMN screened_latest.industry_jp     IS 'JPX 33業種区分';
COMMENT ON COLUMN screened_latest.industry17_jp   IS 'JPX 17業種区分（33業種をまとめた粗い分類）';
COMMENT ON COLUMN screened_latest.market_segment  IS 'プライム／スタンダード／グロース';
COMMENT ON COLUMN screened_latest.size_category   IS 'TOPIX Core30／Large70／Mid400／Small';

CREATE INDEX IF NOT EXISTS idx_screened_industry ON screened_latest(industry_jp);
CREATE INDEX IF NOT EXISTS idx_screened_market   ON screened_latest(market_segment);

-- 33業種はタグとしても引けるようにする（スクリーナーの絞り込みを業種とテーマで統一するため）。
-- kind='industry' として theme と分け、tagging_enabled=false でLLMの候補からは外す。
-- 実データは sync_jpx_master.py が投入する。
