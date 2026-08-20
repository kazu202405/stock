/*
 * スコアの色分け。**ここが唯一の正。**
 *
 * 以前は3画面がそれぞれ別に色を決めていて、意味もしきい値もバラバラだった:
 *   screener.html      70/40  充足度<100% は専用色（オレンジ）
 *   stock.html         58/33  充足度を見ていない
 *   stock_detail.html  58/33  充足度を見ていない
 * 65点の銘柄がスクリーナーでは黄、銘柄ページでは緑、という状態だった。
 *
 * 決めたこと（2026-08-20）:
 *   色は「総合評価」を表す。緑＝**データが全部揃っていて、かつ点数が高い**。
 *   点数がいくら高くても、判定できていない項目があれば緑にはしない。
 *   「どの項目が埋まっていないか」は色ではなく、銘柄ページの12マスメーターで見る。
 */
(function (global) {
  'use strict';

  // しきい値。スクリーナーの値に揃えた（絞り込みの基準になっている画面だから）
  var HIGH = 70;
  var MID = 40;

  /**
   * 'high' | 'medium' | 'low' | 'provisional' | 'none' を返す。
   * row は行オブジェクトでも、スコアの数値そのものでもよい
   * （充足度を持たないAPIから来る一覧があるため）。
   */
  function tone(row) {
    if (row === null || row === undefined) return 'none';

    var isNum = (typeof row === 'number');
    var value = isNum ? row : row.match_rate;
    if (value === null || value === undefined) return 'none';

    if (!isNum) {
      var coverage = row.score_coverage;
      // 充足度が分かっていて100%未満なら、点数に関係なく「暫定」。
      // coverage が undefined の一覧（充足度を返していないAPI）では
      // 点数だけで色を付ける。緑が出てしまう場合があるので、
      // 該当APIには attach_score_quality() を足すこと。
      if (row.score_status === 'provisional' ||
          (coverage !== null && coverage !== undefined && coverage < 100)) {
        return 'provisional';
      }
    }

    if (value >= HIGH) return 'high';
    if (value >= MID) return 'medium';
    return 'low';
  }

  function toneClass(row) {
    return 'score-tone-' + tone(row);
  }

  /** ツールチップ。色だけでは何項目埋まっているか分からないので必ず添える。 */
  function title(row) {
    if (row === null || row === undefined) return '';
    if (typeof row === 'number') return '';
    if (row.match_rate === null || row.match_rate === undefined) {
      return '判定データが不足しています';
    }
    var total = row.score_total;
    if (row.score_coverage === null || row.score_coverage === undefined) return '';
    if (row.score_coverage < 100) {
      return '暫定評価：データ充足度 ' + row.score_coverage + '%' +
             '（' + row.score_judged + '/' + total + '項目）';
    }
    return 'データ充足度 100%（' + total + '/' + total + '項目）';
  }

  global.ScoreColor = {
    HIGH: HIGH,
    MID: MID,
    tone: tone,
    toneClass: toneClass,
    title: title,
  };
})(window);
