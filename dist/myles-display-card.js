const GIF_LIMIT = 16 * 1024;

async function readGif(file) {
  if (file.size > GIF_LIMIT) {
    throw new Error("The GIF must be 16 KiB or smaller.");
  }

  const bytes = new Uint8Array(await file.arrayBuffer());
  const header = String.fromCharCode(...bytes.subarray(0, 6));
  if (bytes.length < 13 || !["GIF87a", "GIF89a"].includes(header)) {
    throw new Error("Choose a GIF file.");
  }

  const width = bytes[6] | (bytes[7] << 8);
  const height = bytes[8] | (bytes[9] << 8);
  if (!width || !height || width > 64 || height > 32) {
    throw new Error("The GIF must fit within 64 × 32 pixels.");
  }
  return { width, height, payload: btoa(String.fromCharCode(...bytes)) };
}

class MylesDisplayCard extends HTMLElement {
  constructor() {
    super();
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        section { padding: 16px; display: grid; gap: 14px; }
        h2, p { margin: 0; }
        h2 { font-size: 1.2rem; font-weight: 600; }
        .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
        label { display: grid; gap: 5px; }
        button, select, input { font: inherit; color: inherit; }
        button, select, input[type="text"] {
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          background: var(--secondary-background-color);
          padding: 8px 12px;
        }
        button { cursor: pointer; }
        button:disabled { opacity: .55; cursor: default; }
        #send-gif { background: var(--primary-color); color: var(--text-primary-color, white); }
        #preview { background: #111; padding: 10px; border-radius: 8px; text-align: center; }
        #preview img { width: min(100%, 256px); height: auto; image-rendering: pixelated; vertical-align: middle; }
        #message { flex: 1; min-width: 120px; }
        #file { max-width: 100%; }
        .hint, #status { color: var(--secondary-text-color); font-size: .875rem; }
        #status.error { color: var(--error-color, #db4437); }
        [hidden] { display: none !important; }
      </style>
      <ha-card>
        <section>
          <h2 id="title"></h2>
          <div class="row">
            <button id="next" type="button">Next mode</button>
            <button id="stop" type="button">Stop GIF</button>
          </div>
          <label id="mode-row" hidden>
            Mode
            <select id="mode"></select>
          </label>
          <label id="brightness-row" hidden>
            <span>Brightness <output id="brightness-value"></output></span>
            <input id="brightness" type="range">
          </label>
          <form id="message-row" class="row" hidden>
            <input id="message" type="text" aria-label="Scrolling message" placeholder="Scrolling message">
            <button id="send-message" type="submit">Show message</button>
          </form>
          <label>
            Choose a GIF
            <input id="file" type="file" accept="image/gif,.gif" aria-describedby="limits">
          </label>
          <p class="hint" id="limits">Up to 64 × 32 pixels and 16 KiB.</p>
          <div id="preview" hidden><img id="image" alt="Preview of selected GIF"></div>
          <div class="row">
            <button id="send-gif" type="button" disabled>Send GIF</button>
            <span class="hint" id="dimensions"></span>
          </div>
          <p id="status" role="status" aria-live="polite"></p>
        </section>
      </ha-card>
    `;

    this.ui = Object.fromEntries([...root.querySelectorAll("[id]")].map(el => [el.id, el]));
    this.selectionVersion = 0;
    this.ui.file.addEventListener("change", () => this.pickGif());
    this.ui.next.addEventListener("click", () => this.publish("command", "next", "Next mode command sent."));
    this.ui.stop.addEventListener("click", () => this.publish("gif/set", "stop", "Stop GIF command sent."));
    this.ui["send-gif"].addEventListener("click", () => {
      if (this.gif) this.publish("gif/set", this.gif.payload, "GIF published. Playback starts when the panel receives it.");
    });
    this.ui.mode.addEventListener("change", () => this.control("mode_entity", "select_option", {
      option: this.ui.mode.value,
    }));
    this.ui.brightness.addEventListener("input", () => {
      this.ui["brightness-value"].textContent = `${this.ui.brightness.value}%`;
    });
    this.ui.brightness.addEventListener("change", () => this.control("brightness_entity", "set_value", {
      value: Number(this.ui.brightness.value),
    }));
    this.ui.brightness.addEventListener("blur", () => this.refresh());
    this.ui["message-row"].addEventListener("submit", async event => {
      event.preventDefault();
      const value = this.ui.message.value.trim();
      if (value && await this.control("message_entity", "set_value", { value }, "Message sent.")) {
        if (this.ui.message.value.trim() === value) this.ui.message.value = "";
      }
    });
  }

  setConfig(config) {
    this.config = { ...config };
    this.topicPrefix = (config.topic_prefix?.trim() || "trafficlight").replace(/\/+$/, "");
    if (!this.topicPrefix || /[+#\u0000]/.test(this.topicPrefix)) {
      throw new Error("Enter a valid MQTT topic prefix.");
    }
    this.ui.title.textContent = config.title?.trim() || "MylesDisplay";
    this.refresh();
  }

  static getStubConfig() {
    return { title: "MylesDisplay", topic_prefix: "trafficlight" };
  }

  static getConfigForm() {
    const labels = {
      title: "Card title",
      topic_prefix: "MQTT topic prefix",
      mode_entity: "Mode selector (optional)",
      brightness_entity: "Brightness control (optional)",
      message_entity: "Scrolling message field (optional)",
    };
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "topic_prefix", selector: { text: {} } },
        { name: "mode_entity", selector: { entity: { filter: { domain: "select" } } } },
        { name: "brightness_entity", selector: { entity: { filter: { domain: "number" } } } },
        { name: "message_entity", selector: { entity: { filter: { domain: "text" } } } },
      ],
      computeLabel: field => labels[field.name],
      computeHelper: field => field.name === "topic_prefix"
        ? "Leave blank to use trafficlight."
        : undefined,
    };
  }

  getCardSize() {
    return 5;
  }

  set hass(hass) {
    this._hass = hass;
    this.refresh();
  }

  refresh() {
    if (!this.config) return;
    const ready = Boolean(this._hass && this._hass.connected !== false);
    this.ui.next.disabled = !ready || this.busy;
    this.ui.stop.disabled = !ready || this.busy;
    this.ui["send-gif"].disabled = !ready || !this.gif || this.busy;
    this.ui.file.disabled = Boolean(this.busy);

    const states = this._hass?.states ?? {};
    const mode = states[this.config.mode_entity];
    const brightness = states[this.config.brightness_entity];
    const message = states[this.config.message_entity];
    const available = entity => entity && !["unknown", "unavailable"].includes(entity.state);
    this.ui["mode-row"].hidden = !this.config.mode_entity;
    this.ui["brightness-row"].hidden = !this.config.brightness_entity;
    this.ui["message-row"].hidden = !this.config.message_entity;
    this.ui.mode.disabled = !ready || !available(mode) || this.busy;
    this.ui.brightness.disabled = !ready || !available(brightness) || this.busy;
    this.ui["send-message"].disabled = !ready || !message || message.state === "unavailable" || this.busy;
    this.ui.message.maxLength = Number(message?.attributes.max) || 255;

    if (mode !== this.renderedMode) {
      this.renderedMode = mode;
      const options = mode?.attributes.options ?? [];
      const placeholder = new Option(mode?.state === "unknown" ? "Waiting for mode" : "Mode unavailable", "");
      placeholder.disabled = true;
      this.ui.mode.replaceChildren(placeholder, ...options.map(option => new Option(option, option)));
      this.ui.mode.value = options.includes(mode?.state) ? mode.state : "";
    }
    if (available(brightness) && this.shadowRoot.activeElement !== this.ui.brightness) {
      this.ui.brightness.min = brightness.attributes.min ?? 1;
      this.ui.brightness.max = brightness.attributes.max ?? 100;
      this.ui.brightness.step = brightness.attributes.step ?? 1;
      this.ui.brightness.value = brightness.state;
      this.ui["brightness-value"].textContent = `${brightness.state}%`;
    } else if (!available(brightness)) {
      this.ui["brightness-value"].textContent = "Unavailable";
    }
  }

  async pickGif() {
    const version = ++this.selectionVersion;
    const file = this.ui.file.files[0];
    this.gif = null;
    this.ui.preview.hidden = true;
    this.ui.image.removeAttribute("src");
    this.ui.dimensions.textContent = "";
    this.status("");
    this.refresh();
    if (!file) return;

    try {
      const gif = await readGif(file);
      if (version !== this.selectionVersion) return;
      this.gif = gif;
      this.ui.image.src = `data:image/gif;base64,${gif.payload}`;
      this.ui.preview.hidden = false;
      this.ui.dimensions.textContent = `${gif.width} × ${gif.height} px`;
    } catch (error) {
      if (version !== this.selectionVersion) return;
      this.ui.file.value = "";
      this.status(error.message, true);
    }
    this.refresh();
  }

  publish(suffix, payload, confirmation) {
    return this.call("mqtt", "publish", {
      topic: `${this.topicPrefix}/${suffix}`,
      payload,
      retain: false,
    }, confirmation);
  }

  control(configKey, service, data, confirmation) {
    const entity = this.config[configKey];
    return this.call(entity.split(".")[0], service, { ...data, entity_id: entity }, confirmation);
  }

  async call(domain, service, data, confirmation = "") {
    if (!this._hass || this._hass.connected === false || this.busy) return false;
    this.busy = true;
    this.status("Sending…");
    this.refresh();
    try {
      await this._hass.callService(domain, service, data);
      this.status(confirmation);
      return true;
    } catch (error) {
      this.status(error?.message ?? String(error), true);
      return false;
    } finally {
      this.busy = false;
      this.refresh();
    }
  }

  status(text, error = false) {
    this.ui.status.textContent = text;
    this.ui.status.classList.toggle("error", error);
  }
}

if (!customElements.get("myles-display-card")) {
  customElements.define("myles-display-card", MylesDisplayCard);
  window.customCards ??= [];
  window.customCards.push({
    type: "myles-display-card",
    name: "MylesDisplay",
    description: "Control the panel and send a GIF over MQTT.",
  });
}
