/* IRTrans remote panel. No external libraries; all actions use HA entities. */
class IRTransPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._remotes = []; this._rows = []; this._pending = new Set(); this._generation = 0;
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;height:100%;overflow:auto;background:var(--primary-background-color,#f5f6f8);color:var(--primary-text-color,#202124);font-family:var(--paper-font-body1_-_font-family,Arial,sans-serif)}
        *{box-sizing:border-box}header{display:flex;align-items:center;gap:12px;padding:10px 16px;background:var(--app-header-background-color,var(--card-background-color,#fff));border-bottom:1px solid var(--divider-color,#ddd);position:sticky;top:0;z-index:2}header h1{font-size:21px;flex:1;margin:0}main{max-width:860px;margin:auto;padding:20px 16px 40px}.toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}.toolbar label{flex:1;min-width:180px}select,input{font:inherit;color:inherit;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#bcc2ca);border-radius:8px;padding:12px;width:100%;min-height:46px}select{margin-top:5px}a{color:var(--primary-color,#009ac7);font-size:14px}button{font:inherit;min-height:46px;cursor:pointer;border-radius:8px;padding:10px 14px;border:1px solid var(--divider-color,#b9c4ce);color:var(--primary-color,#007c9f);background:var(--card-background-color,#fff)}button:disabled,input:disabled{opacity:.45;cursor:default}.send{background:var(--primary-color,#009ac7);color:var(--text-primary-color,#fff);border-color:transparent}.status{padding:14px 16px;border-radius:10px;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#d2d7df);margin-bottom:20px;line-height:1.5}.error{color:var(--error-color,#b00020);margin:12px 0;white-space:pre-wrap}.error:empty{display:none}.cards{display:grid;gap:18px}.command{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#c2c9d2);border-top:3px solid var(--primary-color,#009ac7);border-radius:12px;padding:16px;box-shadow:0 1px 3px #0000000b}.command.active{outline:2px solid var(--primary-color,#009ac7)}.heading{display:flex;gap:12px;align-items:center;margin-bottom:15px}.number{font-size:14px;font-weight:700;min-width:36px;height:36px;display:grid;place-items:center;border-radius:8px;background:var(--secondary-background-color,#edf1f4)}h2{font-size:19px;margin:0;overflow-wrap:anywhere}.name-label{display:block;font-size:13px;color:var(--secondary-text-color,#646b75);margin-bottom:6px}.actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:14px}.badges{font-size:13px;color:var(--secondary-text-color,#646b75);margin-top:10px}.progress{font-weight:600;color:var(--primary-color,#007c9f);line-height:1.5;margin:12px 0 0}.progress:empty{display:none}.empty{padding:28px;text-align:center}input:focus,button:focus-visible,select:focus{outline:2px solid var(--primary-color,#009ac7);outline-offset:2px}@media(min-width:700px){.actions{grid-template-columns:repeat(4,minmax(0,1fr))}}
      </style>
      <header><ha-menu-button></ha-menu-button><h1>IRTrans</h1><button id="refresh" aria-label="Refresh remotes">Refresh</button></header>
      <main><div class="toolbar"><label>Remote<select aria-label="Remote"></select></label><a href="/config/integrations/integration/irtrans">Manage remotes</a></div><div class="status" role="status" aria-live="polite">Loading remotes…</div><div class="error" role="alert"></div><div class="cards"></div></main>`;
    this._select = this.shadowRoot.querySelector('select');
    this._status = this.shadowRoot.querySelector('.status');
    this._error = this.shadowRoot.querySelector('.error');
    this._cards = this.shadowRoot.querySelector('.cards');
    this._select.addEventListener('change', () => this._build());
    this.shadowRoot.querySelector('#refresh').addEventListener('click', () => this._load());
  }
  set hass(value) {
    this._hass = value;
    this.shadowRoot.querySelector('ha-menu-button').hass = value;
    if (this.isConnected && !this._loaded && !this._loading) this._load();
    else this._update();
  }
  set narrow(value) { this.shadowRoot.querySelector('ha-menu-button').narrow = value; }
  connectedCallback() { if (this._hass && !this._loaded && !this._loading) this._load(); }
  disconnectedCallback() { this._generation++; this._loading = false; this._loaded = false; }
  async _load() {
    if (!this._hass) return;
    const generation = ++this._generation;
    this._loading = true;
    try {
      const result = await this._hass.callWS({type: 'irtrans/panel'});
      if (generation !== this._generation || !this.isConnected) return;
      const selected = this._select.value;
      this._remotes = result.remotes;
      this._select.replaceChildren();
      for (const remote of this._remotes) {
        const option = document.createElement('option'); option.value = remote.key;
        option.textContent = this._remotes.some(r => r.key !== remote.key && r.name === remote.name)
          ? `${remote.name} · ${remote.device}` : remote.name;
        this._select.append(option);
      }
      if (this._remotes.some(r => r.key === selected)) this._select.value = selected;
      this._loaded = true; this._error.textContent = ''; this._build();
    } catch (error) {
      if (generation === this._generation) {
        this._error.textContent = error.message || String(error);
        this._status.textContent = 'Unable to load remotes. Check your connection and try Refresh.';
      }
    } finally { if (generation === this._generation) this._loading = false; }
  }
  _build() {
    this._rows = []; this._cards.replaceChildren();
    this._remote = this._remotes.find(r => r.key === this._select.value);
    if (!this._remote) { this._status.textContent = 'No IRTrans remotes available.'; return; }
    for (const slot of this._remote.slots) {
      const card = document.createElement('section'); card.className = 'command';
      card.innerHTML = '<div class="heading"><span class="number"></span><h2></h2></div><label><span class="name-label">Button name</span><input maxlength="100" minlength="1" required></label><p class="progress" role="status" aria-live="polite"></p><div class="actions"></div><div class="badges"></div>';
      card.querySelector('.number').textContent = String(slot.number).padStart(2, '0');
      const title = card.querySelector('h2'), input = card.querySelector('input');
      input.value = slot.label; input.setAttribute('aria-label', `Name for button ${slot.number}`);
      input.addEventListener('change', () => {
        if (!input.value.trim()) { input.value = slot.label; return; }
        this._call('text', 'set_value', slot.entities.name, {value: input.value.trim()});
      });
      const actions = {};
      for (const [action, label] of [['learn', 'Learn'], ['learn_repeat', 'Learn repeat'], ['send', 'Send'], ['hold', `Hold ${this._remote.hold_secs} s`]]) {
        const button = document.createElement('button'); button.textContent = label;
        button.dataset.action = action;
        if (action === 'send') button.className = 'send';
        button.addEventListener('click', () => this._call('button', 'press', slot.entities[action]));
        card.querySelector('.actions').append(button); actions[action] = button;
      }
      this._cards.append(card);
      this._rows.push({slot, card, title, input, actions, progress: card.querySelector('.progress'), badges: card.querySelector('.badges')});
    }
    this._update();
  }
  _update() {
    if (!this._hass || !this._remote) return;
    const states = this._hass.states;
    const status = states[this._remote.status_entity];
    const progress = status?.attributes || {};
    this._status.textContent = status && !['unavailable', 'unknown'].includes(status.state)
      ? status.state : 'Learning status unavailable. Check IRTrans connection.';
    for (const row of this._rows) {
      const name = states[row.slot.entities.name];
      if (name && !['unavailable', 'unknown'].includes(name.state)) row.slot.label = name.state;
      row.title.textContent = row.slot.label;
      if (this.shadowRoot.activeElement !== row.input) row.input.value = row.slot.label;
      row.input.disabled = !name || name.state === 'unavailable' || this._pending.has(row.slot.entities.name);
      const active = progress.learning && progress.slot === row.slot.index;
      row.card.classList.toggle('active', !!active);
      row.progress.textContent = active ? (progress.message || status.state) : '';
      const metadata = states[row.slot.entities.learn]?.attributes || states[row.slot.entities.send]?.attributes || {};
      if (typeof metadata.learned === 'boolean') row.slot.learned = metadata.learned;
      if (typeof metadata.repeat_learned === 'boolean') row.slot.repeat_learned = metadata.repeat_learned;
      row.badges.textContent = `${row.slot.learned ? 'Short press saved' : 'Short press not learned'} · ${row.slot.repeat_learned ? 'Repeat saved' : 'Repeat optional'}`;
      for (const [action, button] of Object.entries(row.actions)) {
        const entity = row.slot.entities[action], state = states[entity];
        button.disabled = !state || state.state === 'unavailable' || this._pending.has(entity);
        if (active && action === (progress.kind === 'repeat' ? 'learn_repeat' : 'learn')) button.textContent = `${progress.sample || ''}/3`;
        else button.textContent = ({learn: 'Learn', learn_repeat: 'Learn repeat', send: 'Send', hold: `Hold ${this._remote.hold_secs} s`})[action];
      }
    }
  }
  async _call(domain, service, entity, extra = {}) {
    if (!entity || this._pending.has(entity)) return;
    this._pending.add(entity); this._error.textContent = ''; this._update();
    try { await this._hass.callService(domain, service, {entity_id: entity, ...extra}); }
    catch (error) { this._error.textContent = error.message || String(error); }
    finally { this._pending.delete(entity); this._update(); }
  }
}
if (!customElements.get('irtrans-panel')) customElements.define('irtrans-panel', IRTransPanel);
