-- app_users からパスワードを外す
--
-- 背景:
--   認証を GIA（キャンパス）の Supabase Auth に一本化した。
--   株アプリは自前でパスワードを検証しないため、password_hash はもう使わない。
--   ただし列が NOT NULL のままだと、新しいユーザーの行を作れず
--   「ログインはできるのにアプリに入れない」状態になる。
--
--   app_users に残す役割は、表示名・紹介コード・紹介ツリー・マーケット所感。
--   主キーは auth.users.id と同じUUIDにそろえてある。
--
-- 適用しなくてもアプリは動く（コード側で 'moved-to-gia-auth' を入れている）。
-- 適用すると、その場しのぎの値を入れる必要がなくなる。

ALTER TABLE app_users
    ALTER COLUMN password_hash DROP NOT NULL;

COMMENT ON COLUMN app_users.password_hash IS
    '未使用。認証はGIAプロジェクトのauth.usersが正本。将来削除してよい';

COMMENT ON COLUMN app_users.id IS
    'GIAプロジェクトの auth.users.id と同じUUID（認証統一のため）';
