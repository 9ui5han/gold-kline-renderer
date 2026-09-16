const checkButton = document.querySelector("#check-button");
const message = document.querySelector("#message");
const overallBadge = document.querySelector("#overall-badge");
const overallText = document.querySelector("#overall-text");
const languageSelect = document.querySelector("#language-select");

const LANGUAGE_KEY = "macro-status-language";
const LANGUAGE_TEXT = {
  zh: {
    title: "宏观事件服务状态",
    subtitle: "检查 Render 服务以及美联储、纽约联储、白宫、国务院、BLS、BEA、Fiscal Data 和美国财政部官方来源。这里只检查服务状态，不判断黄金涨跌。",
    control: "开始检查",
    controlDescription: "页面只读取脱敏状态摘要，不需要输入、保存或传输 Render Token。",
    check: "立即检查",
    message: "点击“立即检查”查看服务状态。",
    language: "语言",
  },
  en: {
    title: "Macro Event Service Status",
    subtitle: "Check the Render service and official sources from the Federal Reserve, New York Fed, White House, State Department, BLS, BEA, Fiscal Data, and the U.S. Treasury. This page checks availability only and does not predict gold prices.",
    control: "Run Check",
    controlDescription: "This page reads only sanitized status data and never asks for or transmits the Render token.",
    check: "Run Check",
    message: "Click “Run Check” to view service status.",
    language: "Language",
  },
};

function applyLanguage(language) {
  const selected = LANGUAGE_TEXT[language] ? language : "zh";
  const copy = LANGUAGE_TEXT[selected];
  if (document.documentElement) {
    document.documentElement.lang = selected === "en" ? "en" : "zh-CN";
  }
  document.title = copy.title;
  document.querySelector("#page-title").textContent = copy.title;
  document.querySelector("#page-subtitle").textContent = copy.subtitle;
  document.querySelector("#control-title").textContent = copy.control;
  document.querySelector("#control-description").textContent = copy.controlDescription;
  document.querySelector("#check-button").textContent = copy.check;
  document.querySelector("#message").textContent = copy.message;
  document.querySelector("#language-label").textContent = copy.language;
  languageSelect.value = selected;
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(LANGUAGE_KEY, selected);
  }
}

function text(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function formatTimeInZone(value, timeZone, includeSeconds = false) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return text(value);
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    ...(includeSeconds ? { second: "2-digit" } : {}),
    hour12: false,
  }).formatToParts(parsed);
  const values = Object.fromEntries(
    parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]),
  );
  const base = `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}`;
  return includeSeconds ? `${base}:${values.second}` : base;
}

function formatBeijingTime(value) {
  return formatTimeInZone(value, "Asia/Shanghai", true);
}

function formatRecentWorkflowUsage(value) {
  const usedAt = formatBeijingTime(value);
  return usedAt === "—" ? "" : `最近被 workflow 采用：${usedAt} 北京时间`;
}

function formatDualEventTime(value, localTimezone = "America/New_York") {
  if (!value) return "—";
  const beijing = formatTimeInZone(value, "Asia/Shanghai");
  const local = formatTimeInZone(value, localTimezone);
  return `北京 ${beijing} ｜ 当地 ${local}`;
}

function formatEventDetail(event, localTimezone = "America/New_York") {
  if (!event || typeof event !== "object") return "—";
  const title = text(event.title, "");
  const precision = text(event.time_precision, "");
  const scheduledTime = text(event.scheduled_time_utc, "");
  const scheduledDate = text(event.scheduled_date, "");
  if (!title) return scheduledTime
    ? formatDualEventTime(scheduledTime, localTimezone)
    : (scheduledDate || "—");
  if (precision === "exact" && scheduledTime) {
    return `${title}｜精确时间：${formatDualEventTime(scheduledTime, localTimezone)}`;
  }
  return `${title}｜仅日期：${scheduledDate || "—"}`;
}

function setOverall(kind, label) {
  overallBadge.className = `overall-badge ${kind}`;
  overallText.textContent = label;
}

