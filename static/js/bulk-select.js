/*
 * 一覧から複数の銘柄を選んで、まとめてお気に入りに入れるための共通部品。
 *
 * なぜ要るか:
 *   好調企業・高配当企業・テクニカル分析の一覧で「これとこれ」と拾いたい。
 *   1件ずつ★を押すと、20件入れるのに20回叩くことになる。
 *
 * ⚠️ **表とカードの両方に入れること。** スマホはカード表示なので、表だけに
 *    付けると外では使えない。片方だけ直す事故が過去に起きている。
 *
 * ⚠️ **チェックは行のクリックを飲み込むこと。** 一覧の行は onclick で銘柄
 *    ページを開くようになっているので、止めないとチェックした瞬間に
 *    別タブが開く。
 *
 * ⚠️ **選択は描画のたびに作り直さない。** 並べ替え・絞り込みで表は何度も
 *    描き直されるが、選んだものは残す（Set をこちら側で持つ）。
 */
window.BulkSelect = (function () {
    'use strict';

    var sets = {};          // key -> Set(company_code)
    var opts = {};          // key -> mountBar のオプション
    var activeKey = null;

    function set(key) {
        if (!sets[key]) sets[key] = new Set();
        return sets[key];
    }

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function count(key) { return set(key).size; }
    function codes(key) { return Array.from(set(key)); }
    function has(key, code) { return set(key).has(code); }

    function clear(key) {
        set(key).clear();
        repaint(key);
    }

    /* チェックの上げ下げ。呼び出し側の再描画は要らない（DOMだけ直す）。 */
    function toggle(key, code, on, ev) {
        if (ev) ev.stopPropagation();
        if (on) set(key).add(code); else set(key).delete(code);
        syncBoxes(key);
        paintBar(key);
    }

    /* 見出しの「全選択」。いま画面に出ている銘柄だけを対象にする。
       絞り込みで隠れているものまで選ぶと、何を選んだのか分からなくなる。 */
    function toggleAll(key, visible, on, ev) {
        if (ev) ev.stopPropagation();
        (visible || []).forEach(function (c) {
            if (on) set(key).add(c); else set(key).delete(c);
        });
        syncBoxes(key);
        paintBar(key);
    }

    function syncBoxes(key) {
        document.querySelectorAll('[data-bulk-key="' + key + '"]').forEach(function (el) {
            var code = el.getAttribute('data-bulk-code');
            if (code) el.checked = set(key).has(code);
        });
        var head = document.querySelector('[data-bulk-head="' + key + '"]');
        if (head) {
            var boxes = document.querySelectorAll('[data-bulk-key="' + key + '"][data-bulk-code]');
            var on = boxes.length > 0;
            boxes.forEach(function (b) { if (!b.checked) on = false; });
            head.checked = on;
        }
    }

    /* 表の見出しセル（全選択） */
    function headCell(key, visible) {
        var json = esc(JSON.stringify(visible || []));
        return '<th class="bulk-cell"><input type="checkbox" data-bulk-head="' + esc(key) + '"'
            + ' title="表示中をすべて選ぶ"'
            + ' onclick="BulkSelect.toggleAll(\'' + esc(key) + '\', ' + json + ', this.checked, event)"></th>';
    }

    /* 表の行のセル */
    function cell(key, code) {
        return '<td class="bulk-cell" onclick="event.stopPropagation()">'
            + checkbox(key, code) + '</td>';
    }

    /* カード用（TableView の select フックに渡す） */
    function cardCheck(key, code) {
        return '<label class="bulk-card-check" onclick="event.stopPropagation()">'
            + checkbox(key, code) + '</label>';
    }

    function checkbox(key, code) {
        return '<input type="checkbox" data-bulk-key="' + esc(key) + '"'
            + ' data-bulk-code="' + esc(code) + '"'
            + (set(key).has(code) ? ' checked' : '')
            + ' onclick="BulkSelect.toggle(\'' + esc(key) + '\', \'' + esc(code) + '\', this.checked, event)">';
    }

    /*
     * 画面下の操作バー。選んだものが1件も無いときは出さない
     * （常に出ていると一覧の下端が隠れて邪魔になる）。
     */
    function mountBar(key, options) {
        opts[key] = options || {};
        activeKey = key;
        ensureBar();
        paintBar(key);
    }

    function ensureBar() {
        if (document.getElementById('bulkBar')) return;
        var el = document.createElement('div');
        el.id = 'bulkBar';
        el.style.display = 'none';
        document.body.appendChild(el);
    }

    function repaint(key) {
        syncBoxes(key);
        paintBar(key);
    }

    function paintBar(key) {
        if (key !== activeKey) return;
        var bar = document.getElementById('bulkBar');
        if (!bar) return;
        var n = count(key);
        if (!n) { bar.style.display = 'none'; return; }

        var o = opts[key] || {};
        var folders = o.folders || [];
        var picker = '';
        if (o.showFolder !== false) {
            picker = '<select id="bulkFolderPick"><option value="">未分類</option>'
                + folders.map(function (f) {
                    return '<option value="' + esc(f.id) + '">' + esc(f.name) + '</option>';
                }).join('') + '</select>';
        }
        bar.innerHTML =
            '<span class="bulk-count">' + n + '件 選択中</span>'
            + picker
            + '<button class="bulk-go" onclick="BulkSelect.run(\'' + esc(key) + '\')">'
            + esc(o.actionLabel || 'お気に入りに追加') + '</button>'
            + '<button class="bulk-clear" onclick="BulkSelect.clear(\'' + esc(key) + '\')">選択解除</button>';
        bar.style.display = 'flex';
    }

    function run(key) {
        var o = opts[key] || {};
        var pick = document.getElementById('bulkFolderPick');
        var folderId = pick ? (pick.value || null) : null;
        if (typeof o.onRun === 'function') o.onRun(codes(key), folderId);
    }

    return {
        headCell: headCell, cell: cell, cardCheck: cardCheck,
        toggle: toggle, toggleAll: toggleAll, clear: clear, run: run,
        count: count, codes: codes, has: has, mountBar: mountBar, repaint: repaint,
    };
})();
