const checkButton = document.querySelector("#check-button");
const message = document.querySelector("#message");
const overallBadge = document.querySelector("#overall-badge");
const overallText = document.querySelector("#overall-text");
const languageSelect = document.querySelector("#language-select");

const LANGUAGE_KEY = "macro-status-language-v2";
let currentLanguage = "zh";
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

const TEXT_TRANSLATIONS = [
  ["等待检查", "Waiting for check"],
  ["全部正常", "All healthy"],
  ["检查中", "Checking"],
  ["检查失败", "Check failed"],
  ["在线", "Online"],
  ["异常", "Error"],
  ["Render 服务", "Render Service"],
  ["有效来源", "Valid sources"],
  ["检查时间（北京时间）", "Check time (Beijing)"],
  ["官方数据源", "Official Sources"],
  ["已接入的宏观事件", "Tracked Macro Events"],
  ["状态", "Status"],
  ["内容类型", "Content type"],
  ["错误代码", "Error code"],
  ["未检查", "Not checked"],
  ["正常", "Healthy"],
  ["响应异常", "Response issue"],
  ["不可访问", "Unreachable"],
  ["来源异常", "Source error"],
  ["等待缓存", "Waiting for cache"],
  ["未识别到事件", "No events found"],
  ["已接入", "Connected"],
  ["缓存事件", "Cached events"],
  ["最近一次", "Previous"],
  ["下一次", "Next"],
  ["来源：", "Source: "],
  ["北京", "Beijing"],
  ["当地", "Local"],
  ["精确时间", "Exact time"],
  ["仅日期", "Date only"],
  ["无", "None"],
  ["日期未知", "Unknown date"],
  ["绿色表示可访问且结构正确；黄色表示只能部分使用；红色表示不可用。", "Green means reachable with a valid structure; yellow means partially usable; red means unavailable."],
  ["按下一次触发时间排列；同一时刻按英文事件名 A–Z 排列。事件时间同时显示北京时间和美国东部时间。", "Sorted by the next trigger time, then by English event name. Event times show Beijing and U.S. Eastern time."],
  ["Federal Reserve", "Federal Reserve"],
  ["美国联邦储备委员会", "U.S. Federal Reserve"],
  ["美国劳工统计局", "U.S. Bureau of Labor Statistics"],
  ["美国经济分析局", "U.S. Bureau of Economic Analysis"],
  ["美联储讲话与证词 RSS", "Federal Reserve speeches and testimony RSS"],
  ["纽约联储 John Williams 官方讲话页", "New York Fed John Williams official speeches"],
  ["总统 Donald Trump 官方讲话页", "President Donald Trump official remarks"],
  ["国务院官方外交新闻接口", "State Department official diplomacy feed"],
  ["财政部国债发行与拍卖", "Treasury debt issuance and auctions"],
  ["财政部国债回购日程", "Treasury buyback schedule"],
  ["财政部债务管理公告", "Treasury debt management notices"],
  ["CPI 消费者物价指数", "CPI Consumer Price Index"],
  ["PPI 生产者物价指数", "PPI Producer Price Index"],
  ["非农与就业报告", "Nonfarm payrolls and employment report"],
  ["PCE 个人消费支出物价", "PCE Personal Consumption Expenditures"],
  ["FOMC 美联储议息会议", "FOMC Federal Reserve meeting"],
  ["美国外交官员讲话与声明", "U.S. diplomatic officials' remarks and statements"],
  ["美国国债发行与拍卖", "U.S. Treasury issuance and auctions"],
  ["美国国债回购", "U.S. Treasury buybacks"],
  ["美国财政部债务公告", "U.S. Treasury debt notices"],
  ["Scott Bessent 财政部长讲话", "Treasury Secretary Scott Bessent remarks"],
  ["Christopher Waller 美联储理事讲话", "Christopher Waller Fed Governor remarks"],
  ["Donald Trump 总统讲话", "President Donald Trump remarks"],
  ["Jerome Powell 美联储讲话", "Jerome Powell Fed Chair remarks"],
  ["John Williams 纽约联储主席讲话", "New York Fed President John Williams remarks"],
  ["Kevin Warsh 美联储主席讲话", "Fed Chair Kevin Warsh remarks"],
  ["Michelle Bowman 美联储理事讲话", "Michelle Bowman Fed Governor remarks"],
  ["Philip Jefferson 美联储副主席讲话", "Fed Vice Chair Philip Jefferson remarks"],
  ["来源未提供详细简介。", "No detailed description was provided by the source."],
];

