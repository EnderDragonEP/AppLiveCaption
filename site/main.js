// AppLiveCaption website: theme switch, tabs, scroll-in motion, the caption
// demo, and the download links of the latest GitHub release.
"use strict";

const REPO = "EnderDragonEP/AppLiveCaption";
const root = document.documentElement;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

// -- Text this script writes, per page language (the page's own text is translated by
//    tools/build_site.py from site/i18n/) ------------------------------------------------

const TEXT = {
  en: {
    switchTo: (theme) => `Switch to ${theme} theme`,
    light: "light",
    dark: "dark",
    download: (version, size) => (version ? `Download v${version} · ${size}` : `Download · ${size}`),
    portable: (size) => `Get the portable zip · ${size}`,
    demo: [
      "Hi everyone, and welcome back to the channel!",
      "Today we're building a tiny weather station.",
      "It measures temperature, humidity and air pressure.",
      "今天我們要帶大家逛台灣最熱鬧的夜市！",
      "第一站是基隆廟口，這裡的鼎邊趖真的超好吃。",
      "Captions show up while people are still talking.",
    ],
  },
  "zh-Hant": {
    switchTo: (theme) => `切換為${theme}主題`,
    light: "淺色",
    dark: "深色",
    download: (version, size) => (version ? `下載 v${version} · ${size}` : `下載 · ${size}`),
    portable: (size) => `下載免安裝版 · ${size}`,
    demo: [
      "大家好，歡迎回到我的頻道！",
      "今天我們要做一個小小的氣象站。",
      "它可以測量溫度、濕度和氣壓。",
      "接下來帶大家逛最熱鬧的夜市！",
      "第一站是基隆廟口，這裡的鼎邊趖真的超好吃。",
      "大家說話的同時，字幕就會出現。",
    ],
  },
  "zh-Hans": {
    switchTo: (theme) => `切换为${theme}主题`,
    light: "浅色",
    dark: "深色",
    download: (version, size) => (version ? `下载 v${version} · ${size}` : `下载 · ${size}`),
    portable: (size) => `下载便携版 · ${size}`,
    demo: [
      "大家好，欢迎回到我的频道！",
      "今天我们要做一个小小的气象站。",
      "它可以测量温度、湿度和气压。",
      "接下来带大家逛最热闹的夜市！",
      "第一站是基隆庙口，这里的鼎边趖真的超好吃。",
      "大家说话的同时，字幕就会出现。",
    ],
  },
};
const T = TEXT[root.lang] || TEXT.en;

// -- Theme ---------------------------------------------------------------------------
// Follows the OS until the button is used; the choice is remembered.

const themeButton = document.querySelector(".theme-toggle");

function currentTheme() {
  return root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function updateThemeButton() {
  const next = currentTheme() === "dark" ? T.light : T.dark;
  themeButton.setAttribute("aria-label", T.switchTo(next));
  themeButton.title = T.switchTo(next);
}

themeButton.addEventListener("click", () => {
  const theme = currentTheme() === "dark" ? "light" : "dark";
  root.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch (e) { /* private mode: not remembered */ }
  updateThemeButton();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateThemeButton);
updateThemeButton();

// -- Language menu: remember the choice (the English page goes there next time), and close
//    the menu on a click elsewhere or Escape --------------------------------------------

const langMenu = document.querySelector(".lang-menu");
for (const link of document.querySelectorAll("[data-lang]")) {
  link.addEventListener("click", () => {
    try { localStorage.setItem("lang", link.dataset.lang); } catch (e) { /* not remembered */ }
  });
}
document.addEventListener("click", (e) => { if (langMenu.open && !langMenu.contains(e.target)) langMenu.open = false; });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && langMenu.open) {
    langMenu.open = false;
    langMenu.querySelector("summary").focus();
  }
});

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

const SENTENCES = T.demo;

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

// -- Download links: point straight at the latest release's installer, and its portable zip -------

const megabytes = (asset) => `${Math.round(asset.size / 2 ** 20)} MB`;

async function loadRelease() {
  const response = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
    headers: { Accept: "application/vnd.github+json" },
  });
  if (!response.ok) return;
  const release = await response.json();
  const version = String(release.tag_name || "").replace(/^v\.?/i, "");
  const assets = release.assets || [];
  const setup = assets.find((a) => a.name.toLowerCase().endsWith("-setup.exe"));
  const zip = assets.find((a) => a.name.toLowerCase().endsWith(".zip"));
  const file = setup || zip;  // the main buttons: the installer, or the zip for a release without one
  if (version) document.querySelectorAll("[data-version]").forEach((el) => { el.textContent = version; });
  if (!file) return;
  document.querySelectorAll("a[data-download]").forEach((a) => { a.href = file.browser_download_url; });
  document.querySelectorAll("[data-download-label]").forEach((el) => {
    el.textContent = T.download(version, megabytes(file));
  });
  if (setup && zip) {
    document.querySelectorAll("a[data-portable-link]").forEach((a) => { a.href = zip.browser_download_url; });
    document.querySelectorAll("[data-portable-label]").forEach((el) => {
      el.textContent = T.portable(megabytes(zip));
    });
  } else {
    // The main buttons already download the zip (or the release has no files): no second link needed.
    document.querySelectorAll("[data-portable]").forEach((el) => { el.hidden = true; });
  }
}

loadRelease().catch(() => { /* offline or rate-limited: the links keep pointing at the releases page */ });
