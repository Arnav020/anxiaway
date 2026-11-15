import { openConnect } from "/js/connect.js";
import { wirePhotos } from "/js/lightbox.js";
import { gauge } from "/js/ui.js";

const $ = (id) => document.getElementById(id);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

$("connect-open")?.addEventListener("click", openConnect);
wirePhotos();

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
// Anything at or above the fold is shown, so a jump down the page never leaves a section blank.
let waiting = [...document.querySelectorAll(".reveal")];
let revealQueued = false;
function reveal() {
  revealQueued = false;
  const edge = innerHeight * 0.92;
  waiting = waiting.filter((node) => {
    if (node.getBoundingClientRect().top > edge) return true;
    node.classList.add("in");
    return false;
  });
}
const queueReveal = () => { if (!revealQueued && waiting.length) { revealQueued = true; requestAnimationFrame(reveal); } };
addEventListener("scroll", queueReveal, { passive: true });
addEventListener("resize", queueReveal);
reveal();

/* Tagline: words light up one at a time */
const tagline = $("tagline");
if (tagline) {
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
}

/* A heartbeat shape, shared by the hero background and the preview card */
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
function fitCanvas(canvas, box) {
  const g = canvas.getContext("2d");
  const size = () => {
    const rect = box.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio, 2);
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  size();
  addEventListener("resize", size);
  return g;
}

