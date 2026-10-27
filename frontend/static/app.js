const state = {
  aspectRatio: "16:9",
  fps: 24,
  file: null,
  filePreviewUrl: "",
  payload: null,
  payloadJson: "",
  runMode: "pod",
  submissionMode: "pod",
  serverConfigured: false,
  pod: null,
  podActionBusy: false,
};

const ratioDimensions = {
  "16:9": { width: 1280, height: 720 },
  "9:16": { width: 720, height: 1280 },
  "1:1": { width: 1024, height: 1024 },
};

const form = document.querySelector("#payload-form");
const promptField = document.querySelector("#prompt");
const optimizeField = document.querySelector("#optimize-prompt");
const secondsField = document.querySelector("#seconds");
const secondsNumberField = document.querySelector("#seconds-number");
const secondsMinLabel = document.querySelector("#seconds-min-label");
const secondsMaxLabel = document.querySelector("#seconds-max-label");
const framesValue = document.querySelector("#frames-value");
const fpsValue = document.querySelector("#fps-value");
const resolutionValue = document.querySelector("#resolution-value");
const charCount = document.querySelector("#char-count");
const feedback = document.querySelector("#feedback");
const uploadPanel = document.querySelector("#upload-panel");
const uploadIcon = document.querySelector("#upload-icon");
const uploadTitle = document.querySelector("#upload-title");
const uploadCopy = document.querySelector("#upload-copy");
const sourceImageInput = document.querySelector("#source-image");
const filePill = document.querySelector("#file-pill");
const fileName = document.querySelector("#file-name");
const imagePreviewCard = document.querySelector("#image-preview-card");
const imagePreview = document.querySelector("#image-preview");
const previewName = document.querySelector("#preview-name");
const payloadPanel = document.querySelector("#payload-panel");
const payloadOutput = document.querySelector("#payload-output");
const payloadSummary = document.querySelector("#payload-summary");
const generateButton = document.querySelector("#generate-button");
const submitButton = document.querySelector("#submit-button");
const copyButton = document.querySelector("#copy-button");
const downloadButton = document.querySelector("#download-button");
const endpointPanel = document.querySelector("#endpoint-panel");
const endpointStatusLine = document.querySelector("#endpoint-status-line");
const endpointOverrides = document.querySelector("#endpoint-overrides");
const endpointUrlField = document.querySelector("#endpoint-url");
const authTokenField = document.querySelector("#auth-token");
const submitModeTitle = document.querySelector("#submit-mode-title");
const submitModeCopy = document.querySelector("#submit-mode-copy");
const helperCopy = document.querySelector("#helper-copy");
const responsePanel = document.querySelector("#response-panel");
const responseSummary = document.querySelector("#response-summary");
const responseStatus = document.querySelector("#response-status");
const responseMedia = document.querySelector("#response-media");
const responseOutput = document.querySelector("#response-output");
const aspectButtons = document.querySelectorAll("[data-aspect-ratio]");
const podStatusBadge = document.querySelector("#pod-status-badge");
const podTitle = document.querySelector("#pod-title");
const podSubtitle = document.querySelector("#pod-subtitle");
const podGpuSelect = document.querySelector("#pod-gpu-select");
const podDeployBtn = document.querySelector("#pod-deploy-btn");
const podStartBtn = document.querySelector("#pod-start-btn");
const podStopBtn = document.querySelector("#pod-stop-btn");
const podTerminateBtn = document.querySelector("#pod-terminate-btn");
const podComfyLink = document.querySelector("#pod-comfy-link");
const podRefreshBtn = document.querySelector("#pod-refresh-btn");
const podLogsDetails = document.querySelector("#pod-logs-details");
const podLogsOutput = document.querySelector("#pod-logs-output");
const POD_STATUS_POLL_INTERVAL_MS = 2000;
const POD_STATUS_POLL_TIMEOUT_MS = 15 * 60 * 1000;
let podLifecyclePollTimer = null;

function secondsToFrames(seconds) {
  return Math.round(seconds * state.fps) + 1;
}

function setFeedback(message, type = "") {
  feedback.textContent = message;
  feedback.className = type ? `feedback ${type}` : "feedback";
}

