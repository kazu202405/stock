/*
 * 会社の規模（時価総額）の言い方。**ここが唯一の正。**
 *
 * なぜ要るか（2026-09-06）:
 *   12項目の適合度で「緑の100点」を取れるのは 3,886社中185社だが、その内訳が
 *   偏っている。実測すると
 *       1000億超        0社
 *       300〜1000億    64社
 *       100〜300億     71社
 *       100億未満      50社
 *   で、**1000億を超える会社は1社も100点を取っていない**。TOPIXの規模区分でも
 *   Mid400以上は0社だった。ROE・営業利益率・自己資本比率・成長率で組んだ
 *   点数は、身軽な会社ほど揃いやすいという構造がある。
 *
 *   ∴ 一覧で「100」が並んでいても、それは同じ土俵の100ではない。
 *     点数の隣に規模を出して、読む人が自分で割り引けるようにする。
 *
 * ⚠️ **点数の定義には手を入れない。** 時価総額を12項目に足すと、点数が
 *    「数字の読み」から「投資して良い度」に変わる。このアプリが避けてきたもの。
 *    規模は別の軸なので、別の記号で並べる。
 *
 * ⚠️ **色を付けない。** 緑と赤は既にスコアとGC/DCで使っている。3つ目の意味を
 *    同じ色に載せると、以前の「1つの色に2つの意味」と同じ読み違いが起きる。
 */
(function (global) {
  'use strict';

  // 単位は億円（DBの market_cap と同じ）。
  // 100億 は既存の toneMarketCap（プライムの注意線）と同じ値にそろえてある。
  var THRESHOLDS = [
    { limit: 100,    label: '超小型' },
    { limit: 1000,   label: '小型'   },
    { limit: 10000,  label: '中型'   },
    { limit: Infinity, label: '大型' }
  ];

  /** 時価総額（億円）→ '超小型' | '小型' | '中型' | '大型' | null */
  function label(marketCapOku) {
    if (marketCapOku === null || marketCapOku === undefined) return null;
    var v = Number(marketCapOku);
    if (!isFinite(v) || v <= 0) return null;
    for (var i = 0; i < THRESHOLDS.length; i++) {
      if (v < THRESHOLDS[i].limit) return THRESHOLDS[i].label;
    }
    return null;
  }

  /** 行オブジェクトからそのまま引く用。 */
  function of(row) {
    return row ? label(row.market_cap) : null;
  }

  /** 一覧に差し込む <span>。規模が分からなければ空文字（欄を作らない）。 */
  function chip(row) {
    var text = of(row);
    if (!text) return '';
    return '<span class="size-chip" title="時価総額から見た規模">' + text + '</span>';
  }

  global.SizeChip = { label: label, of: of, chip: chip, THRESHOLDS: THRESHOLDS };
})(window);
