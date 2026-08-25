-- 勉強会の資料・動画（2026-08-25）
--
-- 企業分析の勉強会そのものを Company Note の中で提供するための器。
-- 動画は外部（YouTubeの限定公開など）のURL、スライドや画像は
-- Supabase Storage の非公開バケット `study-materials` に置く。
--
-- なぜ Storage に置くか（リンクを貼るだけにしない理由）:
--   Google Drive のリンクを貼ると、**権限の管理がDrive側になる**。
--   退会した人もリンクを知っていれば見続けられ、Company Note の会員判定と
--   連動しない。バケットを非公開にして、会員判定を通った人にだけ
--   期限つきURLを都度発行すれば、退会した時点で見られなくなる。
--
-- 見せる相手:
--   有料会員（online 4,980 / real 7,980 / invite 11,000 / premium 33,000）。
--   無料会員は見られない。段による出し分けはしない（2026-08-25 五島さん判断）。
--   判定は既存の is_member_session() を使う。**段の判定を新しく作らない。**

CREATE TABLE IF NOT EXISTS study_materials (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    title        TEXT NOT NULL,
    description  TEXT,

    -- 'video'（外部URLを埋め込む） / 'file'（Storageに置く）
    kind         TEXT NOT NULL CHECK (kind IN ('video', 'file')),

    -- kind='video' のとき。YouTube等のURL
    video_url    TEXT,

    -- kind='file' のとき。バケット内のパス（例: '2026/08/kessan-yomikata.pdf'）
    -- 公開URLは持たない。開くたびに期限つきURLを発行する
    file_path    TEXT,
    file_name    TEXT,          -- 画面に出す元のファイル名
    file_size    BIGINT,        -- バイト
    content_type TEXT,

    -- 並び順。新しいものを上に出すが、固定したい資料は手で前へ出せる
    sort_order   INTEGER NOT NULL DEFAULT 0,

    -- 下書き。書きかけを会員に見せない
    is_published BOOLEAN NOT NULL DEFAULT FALSE,

    -- 開催日・収録日。作成日と別に持つ（後から登録することがあるため）
    held_on      DATE,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 種別ごとに、必要な列が埋まっていることを保証する。
    -- ⚠️ これが無いと「動画なのにURLが空」の行が作れてしまい、
    --    画面には見出しだけが並ぶ（登録した本人も気づけない）。
    CONSTRAINT study_materials_source_required CHECK (
        (kind = 'video' AND video_url IS NOT NULL AND video_url <> '')
        OR
        (kind = 'file' AND file_path IS NOT NULL AND file_path <> '')
    )
);

-- 一覧は「公開されているものを、並び順→新しい順」で引く
CREATE INDEX IF NOT EXISTS idx_study_materials_listing
    ON study_materials (is_published, sort_order DESC, created_at DESC);

-- 更新時刻を自動で入れる
CREATE OR REPLACE FUNCTION set_study_materials_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_study_materials_updated_at ON study_materials;
CREATE TRIGGER trg_study_materials_updated_at
    BEFORE UPDATE ON study_materials
    FOR EACH ROW EXECUTE FUNCTION set_study_materials_updated_at();

-- RLS。アプリはサービスロールで触るので、それ以外からは読めないようにする。
-- ⚠️ 有効にしてポリシーを1つも置かないと、anon キーからは何も見えない。
--    会員への配信はアプリ（Flask）が会員判定をしてから返すので、これでよい。
ALTER TABLE study_materials ENABLE ROW LEVEL SECURITY;
