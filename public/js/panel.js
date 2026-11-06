// The guides: three AI generated portraits that respond to the live session.
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const PEOPLE = [
  { name: "Asha Rao", role: "AI guide", src: "/img/rao.jpg", alt: "Asha Rao, an AI generated guide: a woman in her forties in a charcoal blazer, listening calmly." },
  { name: "Tom Ellis", role: "AI guide", src: "/img/ellis.jpg", alt: "Tom Ellis, an AI generated guide: a man in his sixties with silver hair and thin glasses, listening calmly." },
  { name: "Mina Kim", role: "AI guide", src: "/img/kim.jpg", alt: "Mina Kim, an AI generated guide: a woman in her thirties with a black bob, listening calmly." },
];
const damp = (a, b, k) => a + (b - a) * k;

export class Panel {
  constructor(root) {
    this.root = root;
    this.amp = 0;
    this.shown = 0;
    this.speaker = null;
    this.look = { x: 0, y: 0, tx: 0, ty: 0 };
    root.classList.add("panel");
    this.tiles = PEOPLE.map((person, i) => {
      const fig = document.createElement("figure");
      fig.className = "tile";
      fig.dataset.member = person.name;
      fig.style.setProperty("--breathe", `${7 + i * 1.3}s`);
      fig.style.animationDelay = `${i * 120}ms`;
      const frame = document.createElement("div");
      frame.className = "tile-frame";
      const img = document.createElement("img");
      img.src = person.src;
      img.alt = person.alt;
      img.draggable = false;
      img.decoding = "async";
      frame.append(img);
      const cap = document.createElement("figcaption");
      const name = document.createElement("strong");
      name.textContent = person.name;
      const role = document.createElement("span");
      role.textContent = person.role;
      const bars = document.createElement("span");
      bars.className = "bars";
      bars.setAttribute("aria-hidden", "true");
      const barEls = [0, 1, 2, 3, 4].map(() => bars.appendChild(document.createElement("i")));
      const who = document.createElement("span");
      who.className = "tile-who";
      who.append(name, role);
      cap.append(who, bars);
      fig.append(frame, cap);
      root.append(fig);
      return { fig, img, barEls, person };
    });
    if (!reduced) {
      addEventListener("pointermove", (e) => {
        this.look.tx = (e.clientX / innerWidth - 0.5) * 2;
        this.look.ty = (e.clientY / innerHeight - 0.5) * 2;
      }, { passive: true });
    }
    this.running = true;
    this._loop();
  }

  _loop() {
    if (!this.running) return;
    this.raf = requestAnimationFrame(() => this._loop());
    this.look.x = damp(this.look.x, this.look.tx, 0.05);
    this.look.y = damp(this.look.y, this.look.ty, 0.05);
    this.shown = damp(this.shown, this.amp, 0.35);
    const t = performance.now() / 1000;
    this.tiles.forEach((tile, i) => {
      const speaking = tile.person.name === this.speaker;
      // Slight parallax, plus a little movement while speaking, so the portraits feel present.
      const lift = speaking ? this.shown * -3 : 0;
      tile.img.style.setProperty("--px", `${(this.look.x * (3 + i)).toFixed(2)}px`);
      tile.img.style.setProperty("--py", `${(this.look.y * 3 + lift).toFixed(2)}px`);
      tile.barEls.forEach((bar, b) => {
        const wave = speaking ? 0.2 + this.shown * (0.55 + 0.45 * Math.abs(Math.sin(t * 9 + b * 1.4))) : 0.12;
        bar.style.transform = `scaleY(${wave.toFixed(3)})`;
      });
    });
  }

  setLayout({ top, bottom, right = 0 }) {
    this.root.style.setProperty("--top", `${Math.round(top)}px`);
    this.root.style.setProperty("--bottom", `${Math.round(bottom)}px`);
    this.root.style.setProperty("--right", `${Math.round(right)}px`);
  }
  setSpeaker(name) {
    this.speaker = name;
    if (!name) this.amp = 0;
    this.root.classList.toggle("has-speaker", !!name);
    this.tiles.forEach((tile) => tile.fig.classList.toggle("speaking", tile.person.name === name));
  }
  setAmp(v) { this.amp = Math.max(0, Math.min(1, v)); }
  setPressure(level) { this.root.dataset.pressure = String(level); }
  setTactic(t) { this.root.classList.toggle("silence", t === "hold_silence"); }
  setCalm(on) { this.root.classList.toggle("calm", on); }
  setShift() {}
  stop() { this.running = false; cancelAnimationFrame(this.raf); }
}