function translatePageText(language) {
  if (!document.body || !document.createTreeWalker || typeof NodeFilter === "undefined") {
    return;
  }
  const replacements = (language === "en"
    ? TEXT_TRANSLATIONS
    : TEXT_TRANSLATIONS.map(([zh, en]) => [en, zh]))
    .sort(([left], [right]) => right.length - left.length);
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  let node;
  while ((node = walker.nextNode())) nodes.push(node);
  nodes.forEach((textNode) => {
    let value = textNode.nodeValue;
    replacements.forEach(([from, to]) => {
      value = value.split(from).join(to);
    });
    textNode.nodeValue = value;
  });
}

function applyLanguage(language) {
  const selected = LANGUAGE_TEXT[language] ? language : "zh";
  const copy = LANGUAGE_TEXT[selected];
  currentLanguage = selected;
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
  translatePageText(selected);
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
  return currentLanguage === "en"
    ? `Beijing ${beijing} ｜ Local ${local}`
    : `北京 ${beijing} ｜ 当地 ${local}`;
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

function isRecentUpdate(value) {
  if (!value) return false;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && Date.now() - timestamp >= 0 && Date.now() - timestamp <= 24 * 60 * 60 * 1000;
}

function setEventOfficialLink(card, eventType) {
  const heading = card.querySelector("h3");
  if (!heading) return;
  const event = eventType.previous_event?.event_id
    ? eventType.previous_event
    : eventType.next_event;
  const eventId = text(event?.event_id, "");
  const detailUrl = eventId
    ? `/macro-status/event.html?source=${encodeURIComponent(eventType.source || "")}&event_id=${encodeURIComponent(eventId)}`
    : "";
  const title = heading.textContent;
  const hasDetail = Boolean(detailUrl);
  card.dataset.eventDetailUrl = detailUrl;
  card.classList.toggle("has-official-link", hasDetail);
  card.tabIndex = hasDetail ? 0 : -1;
  card.onclick = (clickEvent) => {
    if (!hasDetail || clickEvent.target.closest("a")) return;
    window.location.href = detailUrl;
  };
  card.onkeydown = (keyboardEvent) => {
    if (hasDetail && (keyboardEvent.key === "Enter" || keyboardEvent.key === " ")) {
      keyboardEvent.preventDefault();
      window.location.href = detailUrl;
    }
  };
  heading.replaceChildren();
  if (hasDetail) {
    const link = document.createElement("a");
    link.href = detailUrl;
    link.textContent = title;
    heading.append(link);
  } else {
    heading.textContent = title;
  }
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
  setEventOfficialLink(card, eventType);
  card.classList.toggle("recently-updated", isRecentUpdate(eventType.cached_at_utc));
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
    applyLanguage(currentLanguage);
  } catch (error) {
    setOverall("bad", "检查失败");
    message.className = "message error";
    message.textContent = `检查失败：${error.message}`;
  } finally {
    checkButton.disabled = false;
    checkButton.textContent = LANGUAGE_TEXT[currentLanguage].check;
  }
}

checkButton.addEventListener("click", runCheck);
languageSelect.addEventListener("change", (event) => applyLanguage(event.target.value));
const savedLanguage = typeof localStorage !== "undefined"
  ? localStorage.getItem(LANGUAGE_KEY)
  : "en";
applyLanguage(savedLanguage || "en");
if (typeof window !== "undefined") {
  runCheck();
  window.setInterval(runCheck, 300000);
}

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