function updatePromptCounter() {
  charCount.textContent = String(promptField.value.length);
}

function formatSecondsLabel(seconds) {
  return `${Number(seconds).toString()}s`;
}

function updateDurationSummary() {
  const seconds = Number.parseFloat(secondsField.value);
  const dimensions = ratioDimensions[state.aspectRatio];
  secondsNumberField.value = String(seconds);
  framesValue.textContent = String(secondsToFrames(seconds));
  fpsValue.textContent = String(state.fps);
  resolutionValue.textContent = `${dimensions.width} × ${dimensions.height}`;
}

function updateDurationFromNumber() {
  const seconds = Number.parseFloat(secondsNumberField.value);
  if (!Number.isFinite(seconds)) {
    return;
  }

  const minimum = Number.parseFloat(secondsField.min);
  const maximum = Number.parseFloat(secondsField.max);
  secondsField.value = String(Math.min(maximum, Math.max(minimum, Math.round(seconds))));
  updateDurationSummary();
}

function updateAspectButtons() {
  aspectButtons.forEach((button) => {
    const isActive = button.dataset.aspectRatio === state.aspectRatio;
    button.classList.toggle("aspect-button-active", isActive);
    button.setAttribute("aria-checked", String(isActive));
  });
}

function updateSubmitModeUi() {
  if (state.submissionMode === "pod") {
    endpointPanel.hidden = true;
    endpointUrlField.disabled = true;
    authTokenField.disabled = true;
    generateButton.hidden = true;
    generateButton.disabled = true;
    generateButton.type = "button";
    payloadPanel.hidden = true;
    helperCopy.hidden = true;
    submitButton.textContent = "Generate Video";
    submitButton.classList.remove("secondary-hero-action");
    submitButton.classList.add("primary-action");
    submitModeTitle.textContent = "Local pod";
    submitModeCopy.textContent =
      "The frontend queues the workflow into the local ComfyUI pod and then polls for completion.";
    return;
  }

  endpointPanel.hidden = false;
  endpointUrlField.disabled = false;
  authTokenField.disabled = false;
  generateButton.hidden = false;
  generateButton.disabled = false;
  generateButton.type = "submit";
  helperCopy.hidden = false;
  if (state.serverConfigured) {
    endpointStatusLine.hidden = false;
    endpointOverrides.open = false;
    submitModeTitle.textContent = "Serverless endpoint";
    submitModeCopy.textContent =
      "Endpoint and API key are configured on the server. Expand the settings below only if you need to override them.";
  } else {
    endpointStatusLine.hidden = true;
    endpointOverrides.open = true;
    submitModeTitle.textContent = "Real endpoint";
    submitModeCopy.innerHTML =
      'Full POST URL, for example <code>https://api.runpod.ai/v2/&lt;endpoint_id&gt;/runsync</code>';
  }
  submitButton.textContent = "Submit to Endpoint";
  submitButton.classList.remove("primary-action");
  submitButton.classList.add("secondary-hero-action");
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function setFile(file) {
  if (state.filePreviewUrl) {
    URL.revokeObjectURL(state.filePreviewUrl);
    state.filePreviewUrl = "";
  }

  state.file = file;
  if (!file) {
    filePill.hidden = true;
    fileName.textContent = "";
    imagePreviewCard.hidden = true;
    imagePreview.removeAttribute("src");
    previewName.textContent = "";
    uploadIcon.hidden = false;
    uploadTitle.hidden = false;
    uploadCopy.hidden = false;
    uploadPanel.classList.remove("has-preview");
    return;
  }

  fileName.textContent = file.name;
  state.filePreviewUrl = URL.createObjectURL(file);
  imagePreview.src = state.filePreviewUrl;
  previewName.textContent = file.name;
  imagePreviewCard.hidden = false;
  uploadIcon.hidden = true;
  uploadTitle.hidden = true;
  uploadCopy.hidden = true;
  uploadPanel.classList.add("has-preview");
  filePill.hidden = true;
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Failed to read the source image."));
    reader.readAsDataURL(file);
  });
}

