"""
Yahoo!ファイナンス日本版へのアクセスを制御するサーキットブレーカー。

背景:
  全銘柄バックフィル中、finance.yahoo.co.jp が全リクエストで500を返す状態に
  なった（一時ブロックまたは先方障害）。従来は失敗しても毎銘柄リクエストを
  投げ続けていたため、数千回の無駄打ちでブロックを長引かせる構造だった。

方針:
  - 連続で規定回数失敗したらブレーカーを開き、しばらくリクエストしない
  - **冷却が明けたら1本だけ試して、通れば閉じる（半開放）**
  - 環境変数 SKIP_YAHOO_JP=true で最初から無効化できる
    （ブロック中の大量バッチではこれを立てて回す）

2026-08-19の修正:
  以前は一度開くと `reset()` を手で呼ぶまで戻らなかった。プロセスが生きている限り
  その worker はYahoo!JPを一切見ないため、**今期予想が全銘柄で取れなくなっていた**
  （3,879件中348件＝9.0%しか入っていなかった真因）。しかも skip は
  `status: circuit_open` として正常系のように記録されるので、バックフィルは
  「全件成功」に見えて中身だけが空になる。時間で戻る道を用意する。
"""

import os
import threading
import time
import requests

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}

# 連続でこの回数失敗したらブレーカーを落とす
FAILURE_THRESHOLD = 5

# 開いてから最初に1本試すまでの待ち時間。開き直すたびに倍にして上限で頭打ちにする。
# 相手が本当に止まっているときに10分おきに叩き続けないため。
COOLDOWN_SECONDS = 600
MAX_COOLDOWN_SECONDS = 3600

_state = {
    'consecutive_failures': 0,
    'tripped': False,
    'notified': False,
    'tripped_at': 0.0,
    'trip_count': 0,     # 何回開いたか。冷却時間の計算に使う
    'probing': False,    # 半開放の1本が飛んでいる最中か
}

# Flaskはスレッドで動くので、半開放の「1本だけ」を守るために要る
_lock = threading.Lock()


def _cooldown_seconds():
    n = max(0, _state['trip_count'] - 1)
    return min(COOLDOWN_SECONDS * (2 ** n), MAX_COOLDOWN_SECONDS)


def _try_begin_probe():
    """リクエストしてよければ True。開いていても冷却明けなら1本だけ通す。

    通した場合は probing を立てるので、呼び出し側は必ず _end_probe() すること。
    """
    with _lock:
        if not _state['tripped']:
            return True
        if _state['probing']:
            return False
        waited = time.time() - _state['tripped_at']
        if waited < _cooldown_seconds():
            return False
        _state['probing'] = True
        print(f'[YahooJP] 冷却が明けたので1件だけ試します'
              f'（{_state["trip_count"]}回目の停止から{int(waited)}秒経過）')
        return True


def _end_probe():
    with _lock:
        _state['probing'] = False


def status_snapshot():
    """いまブレーカーがどうなっているか。バッチが始める前に見る用。"""
    with _lock:
        remain = 0
        if _state['tripped']:
            remain = max(0, int(_cooldown_seconds() - (time.time() - _state['tripped_at'])))
        return {
            'tripped': _state['tripped'],
            'trip_count': _state['trip_count'],
            'consecutive_failures': _state['consecutive_failures'],
            'retry_in_seconds': remain,
            'force_disabled': _force_disabled(),
        }


def _force_disabled():
    return os.getenv('SKIP_YAHOO_JP', '').lower() in ('true', '1', 'yes')


def is_available():
    """Yahoo!JPにリクエストしてよいか。

    冷却が明けていれば、開いていても True を返す（次の1本で試すため）。
    バッチの中断判定にも使われるので、ここで False を返し続けると
    「もう二度と回復しない」と同じ意味になる。
    """
    if _force_disabled():
        return False
    with _lock:
        if not _state['tripped']:
            return True
        return (time.time() - _state['tripped_at']) >= _cooldown_seconds()


def reset():
    with _lock:
        _state['consecutive_failures'] = 0
        _state['tripped'] = False
        _state['notified'] = False
        _state['tripped_at'] = 0.0
        _state['trip_count'] = 0
        _state['probing'] = False


def record_success():
    """1本通ったらブレーカーを閉じる。半開放の成功もここで拾う。"""
    with _lock:
        _state['consecutive_failures'] = 0
        if _state['tripped']:
            print('[YahooJP] 復帰しました。アクセスを再開します')
        _state['tripped'] = False
        _state['trip_count'] = 0
        _state['notified'] = False


def record_failure():
    with _lock:
        _state['consecutive_failures'] += 1
        # 開いている最中の失敗＝半開放の1本が落ちた。閾値を待たずに開き直して
        # 冷却を延ばす（相手がまだ回復していないため）
        if _state['tripped'] or _state['consecutive_failures'] >= FAILURE_THRESHOLD:
            _state['tripped'] = True
            _state['tripped_at'] = time.time()
            _state['trip_count'] += 1
            _state['notified'] = False
            wait = _cooldown_seconds()
            print(f'[YahooJP] アクセスを停止します。{int(wait)}秒後に1件だけ試します'
                  f'（連続失敗{_state["consecutive_failures"]}回／{_state["trip_count"]}回目の停止）')


def fetch(url, timeout=15):
    """Yahoo!JPからHTMLを取得する。
    ブレーカーが落ちている場合はリクエストせず None を返す。
    取得できなかった場合も None を返す（呼び出し側は None を許容すること）。
    """
    return fetch_result(url, timeout=timeout).get('html')


def fetch_result(url, timeout=15):
    """取得結果と失敗理由を返す。

    status は success / no_data / rate_limited / source_error / timeout /
    network_error / disabled / circuit_open のいずれか。従来の fetch() は互換性の
    ためHTMLだけを返す。
    """
    if _force_disabled():
        if not _state['notified']:
            print('[YahooJP] SKIP_YAHOO_JP=true のためスキップします')
            _state['notified'] = True
        return {'html': None, 'status': 'disabled', 'http_status': None,
                'error': None, 'url': url}

    if not _try_begin_probe():
        return {'html': None, 'status': 'circuit_open', 'http_status': None,
                'error': None, 'url': url}

    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        status = response.status_code

        if status != 200:
            print(f'[YahooJP] HTTP {status}: {url}')
            # 404 は「その銘柄にそのページが無い」だけで、遮断されたわけではない。
            # ETF・新規上場銘柄などで普通に起きるため、ブレーカーの判定には含めない。
            if status in (403, 429) or status >= 500:
                record_failure()
            if status == 404:
                reason = 'no_data'
            elif status in (403, 429):
                reason = 'rate_limited'
            else:
                reason = 'source_error'
            return {'html': None, 'status': reason, 'http_status': status,
                    'error': None, 'url': url}

        response.encoding = 'utf-8'
        record_success()
        return {'html': response.text, 'status': 'success', 'http_status': 200,
                'error': None, 'url': url}
    except requests.exceptions.Timeout as e:
        print(f'[YahooJP] タイムアウト {url}: {e}')
        record_failure()
        return {'html': None, 'status': 'timeout', 'http_status': None,
                'error': str(e), 'url': url}
    except Exception as e:
        print(f'[YahooJP] 取得エラー {url}: {e}')
        record_failure()
        return {'html': None, 'status': 'network_error', 'http_status': None,
                'error': str(e), 'url': url}
    finally:
        _end_probe()
