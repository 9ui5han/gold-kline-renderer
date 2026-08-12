const video = document.querySelector("#preview-video");
const urlInput = document.querySelector("#video-url");
const fileInput = document.querySelector("#video-file");
const emptyState = document.querySelector("#empty-state");
const uiLayer = document.querySelector("#tiktok-ui");
const safeLayer = document.querySelector("#safe-layer");
let localObjectUrl = "";

function loadVideo(source) {
  if (!source) return;
  if (localObjectUrl) {
    URL.revokeObjectURL(localObjectUrl);
    localObjectUrl = "";
  }
  video.src = source;
  video.load();
  emptyState.classList.add("hidden");
}

document.querySelector("#load-url").addEventListener("click", () => {
  loadVideo(urlInput.value.trim());
});

urlInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadVideo(urlInput.value.trim());
});

fileInput.addEventListener("change", () => {
  const [file] = fileInput.files;
  if (!file) return;
  localObjectUrl = URL.createObjectURL(file);
  video.src = localObjectUrl;
  video.load();
  emptyState.classList.add("hidden");
});

document.querySelector("#show-ui").addEventListener("change", (event) => {
  uiLayer.classList.toggle("hidden", !event.target.checked);
});

document.querySelector("#show-safe").addEventListener("change", (event) => {
  safeLayer.classList.toggle("hidden", !event.target.checked);
});

const initialUrl = new URLSearchParams(window.location.search).get("video");
if (initialUrl) {
  urlInput.value = initialUrl;
  loadVideo(initialUrl);
}
