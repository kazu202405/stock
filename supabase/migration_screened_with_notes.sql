-- スクリーナーの並びに「運営のメモがあるか」を混ぜるためのビュー（2026-09-06）
--
-- なぜビューを作るか:
--   スクリーナーは50件ずつサーバーで区切って返している。メモの有無は
--   別テーブル（stock_notes）なので、取得したあとにPythonやブラウザで
--   並べ替えると、**同じスコアの塊がページをまたいだときに順序が崩れる**
--   （1ページ目にメモ付きと無しが混ざり、2ページ目にまたメモ付きが出る）。
--   並べ替えはDBにやらせる、という既存の方針をそのまま守る。
--
-- ⚠️ **screened_latest は触らない。** あの表は分析のたびに書き戻される。
--    列を足すのではなく、読むときだけ結合したビューを重ねる。
--
-- ⚠️ security_invoker を立てる。立てないとビューは作成者の権限で動き、
--    元テーブルの RLS を迂回する読み口を作ってしまう。

CREATE OR REPLACE VIEW screened_with_notes
WITH (security_invoker = true) AS
SELECT
    s.*,
    (n.company_code IS NOT NULL) AS has_note
FROM screened_latest s
LEFT JOIN stock_notes n ON n.company_code = s.company_code;

COMMENT ON VIEW screened_with_notes IS
    'screened_latest に「運営のメモがあるか」(has_note) を足しただけの読み取り用。'
    'スクリーナーが並べ替えとページングに使う。書き込みは元テーブルへ。';