function renderSummary(summary) {
  const chips = [
    `${summary.frames} frames`,
    `${summary.seconds.toFixed(1)}s`,
    `${summary.width} × ${summary.height}`,
    `${summary.aspect_ratio}`,
    `${summary.fps} fps`,
    summary.optimize_prompt ? "AI prompt on" : "AI prompt off",
  ];

  payloadSummary.innerHTML = "";
  chips.forEach((text) => {
    const chip = document.createElement("span");
    chip.className = "payload-summary-chip";
    chip.textContent = text;
    payloadSummary.appendChild(chip);
  });
}

async function copyPayload() {
  if (!state.payloadJson) {
    return;
  }

  await navigator.clipboard.writeText(state.payloadJson);
  setFeedback("Payload copied.", "success");
}

function downloadPayload() {
  if (!state.payloadJson) {
    return;
  }

  const blob = new Blob([state.payloadJson], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "ltx-payload.json";
  link.click();
  URL.revokeObjectURL(url);
}

function renderResponse(result) {
  const chips = [
    result.ok ? `HTTP ${result.status_code} ok` : `HTTP ${result.status_code}`,
    result.content_type,
    result.endpoint_url || "local ComfyUI",
  ];

  responseSummary.innerHTML = "";
  chips.forEach((text) => {
    const chip = document.createElement("span");
    chip.className = "payload-summary-chip";
    chip.textContent = text;
    responseSummary.appendChild(chip);
  });

  if (state.submissionMode === "pod") {
    renderResponseMedia(result.response_json?.output || null);
    renderPodStatus(result.response_json || {});
    responseOutput.hidden = true;
    responseOutput.textContent = "";
  } else {
    const endpointOutput = normalizeEndpointOutput(result.response_json?.output);
    renderResponseMedia(endpointOutput);
    const epStatus = result.response_json?.status || "";
    if (epStatus) {
      responseStatus.textContent = `Job status: ${epStatus}`;
      responseStatus.hidden = false;
    } else {
      responseStatus.hidden = true;
      responseStatus.textContent = "";
    }
    responseOutput.hidden = false;
    responseOutput.textContent = result.response_json
      ? JSON.stringify(result.response_json, null, 2)
      : (result.response_text || "");
  }

  responsePanel.hidden = false;
}

function normalizeEndpointOutput(rawOutput) {
  if (!rawOutput) return null;
  const convertEntry = (entry, fallbackKind) => {
    const mediaType = entry.media_type || (fallbackKind === "video" ? "video/mp4" : "image/png");
    const url =
      entry.url ||
      (entry.type === "base64" && entry.data
        ? `data:${mediaType};base64,${entry.data}`
        : entry.data);
    return {
      filename: entry.filename || (fallbackKind === "video" ? "ltx25_output.mp4" : "output.png"),
      media_type: mediaType,
      url,
      download_url: url,
    };
  };
  return {
    videos: (rawOutput.videos || []).map((v) => convertEntry(v, "video")),
    images: (rawOutput.images || []).map((img) => convertEntry(img, "image")),
  };
}

function renderPodStatus(responseJson) {
  const status = responseJson?.status || "";
  const hasOutput = Boolean(responseJson?.output);
  const renderTimeSec = responseJson?.metadata?.render_time_sec;

  if (hasOutput) {
    responseStatus.textContent =
      typeof renderTimeSec === "number"
        ? `Generation time: ${renderTimeSec.toFixed(2)}s`
        : "Generation complete.";
    responseStatus.hidden = false;
    return;
  }

  const statusCopy = {
    queued: "Workflow queued locally.",
    running: "ComfyUI is rendering the video.",
    completed: "Render completed.",
  };

  responseStatus.textContent = statusCopy[status] || "Working on your video.";
  responseStatus.hidden = false;
}

function renderResponseMedia(output) {
  responseMedia.innerHTML = "";

  if (!output || (!output.videos?.length && !output.images?.length)) {
    responseMedia.hidden = true;
    return;
  }

  const renderArtifactCard = (artifact, mediaKind) => {
    const isVideo = (artifact.media_type || "").startsWith("video/") || mediaKind === "video";
    const card = document.createElement("article");
    card.className = "response-media-card";

    const header = document.createElement("div");
    header.className = "response-media-header";

    const title = document.createElement("p");
    title.className = "response-media-title";
    title.textContent = artifact.filename;

    const saveLink = document.createElement("a");
    saveLink.className = "response-media-link";
    saveLink.href = artifact.download_url || artifact.url;
    saveLink.textContent = isVideo ? "Save video" : "Save image";
    saveLink.setAttribute("download", artifact.filename);

    header.appendChild(title);
    header.appendChild(saveLink);
    card.appendChild(header);

    if (isVideo) {
      const video = document.createElement("video");
      video.className = "response-video";
      video.src = artifact.url;
      video.controls = true;
      video.preload = "metadata";
      card.appendChild(video);
    } else {
      const image = document.createElement("img");
      image.className = "response-image";
      image.src = artifact.url;
      image.alt = artifact.filename;
      card.appendChild(image);
    }

    return card;
  };

  output.videos?.forEach((artifact) => {
    responseMedia.appendChild(renderArtifactCard(artifact, "video"));
  });

  output.images?.forEach((artifact) => {
    responseMedia.appendChild(renderArtifactCard(artifact, "image"));
  });

  responseMedia.hidden = false;
}

async function pollPodSubmitStatus(promptId, nodeUsed) {
  const deadline = Date.now() + POD_STATUS_POLL_TIMEOUT_MS;
  const params = new URLSearchParams();
  if (nodeUsed) {
    params.set("node", nodeUsed);
  }
  const qs = params.toString();
  const statusUrl = `api/pod-submit/${encodeURIComponent(promptId)}${qs ? `?${qs}` : ""}`;

  while (Date.now() < deadline) {
    const response = await fetch(statusUrl);
    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.detail || "Failed to poll pod status.");
    }

    renderResponse(result);

    if (result.response_json?.status === "completed") {
      return result;
    }

    await sleep(POD_STATUS_POLL_INTERVAL_MS);
  }

  throw new Error("Pod run timed out while waiting for ComfyUI history.");
}

