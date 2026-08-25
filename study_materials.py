"""勉強会の資料・動画。

企業分析の勉強会そのものを Company Note の中で提供するための器。
動画は外部（YouTubeの限定公開など）のURL、スライドや画像は Supabase Storage の
**非公開バケット**に置く。

なぜ Storage に置くか（リンクを貼るだけにしない理由）:
  Google Drive のリンクを貼ると**権限の管理がDrive側になる**。退会した人も
  リンクを知っていれば見続けられ、Company Note の会員判定と連動しない。
  バケットを非公開にして、会員判定を通った人にだけ期限つきURLを都度発行すれば、
  退会した時点で見られなくなる。

見せる相手:
  有料会員（4,980円〜）。無料会員は見られない。**段による出し分けはしない**
  （2026-08-25 五島さん判断）。判定は既存の is_member_session() を使い、
  段の判定を新しく作らない。

⚠️ migration（supabase/migration_study_materials.sql）は運用側が手で適用する。
   未適用の間もアプリが落ちないよう、テーブルが無いときは空として扱う。
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

BUCKET = 'study-materials'

# 置けるもの。動画はここに入れない（URLで貼る）。
# ⚠️ 動画をこのバケットに置かない理由: 1本1GBを50人が見れば50GBの転送になり、
#    費用が読めなくなる。YouTubeの限定公開なら転送は無料。
ALLOWED_TYPES = {
    'application/pdf': 'pdf',
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
    'application/vnd.ms-powerpoint': 'ppt',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
}

MAX_FILE_BYTES = 50 * 1024 * 1024      # 50MB。バケット側にも同じ上限をかけてある
SIGNED_URL_SECONDS = 60 * 60           # 1時間。読み終わる前に切れない程度


def _client():
    from supabase_client import get_supabase_client
    return get_supabase_client()


def _table_missing(error) -> bool:
    """migration 未適用のときだけ静かに空を返すための判定。"""
    text = str(error).lower()
    return ('study_materials' in text
            and ('does not exist' in text or 'pgrst205' in text
                 or 'schema cache' in text))


def safe_object_path(filename: str) -> str:
    """バケット内のパスを作る。**元のファイル名をそのまま使わない。**

    日本語や空白を含む名前は署名URLの取り回しで壊れやすく、同名の上書き事故も
    起きる。拡張子だけ引き継いで、本体はランダムにする。
    """
    ext = ''
    if filename and '.' in filename:
        ext = re.sub(r'[^A-Za-z0-9]', '', filename.rsplit('.', 1)[1])[:8].lower()
    now = datetime.now(timezone.utc)
    return '%04d/%02d/%s%s' % (now.year, now.month, uuid.uuid4().hex,
                               ('.' + ext) if ext else '')


def upload(file_storage) -> dict:
    """アップロードして、保存した情報を返す。

    file_storage は Flask の request.files['file']。
    受け付けない種類・大きすぎるものは ValueError にする。
    """
    content_type = (file_storage.mimetype or '').split(';')[0].strip()
    if content_type not in ALLOWED_TYPES:
        raise ValueError('この種類のファイルは置けません（%s）。'
                         'PDF・画像・PowerPoint・Excel に対応しています。'
                         % (content_type or '不明'))

    data = file_storage.read()
    if not data:
        raise ValueError('ファイルが空です。')
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('ファイルが大きすぎます（%.1fMB）。上限は%dMBです。'
                         % (len(data) / 1024 / 1024, MAX_FILE_BYTES // 1024 // 1024))

    path = safe_object_path(file_storage.filename)
    _client().storage.from_(BUCKET).upload(
        path, data, {'content-type': content_type})
    return {
        'file_path': path,
        'file_name': file_storage.filename or os.path.basename(path),
        'file_size': len(data),
        'content_type': content_type,
    }


def signed_url(path: str) -> str | None:
    """期限つきの閲覧URLを都度作る。

    ⚠️ 保存しない。保存すると期限切れのURLが画面に残り、退会後も
       生きているURLを配ることになる。
    """
    if not path:
        return None
    try:
        res = _client().storage.from_(BUCKET).create_signed_url(
            path, SIGNED_URL_SECONDS)
    except Exception as e:
        print('署名URLの発行に失敗 %s: %s' % (path, e))
        return None
    if isinstance(res, dict):
        return res.get('signedURL') or res.get('signedUrl') or res.get('signed_url')
    return None


def list_materials(published_only=True) -> list:
    """一覧。並び順→新しい順。テーブルが無ければ空。"""
    try:
        query = _client().table('study_materials').select('*')
        if published_only:
            query = query.eq('is_published', True)
        res = (query.order('sort_order', desc=True)
               .order('created_at', desc=True).execute())
        return res.data or []
    except Exception as e:
        if _table_missing(e):
            return []
        raise


def get_material(material_id: str) -> dict | None:
    try:
        res = (_client().table('study_materials').select('*')
               .eq('id', material_id).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        if _table_missing(e):
            return None
        raise


def create_material(data: dict) -> dict:
    payload = _writable(data)
    res = _client().table('study_materials').insert(payload).execute()
    return res.data[0] if res.data else {}


def update_material(material_id: str, data: dict) -> dict:
    payload = _writable(data)
    if not payload:
        return {}
    res = (_client().table('study_materials').update(payload)
           .eq('id', material_id).execute())
    return res.data[0] if res.data else {}


def delete_material(material_id: str) -> bool:
    """行を消し、置いてあるファイルも消す。

    ⚠️ 行だけ消すとバケットにファイルが残り続ける。月2〜4本でも数年で
       溜まるうえ、消したはずの資料が署名URLで開けてしまう。
    """
    material = get_material(material_id)
    if not material:
        return False
    path = material.get('file_path')
    if path:
        try:
            _client().storage.from_(BUCKET).remove([path])
        except Exception as e:
            print('ファイルの削除に失敗 %s: %s' % (path, e))
    _client().table('study_materials').delete().eq('id', material_id).execute()
    return True


_WRITABLE = ('title', 'description', 'kind', 'video_url', 'file_path',
             'file_name', 'file_size', 'content_type', 'sort_order',
             'is_published', 'held_on')


def _writable(data: dict) -> dict:
    return {k: v for k, v in (data or {}).items() if k in _WRITABLE}


def table_ready() -> bool:
    """migration が適用済みか。画面に案内を出すために使う。"""
    try:
        _client().table('study_materials').select('id').limit(1).execute()
        return True
    except Exception as e:
        if _table_missing(e):
            return False
        raise
