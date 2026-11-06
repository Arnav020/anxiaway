import { Panel } from "/js/panel.js";

const $ = (id) => document.getElementById(id);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Mobile menu */
const menuBtn = $("menu-btn");
const menu = $("menu");
function setMenu(open) {
  menu.hidden = !open;
  menuBtn.setAttribute("aria-expanded", String(open));
  menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  document.body.style.overflow = open ? "hidden" : "";
}
menuBtn.addEventListener("click", () => setMenu(menu.hidden));
menu.addEventListener("click", (e) => { if (e.target.closest("a")) setMenu(false); });

/* Scroll reveals, each once */
const revealer = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) {
      entry.target.classList.add("in");
      revealer.unobserve(entry.target);
    }
  });
}, { rootMargin: "0px 0px -8% 0px" });
document.querySelectorAll(".reveal").forEach((el) => revealer.observe(el));

/* Tagline: words light up one at a time */
const tagline = $("tagline");
tagline.innerHTML = tagline.innerHTML.split("<br>")
  .map((part) => part.trim().split(/\s+/).map((w) => `<span class="w">${w}</span>`).join(" "))
  .join("<br>");
const words = [...tagline.querySelectorAll(".w")];
if (reduced) words.forEach((w) => w.classList.add("on"));
else {
  let pending = false;
  const paint = () => {
    pending = false;
    const box = tagline.getBoundingClientRect();
    const progress = (innerHeight * 0.85 - box.top) / (box.height + innerHeight * 0.35);
    const lit = Math.floor(progress * words.length);
    words.forEach((w, i) => w.classList.toggle("on", i < lit));
  };
  addEventListener("scroll", () => {
    if (!pending) { pending = true; requestAnimationFrame(paint); }
  }, { passive: true });
  paint();
}

