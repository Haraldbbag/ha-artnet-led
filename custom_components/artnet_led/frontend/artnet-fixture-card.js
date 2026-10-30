/*
 * artnet-fixture-card: one card per DMX fixture (artnet_led).
 *
 *   type: custom:artnet-fixture-card
 *   entity: light.ml_saal_1_links     # any entity of the fixture
 *   name: Moving Head links          # optional
 *   step_fine: 1                      # arrow step, in the unit of pan/tilt (° or %)
 *   step_coarse: 10
 *   invert_pan: false
 *   invert_tilt: false
 *   home: {pan: 270, tilt: 135}       # optional, default = centre
 *   scenes:                           # optional position / look buttons
 *     - scene.ml_buehne_mitte
 *
 * The card finds the other entities of the fixture (pan, tilt, zoom ...) through
 * the device and their "fixture_role" attribute.
 */
const CARD_VERSION = "0.2.3";

const ROLE_LABELS = {
  zoom: "Zoom",
  focus: "Fokus",
  strobe: "Strobe",
  amber: "Amber",
};

class ArtnetFixtureCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("entity is required (any entity of the fixture)");
    }
    this._config = {
      step_fine: 1,
      step_coarse: 10,
      invert_pan: false,
      invert_tilt: false,
      scenes: [],
      ...config,
    };
    this._built = false;
    if (this._hass) this._render();
  }

  static getStubConfig(hass) {
    const light = Object.keys(hass.states).find(
      (e) => e.startsWith("light.") && hass.states[e].attributes.fixture_role === "light"
    );
    return { entity: light || "light.example" };
  }

  getCardSize() {
    return 7;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  set hass(hass) {
    this._hass = hass;
    if (this._config) this._render();
  }

  // ------------------------------------------------------------ entities

  _roles() {
    const hass = this._hass;
    const roles = {};
    const reg = hass.entities || {};
    const own = reg[this._config.entity];
    const deviceId = own && own.device_id;
    const candidates = deviceId
      ? Object.keys(reg).filter((e) => reg[e].device_id === deviceId)
      : [this._config.entity];
    for (const eid of candidates) {
      const st = hass.states[eid];
      if (st && st.attributes.fixture_role) roles[st.attributes.fixture_role] = eid;
    }
    return roles;
  }

  _num(eid) {
    const st = eid && this._hass.states[eid];
    if (!st) return null;
    const v = parseFloat(st.state);
    return {
      value: isNaN(v) ? null : v,
      min: Number(st.attributes.min ?? 0),
      max: Number(st.attributes.max ?? 100),
      step: Number(st.attributes.step ?? 1),
      unit: st.attributes.unit_of_measurement || "",
    };
  }

  _call(domain, service, data) {
    return this._hass.callService(domain, service, data);
  }

  _setNumber(eid, value) {
    const n = this._num(eid);
    if (!n) return;
    const v = Math.min(n.max, Math.max(n.min, value));
    return this._call("number", "set_value", { entity_id: eid, value: Math.round(v * 1000) / 1000 });
  }

  // ------------------------------------------------------------ build

  _build() {
    const roles = (this._roleMap = this._roles());
    const cfg = this._config;
    const light = roles.light && this._hass.states[roles.light];
    const title =
      cfg.name || (light && light.attributes.friendly_name) || this._hass.states[cfg.entity]?.attributes.friendly_name || cfg.entity;

    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    const sliders = ["zoom", "focus", "strobe", "amber"].filter((r) => roles[r]);

    root.innerHTML = `
      <style>
        :host { display: block; }
        ha-card, .card { padding: 10px 14px 12px; display: block; }
        .head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .title { flex: 1; font-size: 1.15em; font-weight: 500; }
        .state { font-size: .85em; color: var(--secondary-text-color); }
        button { font: inherit; cursor: pointer; border: none; border-radius: 10px;
          background: var(--secondary-background-color, #eee); color: var(--primary-text-color);
          padding: 4px 10px; min-height: 32px; }
        button.on { background: var(--state-light-active-color, #ffc107); color: #000; }
        button:active { filter: brightness(.9); }
        .move { display: flex; flex-direction: column; gap: 8px; }
        .pad { position: relative; width: 100%; aspect-ratio: 2 / 1; min-height: 160px; border-radius: 12px; touch-action: none;
          background:
            linear-gradient(var(--divider-color, #ccc) 1px, transparent 1px) 0 0 / 100% 25%,
            linear-gradient(90deg, var(--divider-color, #ccc) 1px, transparent 1px) 0 0 / 12.5% 100%,
            var(--secondary-background-color, #f3f3f3);
          cursor: crosshair; overflow: hidden; }
        .dot { position: absolute; width: 22px; height: 22px; margin: -11px 0 0 -11px; border-radius: 50%;
          background: var(--primary-color, #03a9f4); box-shadow: 0 0 0 4px rgba(3,169,244,.25);
          pointer-events: none; }
        .ctrl { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 8px; }
        .cross { display: grid; grid-template-columns: repeat(3, 38px); grid-template-rows: repeat(3, 34px); gap: 4px; }
        .cross button { padding: 0; min-height: 0; min-width: 0; width: 38px; height: 34px; font-size: 15px; }
        .cross button.step { font-size: 11px; }
        .pos { font-size: .85em; color: var(--secondary-text-color); }
        .pos.tilt { text-align: right; }
        .row { display: grid; grid-template-columns: 72px minmax(0, 1fr) 56px; align-items: center; gap: 8px; margin-top: 4px; }
        .row label { font-size: .9em; }
        .row .val { text-align: right; font-size: .85em; color: var(--secondary-text-color); }
        input[type=range] { width: 100%; accent-color: var(--primary-color, #03a9f4); }
        select { font: inherit; padding: 6px; border-radius: 8px; width: 100%;
          background: var(--card-background-color, #fff); color: var(--primary-text-color); border: 1px solid var(--divider-color, #ccc); }
        .buttons { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; align-items: center; }
        .buttons button { flex: 1 1 auto; }
        .buttons button.reset { flex: 0 0 auto; margin-left: auto; min-height: 26px; padding: 2px 12px; font-size: .85em; }
        .warn { color: var(--error-color, #db4437); }
      </style>
      <ha-card>
        <div class="head">
          <div class="title"></div>
          ${roles.light ? '<button class="color" title="Farbe">Farbe</button><button class="power">Aus</button>' : ""}
        </div>
        ${roles.pan || roles.tilt ? `
        <div class="move">
          <div class="pad"><div class="dot"></div></div>
          <div class="ctrl">
            <span class="pos pan"></span>
            <div class="cross">
              <span></span><button data-d="up" title="hoch">▲</button><span></span>
              <button data-d="left" title="links">◀</button><button class="home" title="Grundposition">⌂</button><button data-d="right" title="rechts">▶</button>
              <span></span><button data-d="down" title="runter">▼</button><button class="step" title="Schrittweite">fein</button>
            </div>
            <span class="pos tilt"></span>
          </div>
        </div>` : ""}
        ${roles.light ? `<div class="row"><label>Helligkeit</label><input type="range" min="0" max="100" step="1" data-role="brightness"><span class="val" data-val="brightness"></span></div>` : ""}
        ${sliders.map((r) => `<div class="row"><label>${ROLE_LABELS[r]}</label><input type="range" data-role="${r}"><span class="val" data-val="${r}"></span></div>`).join("")}
        ${roles.gobo ? `<div class="row"><label>Gobo</label><select data-role="gobo"></select><span></span></div>` : ""}
        <div class="buttons">
          ${(cfg.scenes || []).map((s) => `<button data-scene="${s}">${this._hass.states[s]?.attributes.friendly_name || s}</button>`).join("")}
          ${roles.reset ? '<button class="reset">Reset</button>' : ""}
        </div>
      </ha-card>`;
    root.querySelector(".title").textContent = title;
    this._fine = true;

    // light
    root.querySelector(".power")?.addEventListener("click", () =>
      this._call("light", "toggle", { entity_id: roles.light })
    );
    root.querySelector(".color")?.addEventListener("click", () => {
      this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: roles.light }, bubbles: true, composed: true }));
    });

    // sliders
    root.querySelectorAll("input[type=range]").forEach((inp) => {
      const role = inp.dataset.role;
      if (role !== "brightness") {
        const n = this._num(roles[role]);
        if (n) { inp.min = n.min; inp.max = n.max; inp.step = n.step; }
      }
      inp.addEventListener("input", () => { inp._busy = true; this._showVal(role, Number(inp.value)); });
      inp.addEventListener("change", () => {
        inp._busy = false;
        const v = Number(inp.value);
        if (role === "brightness") {
          if (v <= 0) this._call("light", "turn_off", { entity_id: roles.light });
          else this._call("light", "turn_on", { entity_id: roles.light, brightness_pct: v });
        } else {
          this._setNumber(roles[role], v);
        }
      });
    });

    // gobo
    const gobo = root.querySelector("select[data-role=gobo]");
    if (gobo) {
      const st = this._hass.states[roles.gobo];
      (st?.attributes.options || []).forEach((o) => {
        const opt = document.createElement("option");
        opt.value = opt.textContent = o;
        gobo.appendChild(opt);
      });
      gobo.addEventListener("change", () =>
        this._call("select", "select_option", { entity_id: roles.gobo, option: gobo.value })
      );
    }

    // scenes / reset
    root.querySelectorAll("button[data-scene]").forEach((b) =>
      b.addEventListener("click", () => this._call("scene", "turn_on", { entity_id: b.dataset.scene }))
    );
    root.querySelector(".reset")?.addEventListener("click", () => {
      if (confirm(`${title}: Reset auslösen? Die Lampe fährt dabei in die Grundstellung.`)) {
        this._call("button", "press", { entity_id: roles.reset });
      }
    });

    // pan / tilt
    const pad = root.querySelector(".pad");
    if (pad) this._setupPad(pad);
    root.querySelectorAll(".cross button[data-d]").forEach((b) =>
      b.addEventListener("click", () => this._nudge(b.dataset.d))
    );
    root.querySelector(".cross .home")?.addEventListener("click", () => this._home());
    const stepBtn = root.querySelector(".cross .step");
    stepBtn?.addEventListener("click", () => {
      this._fine = !this._fine;
      stepBtn.textContent = this._fine ? "fein" : "grob";
    });

    this._built = true;
  }

  // ------------------------------------------------------------ pan/tilt

  _axis(role) {
    return this._num(this._roleMap[role]);
  }

  // pad coordinates 0..1 <-> entity values
  _toPad(role, value) {
    const n = this._axis(role);
    if (!n || n.value === null) return 0.5;
    let p = (value - n.min) / (n.max - n.min || 1);
    if (this._config[`invert_${role}`]) p = 1 - p;
    return p;
  }

  _fromPad(role, p) {
    const n = this._axis(role);
    if (this._config[`invert_${role}`]) p = 1 - p;
    return n.min + Math.min(1, Math.max(0, p)) * (n.max - n.min);
  }

  _setupPad(pad) {
    let last = 0;
    let pending = null;
    const send = (px, py) => {
      if (this._roleMap.pan) this._setNumber(this._roleMap.pan, this._fromPad("pan", px));
      if (this._roleMap.tilt) this._setNumber(this._roleMap.tilt, this._fromPad("tilt", py));
    };
    const handle = (ev, final) => {
      const r = pad.getBoundingClientRect();
      const px = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
      const py = Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height));
      this._dragPos = [px, py];
      this._placeDot(px, py);
      const now = Date.now();
      if (final || now - last > 200) {
        last = now;
        clearTimeout(pending);
        send(px, py);
      } else {
        clearTimeout(pending);
        pending = setTimeout(() => send(px, py), 200);
      }
    };
    pad.addEventListener("pointerdown", (ev) => {
      pad.setPointerCapture(ev.pointerId);
      this._dragging = true;
      handle(ev, false);
    });
    pad.addEventListener("pointermove", (ev) => { if (this._dragging) handle(ev, false); });
    const end = (ev) => {
      if (!this._dragging) return;
      this._dragging = false;
      handle(ev, true);
    };
    pad.addEventListener("pointerup", end);
    pad.addEventListener("pointercancel", end);
  }

  _placeDot(px, py) {
    const dot = this.shadowRoot.querySelector(".dot");
    if (!dot) return;
    dot.style.left = `${px * 100}%`;
    dot.style.top = `${py * 100}%`;
  }

  _nudge(dir) {
    const step = this._fine ? this._config.step_fine : this._config.step_coarse;
    const axis = dir === "left" || dir === "right" ? "pan" : "tilt";
    const eid = this._roleMap[axis];
    const n = this._axis(axis);
    if (!eid || !n || n.value === null) return;
    // arrows follow the pad: right/down = towards the right/bottom edge of the pad
    let sign = dir === "right" || dir === "down" ? 1 : -1;
    if (this._config[`invert_${axis}`]) sign = -sign;
    this._setNumber(eid, n.value + sign * step);
  }

  _home() {
    const home = this._config.home || {};
    for (const axis of ["pan", "tilt"]) {
      const eid = this._roleMap[axis];
      const n = this._axis(axis);
      if (!eid || !n) continue;
      this._setNumber(eid, home[axis] ?? (n.min + n.max) / 2);
    }
  }

  // ------------------------------------------------------------ update

  _showVal(role, v) {
    const el = this.shadowRoot.querySelector(`[data-val=${role}]`);
    if (!el) return;
    if (role === "brightness") { el.textContent = `${Math.round(v)} %`; return; }
    const n = this._num(this._roleMap[role]);
    el.textContent = `${Math.round(v * 10) / 10}${n && n.unit ? " " + n.unit : ""}`;
  }

  _render() {
    if (!this._built) this._build();
    const roles = this._roleMap;
    const root = this.shadowRoot;
    const hass = this._hass;

    if (roles.light) {
      const st = hass.states[roles.light];
      const on = st && st.state === "on";
      const power = root.querySelector(".power");
      if (power) { power.textContent = on ? "An" : "Aus"; power.classList.toggle("on", on); }
      const inp = root.querySelector("input[data-role=brightness]");
      if (inp && !inp._busy) {
        const pct = on ? Math.round(((st.attributes.brightness ?? 255) / 255) * 100) : 0;
        inp.value = pct;
        this._showVal("brightness", pct);
      }
    }
    root.querySelectorAll("input[type=range]").forEach((inp) => {
      const role = inp.dataset.role;
      if (role === "brightness" || inp._busy) return;
      const n = this._num(roles[role]);
      if (n && n.value !== null) { inp.value = n.value; this._showVal(role, n.value); }
    });
    const gobo = root.querySelector("select[data-role=gobo]");
    if (gobo && hass.states[roles.gobo]) gobo.value = hass.states[roles.gobo].state;

    if (roles.pan || roles.tilt) {
      const pan = this._axis("pan");
      const tilt = this._axis("tilt");
      if (!this._dragging) {
        this._placeDot(pan ? this._toPad("pan", pan.value) : 0.5, tilt ? this._toPad("tilt", tilt.value) : 0.5);
      }
      const fmt = (label, n) => (n && n.value !== null ? `${label} ${Math.round(n.value * 10) / 10}${n.unit ? " " + n.unit : ""}` : "");
      root.querySelector(".pos.pan").textContent = fmt("Pan", pan);
      root.querySelector(".pos.tilt").textContent = fmt("Tilt", tilt);
    }
    const reset = root.querySelector(".reset");
    if (reset && roles.reset) {
      const busy = hass.states[roles.reset]?.attributes.resetting;
      reset.disabled = !!busy;
      reset.textContent = busy ? "Reset läuft …" : "Reset";
    }
  }
}

if (!customElements.get("artnet-fixture-card")) {
  customElements.define("artnet-fixture-card", ArtnetFixtureCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "artnet-fixture-card",
    name: "DMX Fixture (artnet_led)",
    description: "Pan/Tilt-Feld, Helligkeit, Zoom/Fokus, Strobe, Gobo und Reset für eine DMX-Lampe",
  });
  console.info(`%c ARTNET-FIXTURE-CARD %c ${CARD_VERSION} `, "background:#03a9f4;color:#fff", "");
}
