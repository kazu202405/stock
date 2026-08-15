/* =============================================================
   横に広い表を「表 / カード」で切り替える共通部品
   =============================================================
   2026-08-15。銘柄一覧・管理画面など、列の多い表が複数の画面にある。
   同じ対応を画面ごとに書くと、直すときも画面の数だけ直すことになる
   （スクリーナーで横スクロールが効かなくなった不具合は、まさに
   1画面だけに書いた指定の順序ミスが原因だった）。ここに集約する。

   前提: static/css/table-view.css を読み込んでいること。
   Alpine でも素のJSでも使えるよう、フレームワークには依存させない。

   使い方:

       // 1. ツールバーに切り替えボタンを置く
       TableView.mountToggle({
           key: 'dashboard-favorite',        // 記憶に使う名前
           mount: '#favToolbar',             // ボタンを差し込む要素
           onChange: () => renderFavoriteStocks(list),  // 再描画
       });

       // 2. 描画時にモードで分ける
       if (TableView.isCard('dashboard-favorite')) {
           content.innerHTML = TableView.cards(list, {
               title: (r) => r.company_name,
               href:  (r) => '/stock/' + r.company_code,
               sub:   (r) => r.company_code + ' / ' + (r.sector || ''),
               badge: (r) => r.match_rate,
               metrics: [
                   { label: '時価総額', value: (r) => fmtOku(r.market_cap) },
                   ...
               ],
               sortKey: divSort.key,   // 並べ替え中の項目を先頭に寄せる
           });
       } else {
           content.innerHTML = '<div class="tv-scroll">' + tableHtml + '</div>';
       }
   ============================================================= */

window.TableView = (function () {
    'use strict';

    var STORAGE_PREFIX = 'tableView:';

    function storageKey(key) {
        return STORAGE_PREFIX + key;
    }

    /** いまカード表示か。既定は表。 */
    function isCard(key) {
        try {
            return localStorage.getItem(storageKey(key)) === 'card';
        } catch (e) {
            // プライベートモード等で localStorage が使えないことがある。
            // 表示が切り替わらないだけなので、既定の表で続ける。
            return false;
        }
    }

    function setView(key, view) {
        try {
            localStorage.setItem(storageKey(key), view);
        } catch (e) { /* 保存できなくても動く */ }
    }

    function escapeHtml(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    /**
     * 切り替えボタンを差し込む。
     * onChange は「表示が変わったので描き直してほしい」という合図。
     */
    function mountToggle(options) {
        var mount = typeof options.mount === 'string'
            ? document.querySelector(options.mount) : options.mount;
        if (!mount) return;

        var key = options.key;
        var wrap = document.createElement('div');
        wrap.className = 'tv-toggle';

        function paint() {
            var card = isCard(key);
            wrap.innerHTML =
                '<button type="button" class="tv-toggle-btn' + (card ? '' : ' is-on') + '" data-view="table">' +
                '<i class="fas fa-table-list"></i> 表</button>' +
                '<button type="button" class="tv-toggle-btn' + (card ? ' is-on' : '') + '" data-view="card">' +
                '<i class="fas fa-grip"></i> カード</button>';
        }

        wrap.addEventListener('click', function (e) {
            var btn = e.target.closest('.tv-toggle-btn');
            if (!btn) return;
            setView(key, btn.dataset.view);
            paint();
            if (typeof options.onChange === 'function') options.onChange();
        });

        paint();
        mount.appendChild(wrap);
    }

    /**
     * カード列のHTMLを組み立てる。
     *
     * metrics は最大6項目まで出す。並べ替えに使っている項目
     * （config.sortKey）は先頭に寄せて色を変える。カードは横に
     * 2〜3項目しか置けないので、いま何で比べているのかが見えないと
     * 並べ替えの意味が無くなるため。
     */
    function cards(items, config) {
        if (!items || !items.length) return '';
        var metrics = (config.metrics || []).slice();

        if (config.sortKey) {
            var i = metrics.findIndex(function (m) { return m.key === config.sortKey; });
            if (i > 0) metrics.unshift(metrics.splice(i, 1)[0]);
        }
        metrics = metrics.slice(0, 6);

        var html = '<div class="tv-cards">';
        items.forEach(function (row) {
            var title = config.title ? config.title(row) : '';
            var href = config.href ? config.href(row) : null;
            var sub = config.sub ? config.sub(row) : '';
            var badge = config.badge ? config.badge(row) : null;
            var note = config.note ? config.note(row) : '';
            var actions = config.actions ? config.actions(row) : '';

            html += '<div class="tv-card">';
            html += '<div class="tv-card-head"><div style="min-width:0;">';
            html += href
                ? '<a class="tv-card-title" href="' + escapeHtml(href) + '">' + escapeHtml(title) + '</a>'
                : '<div class="tv-card-title">' + escapeHtml(title) + '</div>';
            if (sub) html += '<div class="tv-card-sub">' + escapeHtml(sub) + '</div>';
            html += '</div>';
            // バッジは呼び出し側がHTMLを作る（スコアの色分け等があるため）
            if (badge) html += badge;
            html += '</div>';

            html += '<div class="tv-card-grid">';
            metrics.forEach(function (m) {
                var on = config.sortKey && m.key === config.sortKey;
                html += '<div class="tv-card-cell' + (on ? ' is-sorted' : '') + '">' +
                    '<div class="tv-card-label">' + escapeHtml(m.label) + '</div>' +
                    '<div class="tv-card-value">' + (m.value(row) === null || m.value(row) === undefined
                        ? '---' : m.value(row)) + '</div></div>';
            });
            html += '</div>';

            if (note) html += '<div class="tv-card-note">' + escapeHtml(note) + '</div>';
            if (actions) html += '<div class="tv-card-actions">' + actions + '</div>';
            html += '</div>';
        });
        html += '</div>';
        return html;
    }

    return {
        isCard: isCard,
        setView: setView,
        mountToggle: mountToggle,
        cards: cards,
    };
})();
