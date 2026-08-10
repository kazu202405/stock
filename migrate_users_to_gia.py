"""株アプリのユーザーをGIA（キャンパス）の auth.users に寄せる。

なぜ:
    株アプリは独自の app_users にパスワードを持ち、キャンパスは別プロジェクトの
    Supabase Auth を使っていた。同じ人が2アカウントになり、キャンパスで課金しても
    株アプリが会員だと分からない。認証をGIA側へ一本化する。

このスクリプトがやること:
    1. app_users の各行を、メールで GIA の auth.users と突き合わせる
    2. GIA側に無ければ作る（パスワードはランダム。本人に再設定してもらう）
    3. app_users の id を auth.users.id に付け替える
    4. ユーザーに紐づくデータ（ノート・ウォッチリスト・お気に入り・
       デモ売買・コミュニティ・学習進捗）の user_id を新IDへ差し替える
    5. 破棄指定のユーザーは、そのデータごと削除する

    パスワードは移行できない。株アプリは werkzeug の scrypt、
    Supabase Auth は別方式のため、ハッシュを持ち込めない。

使い方:
    python migrate_users_to_gia.py --dry-run      # 何が起きるか表示するだけ
    python migrate_users_to_gia.py --apply        # 実行
    python migrate_users_to_gia.py --apply --discard a@x.com,b@x.com

安全のため、--apply を付けない限り一切書き込まない。
"""

import argparse
import os
import secrets
import sys

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

from dotenv import load_dotenv

load_dotenv()

import gia_identity
from supabase_client import get_supabase_client

# user_id を持つテーブル（列名つき）。ここに漏れがあると孤児データが残る。
USER_SCOPED = (
    ('notes', 'user_id'),
    ('watched_tickers', 'user_id'),
    ('favorite_stocks', 'user_id'),
    ('demo_account', 'user_id'),
    ('demo_portfolio', 'user_id'),
    ('demo_trades', 'user_id'),
    ('community_questions', 'user_id'),
    ('community_answers', 'user_id'),
    ('community_likes', 'user_id'),
    ('learning_progress', 'user_id'),
)


def count_rows(client, table, column, value):
    try:
        r = (client.table(table).select(column, count='exact')
             .eq(column, value).limit(1).execute())
        return r.count or 0
    except Exception:
        return None   # テーブル未作成


def repoint(client, table, column, old_id, new_id, apply_changes):
    n = count_rows(client, table, column, old_id)
    if not n:
        return 0
    if apply_changes:
        client.table(table).update({column: new_id}).eq(column, old_id).execute()
    return n


def delete_rows(client, table, column, value, apply_changes):
    n = count_rows(client, table, column, value)
    if not n:
        return 0
    if apply_changes:
        client.table(table).delete().eq(column, value).execute()
    return n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='実際に書き込む')
    parser.add_argument('--dry-run', action='store_true', help='表示のみ（既定）')
    parser.add_argument('--discard', default='',
                        help='破棄するメールアドレス（カンマ区切り）')
    parser.add_argument('--rename', action='append', default=[],
                        help='旧メール=新メール。別アドレスの本人アカウントへ'
                             'データごと引き継ぐ（例 major@x.com=global@x.com）')
    args = parser.parse_args()
    apply_changes = args.apply and not args.dry_run

    if not gia_identity.is_configured():
        print('GIA_SUPABASE_* が未設定です。.env を確認してください。')
        return 1

    discard = {e.strip().lower() for e in args.discard.split(',') if e.strip()}
    # 旧アドレス → 引き継ぎ先アドレス。本人の別アカウントへデータを寄せる。
    renames = {}
    for pair in (args.rename or []):
        if '=' in pair:
            old, new = pair.split('=', 1)
            renames[old.strip().lower()] = new.strip()

    client = get_supabase_client()
    users = client.table('app_users').select('*').execute().data or []

    print(f'{"[実行]" if apply_changes else "[確認のみ]"} app_users {len(users)}件\n')

    created, linked, moved, removed = 0, 0, 0, 0
    reset_needed = []

    for u in users:
        email = (u.get('email') or '').strip()
        old_id = u['id']

        if email.lower() in discard:
            total = 0
            for table, column in USER_SCOPED:
                total += delete_rows(client, table, column, old_id, apply_changes)
            if apply_changes:
                client.table('app_users').delete().eq('id', old_id).execute()
            removed += 1
            print(f'  破棄  {email:32} 関連データ{total}件も削除')
            continue

        # 引き継ぎ先が指定されていれば、そのアドレスのGIAアカウントに寄せる
        target_email = renames.get(email.lower(), email)
        if target_email.lower() != email.lower():
            note_prefix = f'{email} → {target_email} へ統合 / '
            email = target_email
        else:
            note_prefix = ''

        account = gia_identity.find_auth_user_by_email(email)
        if account:
            linked += 1
            note = note_prefix + 'GIA側にあり'
        else:
            if apply_changes:
                account = gia_identity.create_auth_user(
                    email, secrets.token_urlsafe(24))
            else:
                account = {'id': '(新規作成予定)', 'email': email}
            created += 1
            reset_needed.append(email)
            note = note_prefix + 'GIA側に新規作成'

        new_id = account['id']
        if new_id == old_id:
            print(f'  そのまま {email:30} ID一致')
            continue

        detail = []
        for table, column in USER_SCOPED:
            n = repoint(client, table, column, old_id,
                        new_id if apply_changes else old_id, apply_changes)
            if n:
                detail.append(f'{table}:{n}')

        # 主キーの差し替えは、紹介ツリーの自己参照FK
        # (app_users.referred_by → app_users.id) があるため単純にはできない。
        # 参照している行を一旦外し、IDを変えてから貼り直す。
        if apply_changes:
            existing_target = (client.table('app_users').select('id')
                               .eq('id', new_id).execute().data)

            children = (client.table('app_users').select('id')
                        .eq('referred_by', old_id).execute().data or [])
            for child in children:
                client.table('app_users').update(
                    {'referred_by': None}).eq('id', child['id']).execute()

            if existing_target:
                # 引き継ぎ先の行が既にある場合は、古い行を消すだけにする
                client.table('app_users').delete().eq('id', old_id).execute()
            else:
                # password_hash は NOT NULL。認証はGIAに移り使わないので、
                # 旧ハッシュを残さず、照合に成功しない印を入れておく。
                client.table('app_users').update({
                    'id': new_id,
                    'email': email,
                    'password_hash': 'moved-to-gia-auth',
                }).eq('id', old_id).execute()

            for child in children:
                client.table('app_users').update(
                    {'referred_by': new_id}).eq('id', child['id']).execute()
            if children:
                print(f'        紹介ツリー {len(children)}件を新IDへ付け替え')
        moved += 1
        print(f'  移行  {email:32} {note} / ' + (', '.join(detail) or '関連データなし'))

    print(f'\n新規作成 {created} / 既存に紐付け {linked} / ID差し替え {moved} / 破棄 {removed}')
    if reset_needed:
        print('\nパスワード再設定が必要な方:')
        for e in reset_needed:
            print(f'  - {e}')
    if not apply_changes:
        print('\n※ 確認のみです。実行するには --apply を付けてください。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