/* Hero: body signals drifting behind the page */
const stage = $("hero-stage");
if ($("hero-trace")) {
  const g = fitCanvas($("hero-trace"), stage);
  const draw = (t) => {
    const w = stage.clientWidth, h = stage.clientHeight;
    g.clearRect(0, 0, w, h);
    const shift = t * 0.05;
    const lines = [
      { y: h * 0.17, color: "rgba(228,220,203,.10)", f: (x) => ecg(x + shift) * 0.9 },
      { y: h * 0.33, color: "rgba(163,177,165,.10)", f: (x) => Math.sin((x + shift) / 34) * 12 + Math.sin((x + shift) / 13) * 3 },
      { y: h * 0.47, color: "rgba(211,160,111,.09)", f: (x) => Math.sin((x + shift * 0.4) / 120) * 16 + Math.sin((x + shift * 0.4) / 37) * 4 },
    ];
    g.lineWidth = 1.5;
    for (const line of lines) {
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

/* Hero: the app preview, with sample data */
if ($("hp-gauge")) gauge($("hp-gauge"), 6, { light: true });
if ($("hp-trace")) {
  const box = $("hp-trace").parentElement;
  const g = fitCanvas($("hp-trace"), box);
  let last = 0;
  const draw = (t) => {
    const w = box.clientWidth, h = box.clientHeight;
    g.clearRect(0, 0, w, h);
    g.strokeStyle = "#A8553D";
    g.lineWidth = 2.5;
    g.lineJoin = "round";
    g.lineCap = "round";
    g.beginPath();
    const shift = t * 0.09;
    for (let x = 0; x <= w; x += 2) {
      const y = h * 0.62 + ecg((x + shift) * 1.25) * (h / 110);
      if (x) g.lineTo(x, y); else g.moveTo(x, y);
    }
    g.stroke();
    if (t - last > 1400) {
      last = t;
      $("hp-bpm").textContent = String(77 + Math.round(Math.sin(t / 5200) * 2 + Math.sin(t / 1700)));
    }
    if (!reduced) requestAnimationFrame(draw);
  };
  requestAnimationFrame(draw);
}

/* Tour: plays by itself, and stops the moment someone takes over */
const tour = $("tour");
if (tour) {
  const tabs = [...tour.querySelectorAll(".tour-tab")];
  const panes = [...tour.querySelectorAll(".tour-pane")];
  const STEP_MS = 6000;
  let index = 0;
  let timer = null;
  tour.style.setProperty("--tour-ms", `${STEP_MS}ms`);
  const show = (i) => {
    index = (i + tabs.length) % tabs.length;
    tabs.forEach((tab, n) => {
      tab.classList.toggle("is-on", n === index);
      tab.setAttribute("aria-selected", String(n === index));
    });
    panes.forEach((pane) => pane.classList.toggle("is-on", pane.dataset.k === tabs[index].dataset.k));
    if (innerWidth <= 960) tabs[index].scrollIntoView({ block: "nearest", inline: "center", behavior: reduced ? "auto" : "smooth" });
  };
  const stop = () => { clearInterval(timer); timer = null; tour.classList.remove("playing"); };
  const play = () => {
    if (reduced || timer) return;
    tour.classList.add("playing");
    timer = setInterval(() => show(index + 1), STEP_MS);
  };
  tabs.forEach((tab, n) => tab.addEventListener("click", () => { stop(); show(n); }));
  tour.addEventListener("pointerenter", stop);
  tour.addEventListener("pointerleave", () => { if (seen) play(); });
  tour.addEventListener("focusin", stop);
  let seen = false;
  new IntersectionObserver(([entry]) => {
    seen = entry.isIntersecting;
    if (seen) play(); else stop();
  }, { threshold: 0.35 }).observe(tour);
  // Small live numbers in the wearable pane
  setInterval(() => {
    if (!$("tv-hr").offsetParent) return;
    const t = performance.now();
    $("tv-hr").textContent = String(78 + Math.round(Math.sin(t / 2100) * 2));
    $("tv-eda").textContent = (4.02 + Math.sin(t / 3300) * 0.04).toFixed(2);
  }, 900);
}

/* The check: change the body reading, and read what fixed code would say */
const cmp = $("cmp");
if (cmp) {
  const CASES = {
    calm: ["Near your baseline", "Compared with your own resting reading, never with other people.", "Anxiety-like: low", "They point the same way", "Your answers and your body reading point the same way this time."],
    raised: ["Well above your baseline", "Heart rate stayed well above your own resting reading.", "Anxiety-like: high", "They differ, and that's common", "Your answers were in the mild range, while your body reading was well above your baseline. They often differ. A raised reading can also come from caffeine, exercise, illness or a warm room."],
    moving: ["Not trusted", "Movement was detected, so heart rate can't be read fairly.", "No trusted reading", "Reading not used", "The body reading wasn't trusted, because of movement or a weak signal, so it isn't used here."],
  };
  cmp.querySelectorAll(".pill").forEach((pill) => pill.addEventListener("click", () => {
    const state = pill.dataset.s;
    const [body, note, model, kind, text] = CASES[state];
    cmp.dataset.state = state;
    cmp.querySelectorAll(".pill").forEach((p) => {
      p.classList.toggle("is-on", p === pill);
      p.setAttribute("aria-pressed", String(p === pill));
    });
    $("cmp-body").textContent = body;
    $("cmp-body-note").textContent = note;
    $("cmp-model").textContent = model;
    $("cmp-kind").textContent = kind;
    const line = $("cmp-text");
    line.textContent = text;
    line.style.animation = "none";
    void line.offsetWidth;
    line.style.animation = "";
  }));
}

/* The agent's loop: cycles until someone picks a step */
const loop = $("loop");
if (loop) {
  const nodes = [...loop.querySelectorAll(".loop-node")];
  const dot = loop.querySelector(".loop-dot");
  let at = 0;
  let cycling = !reduced;
  const pick = (i) => {
    at = i % nodes.length;
    nodes.forEach((node, n) => {
      node.classList.toggle("is-on", n === at);
      node.setAttribute("aria-selected", String(n === at));
    });
    dot.style.setProperty("--i", at);
    $("loop-text").textContent = nodes[at].dataset.t;
  };
  nodes.forEach((node, n) => node.addEventListener("click", () => { cycling = false; pick(n); }));
  setInterval(() => { if (cycling && loop.getBoundingClientRect().top < innerHeight && !document.hidden) pick(at + 1); }, 3200);
}

/* Numbers count up once, when they come into view */
const counters = [...document.querySelectorAll("[data-count]")];
if (counters.length && !reduced) {
  const run = (node) => {
    const goal = Number(node.dataset.count);
    const decimals = (node.dataset.count.split(".")[1] || "").length;
    const start = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - start) / 1300);
      node.textContent = `${(goal * (1 - (1 - k) ** 3)).toFixed(decimals)}${node.dataset.suffix || ""}`;
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
  const watcher = new IntersectionObserver((entries) => entries.forEach((entry) => {
    if (!entry.isIntersecting) return;
    watcher.unobserve(entry.target);
    run(entry.target);
  }), { threshold: 0.6 });
  counters.forEach((node) => watcher.observe(node));
}
