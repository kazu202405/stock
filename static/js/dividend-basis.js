/*
 * 配当利回りを「予想」で出すか「実績」で出すか。**ここが唯一の正。**
 *
 * 2026-08-25 まで、画面ごとに基準が違っていた:
 *   ダッシュボード・スクリーナー  実績（直近12か月に支払われた額 ÷ 株価）
 *   銘柄ページ                    予想（年換算した1株配当 ÷ 株価）
 * 同じ 367A プレミアグループが一覧で 5.93%、銘柄ページで 4.08% と出る。
 * 実績は決算期をまたぐと期末配当と翌期の中間配当が同じ12か月の窓に入るため、
 * その年だけ跳ね上がる。日本で「配当利回り」として見慣れているのも、
 * 他社サイトの表示と一致するのも予想側なので、**予想を主にする**。
 *
 * 予想が無い銘柄だけ実績に落とす。2026-08-25 時点の実測で、
 * 予想あり3,181件 / 実績だけ38件（全体の1%）/ 予想だけ22件。
 * 「予想が無いと並べ替えから消える」という 2026-08-14 の保留理由は、
 * 予想のバックフィルが進んだことで実質なくなっている。
 *
 * ⚠️ どちらを出しているかは必ず画面に書くこと。基準の違う値を、
 *    基準を書かずに同じ列へ混ぜない。
 */
(function (global) {
    'use strict';

    function num(v) {
        if (v === null || v === undefined || v === '') return null;
        var n = Number(v);
        return isFinite(n) ? n : null;
    }

    /**
     * 行から「出すべき配当利回り」を決める。
     * 戻り値の basis は 'forward'（予想）/ 'trailing'（実績）/ null（出せない）。
     */
    function pick(row) {
        if (!row) return { value: null, basis: null, forward: null, trailing: null, dpsForecast: null };
        var forward = num(row.dividend_yield_forward);
        var trailing = num(row.dividend_yield);
        var dps = num(row.dps_forecast);
        if (forward !== null) {
            return { value: forward, basis: 'forward', forward: forward, trailing: trailing, dpsForecast: dps };
        }
        return { value: trailing, basis: trailing === null ? null : 'trailing',
                 forward: null, trailing: trailing, dpsForecast: dps };
    }

    function value(row) {
        return pick(row).value;
    }

    /** '予想' / '実績' / ''（表のヘッダーや注記に添える一文字ふたつ） */
    function basisLabel(row) {
        var b = pick(row).basis;
        return b === 'forward' ? '予想' : b === 'trailing' ? '実績' : '';
    }

    /** 表・カードのセル文字列。'4.08%' か '---'。 */
    function text(row, digits) {
        var v = value(row);
        return v === null ? '---' : v.toFixed(digits === undefined ? 2 : digits) + '%';
    }

    /**
     * セルに添える基準の印。予想のときは何も付けず、
     * **実績に落ちたときだけ** 印を出す（主役は予想なので、例外にだけ印を付ける）。
     */
    function marker(row) {
        return pick(row).basis === 'trailing'
            ? '<span class="div-basis-mark" title="今期予想が取得できないため、直近12か月の実績です">実</span>'
            : '';
    }

    /** セル文字列＋印。表とカードはこれを使う。 */
    function cell(row, digits) {
        var v = value(row);
        if (v === null) return '---';
        return text(row, digits) + marker(row);
    }

    /**
     * 一覧に並べ替え・絞り込み用の列を足す。**取得直後に必ず1回通すこと。**
     * 既存の並べ替えは行オブジェクトのキーを直接読むので、
     * 計算した値を列として持たせないと「表示は予想・並べ替えは実績」になる。
     */
    function normalize(list) {
        (list || []).forEach(function (row) {
            var r = pick(row);
            row.dividend_yield_display = r.value;
            row.dividend_yield_basis = r.basis;
        });
        return list;
    }

    global.DividendBasis = {
        pick: pick, value: value, basisLabel: basisLabel,
        text: text, marker: marker, cell: cell, normalize: normalize,
    };
})(window);
