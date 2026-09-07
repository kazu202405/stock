-- 招待経由で登録した人に、招待の段を覚えさせる（2026-09-07）
--
-- なぜ要るか:
--   /invite（月額11,000円・Company Note + 講義録画 + 月1回の企業研究会）を
--   受け取った人が、まず無料で使ってから入会できるようにした。
--   ところが「無料で入る」を挟むと、その人がアプリ内の /membership を開いた
--   ときに見えるのは公開の段（4,980円）だけで、**招待の段には二度と戻れない**。
--   誰から何に招かれたのかを、無料期間をまたいで持ち続ける必要がある。
--
-- なぜセッションではなく列にするか:
--   招待を受け取った日と、入会を決める日は別の日。別の端末のことも多い。
--   セッションでは持たない。
--
-- ⚠️ **この列は「招かれた段」であって「いま契約している段」ではない。**
--    契約の正本は GIA 側（applicants.plan）で、会員判定は
--    gia_identity.is_paid_member() が見る。ここを会員判定に使わないこと。
--    ここを見てよいのは「どの申込ページへ送るか」だけ。

ALTER TABLE app_users
  ADD COLUMN IF NOT EXISTS invited_plan VARCHAR(20),
  ADD COLUMN IF NOT EXISTS invited_at   TIMESTAMPTZ;

-- 段の名前は決済（Stripe の Price）と紐づく。打ち間違いをそのまま入れると
-- gia2018.com/upgrade/<段> が 404 相当になり、本人には「押しても何も
-- 起きない」としか見えない。入れられる値をDB側で縛る。
--
-- ⚠️ **ここに段を足す前に、app.py の MEMBERSHIP_TIERS に足すこと。**
--    MEMBERSHIP_TIERS に無い段を入れると membership_tier_for() が黙って
--    公開の段（4,980円）に落とすため、招待した本人には「なぜか安いほうしか
--    出てこない」という形で見え、エラーはどこにも出ない。
--    premium（33,000円）はまだ MEMBERSHIP_TIERS に無いので、ここでも許さない。
DO $$
BEGIN
  ALTER TABLE app_users
    ADD CONSTRAINT app_users_invited_plan_known
    CHECK (invited_plan IS NULL OR invited_plan IN ('invite'));
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN app_users.invited_plan IS
  '招かれた会員の段（いまは invite のみ）。契約している段ではない。'
  '申込ページの行き先を決めるためだけに使う。会員判定はGIA側が正本。';
COMMENT ON COLUMN app_users.invited_at IS
  '招待ページを最初に踏んだ日時。';

-- 招待経由の人を数えるとき用（母数が小さいので部分インデックスで足りる）
CREATE INDEX IF NOT EXISTS idx_app_users_invited_plan
  ON app_users(invited_plan) WHERE invited_plan IS NOT NULL;
