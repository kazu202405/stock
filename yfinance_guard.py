"""yfinance（Yahooグローバル）のレート制限に当たったときの待機と再試行。

背景:
  yfinance には2種類のAPIがあり、コストが桁違いに違う。

    バッチ系 `yf.download`  … 200銘柄まとめて1リクエスト。ほぼ制限に当たらない
    個別系 `ticker.info` / `.financials` / `.balance_sheet`
                            … 銘柄ごとに1リクエスト。制限に当たるのはこちら

  全銘柄バックフィルは個別系を数千回叩くため、途中で 429 / YFRateLimitError に
  当たる。従来は「連続N回失敗したら中断」しかなく、当たった時点でその日の作業が
  終わってしまっていた。

方針:
  - 制限を検知したら指数的に待って、同じ銘柄から再開する（取りこぼさない）
  - 制限が続くようなら1銘柄あたりの基本間隔も自動で広げる（当たりに行かない）
  - 成功したら待ち時間を戻す
  - それでも回復しないときだけ諦める

  yahoo_jp_guard がYahoo日本版HTMLのサーキットブレーカーであるのに対し、
  こちらはYahooグローバル側の「待って続ける」ための仕組み。役割が違う。
"""

import random
import time

# 制限検知後の待機。当たるたびに倍にしていく。
INITIAL_BACKOFF_SECONDS = 60
MAX_BACKOFF_SECONDS = 900          # 15分頭打ち
MAX_RETRIES_PER_ITEM = 5           # 1銘柄あたりの再試行回数

# 制限に当たった後は1銘柄あたりの間隔自体も広げる
SLEEP_MULTIPLIER_ON_LIMIT = 1.5
MAX_SLEEP_SECONDS = 8.0

_RATE_LIMIT_MARKERS = (
    'yfratelimiterror',
    'too many requests',
    'rate limit',
    'rate-limit',
    '429',
)


def is_rate_limit_error(error) -> bool:
    """レート制限に当たった例外か。yfinanceは専用例外を出さない経路もある。"""
    if error is None:
        return False
    name = type(error).__name__.lower()
    if 'ratelimit' in name:
        return True
    text = str(error).lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


class RateLimitGuard:
    """1銘柄ずつ処理するループで、制限に当たったら待って続けるための状態。

    使い方:

        guard = RateLimitGuard(base_sleep=0.6)
        for code in codes:
            try:
                result = guard.run(lambda: fetch_one(code))
            except RateLimitExhausted:
                break          # 待っても回復しない
            guard.pause()      # 次の銘柄までの間隔
    """

    def __init__(self, base_sleep=0.6, sleep_fn=time.sleep, on_wait=None):
        self.base_sleep = base_sleep
        self.current_sleep = base_sleep
        self._sleep = sleep_fn
        self._on_wait = on_wait or (lambda seconds, attempt: None)
        self.rate_limit_hits = 0
        self.total_waited = 0.0

    def run(self, call):
        """callを実行する。レート制限なら待って再試行する。

        制限以外の例外はそのまま呼び出し側へ投げる（握り潰さない）。
        """
        backoff = INITIAL_BACKOFF_SECONDS
        for attempt in range(1, MAX_RETRIES_PER_ITEM + 1):
            try:
                value = call()
            except Exception as e:
                if not is_rate_limit_error(e):
                    raise
                self.rate_limit_hits += 1
                if attempt == MAX_RETRIES_PER_ITEM:
                    raise RateLimitExhausted(
                        f'レート制限が{MAX_RETRIES_PER_ITEM}回続けて解けませんでした'
                    ) from e
                # 同時に再開して再び当たらないよう、待ち時間を少しばらす
                wait = min(backoff, MAX_BACKOFF_SECONDS) * (1 + random.random() * 0.2)
                self._on_wait(wait, attempt)
                self._sleep(wait)
                self.total_waited += wait
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                # 当たった以上、次からは間隔自体を広げる
                self.current_sleep = min(
                    self.current_sleep * SLEEP_MULTIPLIER_ON_LIMIT, MAX_SLEEP_SECONDS)
                continue
            else:
                # 一度も待たずに通ったなら、広げた間隔を少しずつ戻す
                if self.current_sleep > self.base_sleep:
                    self.current_sleep = max(
                        self.base_sleep, self.current_sleep * 0.9)
                return value

    def pause(self):
        """次の銘柄へ進む前の間隔"""
        if self.current_sleep > 0:
            self._sleep(self.current_sleep)

    def summary(self):
        return (f'レート制限に当たった回数 {self.rate_limit_hits} / '
                f'待機合計 {self.total_waited / 60:.1f}分 / '
                f'現在の間隔 {self.current_sleep:.2f}秒')


class RateLimitExhausted(Exception):
    """待っても制限が解けなかった"""
