-- ============================================
-- コミュニティQ&A テーブル作成
-- Supabase SQL Editorで実行してください
-- ============================================

-- 質問テーブル
CREATE TABLE IF NOT EXISTS community_questions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    company_code TEXT,
    company_name TEXT,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_resolved BOOLEAN NOT NULL DEFAULT false,
    is_anonymous BOOLEAN NOT NULL DEFAULT false,
    answer_count INTEGER NOT NULL DEFAULT 0,
    like_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 回答テーブル
CREATE TABLE IF NOT EXISTS community_answers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id UUID NOT NULL REFERENCES community_questions(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    content TEXT NOT NULL,
    is_best BOOLEAN NOT NULL DEFAULT false,
    is_anonymous BOOLEAN NOT NULL DEFAULT false,
    like_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- いいねテーブル（質問・回答共通）
CREATE TABLE IF NOT EXISTS community_likes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('question', 'answer')),
    target_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, target_type, target_id)
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_cq_created ON community_questions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cq_company ON community_questions(company_code);
CREATE INDEX IF NOT EXISTS idx_cq_resolved ON community_questions(is_resolved);
CREATE INDEX IF NOT EXISTS idx_ca_question ON community_answers(question_id);
CREATE INDEX IF NOT EXISTS idx_cl_target ON community_likes(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_cl_user ON community_likes(user_id, target_type, target_id);

-- RLS（Row Level Security）は必要に応じて設定してください
-- ALTER TABLE community_questions ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE community_answers ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE community_likes ENABLE ROW LEVEL SECURITY;