function renderPodControlState(data) {
  const pod = data?.pod || null;
  state.pod = pod;
  const vol = data?.default_volume || {};
  const logs = data?.logs || [];

  if (!pod) {
    podStatusBadge.textContent = "Not Deployed ($0/hr compute)";
    podStatusBadge.className = "pod-status-badge pod-status-idle";
    podTitle.textContent = "No LTX 2.5 Pod Active";
    podSubtitle.innerHTML = `Persistent Volume ready: <code>${vol.name || "ltx25-models"} (${vol.id || "vkqbtcijow"} · ${vol.size_gb || 150} GB · ${vol.data_center_id || "EUR-IS-2"} → ${vol.mount_path || "/workspace"})</code>`;
    podGpuSelect.hidden = false;
    podDeployBtn.hidden = false;
    podDeployBtn.disabled = state.podActionBusy;
    podStartBtn.hidden = true;
    podStopBtn.hidden = true;
    podTerminateBtn.hidden = true;
    podComfyLink.hidden = true;
    podLogsDetails.hidden = true;
    return;
  }

  const priceStr = typeof pod.cost_per_hr === "number" ? `$${pod.cost_per_hr.toFixed(2)}/hr` : "";
  podTitle.textContent = `${pod.name} · ${pod.gpu_name} (${pod.id})`;
  podSubtitle.innerHTML = `Data Center: <code>${pod.data_center_id}</code> · Volume: <code>${pod.volume_id} → ${pod.mount_path}</code>`;

  podGpuSelect.hidden = true;
  podDeployBtn.hidden = true;

  if (pod.phase === "READY") {
    podStatusBadge.textContent = `Ready · ${priceStr}`;
    podStatusBadge.className = "pod-status-badge pod-status-ready";
    podStartBtn.hidden = true;
    podStopBtn.hidden = false;
    podTerminateBtn.hidden = false;
    podComfyLink.hidden = false;
    podComfyLink.href = pod.comfy_url;
  } else if (pod.phase === "STOPPED") {
    podStatusBadge.textContent = "Stopped (GPU Released)";
    podStatusBadge.className = "pod-status-badge pod-status-stopped";
    podStartBtn.hidden = false;
    podStopBtn.hidden = true;
    podTerminateBtn.hidden = false;
    podComfyLink.hidden = true;
  } else {
    podStatusBadge.textContent = `Bootstrapping Models & ComfyUI... (${priceStr})`;
    podStatusBadge.className = "pod-status-badge pod-status-booting";
    podStartBtn.hidden = true;
    podStopBtn.hidden = false;
    podTerminateBtn.hidden = false;
    podComfyLink.hidden = true;
  }

  podStartBtn.disabled = state.podActionBusy;
  podStopBtn.disabled = state.podActionBusy;
  podTerminateBtn.disabled = state.podActionBusy;

  if (logs.length > 0) {
    podLogsDetails.hidden = false;
    if (pod.phase === "BOOTSTRAPPING") {
      podLogsDetails.open = true;
    }
    podLogsOutput.textContent = logs.join("\n");
  } else {
    podLogsDetails.hidden = pod.phase !== "BOOTSTRAPPING";
  }
}

