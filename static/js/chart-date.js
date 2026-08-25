/*
 * チャートの日付表示。**日本語の並びにする。**
 *
 * lightweight-charts の既定は英語圏の並び（Aug 25, '26 / 2026-08-25）で、
 * localization.locale に 'ja-JP' を渡しても日付の書式までは変わらない。
 * 十字カーソルのラベルと横軸の目盛りは、それぞれ別の指定が要る:
 *   localization.timeFormatter   … カーソルに出るラベル
 *   timeScale.tickMarkFormatter  … 横軸の目盛り
 *
 * 時刻の値は呼び出し側によって形が違う（BusinessDay オブジェクト /
 * 'YYYY-MM-DD' の文字列 / UNIX秒）ので、どれで来ても読めるようにしてある。
 */
(function (global) {
    'use strict';

    function parts(time) {
        if (time === null || time === undefined) return null;
        if (typeof time === 'object' && time.year) {
            return { y: time.year, m: time.month, d: time.day };
        }
        if (typeof time === 'string') {
            var p = time.split('-');
            if (p.length < 3) return null;
            return { y: Number(p[0]), m: Number(p[1]), d: Number(p[2]) };
        }
        if (typeof time === 'number') {
            // 取引所ローカルの日付に寄せる（UTCの0時だと前日になる）
            var dt = new Date((time + 12 * 3600) * 1000);
            return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
        }
        return null;
    }

    /** カーソルに出すラベル。「2026年8月25日」 */
    function full(time) {
        var p = parts(time);
        return p ? p.y + '年' + p.m + '月' + p.d + '日' : '';
    }

    /**
     * 横軸の目盛り。lightweight-charts が「年の変わり目」「月の変わり目」
     * 「日」を区別して渡してくるので、粒度に合わせて短くする。
     *   TickMarkType: Year=0 / Month=1 / DayOfMonth=2 / Time=3
     */
    function tick(time, tickMarkType) {
        var p = parts(time);
        if (!p) return '';
        if (tickMarkType === 0) return p.y + '年';
        if (tickMarkType === 1) return p.m + '月';
        return String(p.d);
    }

    global.ChartDate = { full: full, tick: tick };
})(window);
