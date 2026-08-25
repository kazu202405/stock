/*
 * 会社名・銘柄コードから銘柄ページへ飛ぶ検索窓。**どのページからでも使える。**
 *
 * 2026-08-25 まで、検索は /search という専用ページにしか無かった。そのページは
 * 検索窓のほかに事業概要・財務5年・CF・財務健全性・株主役員を持っており、
 * その5つは /stock/<code> と同じものだった。「検索するためだけに、同じ内容の
 * ページをもう1つ開く」形になっていたので、窓のほうをヘッダーへ出す。
 *
 * 設計:
 *  - 企業リスト(3,906件)は**最初にフォーカスされたときだけ**読む。
 *    全ページのヘッダーに置くので、毎回読むと表示のたびに無駄が出る
 *  - 候補を選ばずEnterしても飛べる。コードか名前かの判定はサーバー側
 *    （models/root.py の /stock/<code>）が持っていて、解決できなければ
 *    候補付きの stock_not_found を返す。**ここで判定を書かない**
 *    （2か所に書くと、片方だけ直したときに食い違う）
 */
(function (global) {
    'use strict';

    var companies = null;
    var loading = null;

    function load() {
        if (companies) return Promise.resolve(companies);
        if (!loading) {
            loading = fetch('/static/companies.json')
                .then(function (r) { return r.json(); })
                .then(function (data) { companies = data || []; return companies; })
                .catch(function () { companies = []; return companies; });
        }
        return loading;
    }

    function match(query) {
        if (!companies || !query) return [];
        var lower = query.toLowerCase();
        return companies.filter(function (c) {
            return c.c.indexOf(query) === 0 || c.n.toLowerCase().indexOf(lower) >= 0;
        }).slice(0, 8);
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function (ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[ch];
        });
    }

    function go(value) {
        var v = String(value || '').trim();
        if (!v) return;
        global.location.href = '/stock/' + encodeURIComponent(v);
    }

    /**
     * 入力欄と候補リストを結びつける。
     * mount({ input, list, onNavigate }) で複数の場所に置ける
     * （ヘッダーとスマホの全画面検索で同じ動きにする）。
     */
    function mount(opts) {
        var input = opts.input;
        var list = opts.list;
        if (!input || !list) return;

        var active = -1;

        function hide() { list.style.display = 'none'; active = -1; }

        function render() {
            var q = input.value.trim();
            if (!q) { hide(); return; }
            var found = match(q);
            if (!found.length) { hide(); return; }
            list.innerHTML = found.map(function (m) {
                return '<button type="button" class="cs-item" data-code="' + escapeHtml(m.c) + '">'
                    + '<span class="cs-code">' + escapeHtml(m.c) + '</span>'
                    + '<span class="cs-name">' + escapeHtml(m.n) + '</span></button>';
            }).join('');
            list.style.display = 'block';
            active = -1;
        }

        function items() { return list.querySelectorAll('.cs-item'); }

        function highlight() {
            var els = items();
            for (var i = 0; i < els.length; i++) {
                els[i].classList.toggle('is-active', i === active);
            }
            if (active >= 0 && els[active]) els[active].scrollIntoView({ block: 'nearest' });
        }

        input.addEventListener('focus', function () { load().then(render); });
        input.addEventListener('input', function () {
            if (!companies) { load().then(render); return; }
            render();
        });

        input.addEventListener('keydown', function (e) {
            var els = items();
            var open = list.style.display === 'block' && els.length > 0;

            if (e.key === 'Enter') {
                e.preventDefault();
                // 候補を選んでいればそれ、選んでいなければ打った文字列のまま飛ばす。
                // サーバーが会社名でも解決する。
                go(open && active >= 0 ? els[active].dataset.code : input.value);
                return;
            }
            if (!open) return;
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                active = Math.min(active + 1, els.length - 1);
                highlight();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                active = Math.max(active - 1, -1);
                highlight();
            } else if (e.key === 'Escape') {
                hide();
                input.blur();
            }
        });

        list.addEventListener('click', function (e) {
            var item = e.target.closest('.cs-item');
            if (item) go(item.dataset.code);
        });

        document.addEventListener('click', function (e) {
            if (!list.contains(e.target) && e.target !== input) hide();
        });
    }

    global.CompanySearch = { mount: mount, load: load };
})(window);