function updateSource(source) {
  const card = document.querySelector(`[data-source="${source.source}"]`);
  if (!card) return;
  const reachable = source.reachable === true;
  const valid = source.structure_valid === true;
  const kind = reachable && valid ? "good" : reachable ? "partial" : "bad";
  const label = reachable && valid ? "正常" : reachable ? "响应异常" : "不可访问";
  card.className = `source-card ${kind}`;
  card.querySelector('[data-field="state"]').textContent = label;
  card.querySelector('[data-field="http"]').textContent = text(source.http_status);
  card.querySelector('[data-field="content"]').textContent = text(source.content_type);
  card.querySelector('[data-field="error"]').textContent = text(source.error_code, "无");
}

function resetSources() {
  document.querySelectorAll(".source-card").forEach((card) => {
    card.className = "source-card idle";
    card.querySelector('[data-field="state"]').textContent = "未检查";
    card.querySelector('[data-field="http"]').textContent = "—";
    card.querySelector('[data-field="content"]').textContent = "—";
    card.querySelector('[data-field="error"]').textContent = "—";
  });
}

function eventState(eventType) {
  if (eventType.source_healthy !== true) return ["bad", "来源异常"];
  if (eventType.cache_state !== "cached") return ["partial", "等待缓存"];
  if (Number(eventType.event_count || 0) === 0) return ["partial", "未识别到事件"];
  return ["good", "已接入"];
}

function eventTypeName(eventType) {
  return String(
    eventType.label_en || eventType.label_zh || eventType.event_code || "",
  ).trim();
}

function compareEventTypes(left, right) {
  const leftTime = String(left.next_event_at_utc || "").trim();
  const rightTime = String(right.next_event_at_utc || "").trim();
  const leftHasTime = Boolean(leftTime);
  const rightHasTime = Boolean(rightTime);

  if (leftHasTime !== rightHasTime) return leftHasTime ? -1 : 1;
  if (leftTime !== rightTime) return leftTime.localeCompare(rightTime);

  const nameOrder = eventTypeName(left).localeCompare(
    eventTypeName(right),
    "en",
    { sensitivity: "base" },
  );
  if (nameOrder !== 0) return nameOrder;
  return String(left.event_code || "").localeCompare(String(right.event_code || ""));
}

function sortEventTypes(eventTypes) {
  return [...eventTypes].sort(compareEventTypes);
}

function updateEventType(eventType) {
  const card = document.querySelector(`[data-event-code="${eventType.event_code}"]`);
  if (!card) return;
  const [kind, label] = eventState(eventType);
  card.className = `event-card ${kind}`;
  card.querySelector('[data-field="state"]').textContent = label;
  card.querySelector('[data-field="count"]').textContent = text(eventType.event_count, 0);
  const localTimezone = text(eventType.local_timezone, "America/New_York");
  card.querySelector('[data-field="previous"]').textContent = formatDualEventTime(
    eventType.previous_event?.scheduled_time_utc || eventType.previous_event_at_utc,
    localTimezone,
  );
  card.querySelector('[data-field="next"]').textContent = formatDualEventTime(
    eventType.next_event?.scheduled_time_utc || eventType.next_event_at_utc,
    localTimezone,
  );
  const previousDetail = formatEventDetail(eventType.previous_event, localTimezone);
  const nextDetail = formatEventDetail(eventType.next_event, localTimezone);
  if (previousDetail !== "—") {
    card.querySelector('[data-field="previous"]').textContent = previousDetail;
  }
  if (nextDetail !== "—") {
    card.querySelector('[data-field="next"]').textContent = nextDetail;
  }
  const recentUsage = formatRecentWorkflowUsage(eventType.last_used_at_utc);
  let usageMarker = card.querySelector(".workflow-usage");
  if (recentUsage) {
    card.classList.add("recently-used");
    if (!usageMarker) {
      usageMarker = document.createElement("p");
      usageMarker.className = "workflow-usage";
      card.append(usageMarker);
    }
    usageMarker.textContent = recentUsage;
  } else {
    card.classList.remove("recently-used");
    usageMarker?.remove();
  }
}

