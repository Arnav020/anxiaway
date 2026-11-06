// The guides' voices, best available first: Orpheus on Groq, Kokoro in the browser, then the browser's own voice.
const KOKORO_VOICES = { "Asha Rao": "af_heart", "Tom Ellis": "am_fenrir", "Mina Kim": "af_nicole" };
const MAX_SPEED = 1.1; // Seconds of work per second of speech. Slower than this keeps people waiting.
const MAX_WAIT_MS = 5000;

export class Voices {
  constructor() {
    this.ctx = null;
    this.analyser = null;
    this.serverOk = true;
    this.kokoro = "idle";
    this.worker = null;
    this.pending = new Map();
    this.nextId = 1;
    this.current = null;
    this.usedBrowser = false;
    this.onAmp = () => {};
    this.getToken = () => null;
    this.serverFails = 0;
    this.ampTimer = null;
    this.tier = "Browser voice";
  }

  unlock() {
    if (!this.ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      this.ctx = new Ctx();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 512;
      this.analyser.connect(this.ctx.destination);
    }
    this.ctx.resume?.();
  }

  preloadKokoro() {
    if (this.kokoro !== "idle" || !window.Worker) return;
    // The model is large, so never download it on phones or when data is being saved.
    const phone = matchMedia("(max-width: 860px), (pointer: coarse)").matches;
    if (phone || navigator.connection?.saveData) { this.kokoro = "off"; return; }
    this.kokoro = "loading";
    try {
      this.worker = new Worker("/js/tts-worker.js", { type: "module" });
    } catch {
      this.kokoro = "off";
      return;
    }
    const force = new URLSearchParams(location.search).get("voice");
    this.worker.onmessage = ({ data }) => {
      if (data.type === "ready") {
        this.kokoro = data.speed <= MAX_SPEED ? "ready" : "slow";
        if (this.kokoro === "ready") this.tier = "Kokoro voice";
      } else if (data.type === "audio") {
        this.pending.get(data.id)?.(data);
        this.pending.delete(data.id);
      } else if (data.type === "error") {
        if (data.id) { this.pending.get(data.id)?.(null); this.pending.delete(data.id); } else this.kokoro = "off";
      }
    };
    this.worker.onerror = () => { this.kokoro = "off"; };
    this.worker.postMessage({ type: "load", force });
  }

  stop() {
    clearInterval(this.ampTimer);
    if (this.current) { try { this.current.stop(); } catch { /* already finished */ } this.current = null; }
    if (this.usedBrowser && "speechSynthesis" in window) speechSynthesis.cancel();
    this.usedBrowser = false;
    this.onAmp(0);
  }

  _simulateAmp() {
    const start = performance.now();
    clearInterval(this.ampTimer);
    this.ampTimer = setInterval(() => {
      const t = performance.now() - start;
      this.onAmp(Math.abs(Math.sin(t / 95)) * (0.45 + 0.4 * Math.abs(Math.sin(t / 310))));
    }, 40);
  }

  async _play(buffer) {
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.analyser);
    this.current = src;
    const data = new Uint8Array(this.analyser.fftSize);
    clearInterval(this.ampTimer);
    this.ampTimer = setInterval(() => {
      this.analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += ((v - 128) / 128) ** 2;
      this.onAmp(Math.min(1, Math.sqrt(sum / data.length) * 5));
    }, 30);
    await new Promise((resolve) => { src.onended = resolve; src.start(); });
    clearInterval(this.ampTimer);
  }

  async _server(line, member) {
    if (!this.serverOk || !this.ctx) return null;
    try {
      const res = await fetch("/api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: this.getToken(), text: line, member }) });
      if (!res.ok) throw new Error("voice");
      this.serverFails = 0;
      this.tier = "Orpheus voice";
      return await this.ctx.decodeAudioData(await res.arrayBuffer());
    } catch {
      // One failure may be a blip. Two in a row, and the on device voice gets ready instead.
      this.serverFails += 1;
      if (this.serverFails >= 2) this.serverOk = false;
      this.preloadKokoro();
      return null;
    }
  }

  _kokoro(line, member) {
    if (this.kokoro !== "ready" || !this.ctx) return Promise.resolve(null);
    const id = this.nextId++;
    return new Promise((resolve) => {
      const timer = setTimeout(() => { this.pending.delete(id); resolve(null); }, MAX_WAIT_MS);
      this.pending.set(id, (data) => {
        clearTimeout(timer);
        if (!data) return resolve(null);
        const buffer = this.ctx.createBuffer(1, data.samples.length, data.rate);
        buffer.copyToChannel(data.samples, 0);
        resolve(buffer);
      });
      this.worker.postMessage({ type: "speak", id, text: line, voice: KOKORO_VOICES[member] || "af_heart" });
    });
  }

  _browser(line, member, sound) {
    const estimate = line.split(" ").length * 340;
    this._simulateAmp();
    return new Promise((resolve) => {
      if (!sound || !("speechSynthesis" in window)) { setTimeout(resolve, estimate); return; }
      const u = new SpeechSynthesisUtterance(line);
      const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
      const natural = voices.filter((v) => /natural|neural|online|google/i.test(v.name));
      const pool = natural.length ? natural : voices;
      const index = ["Asha Rao", "Tom Ellis", "Mina Kim"].indexOf(member);
      if (pool.length) u.voice = pool[Math.max(0, index) % pool.length];
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      u.onend = finish;
      u.onerror = finish;
      this.usedBrowser = true;
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
      setTimeout(finish, estimate + 5000);
    });
  }

  /** Speak one line. isCurrent() lets a newer line or a stop cancel this one. */
  async speak(line, member, { sound, isCurrent }) {
    if (sound) {
      const buffer = (await this._server(line, member)) || (await this._kokoro(line, member));
      if (!isCurrent()) return;
      if (buffer) { await this._play(buffer); return; }
    }
    await this._browser(line, member, sound);
  }
}
