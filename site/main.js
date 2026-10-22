// AppLiveCaption website: theme switch, tabs, scroll-in motion, the caption
// demo, and the download links of the latest GitHub release.
"use strict";

const REPO = "EnderDragonEP/AppLiveCaption";
const root = document.documentElement;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

// -- Theme ---------------------------------------------------------------------------
// Follows the OS until the button is used; the choice is remembered.

const themeButton = document.querySelector(".theme-toggle");

function currentTheme() {
  return root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function updateThemeButton() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  themeButton.setAttribute("aria-label", `Switch to ${next} theme`);
  themeButton.title = `Switch to ${next} theme`;
}

themeButton.addEventListener("click", () => {
  const theme = currentTheme() === "dark" ? "light" : "dark";
  root.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch (e) { /* private mode: not remembered */ }
  updateThemeButton();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateThemeButton);
updateThemeButton();

// -- Navigation bar: a hairline once the page scrolls ----------------------------------

const nav = document.querySelector(".nav");
const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 8);
window.addEventListener("scroll", onScroll, { passive: true });
onScroll();

// -- Scroll-in motion ----------------------------------------------------------------

const revealed = document.querySelectorAll(".reveal");
if ("IntersectionObserver" in window) {
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add("visible");
        observer.unobserve(entry.target);
      }
    }
  }, { rootMargin: "0px 0px -8% 0px" });
  revealed.forEach((el) => observer.observe(el));
} else {
  revealed.forEach((el) => el.classList.add("visible"));
}

// -- Tabs (WAI-ARIA tabs: arrow keys, Home and End move between them) -------------------

const tabs = [...document.querySelectorAll('[role="tab"]')];

function selectTab(tab, focus) {
  for (const t of tabs) {
    const selected = t === tab;
    t.setAttribute("aria-selected", String(selected));
    t.tabIndex = selected ? 0 : -1;
    document.getElementById(t.getAttribute("aria-controls")).hidden = !selected;
  }
  if (focus) tab.focus();
}

tabs.forEach((tab, i) => {
  tab.addEventListener("click", () => selectTab(tab, false));
  tab.addEventListener("keydown", (e) => {
    const moves = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 };
    if (!(e.key in moves)) return;
    e.preventDefault();
    selectTab(tabs[(moves[e.key] + tabs.length) % tabs.length], true);
  });
});

// -- Caption demo: words appear in gray while "someone speaks", then the line turns final.

const SENTENCES = [
  "Hi everyone, and welcome back to the channel!",
  "Today we're building a tiny weather station.",
  "It measures temperature, humidity and air pressure.",
  "今天我們要帶大家逛台灣最熱鬧的夜市！",
  "第一站是基隆廟口，這裡的鼎邊趖真的超好吃。",
  "Captions show up while people are still talking.",
];

const finalLine = document.querySelector("[data-demo-final]");
const partialLine = document.querySelector("[data-demo-partial]");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Words for English; two or three characters at a time for Chinese.
function chunks(sentence) {
  if (/[㐀-鿿]/.test(sentence)) return sentence.match(/.{1,3}/gu);
  return sentence.split(/(?<= )/);
}

async function runDemo() {
  let i = 1;
  while (true) {
    const sentence = SENTENCES[i % SENTENCES.length];
    let text = "";
    partialLine.textContent = "";
    for (const piece of chunks(sentence)) {
      text += piece;
      partialLine.textContent = text;
      await sleep(140 + Math.random() * 160);
    }
    await sleep(700);
    finalLine.textContent = sentence;
    partialLine.textContent = "";
    await sleep(500);
    i += 1;
  }
}

if (finalLine && partialLine && !reducedMotion.matches) runDemo();

// -- Download links: point straight at the latest release's installer (or else its zip) ---------

async function loadRelease() {
  const response = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
    headers: { Accept: "application/vnd.github+json" },
  });
  if (!response.ok) return;
  const release = await response.json();
  const version = String(release.tag_name || "").replace(/^v\.?/i, "");
  const assets = release.assets || [];
  const file = assets.find((a) => a.name.toLowerCase().endsWith("-setup.exe"))
    || assets.find((a) => a.name.toLowerCase().endsWith(".zip"));
  if (version) document.querySelectorAll("[data-version]").forEach((el) => { el.textContent = version; });
  if (!file) return;
  const size = `${Math.round(file.size / 2 ** 20)} MB`;
  document.querySelectorAll("a[data-download]").forEach((a) => { a.href = file.browser_download_url; });
  document.querySelectorAll("[data-download-label]").forEach((el) => {
    el.textContent = version ? `Download v${version} · ${size}` : `Download · ${size}`;
  });
}

loadRelease().catch(() => { /* offline or rate-limited: the links keep pointing at the releases page */ });