async function refreshPodStatus() {
  try {
    const resp = await fetch("api/pod/status");
    if (!resp.ok) return;
    const data = await resp.json();
    renderPodControlState(data);
    const nextDelay = data?.pod && data.pod.phase === "BOOTSTRAPPING" ? 5000 : 12000;
    window.clearTimeout(podLifecyclePollTimer);
    podLifecyclePollTimer = window.setTimeout(refreshPodStatus, nextDelay);
  } catch (_err) {
    window.clearTimeout(podLifecyclePollTimer);
    podLifecyclePollTimer = window.setTimeout(refreshPodStatus, 15000);
  }
}

async function deployPodFromUi() {
  state.podActionBusy = true;
  podDeployBtn.disabled = true;
  podDeployBtn.textContent = "Deploying...";
  setFeedback("Provisioning LTX 2.5 GPU Pod with persistent volume attached...", "success");
  try {
    const resp = await fetch("api/pod/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gpu_id: podGpuSelect.value }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || "Failed to deploy Pod.");
    }
    renderPodControlState(data);
    setFeedback("Pod deployed! Waiting for ComfyUI and LTX 2.5 models to finish loading...", "success");
    window.clearTimeout(podLifecyclePollTimer);
    podLifecyclePollTimer = window.setTimeout(refreshPodStatus, 4000);
  } catch (err) {
    setFeedback(err.message, "error");
  } finally {
    state.podActionBusy = false;
    podDeployBtn.disabled = false;
    podDeployBtn.textContent = "Deploy Pod";
  }
}

async function triggerPodActionFromUi(action) {
  if (action === "terminate") {
    const ok = window.confirm(
      "Terminate the LTX 2.5 Pod? Compute billing stops immediately and all LTX models remain safely persisted on the network volume (vkqbtcijow)."
    );
    if (!ok) return;
  }
  state.podActionBusy = true;
  podStartBtn.disabled = true;
  podStopBtn.disabled = true;
  podTerminateBtn.disabled = true;
  setFeedback(`Executing Pod action: ${action}...`, "success");
  try {
    const resp = await fetch("api/pod/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        pod_id: state.pod?.id || "",
      }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || `Pod ${action} failed.`);
    }
    renderPodControlState(data);
    setFeedback(
      action === "terminate"
        ? "Pod terminated. Persistent models remain saved on network volume vkqbtcijow."
        : `Pod ${action} succeeded.`,
      "success"
    );
    window.clearTimeout(podLifecyclePollTimer);
    podLifecyclePollTimer = window.setTimeout(refreshPodStatus, 4000);
  } catch (err) {
    setFeedback(err.message, "error");
  } finally {
    state.podActionBusy = false;
    podStartBtn.disabled = false;
    podStopBtn.disabled = false;
    podTerminateBtn.disabled = false;
  }
}

