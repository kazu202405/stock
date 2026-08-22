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
    amount: 1000000,
    start: yearsAgo(5), end: iso(today),
    intervalMonths: 1, dayOfMonth: 1,
    loading: false, error: '', result: null,
    _companies: null,

    yen(v) {
      if (v === null || v === undefined) return '—';
      return '¥' + Math.round(v).toLocaleString();
    },

    // 銘柄コード → 企業名。ノート作成と同じく companies.json を使う
    async lookup() {
      const raw = (this.code || '').trim().toUpperCase();
      this.companyName = '';
      if (!raw) return;
      if (!this._companies) {
        try { this._companies = await (await fetch('/static/companies.json')).json(); }
        catch (e) { return; }
      }
      const hit = this._companies.find(c => c.c.toUpperCase() === raw);
      this.companyName = hit ? hit.n : (raw.length >= 4 ? '（一覧にないコードです）' : '');
    },

    async run() {
      this.error = ''; this.result = null;
      if (!this.code.trim()) { this.error = '銘柄コードを入れてください'; return; }
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
