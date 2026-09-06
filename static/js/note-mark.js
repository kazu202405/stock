/*
 * 運営がメモを書いた銘柄の印。**ここが唯一の正。**
 *
 * なぜ要るか（2026-09-06）:
 *   12項目の適合度で緑の100点を取れるのは185社あり、一覧では全部同じ顔をして
 *   いる。しかも1000億円超の会社に100点は1社も無く、点数は身軽な会社に寄る。
 *   点数は機械の答えで、「見る価値があるか」の答えではない。
 *   人が「この会社は勉強になる」と選んだ印を、別の記号で並べる。
 *
 * ⚠️ **点数の色に混ぜない。** 緑＝点数、緑/赤の薄い行＝GC/DC。3つ目の意味を
 *    同じ色に載せると、適合度の色で一度やった読み違いが再発する。
 *
 * ⚠️ 「あるか」はサーバーが行に付けて返す（app._attach_notes）。画面から
 *    あとで取りに行くと、描画に間に合わず印が付かない行が残る。
 */
(function (global) {
  'use strict';

  function has(row) {
    return !!(row && row.has_note);
  }

  /** 一覧に差し込む <span>。メモが無ければ空文字。 */
  function chip(row) {
    if (!has(row)) return '';
    return '<span class="note-mark" title="運営のメモがあります">'
         + '<i class="fas fa-pen-nib"></i>メモ</span>';
  }

  global.NoteMark = { has: has, chip: chip };
})(window);
