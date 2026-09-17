const params = new URLSearchParams(window.location.search);
const source = params.get("source") || "";
const eventId = params.get("event_id") || "";
const title = document.querySelector("#event-title");
const meta = document.querySelector("#event-meta");
const description = document.querySelector("#event-description");
const contentKind = document.querySelector("#event-content-kind");
const article = document.querySelector("#event-article");
const articleBody = document.querySelector("#event-article-body");
const officialLink = document.querySelector("#official-link");
const languageSelect = document.querySelector("#language-select");
const languageLabel = document.querySelector("#language-label");
const detailsHeading = document.querySelector("#details-heading");
const articleHeading = document.querySelector("#article-heading");
const backLink = document.querySelector("#back-link");
const LANGUAGE_KEY = "macro-status-language-v2";
let currentLanguage = "en";

function value(input, fallback = "—") {
  return input === null || input === undefined || input === "" ? fallback : String(input);
}

function applyLanguage(language) {
  currentLanguage = language === "zh" ? "zh" : "en";
  const zh = currentLanguage === "zh";
  document.documentElement.lang = zh ? "zh-CN" : "en";
  document.title = zh ? "宏观事件详情" : "Macro Event Details";
  languageLabel.textContent = zh ? "语言" : "Language";
  detailsHeading.textContent = zh ? "事件详情" : "Event details";
  articleHeading.textContent = zh ? "文章正文" : "Article text";
  backLink.textContent = zh ? "← 返回宏观事件状态" : "← Back to macro status";
  languageSelect.value = currentLanguage;
  if (typeof localStorage !== "undefined") localStorage.setItem(LANGUAGE_KEY, currentLanguage);
}

function formatTime(event) {
  if (event.scheduled_time_utc) {
    const formatted = new Intl.DateTimeFormat(currentLanguage === "zh" ? "zh-CN" : "en-GB", {
      timeZone: "Asia/Shanghai", dateStyle: "medium", timeStyle: "short",
    }).format(new Date(event.scheduled_time_utc));
    return currentLanguage === "zh" ? `${formatted} 北京时间` : `${formatted} Beijing time`;
  }
  return currentLanguage === "zh"
    ? `${value(event.scheduled_date, "日期未知")}（仅日期）`
    : `${value(event.scheduled_date, "Unknown date")} (date only)`;
}

async function loadEvent() {
  if (!source || !eventId) throw new Error("Event identifier is missing");
  const response = await fetch(`/v1/macro-events/event/${encodeURIComponent(source)}/${encodeURIComponent(eventId)}`, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(value(data.detail, `HTTP ${response.status}`));
  const event = data.event || {};
  title.textContent = value(event.title, event.event_code);
  meta.textContent = `${formatTime(event)} · ${value(event.source)} · ${value(event.status)}`;
  if (event.content_kind === "article" && event.content_available === true) {
    contentKind.textContent = currentLanguage === "zh" ? "有文章摘要" : "Article summary available";
    description.textContent = value(event.description);
    officialLink.textContent = currentLanguage === "zh" ? "查看官方来源" : "Open official article";
  } else {
    contentKind.textContent = currentLanguage === "zh" ? "日程事件 — 此来源没有新闻正文" : "Schedule event — no article body is provided by this source";
    description.textContent = currentLanguage === "zh" ? "此来源只提供发布时间或日程。正式发布后才可能出现新闻正文。" : "This source provides the release or publication schedule only. The full article may appear after the event is officially released.";
    officialLink.textContent = currentLanguage === "zh" ? "查看来源日程" : "Open source schedule";
  }
  if (event.article_available === true && event.article_body) {
    article.hidden = false;
    articleBody.textContent = event.article_body;
  }
  if (/^https?:\/\//i.test(event.official_url || "")) {
    officialLink.href = event.official_url;
    officialLink.hidden = false;
  }
}

loadEvent().catch((error) => {
  title.textContent = currentLanguage === "zh" ? "事件不可用" : "Event unavailable";
  meta.textContent = error.message;
  description.textContent = currentLanguage === "zh" ? "本地近 30 天数据中没有找到该事件。" : "This event is not available in the local 30-day store.";
  contentKind.textContent = currentLanguage === "zh" ? "事件不可用" : "Event unavailable";
});

applyLanguage(typeof localStorage !== "undefined" ? (localStorage.getItem(LANGUAGE_KEY) || "en") : "en");
languageSelect.addEventListener("change", (event) => {
  applyLanguage(event.target.value);
  if (source && eventId) loadEvent();
});
