const params = new URLSearchParams(window.location.search);
const source = params.get("source") || "";
const eventId = params.get("event_id") || "";
const title = document.querySelector("#event-title");
const meta = document.querySelector("#event-meta");
const description = document.querySelector("#event-description");
const contentKind = document.querySelector("#event-content-kind");
const officialLink = document.querySelector("#official-link");

function value(input, fallback = "—") {
  return input === null || input === undefined || input === "" ? fallback : String(input);
}

function formatTime(event) {
  if (event.scheduled_time_utc) {
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Shanghai", dateStyle: "medium", timeStyle: "short",
    }).format(new Date(event.scheduled_time_utc)) + " Beijing time";
  }
  return `${value(event.scheduled_date, "Unknown date")} (date only)`;
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
    contentKind.textContent = "Article summary available";
    description.textContent = value(event.description);
  } else {
    contentKind.textContent = "Schedule event — no article body is provided by this source";
    description.textContent = "This source provides the release or publication schedule only. The full article may appear after the event is officially released.";
  }
  if (/^https?:\/\//i.test(event.official_url || "")) {
    officialLink.href = event.official_url;
    officialLink.hidden = false;
  }
}

loadEvent().catch((error) => {
  title.textContent = "Event unavailable";
  meta.textContent = error.message;
  description.textContent = "This event is not available in the local 30-day store.";
  contentKind.textContent = "Event unavailable";
});