async function pollEndpointStatus(endpointUrl, jobId, authToken) {
  const deadline = Date.now() + POD_STATUS_POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await sleep(3000);
    const params = new URLSearchParams({
      endpoint_url: endpointUrl,
      job_id: jobId,
    });
    if (authToken) {
      params.set("auth_token", authToken);
    }
    const response = await fetch(`api/endpoint-status?${params.toString()}`);
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || "Failed to poll RunPod endpoint status.");
    }
    renderResponse(result);
    const st = result.response_json?.status;
    if (st === "COMPLETED" || st === "FAILED" || st === "CANCELLED" || st === "TIMED_OUT") {
      return result;
    }
  }
  throw new Error("Serverless job timed out while waiting for completion.");
}

async function initializeConfig() {
  const response = await fetch("api/config");
  if (!response.ok) {
    throw new Error("Failed to load app configuration.");
  }

  const config = await response.json();
  state.fps = config.fps;
  state.runMode = config.run_mode || "worker";
  state.submissionMode = config.submission_mode || "endpoint";
  if (config.default_endpoint_url && !endpointUrlField.value) {
    endpointUrlField.value = config.default_endpoint_url;
  }
  state.serverConfigured = Boolean(
    config.has_server_auth && config.default_endpoint_url
  );
  if (config.has_server_auth) {
    authTokenField.placeholder = "Configured on server (optional override)";
  }
  if (state.serverConfigured) {
    const endpointId = (config.default_endpoint_url.match(/\/v2\/([^/]+)\//) || [])[1] || "";
    endpointStatusLine.textContent = endpointId
      ? `Connected to RunPod serverless endpoint ${endpointId} — API key managed by the server.`
      : "Endpoint and API key managed by the server.";
  }
  secondsField.min = String(config.seconds.min);
  secondsField.max = String(config.seconds.max);
  secondsField.step = String(config.seconds.step);
  secondsField.value = String(config.seconds.default);
  secondsNumberField.min = String(config.seconds.min);
  secondsNumberField.max = String(config.seconds.max);
  secondsNumberField.step = String(config.seconds.step);
  secondsNumberField.value = String(config.seconds.default);
  secondsMinLabel.textContent = formatSecondsLabel(config.seconds.min);
  secondsMaxLabel.textContent = formatSecondsLabel(config.seconds.max);
  updateSubmitModeUi();
  updateDurationSummary();
}

async function buildPayload({
  scroll = true,
  showPreview = true,
  successMessage = "Payload ready.",
} = {}) {
  if (!promptField.value.trim()) {
    setFeedback("Prompt is required.", "error");
    promptField.focus();
    return null;
  }

  if (!state.file) {
    setFeedback("Source image is required for the current workflow.", "error");
    return null;
  }

  const imageDataUrl = await readFileAsDataUrl(state.file);
  const response = await fetch("api/payload", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      prompt: promptField.value,
      seconds: Number.parseFloat(secondsField.value),
      aspect_ratio: state.aspectRatio,
      image_name: state.file.name,
      image_data_url: imageDataUrl,
      optimize_prompt: optimizeField.checked,
    }),
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Failed to generate payload.");
  }

  state.payload = data.payload;
  state.payloadJson = JSON.stringify(data.payload, null, 2);
  payloadOutput.textContent = state.payloadJson;
  renderSummary(data.summary);
  payloadPanel.hidden = !showPreview;
  if (scroll && showPreview) {
    payloadPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  if (successMessage) {
    setFeedback(successMessage, "success");
  }
  return data.payload;
}

aspectButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.aspectRatio = button.dataset.aspectRatio;
    updateAspectButtons();
    updateDurationSummary();
  });
});

promptField.addEventListener("input", updatePromptCounter);
secondsField.addEventListener("input", updateDurationSummary);
secondsNumberField.addEventListener("input", updateDurationFromNumber);
copyButton.addEventListener("click", copyPayload);
downloadButton.addEventListener("click", downloadPayload);

uploadPanel.addEventListener("click", () => sourceImageInput.click());
uploadPanel.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    sourceImageInput.click();
  }
});

sourceImageInput.addEventListener("change", (event) => {
  const [file] = event.target.files;
  setFile(file || null);
});

["dragenter", "dragover"].forEach((eventName) => {
  uploadPanel.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadPanel.classList.add("drag-active");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  uploadPanel.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadPanel.classList.remove("drag-active");
  });
});