function resetEventTypes() {
  document.querySelectorAll(".event-card").forEach((card) => {
    card.className = "event-card idle";
    card.querySelector('[data-field="state"]').textContent = "未检查";
    card.querySelector('[data-field="count"]').textContent = "—";
    card.querySelector('[data-field="previous"]').textContent = "—";
    card.querySelector('[data-field="next"]').textContent = "—";
    card.classList.remove("recently-used");
    card.querySelector(".workflow-usage")?.remove();
  });
}

function reorderEventCards(eventTypes) {
  const grid = document.querySelector("#event-grid");
  if (!grid) return;
  sortEventTypes(eventTypes).forEach((eventType) => {
    const card = document.querySelector(
      `[data-event-code="${eventType.event_code}"]`,
    );
    if (card) grid.append(card);
  });
}

async function readJson(response) {
  const body = await response.text();
  try {
    return body ? JSON.parse(body) : {};
  } catch (_error) {
    return { detail: body || `HTTP ${response.status}` };
  }
}

async function runCheck() {
  checkButton.disabled = true;
  checkButton.textContent = "检查中…";
  message.className = "message";
  message.textContent = "正在连接 Render 并检查官方宏观数据源，请稍候。";
  setOverall("idle", "检查中");

  try {
    const [serviceResponse, sourceResponse] = await Promise.all([
      fetch("/health", { cache: "no-store" }),
      fetch("/v1/macro-events/status-summary", { cache: "no-store" }),
    ]);
    const serviceData = await readJson(serviceResponse);
    const sourceData = await readJson(sourceResponse);
    const serviceHealthy = serviceResponse.ok && serviceData.status === "ok";

    document.querySelector("#render-status").textContent =
      serviceHealthy ? "在线" : "异常";

    if (!sourceResponse.ok) {
      const detail = text(sourceData.detail, `HTTP ${sourceResponse.status}`);
      throw new Error(detail);
    }

    const sources = Array.isArray(sourceData.sources) ? sourceData.sources : [];
    const eventTypes = Array.isArray(sourceData.event_types) ? sourceData.event_types : [];
    resetSources();
    resetEventTypes();
    sources.forEach(updateSource);
    const sortedEventTypes = sortEventTypes(eventTypes);
    sortedEventTypes.forEach(updateEventType);
    reorderEventCards(sortedEventTypes);
    document.querySelector("#valid-count").textContent =
      `${text(sourceData.valid_source_count, 0)} / ${text(sourceData.source_count, 10)}`;
    document.querySelector("#checked-time").textContent = formatBeijingTime(
      sourceData.checked_at_utc,
    );
    const status = sourceData.data_status;
    if (!serviceHealthy) {
      setOverall("bad", "Render异常");
      message.className = "message error";
      message.textContent = "Render 服务健康检查异常；下方数据源状态仅供排查参考。";
    } else if (status === "complete") {
      setOverall("good", "全部正常");
      message.textContent = "Render 与全部官方宏观数据源均正常。";
    } else if (status === "partial") {
      setOverall("partial", "部分可用");
      message.textContent = "Render 在线，但至少一个官方数据源异常，请查看下方红色或黄色卡片。";
    } else {
      setOverall("bad", "不可用");
      message.className = "message error";
      message.textContent = "宏观数据源当前不可用，请查看错误代码。";
    }
  } catch (error) {
    setOverall("bad", "检查失败");
    message.className = "message error";
    message.textContent = `检查失败：${error.message}`;
  } finally {
    checkButton.disabled = false;
    checkButton.textContent = "立即检查";
  }
}

checkButton.addEventListener("click", runCheck);
languageSelect.addEventListener("change", (event) => applyLanguage(event.target.value));
const savedLanguage = typeof localStorage !== "undefined"
  ? localStorage.getItem(LANGUAGE_KEY)
  : "zh";
applyLanguage(savedLanguage || "zh");

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    compareEventTypes,
    eventState,
    formatBeijingTime,
    formatDualEventTime,
    formatEventDetail,
    formatRecentWorkflowUsage,
    formatTimeInZone,
    sortEventTypes,
  };
}
