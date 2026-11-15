// The page for judges and the curious: theme fit, architecture, the design rule and prior work.
const $ = (id) => document.getElementById(id);

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
