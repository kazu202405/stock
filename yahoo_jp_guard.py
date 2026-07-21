"""
Yahoo!ファイナンス日本版へのアクセスを制御するサーキットブレーカー。

背景:
  全銘柄バックフィル中、finance.yahoo.co.jp が全リクエストで500を返す状態に
  なった（一時ブロックまたは先方障害）。従来は失敗しても毎銘柄リクエストを
  投げ続けていたため、数千回の無駄打ちでブロックを長引かせる構造だった。

方針:
  - 連続で規定回数失敗したら、以降そのプロセスでは一切リクエストしない
  - 環境変数 SKIP_YAHOO_JP=true で最初から無効化できる
    （ブロック中の大量バッチではこれを立てて回す）
"""

import os
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

_state = {
    'consecutive_failures': 0,
    'tripped': False,
    'notified': False,
}


def _force_disabled():
    return os.getenv('SKIP_YAHOO_JP', '').lower() in ('true', '1', 'yes')


def is_available():
    """Yahoo!JPにリクエストしてよいか"""
    if _force_disabled():
        return False
    return not _state['tripped']


def reset():
    _state['consecutive_failures'] = 0
    _state['tripped'] = False
    _state['notified'] = False


def record_success():
    _state['consecutive_failures'] = 0


def record_failure():
    _state['consecutive_failures'] += 1
    if _state['consecutive_failures'] >= FAILURE_THRESHOLD and not _state['tripped']:
        _state['tripped'] = True
        print(f"[YahooJP] {FAILURE_THRESHOLD}回連続で失敗したため、"
              f"以降このプロセスではYahoo!ファイナンス日本版へのアクセスを停止します。"
              f"（ブロックを長引かせないため）")


def fetch(url, timeout=15):
    """Yahoo!JPからHTMLを取得する。
    ブレーカーが落ちている場合はリクエストせず None を返す。
    取得できなかった場合も None を返す（呼び出し側は None を許容すること）。
    """
    if not is_available():
        if not _state['notified']:
            if _force_disabled():
                print('[YahooJP] SKIP_YAHOO_JP=true のためスキップします')
            _state['notified'] = True
        return None

    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        if response.status_code != 200:
            print(f'[YahooJP] HTTP {response.status_code}: {url}')
            record_failure()
            return None
        response.encoding = 'utf-8'
        record_success()
        return response.text
    except Exception as e:
        print(f'[YahooJP] 取得エラー {url}: {e}')
        record_failure()
        return None
