/*
 * 運営の見どころメモを書き直す。**ここが唯一の正。**
 *
 * なぜ要るか（2026-09-11）:
 *   「取り上げた会社」でメモを直したいのに、銘柄ページへ入ってからしか
 *   書き直せず二度手間だった。書き直しの道を2つにするので、入力の出し方・
 *   保存・失敗の伝え方をここに1つだけ置き、両方の画面から呼ぶ。
 *
 * ⚠️ 画面ごとに書かない。注意書きや失敗の文言が画面によってずれる。
 * ⚠️ 失敗の種類を1つの文言に潰さない。ログインが切れているのか、権限が無いのか、
 *    DBが断ったのかで、次にやることが違う。
 * ⚠️ 書けるのは管理者だけ。画面でボタンを隠すだけにせず、API（PUT）でも見ている。
 */
(function (global) {
    'use strict';

    var NOTE = 'どこを見ると面白いかを数行で。銘柄ページは公開なので、'
             + '非公開の話は書かないでください。（Ctrl+Enterで保存）';

    /** 失敗を伝える。保存できなかった理由ごとに文言を分ける。 */
    function reportFailure(status, data) {
        if (status === 401) {
            showErrorModal('ログインの有効期限が切れています。入り直してからもう一度お試しください。');
        } else if (status === 403) {
            showErrorModal('メモを書けるのは管理者だけです。');
        } else {
            showErrorModal((data.error || 'メモを保存できませんでした。')
                           + (data.detail ? String.fromCharCode(10, 10) + data.detail : ''));
        }
    }

    /** メモを保存する。保存できたら本文、できなければ null。 */
    async function save(code, body) {
        try {
            var res = await fetch('/api/stock-notes/' + encodeURIComponent(code), {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ body: body }),
            });
            var data = {};
            try { data = await res.json(); } catch (_) { /* HTMLが返ることがある */ }
            if (!res.ok) {
                reportFailure(res.status, data);
                return null;
            }
            showToast('メモを保存しました');
            return body;
        } catch (e) {
            showErrorModal('メモを保存できませんでした。通信を確認してください。');
            return null;
        }
    }

    /**
     * 入力モーダルを開いて書き直し、保存まで行う。
     *   await StockNoteEditor.edit({ code, current, title })
     * 保存できたら本文、やめた・失敗したら null。
     */
    async function edit(opts) {
        opts = opts || {};
        var body = await showPromptModal({
            title: opts.title || 'この会社の見どころ',
            note: NOTE,
            value: opts.current || '',
            multiline: true,
            maxLength: 2000,
            okLabel: '保存',
        });
        if (body === null) return null;
        return save(opts.code, body);
    }

    global.StockNoteEditor = { edit: edit, save: save };
})(window);
