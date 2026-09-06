-- 銘柄メモ（2026-09-06）
--
-- 何のために作るか:
--   12項目の適合度で緑の100点を取れるのは 3,886社中185社ある。数としては
--   多くないが、一覧で見ると全部同じ顔をしていて、どれから見ればいいか
--   分からない。しかも実測では 1000億円を超える会社に100点は1社も無く、
--   点数は身軽な会社に寄る。**点数だけでは「見るべき会社」を選べない。**
--
--   そこで、運営が「この会社は勉強になる」と思ったものに一言を残せるように
--   する。点数が機械の答えなら、こちらは人の答え。
--
-- ⚠️ **screened_latest に列を足さない。** あの表は分析のたびに書き戻される。
--    人が書いた文章を、機械が上書きしうる場所に置かない。別の表にして、
--    銘柄コードで結ぶ。分析が何度走ってもメモは消えない。
--
-- ⚠️ **点数の色に混ぜない。** 緑＝点数、緑/赤の薄い行＝GC/DC で既に2つの
--    意味が走っている。メモの印は別の記号にする（適合度の色で「点数の高さ」
--    と「データの確からしさ」を同じオレンジにして読み違いが起きた前例がある）。
--
-- 書けるのは管理者だけ。読むのは全員（会員でなくても銘柄ページは公開なので、
-- そこに出るメモも公開になる。**非公開にしたい話はここに書かない。**）

CREATE TABLE IF NOT EXISTS stock_notes (
    -- 1銘柄に1つ。履歴は持たない（書き直しは上書き）。
    company_code TEXT PRIMARY KEY,

    -- 本文。長文の解説ではなく、「どこを見ると面白いか」を数行で。
    body         TEXT NOT NULL,

    -- 誰が書いたか。app_users.id（＝auth.users.id）
    updated_by   UUID,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 一覧に印を出すとき、コードの集合だけを引く。
CREATE INDEX IF NOT EXISTS idx_stock_notes_updated_at
    ON stock_notes (updated_at DESC);

-- 空文字を入れられないようにする。空のメモがあると、一覧に印だけ出て
-- 開いても何も無い、という状態になる。
ALTER TABLE stock_notes DROP CONSTRAINT IF EXISTS stock_notes_body_not_blank;
ALTER TABLE stock_notes ADD CONSTRAINT stock_notes_body_not_blank
    CHECK (length(btrim(body)) > 0);

-- RLS。読みは全員、書きはサーバー（service_role）だけ。
-- ⚠️ anon に書きを許すと、APIの管理者チェックを通さずに直接書き込める。
ALTER TABLE stock_notes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS stock_notes_read ON stock_notes;
CREATE POLICY stock_notes_read ON stock_notes
    FOR SELECT USING (true);

-- 書き込みポリシーは作らない（= service_role 以外は書けない）。
-- ⚠️ FOR ALL のポリシーを1本置くと、PATCH で主キーごと書き換えられる。
--    選択肢マスタで実際に起きた形なので、読みと書きを分けたままにする。
