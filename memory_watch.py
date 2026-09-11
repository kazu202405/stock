"""定期ジョブのメモリ使用量をログに残す（2026-09-11）。

背景:
  Render（512MB）が "Ran out of memory" で落ち続けた（9/10〜9/11 で5回）。
  どれも重いジョブが始まって3〜8分後だったが、**何がどれだけ使っているかの
  記録が無かった。** OOM はプロセスごと強制終了されるので「終わり」のログは出ない。
  ∴ 走っている間に一定間隔で書いておき、**落ちる直前の1行**から原因を読む。

読み方（Render のログで `[mem]` を検索する）:
    [mem] price_update_morning 開始: 232MB
    [mem] 431MB 実行中: price_update_morning（スレッド14本）
    [mem] price_update_morning 終了: 開始 232MB → 最大 431MB → 終了 260MB（3分35秒）
  落ちた回は「終了」が出ない。最後の `[mem]` 行が落ちる直前の状態。
  「最大」は15秒おきに測った中での最大。瞬間的な山は取りこぼしうる。
  同じジョブが2本同時に走ると「×2」が付く（9/9 から続く二重実行の手がかり）。

⚠️ print は flush=True にすること。stdout はブロック単位でまとめて書き出されるので、
   落ちるとまだ書き出していない行ごと消える。いちばん欲しい直前の行が残らない。
⚠️ DB には書かない。落ちかけている最中に通信とメモリを使わせないため。
   job_runs に行を足すと、鮮度パネルと監視の読み手も全部見直すことになる。
⚠️ Linux の /proc/self/status だけを読む。手元の Windows では何も出さない。
"""

import threading
import time

# ジョブが走っている間の間隔。OOM は開始から3〜8分で起きていたので、
# 1分間隔だと直前の様子がほとんど残らない。
SAMPLE_SECONDS_BUSY = 15
# 何も走っていない間の間隔。ふだんの使用量（土台）を見るため。
SAMPLE_SECONDS_IDLE = 300

_lock = threading.Lock()
_running = {}        # job_id -> {'n', 'started', 'start_mb', 'peak_mb'}
_wake = threading.Event()
_sampler = {'thread': None}


def rss_mb(path='/proc/self/status'):
    """このプロセスがいま使っている物理メモリ（MB）。取れなければ None。"""
    try:
        with open(path) as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) / 1024.0      # 単位は kB
    except (OSError, ValueError, IndexError):
        return None
    return None


def _fmt(mb):
    return '不明' if mb is None else '%dMB' % round(mb)


def _log(text):
    print('[mem] ' + text, flush=True)


def _bump_peak(entry, mb):
    if mb is not None and (entry['peak_mb'] is None or mb > entry['peak_mb']):
        entry['peak_mb'] = mb


def job_started(job_id, now=None):
    """ジョブの開始を記録する。同じジョブが重なったら本数を数える。"""
    mb = rss_mb()
    now = now if now is not None else time.time()
    with _lock:
        entry = _running.get(job_id)
        if entry:
            entry['n'] += 1
            _bump_peak(entry, mb)
        else:
            entry = _running[job_id] = {'n': 1, 'started': now,
                                        'start_mb': mb, 'peak_mb': mb}
        n = entry['n']
    extra = '（同じジョブが%d本同時）' % n if n > 1 else ''
    _log('%s 開始: %s%s' % (job_id, _fmt(mb), extra))
    _wake.set()          # 間隔を短い方へ切り替える


def job_finished(job_id, now=None):
    """ジョブの終了を記録する。最後の1本が終わったときだけ要約を出す。"""
    mb = rss_mb()
    now = now if now is not None else time.time()
    with _lock:
        entry = _running.get(job_id)
        if not entry:
            return None
        entry['n'] -= 1
        _bump_peak(entry, mb)
        if entry['n'] > 0:
            return None
        del _running[job_id]
    secs = int(now - entry['started'])
    line = '%s 終了: 開始 %s → 最大 %s → 終了 %s（%d分%02d秒）' % (
        job_id, _fmt(entry['start_mb']), _fmt(entry['peak_mb']), _fmt(mb),
        secs // 60, secs % 60)
    _log(line)
    return line


def sample():
    """いまの使用量と、走っているジョブを1行に書く。"""
    mb = rss_mb()
    if mb is None:
        return None
    with _lock:
        for entry in _running.values():
            _bump_peak(entry, mb)
        names = ['%s×%d' % (k, v['n']) if v['n'] > 1 else k
                 for k, v in sorted(_running.items())]
    _log('%s 実行中: %s（スレッド%d本）' % (
        _fmt(mb), ', '.join(names) or 'なし', threading.active_count()))
    return mb


def _sample_loop():
    while True:
        with _lock:
            busy = bool(_running)
        _wake.wait(SAMPLE_SECONDS_BUSY if busy else SAMPLE_SECONDS_IDLE)
        _wake.clear()
        try:
            sample()
        except Exception as e:
            # 見張りが本体を止めない。1回書けなくても次で書く。
            print(f'[mem] 記録に失敗: {str(e)[:120]}', flush=True)


def ensure_sampler():
    """記録用のスレッドを1本だけ立てる。測れない環境では立てない。"""
    if rss_mb() is None:
        return False
    with _lock:
        thread = _sampler['thread']
        if thread is not None and thread.is_alive():
            return True
        thread = threading.Thread(target=_sample_loop, name='memory-watch',
                                  daemon=True)
        _sampler['thread'] = thread
    thread.start()
    return True


def attach(scheduler):
    """スケジューラにメモリの記録を付ける。

    ⚠️ スケジューラを作る場所（build_scheduler）で呼ぶこと。起動時の1か所だけに
       書くと、止まったスケジューラを入れ替えたあとの個体に付かない。
    """
    from apscheduler.events import (EVENT_JOB_ERROR, EVENT_JOB_EXECUTED,
                                    EVENT_JOB_SUBMITTED)

    def listener(event):
        if event.code == EVENT_JOB_SUBMITTED:
            job_started(event.job_id)
        else:
            job_finished(event.job_id)

    scheduler.add_listener(
        listener, EVENT_JOB_SUBMITTED | EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    ensure_sampler()
    return listener
