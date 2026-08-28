"""書き込み系APIには必ず入口の判定を置く（2026-08-25 の点検）。

きっかけ:
  /api/watchlist/update に認証が無く、**未ログインでも POST するだけで**
  任意の銘柄の財務データを上書きできた。調べたら1本ではなく、
  運用系のバッチ起動（全銘柄の再取得・kabutanのスクレイピング・再計算）が
  軒並み開いていた。

  実害は情報漏洩ではなく「外から起動される」ことで出る:
    - 全3,880銘柄の再取得が走り、Yahooから遮断される
      （実際この日、別件で3時間遮断されている）
    - EDINET DB の無料枠（100回/日）を使い切られる

⚠️ このテストの主眼は「入口の判定を1つも書き忘れないこと」。
   61本もあると、新しいAPIを足すときに1本だけ抜ける。抜けても
   画面は正常に動くので、抜けたことに気づく手段が無い。
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WRITE_METHODS = ('POST', 'PUT', 'DELETE', 'PATCH')

# 入口で誰かを確かめている印。デコレータでも、本体での明示チェックでもよい。
# get_or_create_guest_user_id / _get_demo_user_id は「利用者ごとに分ける」
# 仕組みで、他人のデータには触れない（ノート・デモ売買・お気に入り）。
GUARD_MARKERS = (
    '@admin_required_api', '@member_required_api', '@login_required_api',
    '@role_required', "session.get('user_role')", "session.get('user_id')",
    'get_current_user', 'is_member_session',
    'get_or_create_guest_user_id', '_get_demo_user_id',
)

# 意図して開けているもの。足すときは理由を書くこと。
INTENTIONALLY_OPEN = {
    '/api/auth/login',
    '/api/auth/register',
    '/api/auth/logout',
}

BODY_LINES = 40


def read_source():
    with open(os.path.join(ROOT, 'tests', 'test_write_endpoints_guarded.py'),
              encoding='utf-8') as f:
        return f.read()


def write_routes():
    """app.py の書き込み系ルートを (行番号, メソッド, パス, 判定の有無) で返す。"""
    with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as f:
        lines = f.read().splitlines()

    found = []
    for i, line in enumerate(lines):
        m = re.match(r"@app\.route\('([^']+)'(.*)\)", line.strip())
        if not m:
            continue
        path = m.group(1)
        methods = re.findall(r"'(GET|POST|PUT|DELETE|PATCH)'", m.group(2)) or ['GET']
        if not any(x in methods for x in WRITE_METHODS):
            continue

        j = i + 1
        decorators = []
        while j < len(lines) and lines[j].strip().startswith('@'):
            decorators.append(lines[j])
            j += 1
        # ⚠️ 本文は**次のルート／次の関数の手前で必ず打ち切る**。
        #    固定行数で読むと、短い関数のとき窓が次の関数まで食い込み、
        #    隣が守られているだけで「守られている」と誤判定する。
        #    実際 /api/dc-stocks/scrape（本文11行）がこれで見逃されていた。
        end = j
        while end < len(lines) and end < j + BODY_LINES:
            if end > j and (lines[end].startswith('@app.')
                            or lines[end].startswith('def ')):
                break
            end += 1
        body = '\n'.join(decorators + lines[j:end])
        guarded = any(marker in body for marker in GUARD_MARKERS)
        found.append((i + 1, ','.join(methods), path, guarded))
    return found


class WriteEndpointGuardTest(unittest.TestCase):

    def test_書き込み系APIに判定の無いものが無い(self):
        routes = write_routes()
        self.assertGreater(len(routes), 50, 'ルートを拾えていない（正規表現が壊れた？）')

        open_routes = [(ln, me, p) for ln, me, p, guarded in routes
                       if not guarded and p not in INTENTIONALLY_OPEN]

        detail = '\n'.join('  app.py:%d %s %s' % r for r in open_routes)
        self.assertEqual(
            open_routes, [],
            '入口の判定が見当たらない書き込みAPI:\n' + detail
            + '\n\n運用系（全銘柄の再取得・スクレイピング・再計算）と共有データは'
              ' @admin_required_api、外部を叩くものは @member_required_api、'
              '利用者自身のデータは @login_required_api を付けること。')

    def test_本文は次の関数の手前で打ち切る(self):
        """⚠️ 固定行数で本文を読むと、短い関数のとき窓が次の関数まで食い込み、
        **隣が守られているだけで「守られている」と誤判定する。**

        2026-08-28、これで3本を見逃していた:
            /api/stock/batch                    最大200銘柄を外部APIで分析
            /api/dc-stocks/scrape               kabutanを叩いてDBに保存
            /api/technical-stocks/analyze/stop  走っている分析を止める
        いずれも未ログインで200が返り、DC取得は実際に52件返した。

        テストが「OK」と言っていたのに開いていた＝**テスト自体が
        フェイルオープンしていた**。範囲の打ち切りを固定する。
        """
        src = read_source()
        block = src.split('def write_routes(', 1)[1].split(chr(10) + 'class ', 1)[0]
        self.assertIn("lines[end].startswith('@app.')", block)
        self.assertIn("lines[end].startswith('def ')", block)
        self.assertIn('lines[j:end]', block)
        self.assertNotIn('lines[j:j + BODY_LINES]', block)

    def test_停止と開始の権限を揃える(self):
        """開始が管理者限定なのに停止が誰でも押せると、走っている処理を
        外から止められる。対で見る。"""
        routes = {p: guarded for _ln, _m, p, guarded in write_routes()}
        pairs = [('/api/technical-stocks/analyze', '/api/technical-stocks/analyze/stop'),
                 ('/api/watchlist/analyze', '/api/watchlist/analyze/stop'),
                 ('/api/gc-stocks/analyze', '/api/gc-stocks/analyze/stop')]
        for start, stop in pairs:
            if start in routes and stop in routes:
                self.assertEqual(routes[start], routes[stop],
                                 '%s と %s で判定が違う' % (start, stop))

    def test_運用系バッチは管理者のみ(self):
        """会員に開けると、会員1人でも外部への叩き方を握れてしまう。"""
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as f:
            source = f.read()

        for path in ('/api/scheduler/trigger',
                     '/api/price-history/update',
                     '/api/ma-crosses/recalculate',
                     '/api/earnings/update',
                     '/api/gc-stocks/scrape',
                     '/api/watchlist/analyze',
                     '/api/watchlist/add',
                     '/api/dividend-stocks/add'):
            marker = "@app.route('%s'" % path
            self.assertIn(marker, source, path)
            after = source[source.index(marker):source.index(marker) + 200]
            self.assertIn('@admin_required_api', after, path)

    def test_管理者判定はメール基準も拾う(self):
        """role_required('admin') は app_users.role しか見ない。メール基準
        （GIA_ADMIN_EMAILS）の管理者だと「画面は開くのにボタンだけ403」になる。"""
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as f:
            source = f.read()
        block = source.split('def admin_required_api', 1)[1].split('\ndef ', 1)[0]
        self.assertIn("session.get('user_role')", block)


class PublicPageStillWorksTest(unittest.TestCase):
    """公開ページ（/stock/<code>）が非会員に壊れないこと。"""

    def test_株主役員は非会員にも保存済みを返す(self):
        """自動で叩かれるАPIなので会員限定にすると公開ページが壊れる。
        外部へ取りに行くのだけを止める。"""
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as f:
            source = f.read()
        marker = "@app.route('/api/stock/holders-officers/<company_code>'"
        block = source[source.index(marker):]
        block = block.split('@app.route', 2)[1]

        self.assertNotIn('@member_required_api', block.split('def ')[0])
        self.assertIn('if not is_member_session():', block)
        self.assertIn('fetch_and_store_holders_officers', block)
        # 保存済みを返す分岐が、取得の呼び出しより前にあること
        self.assertLess(block.index('if not is_member_session():'),
                        block.index('payload, status = fetch_and_store'))


if __name__ == '__main__':
    unittest.main()
