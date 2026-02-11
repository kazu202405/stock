-- ============================================
-- 投稿名（display_name / poster_name）追加
-- Supabase SQL Editorで実行してください
-- ============================================

-- app_usersにデフォルト表示名を追加
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS display_name TEXT;

-- notesに投稿者名を追加
ALTER TABLE notes ADD COLUMN IF NOT EXISTS poster_name TEXT;

-- community_questionsに投稿者名を追加
ALTER TABLE community_questions ADD COLUMN IF NOT EXISTS poster_name TEXT;

-- community_answersに投稿者名を追加
ALTER TABLE community_answers ADD COLUMN IF NOT EXISTS poster_name TEXT;
