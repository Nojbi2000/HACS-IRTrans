/* IRTrans remote panel. No external libraries; all actions use HA entities. */
class IRTransPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._editMode = false; this._creating = false; this._remotes = []; this._rows = []; this._pending = new Set(); this._generation = 0;
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;height:100%;overflow:auto;background:var(--primary-background-color,#f5f6f8);color:var(--primary-text-color,#202124);font-family:var(--paper-font-body1_-_font-family,Arial,sans-serif)}
        *{box-sizing:border-box}header{display:flex;align-items:center;gap:12px;padding:10px 16px;background:var(--app-header-background-color,var(--card-background-color,#fff));border-bottom:1px solid var(--divider-color,#ddd);position:sticky;top:0;z-index:2}header h1{font-size:21px;flex:1;margin:0}main{max-width:860px;margin:auto;padding:20px 16px 40px}.toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}.toolbar label{flex:1;min-width:180px}select,input{font:inherit;color:inherit;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#bcc2ca);border-radius:8px;padding:12px;width:100%;min-height:46px}select{margin-top:5px}a{color:var(--primary-color,#009ac7);font-size:14px}button{font:inherit;min-height:46px;cursor:pointer;border-radius:8px;padding:10px 14px;border:1px solid var(--divider-color,#b9c4ce);color:var(--primary-color,#007c9f);background:var(--card-background-color,#fff)}button:disabled,input:disabled{opacity:.45;cursor:default}.send{background:var(--primary-color,#009ac7);color:var(--text-primary-color,#fff);border-color:transparent}.status{padding:14px 16px;border-radius:10px;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#d2d7df);margin-bottom:20px;line-height:1.5}.error{color:var(--error-color,#b00020);margin:12px 0;white-space:pre-wrap}.error:empty{display:none}.cards{display:grid;gap:18px}.command{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#c2c9d2);border-top:3px solid var(--primary-color,#009ac7);border-radius:12px;padding:16px;box-shadow:0 1px 3px #0000000b}.command.active{outline:2px solid var(--primary-color,#009ac7)}.heading{display:flex;gap:12px;align-items:center;margin-bottom:15px}.number{font-size:14px;font-weight:700;min-width:36px;height:36px;display:grid;place-items:center;border-radius:8px;background:var(--secondary-background-color,#edf1f4)}h2{font-size:19px;margin:0;overflow-wrap:anywhere}.name-label{display:block;font-size:13px;color:var(--secondary-text-color,#646b75);margin-bottom:6px}.actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:14px}.badges{font-size:13px;color:var(--secondary-text-color,#646b75);margin-top:10px}.progress{font-weight:600;color:var(--primary-color,#007c9f);line-height:1.5;margin:12px 0 0}.progress:empty{display:none}.empty{padding:28px;text-align:center}input:focus,button:focus-visible,select:focus{outline:2px solid var(--primary-color,#009ac7);outline-offset:2px}@media(min-width:700px){.actions{grid-template-columns:repeat(4,minmax(0,1fr))}}
        .command{padding:12px 14px}.cards{gap:12px}.main-row{display:flex;gap:14px;align-items:center}.main-row .heading{flex:1;min-width:0;margin:0}.main-row .actions{display:flex;margin:0;flex:none}.main-row .actions button{min-width:78px}.editor{border-top:1px solid var(--divider-color,#ddd);margin-top:14px;padding-top:14px}.editor[hidden],.add-form[hidden]{display:none}.editor .actions{display:grid;grid-template-columns:1fr 1fr}.mode-note{font-size:14px;color:var(--secondary-text-color,#646b75);margin:0 0 12px}.gear{display:grid;place-items:center;width:46px;padding:10px}.gear svg{width:24px;height:24px}.gear[aria-pressed="true"]{background:var(--primary-color,#009ac7);color:var(--text-primary-color,#fff)}.add-form{border:1px solid var(--divider-color,#c2c9d2);border-radius:12px;padding:18px;background:var(--card-background-color,#fff);margin-bottom:18px}.add-form h2{margin-bottom:14px}.add-form label{display:block;margin:12px 0}.add-form .actions{display:flex;justify-content:flex-end}.add-form input{margin-top:6px}.toolbar button{align-self:flex-end}.status{padding:10px 14px;margin-bottom:14px}h2{font-size:18px}@media(max-width:430px){.main-row{gap:8px}.main-row .heading{gap:8px}.main-row .actions{gap:6px}.main-row .actions button{min-width:64px;padding:10px 8px}.number{min-width:28px;height:30px;font-size:12px}.command{padding:10px}.main-row h2{font-size:16px}}
      </style>
      <header><ha-menu-button></ha-menu-button><h1>IRTrans</h1><button id="refresh" aria-label="Refresh remotes">Refresh</button><button class="gear" id="edit-mode" aria-label="Edit and learn mode" title="Edit & learn" aria-pressed="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4M5 5l3 3m8 8 3 3M5 19l3-3m8-8 3-3"/></svg></button></header>
      <main><div class="toolbar"><label>Remote<select aria-label="Remote"></select></label><button id="add-remote">Add remote</button></div><form class="add-form" hidden><h2>Add remote</h2><label>Remote name<input id="remote-name" maxlength="100" required autocomplete="off"></label><label>Number of buttons<input id="remote-count" type="number" min="1" max="32" step="1" value="8" required></label><div class="actions"><button type="button" id="cancel-add">Cancel</button><button class="send" type="submit">Create remote</button></div></form><p class="mode-note" hidden>Edit & learn mode · name each button and capture its short press and optional repeat. <a href="/config/integrations/integration/irtrans">More settings</a></p><div class="status" role="status" aria-live="polite">Loading remotes…</div><div class="error" role="alert"></div><div class="cards"></div></main>`;
    this._select = this.shadowRoot.querySelector('select');
    this._form = this.shadowRoot.querySelector('.add-form');
    this._status = this.shadowRoot.querySelector('.status');
    this._error = this.shadowRoot.querySelector('.error');
    this._cards = this.shadowRoot.querySelector('.cards');
    this._select.addEventListener('change', () => this._build());
    this.shadowRoot.querySelector('#refresh').addEventListener('click', () => this._load());
    this.shadowRoot.querySelector('#edit-mode').addEventListener('click', () => {
      this._editMode = !this._editMode;
      this.shadowRoot.querySelector('#edit-mode').setAttribute('aria-pressed', String(this._editMode));
      this.shadowRoot.querySelector('.mode-note').hidden = !this._editMode;
      for (const row of this._rows) row.editor.hidden = !this._editMode;
      this._update();
    });
    this.shadowRoot.querySelector('#add-remote').addEventListener('click', () => this._openAdd());
    this.shadowRoot.querySelector('#cancel-add').addEventListener('click', () => { this._form.hidden = true; });
    this._form.addEventListener('submit', event => this._addRemote(event));
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
  async _load(preferredKey = null) {
    if (!this._hass) return;
    const generation = ++this._generation;
    this._loading = true;
    try {
      const result = await this._hass.callWS({type: 'irtrans/panel'});
      if (generation !== this._generation || !this.isConnected) return;
      const selected = preferredKey || this._select.value;
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
    if (!this._remote) { this._status.textContent = 'No IRTrans remotes available.'; this._update(); return; }
    for (const slot of this._remote.slots) {
      const card = document.createElement('section'); card.className = 'command';
      card.innerHTML = '<div class="main-row"><div class="heading"><span class="number"></span><h2></h2></div><div class="actions send-actions"></div></div><p class="progress" role="status" aria-live="polite"></p><div class="editor"><label><span class="name-label">Button name</span><input maxlength="100" minlength="1" required></label><div class="actions learn-actions"></div><div class="badges"></div></div>';
      const editor = card.querySelector('.editor'); editor.hidden = !this._editMode;
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
        if (action === 'send' || action === 'hold') button.className = 'send';
        button.addEventListener('click', () => this._call('button', 'press', slot.entities[action]));
        card.querySelector(action === 'send' || action === 'hold' ? '.send-actions' : '.learn-actions').append(button); actions[action] = button;
      }
      this._cards.append(card);
      this._rows.push({slot, card, title, input, editor, actions, progress: card.querySelector('.progress'), badges: card.querySelector('.badges')});
    }
    this._update();
  }
  _update() {
    this.shadowRoot.querySelector('#add-remote').disabled = !this._remote || this._creating;
    this.shadowRoot.querySelector('#refresh').disabled = this._creating;
    this.shadowRoot.querySelector('#edit-mode').disabled = this._creating;
    this._select.disabled = this._creating;
    if (!this._hass || !this._remote) return;
    const states = this._hass.states;
    const status = states[this._remote.status_entity];
    const progress = status?.attributes || {};
    this._status.textContent = this._creating ? 'Creating remote…' : !this._editMode && ['idle', 'done'].includes(progress.phase) && progress.online && !progress.learning ? 'Ready' : status && !['unavailable', 'unknown'].includes(status.state)
      ? status.state : 'Learning status unavailable. Check IRTrans connection.';
    for (const row of this._rows) {
      const name = states[row.slot.entities.name];
      if (name && !['unavailable', 'unknown'].includes(name.state)) row.slot.label = name.state;
      row.title.textContent = row.slot.label;
      if (this.shadowRoot.activeElement !== row.input) row.input.value = row.slot.label;
      row.input.disabled = this._creating || !name || name.state === 'unavailable' || this._pending.has(row.slot.entities.name);
      const active = progress.learning && progress.slot === row.slot.index;
      row.card.classList.toggle('active', !!active);
      row.progress.textContent = active ? (progress.message || status.state) : '';
      const metadata = states[row.slot.entities.learn]?.attributes || states[row.slot.entities.send]?.attributes || {};
      if (typeof metadata.learned === 'boolean') row.slot.learned = metadata.learned;
      if (typeof metadata.repeat_learned === 'boolean') row.slot.repeat_learned = metadata.repeat_learned;
      row.badges.textContent = `${row.slot.learned ? 'Short press saved' : 'Short press not learned'} · ${row.slot.repeat_learned ? 'Repeat saved' : 'Repeat optional'}`;
      for (const [action, button] of Object.entries(row.actions)) {
        const entity = row.slot.entities[action], state = states[entity];
        button.disabled = this._creating || !state || state.state === 'unavailable' || this._pending.has(entity);
        if (active && action === (progress.kind === 'repeat' ? 'learn_repeat' : 'learn')) button.textContent = `${progress.sample || ''}/3`;
        else button.textContent = ({learn: 'Learn', learn_repeat: 'Learn repeat', send: 'Send', hold: `Hold ${this._remote.hold_secs} s`})[action];
      }
    }
  }
  _openAdd() {
    if (!this._remote || this._creating) return;
    this._addEntry = this._remote.entry_id;
    this._requestId = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
    this.shadowRoot.querySelector('#remote-name').value = `Remote ${String(this._remotes.filter(r => r.entry_id === this._addEntry).length + 1).padStart(2, '0')}`;
    this.shadowRoot.querySelector('#remote-count').value = '8';
    this._error.textContent = ''; this._form.hidden = false;
    this.shadowRoot.querySelector('#remote-name').focus();
  }
  async _addRemote(event) {
    event.preventDefault();
    if (this._creating || !this._form.reportValidity()) return;
    const name = this.shadowRoot.querySelector('#remote-name').value.trim();
    if (!name) { this._error.textContent = 'Enter a remote name.'; return; }
    this._creating = true; this._error.textContent = '';
    for (const input of this._form.querySelectorAll('input,button')) input.disabled = true;
    this._update();
    try {
      const result = await this._hass.callWS({type: 'irtrans/add_remote', entry_id: this._addEntry,
        name, button_count: Number(this.shadowRoot.querySelector('#remote-count').value), request_id: this._requestId});
      this._form.hidden = true;
      await this._load(result.key);
      if (!result.ready) this._error.textContent = 'Remote saved. IRTrans is reconnecting; use Refresh when it is ready.';
    } catch (error) { this._error.textContent = error.message || String(error); }
    finally {
      this._creating = false;
      for (const input of this._form.querySelectorAll('input,button')) input.disabled = false;
      this._update();
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