uploadPanel.addEventListener("drop", (event) => {
  const [file] = event.dataTransfer.files;
  if (file && file.type.startsWith("image/")) {
    setFile(file);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.submissionMode === "pod") {
    submitButton.click();
    return;
  }

  setFeedback("");

  generateButton.disabled = true;
  generateButton.textContent = "Generating...";

  try {
    await buildPayload();
  } catch (error) {
    setFeedback(error.message, "error");
  } finally {
    generateButton.disabled = false;
    generateButton.textContent = "Generate Payload";
  }
});

submitButton.addEventListener("click", async () => {
  setFeedback("");

  let endpointUrl = "";
  if (state.submissionMode === "endpoint") {
    endpointUrl = endpointUrlField.value.trim();
  }

  if (state.submissionMode === "endpoint" && !endpointUrl) {
    setFeedback("Endpoint URL is required before submit.", "error");
    endpointUrlField.focus();
    return;
  }

  submitButton.disabled = true;
  submitButton.textContent = state.submissionMode === "pod" ? "Running..." : "Submitting...";

  try {
    const payload = await buildPayload({
      scroll: false,
      showPreview: state.submissionMode !== "pod",
      successMessage:
        state.submissionMode === "pod"
          ? ""
          : "Payload refreshed for submit.",
    });
    if (!payload) {
      return;
    }

    const response = await fetch(
      state.submissionMode === "pod" ? "api/pod-submit" : "api/submit",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          payload,
          ...(state.submissionMode === "endpoint"
            ? {
                endpoint_url: endpointUrl,
                auth_token: authTokenField.value,
              }
            : {}),
        }),
      }
    );

    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || "Submit failed.");
    }

    renderResponse(result);
    responsePanel.scrollIntoView({ behavior: "smooth", block: "start" });

    if (state.submissionMode === "pod") {
      const promptId = result.response_json?.prompt_id || "";
      const nodeUsed = result.response_json?.node_used || "";
      if (!promptId) {
        throw new Error("Local pod submit did not return a prompt_id.");
      }

      setFeedback("Workflow queued locally. Waiting for ComfyUI to finish.", "success");
      submitButton.textContent = "Waiting for ComfyUI...";
      const completedResult = await pollPodSubmitStatus(promptId, nodeUsed);
      setFeedback(
        completedResult.ok
          ? "Local pod run finished successfully."
          : `Local pod run returned HTTP ${completedResult.status_code}.`,
        completedResult.ok ? "success" : "error"
      );
      return;
    }

    const rpJobId = result.response_json?.id;
    const rpStatus = result.response_json?.status;
    if (rpJobId && (rpStatus === "IN_QUEUE" || rpStatus === "IN_PROGRESS")) {
      setFeedback(`Job ${rpJobId} queued on RunPod Serverless. Polling status...`, "success");
      submitButton.textContent = "Polling RunPod...";
      const finalResult = await pollEndpointStatus(endpointUrl, rpJobId, authTokenField.value);
      const finalStatus = finalResult.response_json?.status;
      setFeedback(
        finalStatus === "COMPLETED"
          ? "LTX 2.5 video generation completed!"
          : `Job finished with status: ${finalStatus}`,
        finalStatus === "COMPLETED" ? "success" : "error"
      );
      return;
    }

    setFeedback(
      result.ok
        ? `Submitted successfully. Endpoint returned HTTP ${result.status_code}.`
        : `Submitted. Endpoint returned HTTP ${result.status_code}.`,
      result.ok ? "success" : "error"
    );
  } catch (error) {
    setFeedback(error.message, "error");
  } finally {
    submitButton.disabled = false;
    updateSubmitModeUi();
  }
});

podDeployBtn?.addEventListener("click", deployPodFromUi);
podStartBtn?.addEventListener("click", () => triggerPodActionFromUi("start"));
podStopBtn?.addEventListener("click", () => triggerPodActionFromUi("stop"));
podTerminateBtn?.addEventListener("click", () => triggerPodActionFromUi("terminate"));
podRefreshBtn?.addEventListener("click", refreshPodStatus);

updatePromptCounter();
updateAspectButtons();
setFile(null);
initializeConfig().catch((error) => {
  setFeedback(error.message, "error");
});
refreshPodStatus();
