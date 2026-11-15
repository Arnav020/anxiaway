// The app frame: one menu on every screen, so the check, vitals, practice and record read as one product.
import { el, icon, api } from "/js/store.js";

const TABS = [
  ["home", "Home", "/home", "house"],
  ["check", "Check", "/checkin", "clipboard-check"],
  ["vitals", "Wearable", "/vitals", "watch"],
  ["practice", "Practice", "/practice", "path"],
  ["record", "Record", "/record", "chart-line"],
];
const STATUS_KEY = "toward.device.status";

function tabList(active, className) {
  const nav = el("nav", className);
  nav.setAttribute("aria-label", "Toward");
  TABS.forEach(([id, label, href, glyph]) => {
    const a = el("a", "shell-tab");
    a.href = href;
    if (id === active) a.setAttribute("aria-current", "page");
    a.append(icon(glyph, "i20"), el("span", "", label));
    if (id === "vitals") {
      a.dataset.wearable = "soon";
      a.append(el("em", "tab-badge", "Soon"));
      a.title = "Wearable: integrating soon";
    }
    nav.append(a);
  });
  return nav;
}

export async function deviceStatus() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(STATUS_KEY) || "null");
    if (cached && Date.now() - cached.at < 120000) return cached.value;
  } catch { /* no cache */ }
  const value = await api("device/status");
  try { sessionStorage.setItem(STATUS_KEY, JSON.stringify({ at: Date.now(), value })); } catch { /* optional */ }
  return value;
}

export function mountShell(active) {
  document.body.classList.add("app");
  const bar = el("header", "shell no-print");
  const brand = el("a", "shell-brand");
  // The logo and the labelled link both leave the app for the landing page.
  brand.href = "/";
  brand.title = "Back to the landing page";
  brand.setAttribute("aria-label", "Toward, back to the landing page");
  brand.append(el("span", "brand-dot"), el("span", "title-2", "Toward"));
  const about = el("a", "shell-about body-5");
  about.href = "/";
  about.append(icon("arrow-left", "i20"), el("span", "", "Landing page"));
  bar.append(brand, tabList(active, "shell-tabs"), about);
  const bottom = tabList(active, "shell-bottom no-print");
  document.body.prepend(bar);
  document.body.append(bottom);

  const foot = el("footer", "app-foot no-print");
  const note = el("p", "caption-5 muted", "A student research prototype for anxiety screening and practice support. It doesn't diagnose or treat. If you're struggling, speak to a counsellor or doctor. In India, Tele-MANAS is 14416.");
  const links = el("nav", "app-foot-links caption-4");
  links.setAttribute("aria-label", "More");
  [["About Toward", "/"], ["How it's built", "/about"], ["Privacy", "/privacy"]].forEach(([text, href]) => {
    const a = el("a", "", text);
    a.href = href;
    links.append(a);
  });
  foot.append(note, links);
  (document.querySelector("main") || document.body).after(foot);

  deviceStatus().then((status) => {
    if (!status.configured) return;
    document.querySelectorAll("[data-wearable]").forEach((tab) => {
      tab.dataset.wearable = "live";
      tab.title = "Wearable connected";
      tab.querySelector(".tab-badge").textContent = "Live";
    });
  }).catch(() => {});
}
