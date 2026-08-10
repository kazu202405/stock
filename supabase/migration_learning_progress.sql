-- 学習の進捗記録
--
-- 背景:
--   Company Note は「儲かった」ではなく「賢くなった」が見えることを狙う。
--   学習ノートの解説は読めるが、どこまで理解したかを持つ場所が無かった。
--
-- 設計:
--   term_id は learning.html の terms[].id（'per' 'pbr' 'operating_cf' 等）。
--   解説文そのものはユーザーごとに変わらないのでDB化しない。
--   ここに持つのは「誰がどの項目を理解したか」だけ。
--
--   状態は understood の1段階に絞る。「読んだ」「なんとなく分かった」まで
--   分けても本人が使い分けられず、記録が続かない。
--   チェックを外せば行ごと消す運用にし、履歴は追わない。

CREATE TABLE IF NOT EXISTS learning_progress (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL,
    term_id      VARCHAR(60) NOT NULL,
    understood_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 同じ人が同じ項目を二重に持たない
    CONSTRAINT learning_progress_user_term_unique UNIQUE (user_id, term_id)
);

-- マイページ・学習ノートはどちらも「その人の全項目」を引くのでuser_id先頭で足りる
CREATE INDEX IF NOT EXISTS idx_learning_progress_user
    ON learning_progress (user_id);

COMMENT ON TABLE  learning_progress             IS '学習ノートの項目ごとの理解済み記録';
COMMENT ON COLUMN learning_progress.term_id     IS 'learning.html の terms[].id。解説文はDBに持たない';
COMMENT ON COLUMN learning_progress.understood_at IS '理解したと記録した日時。取り消し時は行ごと削除する';
