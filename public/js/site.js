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
const parts = tagline.innerHTML.split("<br>");
tagline.innerHTML = parts
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

/* The rule chart: same arousal, different response */
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
  toward: "Arousal rises, and the challenge holds. Stopping stays one tap away the whole time.",
  typical: "Arousal rises, so the system eases off. The person gets through by the challenge shrinking.",
};
function setRule(name) {
  $("rule-line").setAttribute("d", path(lines[name]));
  $("rule-text").textContent = copy[name];
  $("rule-toward").classList.toggle("is-on", name === "toward");
  $("rule-typical").classList.toggle("is-on", name === "typical");
  $("rule-toward").setAttribute("aria-pressed", String(name === "toward"));
  $("rule-typical").setAttribute("aria-pressed", String(name === "typical"));
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

/* The hero panel */
const LINES = [
  ["Prof Ellis", "Walk us through the main decision you made, and why you made it.", "follow_up", 2],
  ["Ms Kim", "What is the weakest part of your work? Be specific.", "probe_assumption", 3],
  ["Dr Rao", "I'm not convinced yet. How would you defend that assumption?", "polite_challenge", 4],
  ["Ms Kim", "Take your time.", "hold_silence", 4],
];
const stage = $("hero-stage");
{
  const room = new Panel($("hero-panel"));
  const copy = stage.querySelector(".hero-copy");
  const fit = () => room.setLayout({ top: copy.getBoundingClientRect().bottom - stage.getBoundingClientRect().top + 16, bottom: stage.querySelector(".hero-caption").offsetHeight + 32 });
  fit();
  addEventListener("resize", fit);
  document.fonts?.ready.then(fit);
  let index = 0;
  const caption = stage.querySelector(".hero-caption");
  const play = () => {
    const [who, line, tactic, level] = LINES[index % LINES.length];
    index += 1;
    caption.style.opacity = 0;
    setTimeout(() => {
      $("hero-speaker").textContent = who;
      $("hero-line").textContent = line;
      caption.style.opacity = 1;
      room.setSpeaker(who);
      room.setTactic(tactic);
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
  setTimeout(play, 1200);
}
