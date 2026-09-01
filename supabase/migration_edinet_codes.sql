-- EDINETコード一覧の取り込み（2026-09-01）
--
-- 金融庁EDINETが公開している提出者一覧（EdinetcodeDlInfo.csv）。
-- **APIキー無しでダウンロードできる**ので、鍵を待たずに使える。
--   https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip
--
-- 何に使うか:
--   1. 証券コード → EDINETコード の対応表。有報を引くのにこれが要る（第2段）
--   2. 登記上の本店所在地・資本金・法人番号・決算日。1リクエストで全件取れる
--
-- ⚠️ **所在地を screened_latest.headquarters に流し込まないこと。**
--    あちらはYahoo日本版から取った「本社」、こちらは有報の「登記上の本店」で、
--    別のものが入る。6498キッツは本社=東京都港区／登記上の本店=千葉市美浜区。
--    混ぜると1つの列に2つの意味が入り、どちらなのか誰にも分からなくなる。
--    さらにEDINET側は99%が都道府県から始まらない（「豊田市トヨタ町１番地」）。
--
-- ⚠️ 決算月も上書きしない。yfinance由来の値と44件の食い違いがあり、
--    どちらが正しいかは有報を見るまで決められない（第2段で判定する）。

create table if not exists edinet_codes (
    company_code      text primary key,   -- 4桁（EDINETの5桁から末尾の0を落としたもの）
    edinet_code       text not null,
    submitter_name    text,
    submitter_name_en text,
    submitter_kana    text,
    registered_address text,              -- 登記上の本店所在地（本社ではない）
    industry          text,
    fiscal_day        text,               -- 「3月31日」
    fiscal_month      int,                -- 3
    capital           bigint,             -- 百万円
    corporate_number  text,
    listed            text,               -- 上場区分
    consolidated      text,               -- 連結の有無
    submitter_type    text,
    updated_at        timestamptz not null default now()
);

create index if not exists idx_edinet_codes_edinet on edinet_codes(edinet_code);

alter table edinet_codes enable row level security;
-- 読み書きは service_role のみ（アプリは service_role で接続している）。
