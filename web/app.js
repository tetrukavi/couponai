/* couponAI UI: Alpine component consuming the SSE stream from /api/search/stream */

const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};

const WALLET = [
  ['#6366f1', '#1e1b4b'], ['#10b981', '#064e3b'], ['#f43f5e', '#4c0519'],
  ['#0ea5e9', '#082f49'], ['#f59e0b', '#451a03'], ['#a855f7', '#3b0764'],
];

// Downscale photos before upload (phone photos are 3-12 MB; 1600px is plenty to read a code).
async function shrinkImage(file, maxSide = 1600) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = () => rej(new Error('Could not open that image'));
      i.src = url;
    });
    const k = Math.min(1, maxSide / Math.max(img.width, img.height));
    const c = document.createElement('canvas');
    c.width = Math.round(img.width * k);
    c.height = Math.round(img.height * k);
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#fff';  // transparent PNGs would otherwise turn black in JPEG
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL('image/jpeg', 0.88).split(',')[1];
  } finally {
    URL.revokeObjectURL(url);
  }
}

window.couponApp = function () {
  return {
    q: '',
    dark: document.documentElement.classList.contains('dark'),
    health: {},
    storeNames: [],
    recent: store.get('couponai-recent', []),
    started: false,
    running: false,
    store: null,
    plan: [],
    local: null,
    sources: {},
    rec: null,
    error: null,
    revealed: {},
    t0: 0,
    elapsed: 0,
    es: null,
    timer: null,
    PRESETS: ['BuyMe', 'Gift'],  // replaced by your clubs from /api/health
    tab: 'check',
    coupons: [],
    couponsLoaded: false,
    showAll: false,
    editBal: {},
    confirmDelete: null,
    viewer: null,      // URL of the photo shown full screen
    photoFor: null,    // coupon whose photo is being added/replaced from its card
    photoBusy: null,   // voucher_id while a card photo uploads
    sheetOpen: false,
    form: {},
    unsure: [],
    reading: false,
    saving: false,
    formError: null,
    networkNames: [],
    toast: null,
    online: null,
    editing: {},
    editError: {},
    refreshArmed: false,

    async init() {
      // any 401 (session expired / logged out elsewhere) goes back to the login page
      const _fetch = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const r = await _fetch(...args);
        if (r.status === 401 && !String(args[0]).includes('/api/login')) location.replace('/login');
        return r;
      };
      fetch('/api/health').then(r => r.json()).then(h => {
        this.health = h;
        if (h.purchase_sources?.length) this.PRESETS = h.purchase_sources;
      }).catch(() => {});
      fetch('/api/stores').then(r => r.json()).then(s => { this.storeNames = s; }).catch(() => {});
      const fromUrl = new URLSearchParams(location.search).get('q');
      if (fromUrl) { this.q = fromUrl; this.search(); } else { this.$refs.q?.focus(); }
    },

    toggleTheme() {
      this.dark = !this.dark;
      document.documentElement.classList.toggle('dark', this.dark);
      try { localStorage.setItem('couponai-theme', this.dark ? 'dark' : 'light'); } catch (e) {}
    },

    reset() {
      this.es?.close();
      clearInterval(this.timer);
      Object.assign(this, { store: null, plan: [], local: null, sources: {}, rec: null, error: null,
                            revealed: {}, online: null, refreshArmed: false });
    },

    confirmRefresh() {
      if (this.refreshArmed) { this.search('refresh'); return; }
      this.refreshArmed = true;
      setTimeout(() => { this.refreshArmed = false; }, 4000);
    },
    usd(n) { return n == null ? '–' : (n > 0 && n < 0.01 ? '<$0.01' : '$' + Number(n).toFixed(2)); },
    monthName(m) { return m ? new Date(m + '-01T12:00').toLocaleString('en', { month: 'long' }) : ''; },
    shortDate(iso) { return iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : ''; },

    // mode: 'ask' (free: coupons + saved results), 'run' / 'refresh' (user approved paying)
    search(mode = 'ask') {
      const q = this.q.trim();
      if (!q) return;
      if (typeof mode !== 'string') mode = 'ask';
      this.reset();
      this.started = true;
      this.running = true;
      this.t0 = performance.now();
      this.timer = setInterval(() => { this.elapsed = performance.now() - this.t0; }, 100);
      this.recent = [q, ...this.recent.filter(r => r !== q)].slice(0, 6);
      store.set('couponai-recent', this.recent);
      history.replaceState(null, '', '?q=' + encodeURIComponent(q));
      this.$refs.q?.blur();

      const es = new EventSource(`/api/search/stream?q=${encodeURIComponent(q)}&mode=${mode}`);
      this.es = es;
      const on = (name, fn) => es.addEventListener(name, e => fn(JSON.parse(e.data)));
      on('normalized', d => { this.store = d; });
      on('plan', d => { this.plan = d.sources; });
      on('local', d => { this.local = d; });
      on('source', d => { this.sources = { ...this.sources, [d.source]: d }; });
      on('recommendation', d => { this.rec = d; });
      on('online', d => {
        this.online = d;
        if (d.month_total_usd != null) this.health = { ...this.health, month_total_usd: d.month_total_usd };
      });
      on('done', () => this.finish());
      es.addEventListener('error', e => {
        if (e.data) { try { this.error = JSON.parse(e.data).message; } catch (_) {} }
        else if (this.running) { this.error = 'Connection lost.'; }
        this.finish();
      });
    },

    finish() {
      this.es?.close();
      this.running = false;
      clearInterval(this.timer);
      this.elapsed = performance.now() - this.t0;
    },

    progressDone() { return (this.local ? 1 : 0) + Object.keys(this.sources).length; },
    progressTotal() { return 1 + this.plan.length; },
    progressPct() {
      if (!this.running && this.started) return 100;
      return Math.max(4, Math.round(100 * this.progressDone() / Math.max(1, this.progressTotal())));
    },
    progressText() {
      const s = (this.elapsed / 1000).toFixed(1) + 's';
      return this.running ? `${this.progressDone()}/${this.progressTotal()} sources · ${s}` : `done · ${s}`;
    },

    statusText(r) {
      if (!r) return this.running ? 'searching…' : 'not searched';
      const t = r.cached ? 'saved ' + this.shortDate(r.saved_at) : (r.elapsed_ms / 1000).toFixed(1) + 's';
      return {
        ok: `${r.offers.length} found · ${t}`, empty: `nothing found · ${t}`, timeout: 'timed out',
        error: 'error', disabled: 'offline', mock: 'not connected',
      }[r.status] || r.status;
    },
    statusClass(r) {
      if (!r) return 'text-slate-400';
      if (r.status === 'ok') return 'text-emerald-600 dark:text-emerald-400';
      if (r.status === 'error' || r.status === 'timeout') return 'text-rose-500';
      return 'text-slate-400';
    },

    walletColors(v, i) {
      const [a, b] = WALLET[(v.card_network || v.issuer).length % WALLET.length] || WALLET[i % WALLET.length];
      return `--c1:${a};--c2:${b}`;
    },
    // ---------- My coupons ----------
    openCoupons() {
      this.tab = 'coupons';
      this.loadCoupons();
    },
    async loadCoupons() {
      try {
        this.coupons = await (await fetch('/api/coupons')).json();
        this.couponsLoaded = true;
        this.health = { ...this.health, voucher_count: this.coupons.filter(c => c.usable).length };
      } catch (e) { this.flash('Could not load coupons'); }
    },
    visibleCoupons() { return this.showAll ? this.coupons : this.coupons.filter(c => c.usable); },

    async logout() {
      await fetch('/api/logout', { method: 'POST' }).catch(() => {});
      location.replace('/login');
    },

    async api(method, url, body) {
      const r = await fetch(url, {
        method, headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!r.ok) {
        let msg = `Error ${r.status}`;
        try {
          const d = await r.json();
          msg = typeof d.detail === 'string' ? d.detail
            : (d.detail || []).map(x => (x.msg || '').replace(/^Value error, /, '')).join('; ') || msg;
        } catch (_) {}
        throw new Error(msg);
      }
      return r.status === 204 ? null : r.json();
    },

    async setBalance(c) {
      const v = this.editBal[c.voucher_id];
      if (v === undefined || v === '') return;
      try {
        await this.api('PATCH', `/api/coupons/${c.voucher_id}`, { remaining_balance: Number(v) });
        this.editBal = { ...this.editBal, [c.voucher_id]: '' };
        this.flash('Balance updated');
        this.loadCoupons();
      } catch (e) { this.flash(e.message); }
    },
    async markUsed(c) {
      try {
        await this.api('PATCH', `/api/coupons/${c.voucher_id}`, { status: 'used', remaining_balance: 0 });
        this.flash('Marked as used');
        this.loadCoupons();
      } catch (e) { this.flash(e.message); }
    },
    async removeCoupon(c) {
      if (this.confirmDelete !== c.voucher_id) {
        this.confirmDelete = c.voucher_id;
        setTimeout(() => { if (this.confirmDelete === c.voucher_id) this.confirmDelete = null; }, 3000);
        return;
      }
      this.confirmDelete = null;
      try {
        await this.api('DELETE', `/api/coupons/${c.voucher_id}`);
        this.flash('Deleted');
        this.loadCoupons();
      } catch (e) { this.flash(e.message); }
    },

    // ---------- Add coupon ----------
    emptyForm() {
      return { kind: 'store', store: '', card_network: '', code: '', face_value: '', paid_amount: '',
               remaining_balance: '', expiry_date: '', purchased_from: '', notes: '', link: '', photo: '' };
    },
    openSheet() {
      this.form = this.emptyForm();
      this.unsure = [];
      this.formError = null;
      this.sheetOpen = true;
      if (!this.networkNames.length) {
        fetch('/api/networks').then(r => r.json()).then(n => { this.networkNames = n; }).catch(() => {});
      }
    },
    fieldClass(name) {
      const base = 'mt-1 w-full rounded-xl border bg-transparent px-3 py-2.5 text-[16px] outline-none focus:border-indigo-400 focus:ring-4 focus:ring-indigo-500/10 ';
      return base + (this.unsure.includes(name)
        ? 'border-amber-400 bg-amber-400/10'
        : 'border-slate-200 dark:border-ink-700');
    },
    discount() {
      const f = Number(this.form.face_value), p = Number(this.form.paid_amount);
      return f > 0 && p > 0 && p < f ? Math.round((1 - p / f) * 1000) / 10 : 0;
    },

    async saveCoupon() {
      const f = this.form;
      const num = v => (v === '' || v == null ? null : Number(v));
      const body = {
        valid_stores: f.kind === 'store' ? f.store.split(/[;,]/) : [],
        card_network: f.kind === 'network' ? f.card_network : null,
        code: f.code, face_value: num(f.face_value), paid_amount: num(f.paid_amount),
        remaining_balance: num(f.remaining_balance), expiry_date: f.expiry_date || null,
        purchased_from: f.purchased_from, notes: f.notes, link: f.link,
      };
      this.saving = true;
      this.formError = null;
      try {
        const c = await this.api('POST', '/api/coupons', body);
        this.sheetOpen = false;
        let msg = 'Coupon saved ✓';
        if (f.photo) {
          try {
            await this.api('PUT', `/api/coupons/${c.voucher_id}/image`, { media_type: 'image/jpeg', data: f.photo });
          } catch (e) { msg = 'Coupon saved, but the photo failed: ' + e.message; }
        }
        this.flash(msg);
        this.loadCoupons();
      } catch (e) {
        this.formError = e.message;
      } finally {
        this.saving = false;
      }
    },

    async readScreenshot(ev) {
      const file = ev.target.files?.[0];
      ev.target.value = '';
      if (!file) return;
      this.reading = true;
      this.formError = null;
      try {
        const data = await shrinkImage(file);
        this.form.photo = data;  // kept with the coupon unless removed in the form
        const d = await this.api('POST', '/api/coupons/extract', { media_type: 'image/jpeg', data });
        if (!d.is_coupon) throw new Error("That doesn't look like a coupon. Try another image.");
        const s = v => (v == null ? '' : String(v));
        this.form = {
          ...this.form,
          kind: d.card_network ? 'network' : 'store',
          store: (d.valid_stores || []).join(', ') || (d.card_network ? '' : s(d.issuer)),
          card_network: s(d.card_network),
          code: s(d.code), face_value: s(d.face_value), paid_amount: s(d.paid_amount),
          remaining_balance: s(d.remaining_balance), expiry_date: s(d.expiry_date),
          purchased_from: s(d.purchased_from) || this.form.purchased_from, notes: s(d.notes),
          link: s(d.link),
        };
        // always ask to double-check the code: one wrong digit makes it useless
        this.unsure = [...new Set([...(d.unsure_fields || []), 'code'])]
          .map(x => (x === 'store' || x === 'issuer' ? 'valid_stores' : x));
      } catch (e) {
        this.formError = e.message;
      } finally {
        this.reading = false;
      }
    },

    // ---------- Photo ----------
    photoUrl(c) { return `/api/coupons/${c.voucher_id}/image?v=${encodeURIComponent(c.image)}`; },
    async pickFormPhoto(ev) {
      const file = ev.target.files?.[0];
      ev.target.value = '';
      if (!file) return;
      try { this.form.photo = await shrinkImage(file); } catch (e) { this.formError = e.message; }
    },
    choosePhoto(c) {
      this.photoFor = c;
      this.$refs.cardPhoto.click();
    },
    async uploadCardPhoto(ev) {
      const file = ev.target.files?.[0];
      ev.target.value = '';
      const c = this.photoFor;
      this.photoFor = null;
      if (!file || !c) return;
      this.photoBusy = c.voucher_id;
      try {
        const data = await shrinkImage(file);
        await this.api('PUT', `/api/coupons/${c.voucher_id}/image`, { media_type: 'image/jpeg', data });
        this.flash('Photo saved');
        await this.loadCoupons();
      } catch (e) { this.flash(e.message); } finally { this.photoBusy = null; }
    },
    async removePhoto(c) {
      if (this.confirmDelete !== 'photo:' + c.voucher_id) {
        this.confirmDelete = 'photo:' + c.voucher_id;
        setTimeout(() => { if (this.confirmDelete === 'photo:' + c.voucher_id) this.confirmDelete = null; }, 3000);
        return;
      }
      this.confirmDelete = null;
      try {
        await this.api('DELETE', `/api/coupons/${c.voucher_id}/image`);
        this.flash('Photo removed');
        this.loadCoupons();
      } catch (e) { this.flash(e.message); }
    },

    startEdit(c) {
      this.editing = { ...this.editing, [c.voucher_id]: { notes: c.notes || '', link: c.link || '' } };
      this.editError = { ...this.editError, [c.voucher_id]: null };
    },
    stopEdit(c) {
      const { [c.voucher_id]: _, ...rest } = this.editing;
      this.editing = rest;
    },
    async saveDetails(c) {
      const e = this.editing[c.voucher_id];
      try {
        await this.api('PATCH', `/api/coupons/${c.voucher_id}`, { notes: e.notes, link: e.link });
        this.stopEdit(c);
        this.flash('Saved');
        this.loadCoupons();
      } catch (err) {
        this.editError = { ...this.editError, [c.voucher_id]: err.message };
      }
    },
    money(n) { return n == null ? 'Ticket' : '₪' + this.fmt(n); },

    flash(msg) {
      this.toast = msg;
      clearTimeout(this._toastT);
      this._toastT = setTimeout(() => { this.toast = null; }, 2200);
    },

    toggleCode(id) { this.revealed = { ...this.revealed, [id]: !this.revealed[id] }; },
    initials(s) { return (s || '?').split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase(); },
    fmt(n) { return n == null ? '–' : Number(n).toLocaleString('en-IL', { maximumFractionDigits: 1 }); },
  };
};
