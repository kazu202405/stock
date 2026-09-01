-- どの有価証券報告書を取り込んだかを覚える（2026-09-02）
--
-- なぜ要るか:
--   有報は年に1回出る。取り込んだきりだと、翌年の有報が出ても古いままになる。
--   ∴ 「どの書類を、いつ取り込んだか」を持っておき、より新しい有報が出たら
--     その会社だけ取り直す。
--
-- ⚠️ 判定は **report_period_end（対象決算期）** で行う。提出日ではない。
--    訂正報告書や再提出があると提出日は動くが、対象の決算期は変わらない。
--
-- 既存の edinet_codes（証券コード→EDINETコードの対応表）に足す。
-- 銘柄1件につき1行という粒度が同じで、別表にする理由が無い。

alter table edinet_codes
    add column if not exists report_doc_id      text,
    add column if not exists report_period_end  date,
    add column if not exists report_fetched_at  timestamptz;

create index if not exists idx_edinet_codes_report_period
    on edinet_codes (report_period_end);
