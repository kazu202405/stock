-- 定期実行が「実際に何をしたか」を1行だけ残す（2026-08-31）
--
-- なぜ要るか:
--   鮮度パネルはデータの新しさを見ているが、データは
--   **「取れなかった」と「変わらなかった」を区別できない**。
--   2026-08-31、株価バッチが3回とも0件で終わり、スクリーナーが丸1日
--   前営業日の終値を出し続けたが、price_updated_at は株価が変わったときしか
--   動かないため、パネルは「98.3%が1営業日以内」と緑寄りのままだった。
--
--   ここに1回1行だけ記録すれば、「走った・取れた件数・失敗」が直接読める。
--   銘柄ごとに印を付ける案（price_checked_at 列）もあったが、
--   1回の実行で3,669行を書き直すことになるので、1行の記録にした。
--
-- ⚠️ 記録の失敗でジョブ本体を止めないこと（record_job_run は例外を飲む）。
--    見張りが本体を殺すのは本末転倒。

create table if not exists job_runs (
    id       bigserial primary key,
    job_id   text        not null,
    ran_at   timestamptz not null default now(),
    ok       boolean     not null,
    detail   text
);

create index if not exists job_runs_job_id_ran_at_idx
    on job_runs (job_id, ran_at desc);

alter table job_runs enable row level security;
-- 読み書きは service_role のみ（アプリは service_role で接続している）。
-- 一般ユーザー向けのポリシーは作らない＝anonキーからは触れない。
