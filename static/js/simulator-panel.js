/*
 * 過去シミュレーションのAlpineコンポーネント。
 * 単独ページ(/simulator)とマイノートのタブの両方から使うので、
 * テンプレートに直書きせずここに置く（片方だけ直してズレるのを防ぐ）。
 */
function simulator() {
  const today = new Date();
  const iso = d => d.toISOString().slice(0, 10);
  const yearsAgo = n => { const d = new Date(today); d.setFullYear(d.getFullYear() - n); return iso(d); };

  return {
    mode: 'lump',
    code: '', companyName: '',
    query: '', hits: [], cursor: -1,
    amount: 1000000,
    start: yearsAgo(5), end: iso(today),
    intervalMonths: 1, dayOfMonth: 1,
    loading: false, error: '', result: null,
    _companies: null,

    yen(v) {
      if (v === null || v === undefined) return '—';
      return '¥' + Math.round(v).toLocaleString();
    },

    async _load() {
      if (this._companies) return this._companies;
      try { this._companies = await (await fetch('/static/companies.json')).json(); }
      catch (e) { this._companies = []; }
      return this._companies;
    },

    // 全角の英数字を半角に寄せる。「７２０３」でも引けるように
    _norm(v) {
      return (v || '')
        .replace(/[Ａ-Ｚａ-ｚ０-９]/g, c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0))
        .replace(/[\s　]/g, '').toUpperCase();
    },

    // コードでも会社名でも探せるようにする。検索欄と同じで、
    // 前方一致を先に出す（「トヨタ」で「トヨタ自動車」が下に沈むと探した気がしない）
    async suggest() {
      const q = this._norm(this.query);
      this.cursor = -1;
      if (!q) { this.hits = []; this.companyName = ''; this.code = ''; return; }

      const list = await this._load();
      const starts = [], contains = [];
      for (const c of list) {
        const n = this._norm(c.n);
        if (c.c.toUpperCase().startsWith(q) || n.startsWith(q)) starts.push(c);
        else if (n.includes(q)) contains.push(c);
        if (starts.length >= 8) break;
      }
      this.hits = starts.concat(contains).slice(0, 8);

      // コードそのものを打ち切った場合は、選ばなくても確定させる
      const exact = list.find(c => c.c.toUpperCase() === q);
      if (exact) { this.code = exact.c; this.companyName = exact.n; }
      else { this.code = ''; this.companyName = ''; }
    },

    move(step) {
      if (!this.hits.length) return;
      this.cursor = (this.cursor + step + this.hits.length) % this.hits.length;
    },

    choose(h) {
      if (!h) return;
      this.code = h.c;
      this.companyName = h.n;
      this.query = h.n;
      this.hits = [];
      this.cursor = -1;
    },

    async run() {
      this.error = ''; this.result = null;
      if (!this.code.trim()) { this.error = '銘柄を選んでください（候補から選ぶか、4桁のコードを入れてください）'; return; }
      if (!this.amount || this.amount <= 0) { this.error = '金額を入れてください'; return; }
      if (this.start > this.end) { this.error = '開始日が終了日より後になっています'; return; }

      this.loading = true;
      try {
        const r = await fetch('/api/simulate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            company_code: this.code.trim(),
            mode: this.mode,
            start: this.start,
            end: this.end,
            amount: this.amount,
            interval_months: this.intervalMonths,
            day_of_month: this.dayOfMonth,
          }),
        });
        const j = await r.json();
        if (j.error) {
          this.error = j.error + (j.available_from ? `（この銘柄は ${j.available_from} 以降のデータがあります）` : '');
        } else {
          this.result = j;
        }
      } catch (e) {
        this.error = '計算できませんでした。時間をおいてお試しください。';
      } finally {
        this.loading = false;
      }
    },
  };
}
