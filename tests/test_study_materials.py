"""勉強会の資料・動画（2026-08-25 追加）。

企業分析の勉強会そのものを Company Note の中で提供する。動画は外部
（YouTubeの限定公開など）のURL、スライドや画像は Supabase Storage の
**非公開バケット**に置く。

⚠️ 主眼は3つ。
  1. **有料会員（4,980円〜）だけに出す。** 段による出し分けはしない
     （2026-08-25 五島さん判断）ので、既存の会員判定をそのまま使い、
     段の判定を新しく作らない。
  2. **ファイルのURLを保存しない。** 開くたびに期限つきURLを発行する。
     保存すると、退会したあとも生きているURLを配ることになる。
  3. **動画をバケットに置かない。** 1本1GBを50人が見れば50GBの転送になり、
     費用が読めなくなる。限定公開のURLなら転送は無料。
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

import study_materials as sm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class GatingTest(unittest.TestCase):
    """有料会員だけに出す。"""

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config['TESTING'] = True

    def test_会員向けAPIは会員限定(self):
        source = read('app.py')
        block = source.split("@app.route('/api/study-materials'", 1)[1][:200]
        self.assertIn('@member_required_api', block)

    def test_管理APIは管理者限定(self):
        source = read('app.py')
        for path in ("'/api/admin/study-materials'",
                     "'/api/admin/study-materials/upload'"):
            block = source.split("@app.route(%s" % path, 1)[1][:220]
            self.assertIn('@admin_required_api', block)

    def test_未ログインは401(self):
        client = self.app_module.app.test_client()
        self.assertEqual(client.get('/api/study-materials').status_code, 401)

    def test_管理画面は管理者だけ(self):
        block = read('models', 'root.py').split(
            "@app.route('/admin/study-materials')", 1)[1].split('@app.route', 1)[0]
        self.assertIn('_require_admin()', block)

    def test_段の判定を新しく作らない(self):
        """段による出し分けはしない。判定を増やすと会員判定が2か所になる。"""
        source = read('app.py')
        block = source.split("@app.route('/api/study-materials'", 1)[1][:1200]
        for word in ('premium', 'invite', "plan ==", 'get_membership'):
            self.assertNotIn(word, block, word)


class SignedUrlTest(unittest.TestCase):
    """ファイルのURLは保存せず、開くたびに発行する。"""

    def test_期限つきURLを都度発行する(self):
        source = read('app.py')
        block = source.split('def api_list_study_materials', 1)[1] \
                      .split(chr(10) + '@app.route', 1)[0]
        self.assertIn('sm.signed_url(', block)

    def test_内部のパスを画面に返さない(self):
        """バケット内のパスが分かると、他の署名URLを推測する足がかりになる。"""
        source = read('app.py')
        block = source.split('def api_list_study_materials', 1)[1] \
                      .split(chr(10) + '@app.route', 1)[0]
        self.assertIn("item.pop('file_path', None)", block)

    def test_URLをDBに持たない(self):
        """列に signed url を保存する設計にしていないこと。"""
        migration = read('supabase', 'migration_study_materials.sql')
        self.assertNotIn('signed_url', migration)
        self.assertIn('file_path', migration)

    def test_期限は読み終わる程度(self):
        self.assertGreaterEqual(sm.SIGNED_URL_SECONDS, 600)
        self.assertLessEqual(sm.SIGNED_URL_SECONDS, 24 * 3600)


class UploadTest(unittest.TestCase):

    def test_動画の種類を受け付けない(self):
        """動画をバケットに置くと転送量で費用が読めなくなる。"""
        for mime in ('video/mp4', 'video/quicktime', 'application/octet-stream'):
            self.assertNotIn(mime, sm.ALLOWED_TYPES, mime)

    def test_スライドと画像は置ける(self):
        for mime in ('application/pdf', 'image/png', 'image/jpeg',
                     'application/vnd.openxmlformats-officedocument.presentationml.presentation'):
            self.assertIn(mime, sm.ALLOWED_TYPES, mime)

    def test_大きさに上限がある(self):
        self.assertLessEqual(sm.MAX_FILE_BYTES, 100 * 1024 * 1024)

    def test_元のファイル名をそのまま使わない(self):
        """日本語や空白は署名URLの取り回しで壊れやすく、同名の上書きも起きる。"""
        path = sm.safe_object_path('決算の 読み方 v2.pdf')
        self.assertTrue(path.endswith('.pdf'))
        self.assertNotIn('決算', path)
        self.assertNotIn(' ', path)

    def test_同じ名前でも別のパスになる(self):
        a = sm.safe_object_path('a.pdf')
        b = sm.safe_object_path('a.pdf')
        self.assertNotEqual(a, b)

    def test_拡張子が無くても落ちない(self):
        self.assertTrue(sm.safe_object_path('noext'))
        self.assertTrue(sm.safe_object_path(''))


class MigrationTest(unittest.TestCase):

    def setUp(self):
        self.sql = read('supabase', 'migration_study_materials.sql')

    def test_種別ごとに必要な列を強制する(self):
        """これが無いと「動画なのにURLが空」の行が作れてしまい、
        画面には見出しだけが並ぶ（登録した本人も気づけない）。"""
        self.assertIn('CHECK', self.sql)
        self.assertIn("kind = 'video' AND video_url IS NOT NULL", self.sql)
        self.assertIn("kind = 'file' AND file_path IS NOT NULL", self.sql)

    def test_下書きを持てる(self):
        self.assertIn('is_published', self.sql)

    def test_RLSを有効にする(self):
        self.assertIn('ENABLE ROW LEVEL SECURITY', self.sql)

    def test_サーバー側でも同じ検証をする(self):
        """DBの制約だけだと、画面には素っ気ないエラーしか出せない。"""
        source = read('app.py')
        self.assertIn('def _validate_study_material', source)


class DegradesWithoutTableTest(unittest.TestCase):
    """migration は運用側が手で適用する。未適用の間も落ちない。"""

    def test_テーブルが無ければ空を返す(self):
        self.assertEqual(sm.list_materials(), [])

    def test_適用済みかを画面に出せる(self):
        self.assertIn('def table_ready', read('study_materials.py'))
        self.assertIn('smNotReady', read('templates', 'admin_study_materials.html'))


class DeleteTest(unittest.TestCase):

    def test_行とファイルを両方消す(self):
        """行だけ消すとバケットにファイルが残り、消したはずの資料が
        署名URLで開けてしまう。"""
        source = read('study_materials.py')
        block = source.split('def delete_material', 1)[1].split(chr(10) + 'def ', 1)[0]
        self.assertIn('.remove([path])', block)
        self.assertIn(".delete().eq('id', material_id)", block)


class LearningTabTest(unittest.TestCase):

    def setUp(self):
        self.html = read('templates', 'learning.html')

    def test_勉強会のタブがある(self):
        self.assertIn("activeTab === 'study'", self.html)
        self.assertIn('loadStudyMaterials()', self.html)

    def test_非会員には案内を出す(self):
        self.assertIn('id="studyGate"', self.html)
        self.assertIn('res.status === 401 || res.status === 403', self.html)

    def test_本文をエスケープする(self):
        self.assertIn('function studyEscape(', self.html)
        self.assertIn('studyEscape(item.title', self.html)

    def test_動画は比率を保つ(self):
        """高さを固定すると端末によって黒帯が出る。"""
        self.assertIn('padding-top: 56.25%', self.html)

    def test_いろいろなYouTubeのURLを埋め込みに直す(self):
        for pattern in ('watch', 'embed', 'shorts', 'youtu'):
            self.assertIn(pattern, self.html, pattern)


if __name__ == '__main__':
    unittest.main()