/* The rule chart */
const svg = $("rule-svg");
const X0 = 48, X1 = 500, Y0 = 256, Y1 = 24;
const px = (x) => X0 + (x / 100) * (X1 - X0);
const py = (y) => Y0 - (y / 100) * (Y0 - Y1);
const path = (pts) => pts.map(([x, y], i) => `${i ? "L" : "M"}${px(x).toFixed(1)} ${py(y).toFixed(1)}`).join("");
const arousal = [[0, 18], [15, 22], [30, 44], [45, 66], [60, 74], [75, 70], [90, 60], [100, 56]];
const smooth = (pts) => {
  let d = `M${px(pts[0][0])} ${py(pts[0][1])}`;
  for (let i = 1; i < pts.length; i++) {
    const [ax, ay] = pts[i - 1], [bx, by] = pts[i];
    const mx = (px(ax) + px(bx)) / 2;
    d += `C${mx} ${py(ay)} ${mx} ${py(by)} ${px(bx)} ${py(by)}`;
  }
  return d;
};
const lines = {
  toward: [[0, 30], [20, 30], [20, 50], [100, 50]],
  typical: [[0, 30], [20, 30], [20, 50], [38, 50], [38, 34], [55, 34], [55, 20], [100, 20]],
};
svg.innerHTML = `
  <line x1="${X0}" x2="${X1}" y1="${Y0}" y2="${Y0}" stroke="#D5D2C8" stroke-width="1"/>
  <line x1="${X0}" x2="${X0}" y1="${Y1}" y2="${Y0}" stroke="#D5D2C8" stroke-width="1"/>
  <text x="${X0}" y="${Y0 + 20}">Start of session</text>
  <text x="${X1}" y="${Y0 + 20}" text-anchor="end">End</text>
  <text x="${X0 - 8}" y="${Y1 + 8}" text-anchor="end">High</text>
  <text x="${X0 - 8}" y="${Y0}" text-anchor="end">Low</text>
  <path d="${smooth(arousal)}" fill="none" stroke="#A8553D" stroke-width="3" stroke-linecap="round"/>
  <path id="rule-line" d="${path(lines.toward)}" fill="none" stroke="#2F3E52" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
const copy = {
  toward: "Anxiety-like arousal rises, and the challenge holds. Stopping stays one tap away the whole time.",
  typical: "Arousal rises, so the system eases off. The person gets through because the challenge shrinks.",
};
function setRule(name) {
  $("rule-line").setAttribute("d", path(lines[name]));
  $("rule-text").textContent = copy[name];
  for (const key of ["toward", "typical"]) {
    $(`rule-${key}`).classList.toggle("is-on", key === name);
    $(`rule-${key}`).setAttribute("aria-pressed", String(key === name));
  }
}
$("rule-toward").addEventListener("click", () => setRule("toward"));
$("rule-typical").addEventListener("click", () => setRule("typical"));

/* Architecture: tap a node to read it */
const nodes = [...document.querySelectorAll(".node")];
nodes.forEach((node) => {
  const pick = () => {
    nodes.forEach((n) => n.classList.remove("is-on"));
    node.classList.add("is-on");
    $("arch-text").textContent = node.dataset.t;
  };
  node.addEventListener("click", pick);
  node.addEventListener("pointerenter", pick);
});

/* Hero: live looking body signals drifting behind the guides */
const trace = $("hero-trace");
const stage = $("hero-stage");
if (trace) {
  const g = trace.getContext("2d");
  const ecg = (x) => {
    const p = ((x % 240) + 240) % 240;
    if (p < 8) return -6 * Math.sin((p / 8) * Math.PI);
    if (p > 20 && p < 26) return 8 * ((p - 20) / 6);
    if (p >= 26 && p < 31) return 8 - 46 * ((p - 26) / 5);
    if (p >= 31 && p < 37) return -38 + 54 * ((p - 31) / 6);
    if (p >= 37 && p < 43) return 16 - 16 * ((p - 37) / 6);
    if (p > 70 && p < 100) return -9 * Math.sin(((p - 70) / 30) * Math.PI);
    return 0;
  };
  const size = () => {
    const box = stage.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio, 2);
    trace.width = box.width * dpr;
    trace.height = box.height * dpr;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  size();
  addEventListener("resize", size);
  const draw = (t) => {
    const w = stage.clientWidth, h = stage.clientHeight;
    g.clearRect(0, 0, w, h);
    const shift = t * 0.05;
    const lines_ = [
      { y: h * 0.17, color: "rgba(228,220,203,.10)", f: (x) => ecg(x + shift) * 0.9 },
      { y: h * 0.33, color: "rgba(163,177,165,.10)", f: (x) => Math.sin((x + shift) / 34) * 12 + Math.sin((x + shift) / 13) * 3 },
      { y: h * 0.47, color: "rgba(211,160,111,.09)", f: (x) => Math.sin((x + shift * 0.4) / 120) * 16 + Math.sin((x + shift * 0.4) / 37) * 4 },
    ];
    g.lineWidth = 1.5;
    for (const line of lines_) {
      g.strokeStyle = line.color;
      g.beginPath();
      for (let x = 0; x <= w; x += 2) {
        const y = line.y + line.f(x);
        if (x) g.lineTo(x, y); else g.moveTo(x, y);
      }
      g.stroke();
    }
    if (!reduced) requestAnimationFrame(draw);
  };
  requestAnimationFrame(draw);
}

/* The guides */
const LINES = [
  ["Tom Ellis", "Stay with it for a moment. What is your anxiety telling you right now?", "moderate", 2],
  ["Mina Kim", "What do you expect will happen next?", "moderate", 3],
  ["Asha Rao", "Suppose it starts to happen. What is your next step?", "high", 3],
];
{
  const room = new Panel($("hero-panel"));
  const copyEl = stage.querySelector(".hero-copy");
  const caption = stage.querySelector(".hero-caption");
  const fit = () => room.setLayout({ top: copyEl.getBoundingClientRect().bottom - stage.getBoundingClientRect().top + 16, bottom: caption.offsetHeight + 32 });
  fit();
  addEventListener("resize", fit);
  document.fonts?.ready.then(fit);
  let index = 0;
  const play = () => {
    const [who, line, verdict] = LINES[index % LINES.length];
    index += 1;
    caption.style.opacity = 0;
    setTimeout(() => {
      $("hero-speaker").textContent = who;
      $("hero-line").textContent = line;
      $("hero-verdict").textContent = `Anxiety-like: ${verdict}`;
      $("hero-verdict").dataset.v = verdict;
      caption.style.opacity = 1;
      room.setSpeaker(who);
    }, 300);
    const start = performance.now();
    const talk = line.length * 62;
    const mouth = () => {
      const t = performance.now() - start - 300;
      if (t > talk) {
        room.setAmp(0);
        room.setSpeaker(null);
        setTimeout(play, 1500);
        return;
      }
      room.setAmp(t < 0 ? 0 : Math.abs(Math.sin(t / 95)) * (0.45 + 0.4 * Math.abs(Math.sin(t / 310))));
      setTimeout(mouth, 40);
    };
    mouth();
  };
  setTimeout(play, 1000);
}
