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
      // 一覧が渡してくるものは2通りある。どちらでも暫定を判定できる:
      //   score_coverage / score_status … attach_score_quality() で内訳まで出す。
      //     件数の少ない一覧向け（ウォッチリスト・お気に入り・高配当）
      //   score_complete … screened_latest に保存済みの真偽値を1列取るだけ。
      //     件数の多い一覧向け（テクニカルは3,700件あり、内訳を出すと1.5MB/2.6秒になる）
      // どちらも無い場合は点数だけで色を付ける（緑が出てしまうので、
      // 一覧を追加するときはどちらかを必ず渡すこと）。
      if (row.score_status === 'provisional' ||
          row.score_complete === false ||
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
    if (row.score_coverage === null || row.score_coverage === undefined) {
      // 内訳を持たない一覧。真偽値だけは伝えられる
      if (row.score_complete === false) return '暫定評価：まだ判定できていない項目があります';
      if (row.score_complete === true) return 'データ充足度 100%';
      return '';
    }
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
