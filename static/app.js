(() => {
  "use strict";

  const MAX_CLIP_SECONDS = 12 * 60 * 60;
  const TERMINAL_JOB_STATES = new Set(["ready", "error", "cancelled"]);
  const ACTIVE_WORK_KEY = "cutframe.active-work.v1";
  const API_MUTATION_HEADER = Object.freeze({ "X-CutFrame-Request": "1" });

  const state = {
    sourceKind: null,
    sourceRevision: 0,
    localFile: null,
    localObjectUrl: null,
    youtube: null,
    youtubePlayer: null,
    youtubeClock: null,
    durationClock: null,
    duration: 0,
    start: 0,
    end: 0,
    previewingSelection: false,
    previewRequest: null,
    operationRevision: 0,
    uploadRequest: null,
    uploadId: null,
    activeJobId: null,
    pollTimer: null,
    pollFailures: 0,
    processing: false,
    previewErrorKind: null,
    activeJobSummary: null,
    lastAnnouncement: "",
    cancellationPending: false,
    health: null,
  };

  const elements = {
    brandHome: document.querySelector("#brandHome"),
    healthMenu: document.querySelector("#healthMenu"),
    toolHealth: document.querySelector("#toolHealth"),
    toolHealthLabel: document.querySelector("#toolHealthLabel"),
    healthTitle: document.querySelector("#healthTitle"),
    healthMessage: document.querySelector("#healthMessage"),
    toolPaths: document.querySelector("#toolPaths"),
    fileLimit: document.querySelector("#fileLimit"),
    fileTab: document.querySelector("#fileTab"),
    urlTab: document.querySelector("#urlTab"),
    fileSource: document.querySelector("#fileSource"),
    urlSource: document.querySelector("#urlSource"),
    videoFile: document.querySelector("#videoFile"),
    dropZone: document.querySelector("#dropZone"),
    urlForm: document.querySelector("#urlForm"),
    videoUrl: document.querySelector("#videoUrl"),
    previewUrlButton: document.querySelector("#previewUrlButton"),
    cookieBrowser: document.querySelector("#cookieBrowser"),
    sourceStatus: document.querySelector("#sourceStatus"),
    previewPanel: document.querySelector("#previewPanel"),
    emptyPreview: document.querySelector("#emptyPreview"),
    loadingPreview: document.querySelector("#loadingPreview"),
    loadingPreviewTitle: document.querySelector("#loadingPreviewTitle"),
    loadingPreviewCopy: document.querySelector("#loadingPreviewCopy"),
    cancelPreview: document.querySelector("#cancelPreview"),
    previewError: document.querySelector("#previewError"),
    previewErrorTitle: document.querySelector("#previewErrorTitle"),
    previewErrorMessage: document.querySelector("#previewErrorMessage"),
    retryPreview: document.querySelector("#retryPreview"),
    openPreviewSource: document.querySelector("#openPreviewSource"),
    video: document.querySelector("#videoPreview"),
    youtubePreview: document.querySelector("#youtubePreview"),
    previewMeta: document.querySelector("#previewMeta"),
    sourceName: document.querySelector("#sourceName"),
    sourceDetails: document.querySelector("#sourceDetails"),
    openSourceLink: document.querySelector("#openSourceLink"),
    replaceButton: document.querySelector("#replaceButton"),
    previewNotice: document.querySelector("#previewNotice"),
    timelinePanel: document.querySelector("#timelinePanel"),
    selectionSummary: document.querySelector("#selectionSummary"),
    capabilityNote: document.querySelector("#capabilityNote"),
    previewSelection: document.querySelector("#previewSelection"),
    captureFrame: document.querySelector("#captureFrame"),
    rangeTrack: document.querySelector("#rangeTrack"),
    startRange: document.querySelector("#startRange"),
    endRange: document.querySelector("#endRange"),
    rangeStartLabel: document.querySelector("#rangeStartLabel"),
    rangeMiddleLabel: document.querySelector("#rangeMiddleLabel"),
    rangeEndLabel: document.querySelector("#rangeEndLabel"),
    startTime: document.querySelector("#startTime"),
    endTime: document.querySelector("#endTime"),
    setStartCurrent: document.querySelector("#setStartCurrent"),
    setEndCurrent: document.querySelector("#setEndCurrent"),
    rangeError: document.querySelector("#rangeError"),
    clipForm: document.querySelector("#clipForm"),
    outputFormat: document.querySelector("#outputFormat"),
    videoQuality: document.querySelector("#videoQuality"),
    muteAudio: document.querySelector("#muteAudio"),
    stripMetadata: document.querySelector("#stripMetadata"),
    fastCopy: document.querySelector("#fastCopy"),
    moreOptions: document.querySelector("#moreOptions"),
    youtubeTroubleshooting: document.querySelector("#youtubeTroubleshooting"),
    exportNote: document.querySelector("#exportNote"),
    createClip: document.querySelector("#createClip"),
    createClipLabel: document.querySelector("#createClipLabel"),
    formStatus: document.querySelector("#formStatus"),
    exportJob: document.querySelector("#exportJob"),
    exportPhase: document.querySelector("#exportPhase"),
    exportElapsed: document.querySelector("#exportElapsed"),
    exportPercent: document.querySelector("#exportPercent"),
    exportProgress: document.querySelector("#exportProgress"),
    exportMessage: document.querySelector("#exportMessage"),
    exportError: document.querySelector("#exportError"),
    exportAnnouncement: document.querySelector("#exportAnnouncement"),
    cancelExport: document.querySelector("#cancelExport"),
    downloadResult: document.querySelector("#downloadResult"),
    jobSource: document.querySelector("#jobSource"),
    jobRange: document.querySelector("#jobRange"),
    jobOutput: document.querySelector("#jobOutput"),
  };

  let youtubeApiPromise = null;

  function mutationHeaders(extra = {}) {
    return { ...API_MUTATION_HEADER, ...extra };
  }

  function readActiveWork() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(ACTIVE_WORK_KEY) || "null");
      return saved && typeof saved.id === "string" ? saved : null;
    } catch {
      return null;
    }
  }

  function persistActiveWork(kind, id, summary = state.activeJobSummary) {
    if (!id) return;
    try {
      sessionStorage.setItem(ACTIVE_WORK_KEY, JSON.stringify({ kind, id, summary }));
    } catch {
      // Recovery is optional when session storage is unavailable.
    }
  }

  function clearActiveWork(id = "") {
    try {
      const saved = readActiveWork();
      if (!id || !saved || saved.id === id) sessionStorage.removeItem(ACTIVE_WORK_KEY);
    } catch {
      // Nothing else depends on browser storage.
    }
  }

  function switchSourceTab(tab, focus = false) {
    const fileActive = tab === "file";
    elements.fileTab.classList.toggle("active", fileActive);
    elements.fileTab.setAttribute("aria-selected", String(fileActive));
    elements.fileTab.tabIndex = fileActive ? 0 : -1;
    elements.urlTab.classList.toggle("active", !fileActive);
    elements.urlTab.setAttribute("aria-selected", String(!fileActive));
    elements.urlTab.tabIndex = fileActive ? -1 : 0;
    elements.fileSource.classList.toggle("active", fileActive);
    elements.fileSource.hidden = !fileActive;
    elements.urlSource.classList.toggle("active", !fileActive);
    elements.urlSource.hidden = fileActive;
    if (focus) (fileActive ? elements.fileTab : elements.urlTab).focus();
  }

  function handleTabKey(event) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const currentIndex = event.currentTarget === elements.fileTab ? 0 : 1;
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? 1
        : event.key === "ArrowRight"
          ? (currentIndex + 1) % 2
          : (currentIndex - 1 + 2) % 2;
    switchSourceTab(nextIndex === 0 ? "file" : "url", true);
  }

  function setSourceStatus(message, isError = false) {
    elements.sourceStatus.textContent = message;
    elements.sourceStatus.classList.toggle("error", isError);
  }

  function setFormStatus(message, isError = false) {
    elements.formStatus.textContent = message;
    elements.formStatus.classList.toggle("error", isError);
  }

  function setPreviewNotice(message, isError = false) {
    elements.previewNotice.textContent = message;
    elements.previewNotice.classList.toggle("error", isError);
  }

  function showPreviewState(kind) {
    elements.emptyPreview.hidden = kind !== "empty";
    elements.loadingPreview.hidden = kind !== "loading";
    elements.previewError.hidden = kind !== "error";
    elements.video.hidden = kind !== "local";
    elements.youtubePreview.hidden = kind !== "youtube";
    elements.previewPanel.setAttribute("aria-busy", String(kind === "loading"));
  }

  function showPreviewError(title, message, sourceUrl = "", kind = state.sourceKind) {
    state.previewErrorKind = kind;
    elements.previewErrorTitle.textContent = title;
    elements.previewErrorMessage.textContent = message;
    elements.openPreviewSource.hidden = !sourceUrl;
    elements.openPreviewSource.href = sourceUrl || "#";
    elements.retryPreview.textContent = kind === "local" ? "Choose another file" : "Try again";
    showPreviewState("error");
    requestAnimationFrame(() => elements.previewError.focus());
  }

  function cleanupMedia() {
    state.previewingSelection = false;
    elements.previewSelection.textContent = "Play clip";
    if (state.durationClock) {
      clearInterval(state.durationClock);
      state.durationClock = null;
    }
    if (state.youtubeClock) {
      clearInterval(state.youtubeClock);
      state.youtubeClock = null;
    }
    if (state.youtubePlayer && typeof state.youtubePlayer.destroy === "function") {
      try {
        state.youtubePlayer.destroy();
      } catch {
        // The iframe may already have been removed by a browser navigation.
      }
    }
    state.youtubePlayer = null;
    elements.youtubePreview.replaceChildren();
    elements.video.pause();
    elements.video.removeAttribute("src");
    elements.video.load();
    if (state.localObjectUrl) {
      URL.revokeObjectURL(state.localObjectUrl);
      state.localObjectUrl = null;
    }
  }

  function resetSource({ preserveUrl = false } = {}) {
    state.sourceRevision += 1;
    if (state.previewRequest) state.previewRequest.abort();
    state.previewRequest = null;
    cleanupMedia();
    state.sourceKind = null;
    state.localFile = null;
    state.youtube = null;
    state.previewErrorKind = null;
    state.duration = 0;
    state.start = 0;
    state.end = 0;
    elements.videoFile.value = "";
    if (!preserveUrl) elements.videoUrl.value = "";
    elements.previewMeta.hidden = true;
    elements.openSourceLink.hidden = true;
    elements.capabilityNote.hidden = true;
    elements.youtubeTroubleshooting.hidden = true;
    elements.youtubeTroubleshooting.open = false;
    elements.cookieBrowser.value = "none";
    elements.exportJob.hidden = true;
    resetExportPanel();
    setEditorEnabled(false);
    syncEditor("", true);
    setSourceStatus("");
    setPreviewNotice("");
    showPreviewState("empty");
  }

  async function handleLocalFile(file) {
    if (!file || state.processing) return;
    const supportedName = /\.(mp4|mov|mkv|webm|avi|m4v|mpeg|mpg|ts)$/i.test(file.name);
    if (!(file.type || "").startsWith("video/") && !supportedName) {
      const cancelled = await cancelCurrentWork({ silent: true });
      if (!cancelled || state.processing) return;
      state.activeJobId = null;
      clearActiveWork();
      resetSource();
      switchSourceTab("file");
      const message = "Choose an MP4, MOV, MKV, WebM, AVI, M4V, MPEG, MPG, or TS video.";
      showPreviewError("Unsupported video file", message, "", "local");
      setSourceStatus(message, true);
      return;
    }

    const cancelled = await cancelCurrentWork({ silent: true });
    if (!cancelled || state.processing) return;
    state.activeJobId = null;
    clearActiveWork();
    resetSource();
    const revision = state.sourceRevision;
    switchSourceTab("file");
    state.sourceKind = "local";
    state.localFile = file;
    state.localObjectUrl = URL.createObjectURL(file);
    state.duration = 0;
    elements.video.src = state.localObjectUrl;
    elements.sourceName.textContent = file.name;
    elements.sourceDetails.textContent = `${formatBytes(file.size)} | Reading video details`;
    elements.previewMeta.hidden = false;
    elements.capabilityNote.hidden = true;
    showPreviewState("local");
    setSourceStatus("Previewing locally. The file is not sent to the server until export.");
    elements.video.load();

    const failIfCurrent = () => {
      if (revision !== state.sourceRevision || state.sourceKind !== "local") return;
      showPreviewError(
        "This browser cannot preview the file",
        "Choose a browser-friendly MP4 or WebM file. This source cannot be trimmed until its preview duration is available.",
        "",
        "local",
      );
      setSourceStatus("The selected file could not be previewed.", true);
    };
    elements.video.addEventListener("error", failIfCurrent, { once: true });
  }

  function onLocalMetadataLoaded() {
    if (state.sourceKind !== "local") return;
    const duration = Number(elements.video.duration);
    if (!Number.isFinite(duration) || duration <= 0) {
      const message = "This browser could not read the video duration. Choose a browser-friendly MP4 or WebM file.";
      showPreviewError("Video duration unavailable", message, "", "local");
      setSourceStatus(message, true);
      return;
    }
    initializeTimeline(duration);
    const dimensions = elements.video.videoWidth && elements.video.videoHeight
      ? `${elements.video.videoWidth} x ${elements.video.videoHeight}`
      : "Video";
    elements.sourceDetails.textContent = `${dimensions} | ${formatTime(duration, true)} | ${formatBytes(state.localFile?.size || 0)}`;
    setSourceStatus("Ready. Choose a range and export when you are done.");
  }

  async function previewUrl(event) {
    if (event) event.preventDefault();
    const url = elements.videoUrl.value.trim();
    if (!url) {
      setSourceStatus("Enter a YouTube video link.", true);
      elements.videoUrl.focus();
      return;
    }

    const cancelled = await cancelCurrentWork({ silent: true });
    if (!cancelled || state.processing) return;
    state.activeJobId = null;
    clearActiveWork();
    resetSource({ preserveUrl: true });
    switchSourceTab("url");
    const revision = state.sourceRevision;
    const controller = new AbortController();
    state.previewRequest = controller;
    elements.previewUrlButton.disabled = true;
    elements.loadingPreviewTitle.textContent = "Opening YouTube preview";
    elements.loadingPreviewCopy.textContent = "Resolving the video link. No media is being downloaded.";
    showPreviewState("loading");
    setSourceStatus("Opening the streaming preview.");

    try {
      const response = await fetch("/api/preview", {
        method: "POST",
        headers: mutationHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ url }),
        signal: controller.signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Could not open this video.");
      if (revision !== state.sourceRevision) return;
      state.previewRequest = null;
      state.sourceKind = "youtube";
      state.youtube = payload;
      elements.sourceName.textContent = "YouTube video";
      elements.sourceDetails.textContent = "Streaming preview | Loading duration";
      elements.previewMeta.hidden = false;
      elements.openSourceLink.href = payload.canonical_url;
      elements.openSourceLink.hidden = false;
      elements.capabilityNote.textContent = "Frame capture is available for local previews only.";
      elements.capabilityNote.hidden = false;
      setSourceStatus("Preview streams directly from YouTube or Google. CutFrame has not downloaded the video.");
      mountYouTubeIframe(payload, revision);
    } catch (error) {
      if (error.name === "AbortError" || revision !== state.sourceRevision) return;
      showPreviewError("Could not open this link", error.message, "", "youtube");
      setSourceStatus(error.message, true);
    } finally {
      if (revision === state.sourceRevision) {
        elements.previewUrlButton.disabled = false;
        state.previewRequest = null;
      }
    }
  }

  function mountYouTubeIframe(payload, revision) {
    const iframe = document.createElement("iframe");
    iframe.id = `youtube-frame-${revision}`;
    iframe.src = payload.embed_url;
    iframe.title = "YouTube video preview";
    iframe.allow = "autoplay; encrypted-media; picture-in-picture";
    iframe.allowFullscreen = true;
    iframe.referrerPolicy = "strict-origin-when-cross-origin";
    elements.youtubePreview.replaceChildren(iframe);
    showPreviewState("youtube");
    setPreviewNotice("The preview streams from YouTube. Exact exports may use a more precise cut than the player seek position.");

    loadYouTubeApi()
      .then(() => attachYouTubePlayer(iframe, payload, revision))
      .catch(() => {
        if (revision !== state.sourceRevision) return;
        setPreviewNotice("The video can still play, but trim synchronization is unavailable because the YouTube player API did not load.", true);
      });
  }

  function loadYouTubeApi() {
    if (window.YT && typeof window.YT.Player === "function") return Promise.resolve(window.YT);
    if (youtubeApiPromise) return youtubeApiPromise;
    youtubeApiPromise = new Promise((resolve, reject) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        resolve(window.YT);
      };
      window.onYouTubeIframeAPIReady = finish;
      const existing = document.querySelector('script[src="https://www.youtube.com/iframe_api"]');
      const script = existing || document.createElement("script");
      if (!existing) {
        script.src = "https://www.youtube.com/iframe_api";
        script.async = true;
        document.head.appendChild(script);
      }
      script.addEventListener("error", () => {
        if (settled) return;
        settled = true;
        youtubeApiPromise = null;
        reject(new Error("The YouTube player API could not be loaded."));
      }, { once: true });
      setTimeout(() => {
        if (window.YT && typeof window.YT.Player === "function") finish();
        else if (!settled) {
          settled = true;
          youtubeApiPromise = null;
          reject(new Error("The YouTube player API timed out."));
        }
      }, 20000);
    });
    return youtubeApiPromise;
  }

  function attachYouTubePlayer(iframe, payload, revision) {
    if (revision !== state.sourceRevision || state.sourceKind !== "youtube") return;
    try {
      state.youtubePlayer = new window.YT.Player(iframe, {
        events: {
          onReady: (event) => onYouTubeReady(event, revision),
          onStateChange: (event) => onYouTubeStateChange(event, revision),
          onError: (event) => onYouTubeError(event, revision),
        },
      });
    } catch {
      const target = document.createElement("div");
      target.id = `youtube-player-${revision}`;
      elements.youtubePreview.replaceChildren(target);
      state.youtubePlayer = new window.YT.Player(target, {
        host: "https://www.youtube-nocookie.com",
        videoId: payload.video_id,
        playerVars: {
          controls: 1,
          playsinline: 1,
          rel: 0,
          origin: window.location.origin,
        },
        events: {
          onReady: (event) => onYouTubeReady(event, revision),
          onStateChange: (event) => onYouTubeStateChange(event, revision),
          onError: (event) => onYouTubeError(event, revision),
        },
      });
    }
  }

  function onYouTubeReady(event, revision) {
    if (revision !== state.sourceRevision || state.sourceKind !== "youtube") return;
    state.youtubePlayer = event.target;
    readYouTubeDuration(revision);
    let attempts = 0;
    state.durationClock = setInterval(() => {
      attempts += 1;
      if (readYouTubeDuration(revision) || attempts >= 60) {
        clearInterval(state.durationClock);
        state.durationClock = null;
        if (!state.duration && revision === state.sourceRevision) {
          setPreviewNotice("Press play once so YouTube can load the duration, then the trim controls will unlock.");
        }
      }
    }, 500);
    state.youtubeClock = setInterval(syncYouTubeClock, 200);
  }

  function readYouTubeDuration(revision) {
    if (revision !== state.sourceRevision || !state.youtubePlayer) return false;
    let duration = 0;
    try {
      duration = Number(state.youtubePlayer.getDuration());
    } catch {
      return false;
    }
    if (!Number.isFinite(duration) || duration <= 0) return false;
    if (Math.abs(duration - state.duration) > 0.05) {
      initializeTimeline(duration);
      let title = "YouTube video";
      try {
        title = state.youtubePlayer.getVideoData()?.title || title;
      } catch {
        // Title is optional and not available for every embed.
      }
      elements.sourceName.textContent = title;
      elements.sourceDetails.textContent = `YouTube | ${formatTime(duration, true)} | Streaming preview`;
      setSourceStatus("Ready. Only the selected range will be fetched when you export.");
    }
    return true;
  }

  function onYouTubeStateChange(event, revision) {
    if (revision !== state.sourceRevision) return;
    readYouTubeDuration(revision);
    if (event.data === window.YT.PlayerState.ENDED && state.previewingSelection) {
      finishSelectionPreview();
    }
    if (event.data === window.YT.PlayerState.PAUSED && state.previewingSelection) {
      const current = getCurrentTime();
      if (current < state.end - 0.05) finishSelectionPreview(false);
    }
  }

  function onYouTubeError(event, revision) {
    if (revision !== state.sourceRevision) return;
    const messages = {
      2: "The YouTube video ID is invalid.",
      5: "YouTube could not play this video in an HTML5 player.",
      100: "This video is private, removed, or unavailable.",
      101: "The uploader does not allow this video to play in embedded players.",
      150: "The uploader does not allow this video to play in embedded players.",
      153: "YouTube could not verify the embedded player request.",
    };
    const message = messages[event.data] || "YouTube could not play this video here.";
    showPreviewError("YouTube preview unavailable", message, state.youtube?.canonical_url || "", "youtube");
    setSourceStatus(message, true);
  }

  function initializeTimeline(duration) {
    state.duration = duration;
    state.start = 0;
    state.end = duration;
    elements.startRange.max = String(duration);
    elements.endRange.max = String(duration);
    elements.startRange.value = "0";
    elements.endRange.value = String(duration);
    setEditorEnabled(true);
    syncEditor();
  }

  function setEditorEnabled(enabled) {
    const sourceReady = enabled && state.duration > 0 && !state.processing;
    elements.timelinePanel.setAttribute("aria-disabled", String(!sourceReady));
    [
      elements.startTime,
      elements.endTime,
      elements.startRange,
      elements.endRange,
      elements.setStartCurrent,
      elements.setEndCurrent,
      elements.previewSelection,
    ].forEach((control) => {
      control.disabled = !sourceReady;
    });
    elements.captureFrame.disabled = !sourceReady || state.sourceKind !== "local";
    elements.createClip.disabled = !sourceReady || state.processing;
  }

  function syncEditor(origin = "", clearError = true) {
    const maximum = Math.max(0, state.duration);
    state.start = clamp(state.start, 0, Math.max(0, maximum - 0.01));
    state.end = maximum > 0
      ? clamp(state.end, state.start + 0.01, maximum)
      : 0;

    if (origin !== "startRange") elements.startRange.value = String(state.start);
    if (origin !== "endRange") elements.endRange.value = String(state.end);
    if (origin !== "startTime") elements.startTime.value = formatTime(state.start, true);
    if (origin !== "endTime") elements.endTime.value = formatTime(state.end, true);
    const startPercent = maximum ? (state.start / maximum) * 100 : 0;
    const endPercent = maximum ? (state.end / maximum) * 100 : 100;
    elements.rangeTrack.style.setProperty("--start", `${startPercent}%`);
    elements.rangeTrack.style.setProperty("--end", `${endPercent}%`);
    elements.rangeStartLabel.textContent = formatTime(0);
    elements.rangeMiddleLabel.textContent = formatTime(maximum / 2);
    elements.rangeEndLabel.textContent = formatTime(maximum);
    elements.selectionSummary.textContent = maximum
      ? `${formatTime(state.end - state.start, true)} selected from ${formatTime(maximum, true)}`
      : "Choose a video to set the range.";
    elements.startRange.setAttribute("aria-valuetext", formatTime(state.start, true));
    elements.endRange.setAttribute("aria-valuetext", formatTime(state.end, true));
    if (clearError) elements.rangeError.textContent = "";
    updatePlayhead(getCurrentTime());
  }

  function updateRange(which, commit = false) {
    const input = which === "start" ? elements.startRange : elements.endRange;
    const value = Number(input.value);
    if (which === "start") {
      state.start = Math.min(value, state.end - 0.01);
      syncEditor("startRange");
    } else {
      state.end = Math.max(value, state.start + 0.01);
      syncEditor("endRange");
    }
    seekTo(value, commit);
  }

  function commitTime(which) {
    const input = which === "start" ? elements.startTime : elements.endTime;
    const parsed = parseTime(input.value);
    if (parsed === null) {
      elements.rangeError.textContent = "Use seconds, MM:SS, or HH:MM:SS.";
      syncEditor("", false);
      return;
    }
    if (which === "start" && (parsed >= state.end || parsed < 0)) {
      elements.rangeError.textContent = "Start time must be before the end time.";
      syncEditor("", false);
      return;
    }
    if (which === "end" && (parsed <= state.start || parsed > state.duration)) {
      elements.rangeError.textContent = "End time must be after start and inside the video.";
      syncEditor("", false);
      return;
    }
    state[which] = parsed;
    syncEditor(which === "start" ? "startTime" : "endTime");
    input.value = formatTime(parsed, true);
    seekTo(parsed, true);
  }

  function getCurrentTime() {
    if (state.sourceKind === "local") {
      return Number.isFinite(elements.video.currentTime) ? elements.video.currentTime : 0;
    }
    if (state.sourceKind === "youtube" && state.youtubePlayer) {
      try {
        const value = Number(state.youtubePlayer.getCurrentTime());
        return Number.isFinite(value) ? value : 0;
      } catch {
        return 0;
      }
    }
    return 0;
  }

  function seekTo(seconds, allowAhead) {
    const target = clamp(seconds, 0, state.duration || seconds);
    if (state.sourceKind === "local") {
      elements.video.currentTime = target;
    } else if (state.sourceKind === "youtube" && state.youtubePlayer) {
      try {
        state.youtubePlayer.seekTo(target, Boolean(allowAhead));
      } catch {
        // The player is still becoming ready.
      }
    }
    updatePlayhead(target);
  }

  function setCurrent(which) {
    const current = getCurrentTime();
    if (!Number.isFinite(current)) return;
    if (which === "start") state.start = Math.min(current, state.end - 0.01);
    else state.end = Math.max(current, state.start + 0.01);
    syncEditor();
  }

  async function playSelection() {
    state.previewingSelection = true;
    seekTo(state.start, true);
    elements.previewSelection.textContent = "Playing clip";
    try {
      if (state.sourceKind === "local") await elements.video.play();
      else if (state.youtubePlayer) state.youtubePlayer.playVideo();
    } catch {
      finishSelectionPreview(false);
      setPreviewNotice("The browser blocked playback. Press play in the preview, then try again.", true);
    }
  }

  function syncYouTubeClock() {
    if (state.sourceKind !== "youtube") return;
    const current = getCurrentTime();
    updatePlayhead(current);
    if (state.previewingSelection && current >= state.end - 0.04) finishSelectionPreview();
  }

  function stopLocalSelection() {
    const current = getCurrentTime();
    updatePlayhead(current);
    if (state.previewingSelection && current >= state.end - 0.025) finishSelectionPreview();
  }

  function finishSelectionPreview(returnToStart = true) {
    if (state.sourceKind === "local") elements.video.pause();
    else if (state.youtubePlayer) {
      try {
        state.youtubePlayer.pauseVideo();
      } catch {
        // The iframe can disappear while a source is being replaced.
      }
    }
    state.previewingSelection = false;
    elements.previewSelection.textContent = "Play clip";
    if (returnToStart) seekTo(state.start, true);
  }

  function updatePlayhead(seconds) {
    const percent = state.duration
      ? clamp((Number(seconds) / state.duration) * 100, 0, 100)
      : 0;
    elements.rangeTrack.style.setProperty("--playhead", `${percent}%`);
  }

  function captureFrame() {
    const sourceWidth = elements.video.videoWidth;
    const sourceHeight = elements.video.videoHeight;
    if (state.sourceKind !== "local" || !sourceWidth || !sourceHeight) {
      setPreviewNotice("The current local frame is not ready yet.", true);
      return;
    }
    const scale = Math.min(1, 1920 / sourceWidth, 1080 / sourceHeight);
    const width = Math.max(1, Math.round(sourceWidth * scale));
    const height = Math.max(1, Math.round(sourceHeight * scale));
    const capturedAt = getCurrentTime();
    const revision = state.sourceRevision;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { alpha: false });
    try {
      context.drawImage(elements.video, 0, 0, width, height);
      canvas.toBlob((blob) => {
        canvas.width = 1;
        canvas.height = 1;
        if (!blob) {
          setPreviewNotice("The browser could not save this frame.", true);
          return;
        }
        downloadBlob(blob, `frame-${formatFilenameTime(capturedAt)}.jpg`);
        const sizeNote = scale < 1 ? ` at ${width} x ${height}` : "";
        if (revision === state.sourceRevision) {
          setPreviewNotice(`Frame saved at ${formatTime(capturedAt, true)}${sizeNote}.`);
        }
      }, "image/jpeg", 0.9);
    } catch {
      setPreviewNotice("This source does not allow browser frame capture.", true);
    }
  }

  function updateExportOptions() {
    const format = elements.outputFormat.value;
    const audioOnly = format === "mp3" || format === "m4a";
    const copyCompatible = format === "mp4" || format === "mov";
    if (!copyCompatible) elements.fastCopy.checked = false;
    if (audioOnly) elements.muteAudio.checked = false;
    elements.fastCopy.disabled = state.processing || !copyCompatible;
    elements.videoQuality.disabled = state.processing || audioOnly || elements.fastCopy.checked;
    elements.muteAudio.disabled = state.processing || audioOnly || format === "gif";

    const notes = {
      mp4: "MP4 works almost everywhere. Smaller videos are not enlarged.",
      webm: "WebM uses VP9 video and Opus audio for efficient web playback.",
      mov: "MOV is useful for Apple software and editing workflows.",
      gif: "GIF has no audio and grows quickly. Short selections work best.",
      mp3: "MP3 exports the audio track at 192 kbps.",
      m4a: "M4A exports the audio track as AAC at 192 kbps.",
    };
    elements.exportNote.textContent = elements.fastCopy.checked
      ? "Fast copy usually avoids re-encoding, so it is quicker. Its start may move to a nearby keyframe. If copying is incompatible, CutFrame re-encodes automatically."
      : `${notes[format]} Exact cut re-encodes for precise boundaries and can take longer.`;
  }

  function submittedJobSummary() {
    const format = elements.outputFormat.options[elements.outputFormat.selectedIndex]?.text || elements.outputFormat.value;
    const audioOnly = ["mp3", "m4a"].includes(elements.outputFormat.value);
    const quality = audioOnly
      ? "Audio only"
      : elements.videoQuality.options[elements.videoQuality.selectedIndex]?.text || elements.videoQuality.value;
    return {
      kind: state.sourceKind,
      source: state.sourceKind === "local"
        ? state.localFile?.name || "Local video"
        : elements.sourceName.textContent || "YouTube video",
      range: `${formatTime(state.start, true)} to ${formatTime(state.end, true)} (${formatTime(state.end - state.start, true)})`,
      output: `${format} | ${quality} | ${elements.fastCopy.checked ? "Fast copy" : "Exact cut"}`,
    };
  }

  function renderJobSummary(summary) {
    const safe = summary && typeof summary === "object" ? summary : {};
    elements.jobSource.textContent = safe.source || "Source unavailable after reload";
    elements.jobRange.textContent = safe.range || "Submitted range";
    elements.jobOutput.textContent = safe.output || "Submitted output";
  }

  function exportOptions() {
    return {
      start: state.start,
      end: state.end,
      format: elements.outputFormat.value,
      quality: elements.videoQuality.value,
      mute: elements.muteAudio.checked,
      strip_metadata: elements.stripMetadata.checked,
      fast_copy: elements.fastCopy.checked,
    };
  }

  function validateExport() {
    if (!state.sourceKind || !state.duration) return "Choose a source video first.";
    if (!Number.isFinite(state.start) || !Number.isFinite(state.end) || state.end <= state.start) {
      return "Choose a valid clip range.";
    }
    if (state.end > state.duration + 0.05) return "The end time is past the video duration.";
    if (state.end - state.start > MAX_CLIP_SECONDS) return "A single clip can be at most 12 hours long.";
    return "";
  }

  async function createClip(event) {
    event.preventDefault();
    const validationError = validateExport();
    if (validationError) {
      setFormStatus(validationError, true);
      return;
    }
    if (state.sourceKind === "local") {
      try {
        const healthResponse = await fetch("/api/health", { cache: "no-store" });
        const tools = await responseJson(healthResponse, "Could not verify local export support.");
        state.health = tools;
        if (!tools.ffmpeg) {
          setFormStatus("FFmpeg is required before a local video can be uploaded. Install it, restart CutFrame, and try again.", true);
          elements.healthMenu.open = true;
          elements.toolHealth.focus();
          return;
        }
      } catch (error) {
        setFormStatus(`${error.message} Restart the local server, then try again.`, true);
        return;
      }
    }
    const previousWorkCancelled = await cancelCurrentWork({ silent: true });
    if (!previousWorkCancelled || state.processing) return;
    state.activeJobId = null;
    clearActiveWork();
    const operationRevision = ++state.operationRevision;
    state.processing = true;
    state.pollFailures = 0;
    state.activeJobSummary = submittedJobSummary();
    renderJobSummary(state.activeJobSummary);
    setEditorEnabled(true);
    setProcessingUi(true);
    setFormStatus("");
    resetExportPanel();
    elements.exportJob.hidden = false;
    elements.cancelExport.hidden = false;
    elements.cancelExport.disabled = false;

    try {
      let job;
      if (state.sourceKind === "local") {
        job = await uploadLocalAndCreateJob(operationRevision);
      } else {
        updateExportPanel({
          status: "queued",
          phase: "Starting export",
          progress: 0,
          message: "Sending the selected time range to the local export worker.",
        });
        const response = await fetch("/api/exports", {
          method: "POST",
          headers: mutationHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            ...exportOptions(),
            source_url: state.youtube.canonical_url,
            cookie_browser: elements.cookieBrowser.value,
          }),
        });
        job = await responseJson(response, "The YouTube export could not be started.");
      }
      if (operationRevision !== state.operationRevision) return;
      state.activeJobId = job.job_id;
      state.uploadId = null;
      persistActiveWork("job", job.job_id);
      updateExportPanel(job);
      scheduleJobPoll(operationRevision, 250);
    } catch (error) {
      if (error.name === "AbortError" || operationRevision !== state.operationRevision) return;
      const errorCode = error.code;
      const errorMessage = error.message;
      const cleanupConfirmed = await cancelCurrentWork({ silent: true });
      if (!cleanupConfirmed || state.activeJobId) {
        if (!cleanupConfirmed) setFormStatus(`${errorMessage} Cleanup could not be confirmed. Try cancelling again.`, true);
        return;
      }
      clearActiveWork();
      updateExportPanel({
        status: "error",
        phase: "Export failed",
        progress: null,
        message: errorMessage,
        error: errorMessage,
        code: errorCode,
      });
      revealYouTubeHelp(errorCode);
    }
  }

  async function uploadLocalAndCreateJob(operationRevision) {
    const file = state.localFile;
    if (!file) throw new Error("Choose the local video again.");
    state.uploadRequest = new AbortController();
    updateExportPanel({
      status: "running",
      phase: "Preparing local upload",
      progress: 0,
      message: "Creating a local chunked upload session.",
    });
    const createResponse = await fetch("/api/uploads", {
      method: "POST",
      headers: mutationHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ filename: file.name, size: file.size }),
      signal: state.uploadRequest.signal,
    });
    const upload = await responseJson(createResponse, "The local upload could not be started.");
    state.uploadId = upload.upload_id;
    persistActiveWork("upload", upload.upload_id);
    const chunkSize = Math.max(64 * 1024, Number(upload.chunk_size) || 8 * 1024 * 1024);
    let offset = Number(upload.received) || 0;

    while (offset < file.size) {
      if (operationRevision !== state.operationRevision) throw abortError();
      const end = Math.min(file.size, offset + chunkSize);
      const chunk = file.slice(offset, end);
      let payload = null;
      let failure = null;
      let reconciledOffset = null;
      for (let attempt = 1; attempt <= 3; attempt += 1) {
        try {
          const response = await fetch(`/api/uploads/${upload.upload_id}`, {
            method: "PUT",
            headers: {
              ...API_MUTATION_HEADER,
              "Content-Type": "application/octet-stream",
              "X-Upload-Offset": String(offset),
            },
            body: chunk,
            signal: state.uploadRequest.signal,
          });
          payload = await responseJson(response, "A video chunk could not be uploaded.");
          failure = null;
          break;
        } catch (error) {
          if (error.name === "AbortError") throw error;
          if (
            error.code === "offset_mismatch"
            && Number.isFinite(error.expectedOffset)
            && error.expectedOffset >= 0
            && error.expectedOffset <= file.size
            && error.expectedOffset !== offset
          ) {
            reconciledOffset = error.expectedOffset;
            failure = null;
            break;
          }
          failure = error;
          const retryable = error.code === "short_chunk"
            || !Number.isFinite(error.status)
            || error.status === 408
            || error.status === 429
            || (error.status >= 500 && error.status !== 507);
          if (!retryable || attempt >= 3) break;
          const retryDelay = 350 * (2 ** (attempt - 1));
          updateExportPanel({
            status: "running",
            phase: "Retrying local upload",
            progress: file.size ? (offset / file.size) * 100 : 0,
            message: `A chunk was interrupted. Retry ${attempt + 1} of 3 starts shortly.`,
          });
          await delay(retryDelay);
        }
      }
      if (failure) throw failure;
      if (reconciledOffset !== null) {
        offset = reconciledOffset;
        updateExportPanel({
          status: "running",
          phase: "Resuming local upload",
          progress: file.size ? (offset / file.size) * 100 : 0,
          message: `The local server confirmed ${formatBytes(offset)}. Resuming from that offset.`,
        });
        continue;
      }
      offset = Number(payload.received);
      if (!Number.isFinite(offset) || offset < end) {
        throw new Error("The local upload stopped at an unexpected offset.");
      }
      const percent = file.size ? (offset / file.size) * 100 : 100;
      updateExportPanel({
        status: "running",
        phase: "Uploading local video",
        progress: percent,
        message: `${formatBytes(offset)} of ${formatBytes(file.size)} copied to the local server.`,
      });
    }

    updateExportPanel({
      status: "queued",
      phase: "Starting export",
      progress: null,
      message: "The local video is staged. Starting the media worker.",
    });
    const finishResponse = await fetch(`/api/uploads/${upload.upload_id}/export`, {
      method: "POST",
      headers: mutationHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(exportOptions()),
      signal: state.uploadRequest.signal,
    });
    return responseJson(finishResponse, "The local export could not be started.");
  }

  function scheduleJobPoll(operationRevision, wait = 750) {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(() => pollJob(operationRevision), wait);
  }

  async function pollJob(operationRevision) {
    if (!state.activeJobId || operationRevision !== state.operationRevision) return;
    try {
      const response = await fetch(`/api/exports/${state.activeJobId}`, {
        headers: { "Accept": "application/json" },
        cache: "no-store",
      });
      const job = await responseJson(response, "The export status could not be read.");
      if (operationRevision !== state.operationRevision) return;
      state.pollFailures = 0;
      updateExportPanel(job);
      if (TERMINAL_JOB_STATES.has(job.status)) {
        finishProcessing();
        if (job.status === "ready") {
          persistActiveWork("job", job.job_id);
          beginDownload(job);
        } else {
          clearActiveWork(job.job_id);
          state.activeJobId = null;
          revealYouTubeHelp(job.code);
        }
        return;
      }
      scheduleJobPoll(operationRevision);
    } catch (error) {
      if (operationRevision !== state.operationRevision) return;
      const permanent = error.status >= 400
        && error.status < 500
        && ![408, 429].includes(error.status);
      if (permanent) {
        const wasCancellation = state.cancellationPending && error.status === 404;
        updateExportPanel({
          status: wasCancellation ? "cancelled" : "error",
          phase: wasCancellation ? "Cancelled" : "Export unavailable",
          progress: null,
          message: wasCancellation
            ? "The export was cancelled."
            : error.message || "This export no longer exists. It may have expired.",
          error: wasCancellation ? "" : error.message,
        });
        clearActiveWork(state.activeJobId);
        state.activeJobId = null;
        finishProcessing();
        return;
      }
      state.pollFailures += 1;
      const retryDelay = Math.min(15000, 1000 * (2 ** Math.min(state.pollFailures, 4)));
      updateExportPanel({
        status: "running",
        phase: state.cancellationPending ? "Confirming cancellation" : "Reconnecting to export",
        progress: null,
        message: `The status request was interrupted. Trying again in ${Math.round(retryDelay / 1000)} seconds.`,
      });
      scheduleJobPoll(operationRevision, retryDelay);
    }
  }

  function updateExportPanel(job) {
    const cancelHadFocus = document.activeElement === elements.cancelExport;
    elements.exportJob.hidden = false;
    elements.exportPhase.textContent = job.phase || "Working";
    elements.exportMessage.textContent = job.message || "Processing the selected range.";
    elements.exportError.textContent = job.error || "";
    const terminal = TERMINAL_JOB_STATES.has(job.status);
    const progress = Number(job.progress);
    const hasProgress = job.progress !== null
      && job.progress !== undefined
      && Number.isFinite(progress);
    if (hasProgress) {
      elements.exportProgress.value = clamp(progress, 0, 100);
      elements.exportProgress.removeAttribute("data-indeterminate");
      elements.exportPercent.textContent = `${Math.round(progress)}%`;
    } else if (!terminal) {
      elements.exportProgress.removeAttribute("value");
      elements.exportProgress.setAttribute("data-indeterminate", "true");
      elements.exportPercent.textContent = "Working";
    } else {
      elements.exportProgress.removeAttribute("value");
      elements.exportProgress.removeAttribute("data-indeterminate");
      elements.exportPercent.textContent = {
        ready: "Ready",
        error: "Failed",
        cancelled: "Cancelled",
      }[job.status] || "Stopped";
    }
    const terminalElapsed = {
      ready: "Complete",
      error: "Stopped with an error",
      cancelled: "Stopped",
    }[job.status];
    elements.exportElapsed.textContent = Number.isFinite(Number(job.elapsed_seconds))
      ? `${formatElapsed(Number(job.elapsed_seconds))} elapsed`
      : terminalElapsed || (job.status === "queued" ? "Waiting for a worker" : "In progress");
    elements.cancelExport.hidden = terminal;
    elements.cancelExport.disabled = job.phase === "Cancelling";
    if (job.status !== "ready") elements.downloadResult.hidden = true;
    const announcement = terminal
      ? `${elements.exportPhase.textContent}. ${elements.exportMessage.textContent}`
      : elements.exportPhase.textContent;
    if (announcement !== state.lastAnnouncement) {
      state.lastAnnouncement = announcement;
      elements.exportAnnouncement.textContent = announcement;
    }
    if (terminal && cancelHadFocus) {
      requestAnimationFrame(() => {
        if (!elements.downloadResult.hidden) elements.downloadResult.focus();
        else if (!elements.createClip.disabled) elements.createClip.focus();
        else elements.fileTab.focus();
      });
    }
  }

  function resetExportPanel() {
    elements.exportPhase.textContent = "Preparing export";
    elements.exportElapsed.textContent = "Starting";
    elements.exportPercent.textContent = "0%";
    elements.exportProgress.value = 0;
    elements.exportMessage.textContent = "Preparing your clip.";
    elements.exportError.textContent = "";
    elements.exportAnnouncement.textContent = "";
    state.lastAnnouncement = "";
    elements.downloadResult.hidden = true;
    elements.downloadResult.removeAttribute("href");
  }

  function beginDownload(job) {
    if (!job.download_url) return;
    elements.downloadResult.href = job.download_url;
    elements.downloadResult.download = job.filename || "CutFrame-clip";
    elements.downloadResult.textContent = `Download ${job.filename || "clip"}`;
    elements.downloadResult.hidden = false;
    elements.cancelExport.hidden = true;
    setFormStatus("Export ready. Use the download button to save it directly from the local server.");
    requestAnimationFrame(() => elements.downloadResult.focus());
  }

  async function cancelCurrentWork({ silent = false } = {}) {
    const uploadId = state.uploadId;
    const jobId = state.activeJobId;
    const operationRevision = ++state.operationRevision;
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
    if (state.uploadRequest) state.uploadRequest.abort();
    state.uploadRequest = null;
    if (!uploadId && !jobId) {
      if (state.processing) finishProcessing();
      return true;
    }

    state.cancellationPending = true;
    if (!silent) {
      updateExportPanel({
        status: "running",
        phase: "Cancelling",
        progress: null,
        message: "Stopping the upload and any active media process.",
      });
    }

    let mutationSucceeded = false;
    let latestJob = null;
    const cancellationRequests = [];
    if (uploadId) {
      cancellationRequests.push(fetch(`/api/uploads/${uploadId}`, {
        method: "DELETE",
        headers: API_MUTATION_HEADER,
      }));
    }
    const exportIds = [...new Set([uploadId, jobId].filter(Boolean))];
    for (const id of exportIds) {
      cancellationRequests.push(fetch(`/api/exports/${id}`, {
        method: "DELETE",
        headers: API_MUTATION_HEADER,
      }));
    }
    const cancellationResults = await Promise.allSettled(cancellationRequests);
    for (const result of cancellationResults) {
      if (result.status !== "fulfilled") continue;
      const response = result.value;
      mutationSucceeded = mutationSucceeded || response.ok;
      if (response.status !== 204) {
        const payload = await response.json().catch(() => null);
        if (payload?.status) latestJob = payload;
      }
    }

    const candidateId = jobId || uploadId;
    state.uploadId = null;
    if (!mutationSucceeded) {
      state.uploadId = uploadId;
      state.activeJobId = jobId;
      if (!silent) {
        updateExportPanel({
          status: "running",
          phase: "Cancellation not confirmed",
          progress: null,
          message: "The local server could not be reached. Check the server, then try cancelling again.",
        });
        elements.cancelExport.disabled = false;
      }
      persistActiveWork(uploadId ? "upload" : "job", candidateId);
      return false;
    }

    if (latestJob?.status === "ready") {
      state.activeJobId = candidateId;
      updateExportPanel(latestJob);
      finishProcessing();
      persistActiveWork("job", candidateId);
      beginDownload(latestJob);
      return true;
    }

    if (latestJob && !TERMINAL_JOB_STATES.has(latestJob.status)) {
      state.activeJobId = candidateId;
      updateExportPanel(latestJob);
      persistActiveWork("job", candidateId);
      scheduleJobPoll(operationRevision, 250);
      return true;
    }

    if (uploadId) await delay(300);
    try {
      const statusResponse = await fetch(`/api/exports/${candidateId}`, {
        headers: { "Accept": "application/json" },
        cache: "no-store",
      });
      if (statusResponse.status !== 404) {
        const job = await responseJson(statusResponse, "Cancellation could not be confirmed.");
        if (!TERMINAL_JOB_STATES.has(job.status)) {
          state.activeJobId = candidateId;
          updateExportPanel(job);
          persistActiveWork("job", candidateId);
          scheduleJobPoll(operationRevision, 250);
          return true;
        }
        if (job.status === "ready") {
          state.activeJobId = candidateId;
          updateExportPanel(job);
          finishProcessing();
          persistActiveWork("job", candidateId);
          beginDownload(job);
          return true;
        }
      }
    } catch {
      state.activeJobId = candidateId;
      persistActiveWork("job", candidateId);
      if (!silent) {
        updateExportPanel({
          status: "running",
          phase: "Confirming cancellation",
          progress: null,
          message: "The cancellation request was sent. Waiting for the local server to confirm it.",
        });
      }
      scheduleJobPoll(operationRevision, 1000);
      return true;
    }

    state.activeJobId = null;
    clearActiveWork(candidateId);
    if (!silent) {
      updateExportPanel({
        status: "cancelled",
        phase: "Cancelled",
        progress: null,
        message: "The export was cancelled.",
      });
    }
    finishProcessing();
    return true;
  }

  function finishProcessing() {
    state.processing = false;
    state.uploadRequest = null;
    state.uploadId = null;
    state.cancellationPending = false;
    setProcessingUi(false);
    setEditorEnabled(Boolean(state.sourceKind && state.duration));
  }

  function setProcessingUi(processing) {
    elements.createClip.classList.toggle("loading", processing);
    elements.createClipLabel.textContent = processing ? "Export in progress" : "Export clip";
    elements.createClip.disabled = processing || !state.sourceKind || !state.duration;
    [
      elements.fileTab,
      elements.urlTab,
      elements.videoFile,
      elements.videoUrl,
      elements.previewUrlButton,
      elements.replaceButton,
      elements.outputFormat,
      elements.stripMetadata,
      elements.cookieBrowser,
    ].forEach((control) => {
      control.disabled = processing;
    });
    elements.dropZone.classList.toggle("disabled", processing);
    updateExportOptions();
  }

  function revealYouTubeHelp(code) {
    if (state.sourceKind !== "youtube" && state.activeJobSummary?.kind !== "youtube") return;
    if (["rate_limited", "authentication_required", "cookies_locked"].includes(code)) {
      elements.moreOptions.open = true;
      elements.youtubeTroubleshooting.hidden = false;
      elements.youtubeTroubleshooting.open = true;
    }
  }

  async function responseJson(response, fallbackMessage) {
    const payload = await response.json().catch(() => ({}));
    if (response.ok) return payload;
    const error = new Error(payload.error || fallbackMessage);
    error.code = payload.code;
    error.status = response.status;
    if (payload.expected_offset !== undefined) {
      error.expectedOffset = Number(payload.expected_offset);
    }
    throw error;
  }

  async function checkHealth() {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      const tools = await responseJson(response, "Could not read the media-tool status.");
      state.health = tools;
      const runtime = tools.js_runtime
        ? `${tools.js_runtime} ${tools.js_runtime_version || ""}`.trim()
        : "Not found";
      elements.toolPaths.textContent = [
        `FFmpeg: ${tools.ffmpeg ? "Ready" : "Not found"}`,
        `FFprobe: ${tools.ffprobe ? "Ready" : "Optional; using FFmpeg fallback"}`,
        `yt-dlp: ${tools.yt_dlp ? tools.yt_dlp_version || "Ready" : "Not found"}`,
        `yt-dlp update: ${tools.yt_dlp_outdated ? "Update recommended" : "Current enough"}`,
        `EJS support: ${tools.yt_dlp_ejs ? "Ready" : "Missing"}`,
        `YouTube JS runtime: ${runtime}`,
      ].join("\n");
      if (tools.max_local_file_bytes) {
        elements.fileLimit.textContent = `Choose a supported video up to ${formatBytes(tools.max_local_file_bytes)}.`;
      }
      const fullyReady = tools.ffmpeg && tools.youtube_export_ready && !tools.yt_dlp_outdated;
      elements.toolHealth.classList.toggle("ready", fullyReady);
      elements.toolHealth.classList.toggle("warning", !fullyReady);
      if (fullyReady) {
        elements.toolHealthLabel.textContent = "Media tools ready";
        elements.healthTitle.textContent = "Local and YouTube export ready";
        elements.healthMessage.textContent = "FFmpeg, yt-dlp, EJS, and a supported JavaScript runtime are available.";
      } else if (tools.ffmpeg) {
        elements.toolHealthLabel.textContent = "Local export ready";
        elements.healthTitle.textContent = "YouTube export needs setup";
        elements.healthMessage.textContent = "Local files can export. Open the technical details to see what YouTube support is missing.";
      } else {
        elements.toolHealthLabel.textContent = "FFmpeg required";
        elements.healthTitle.textContent = "Media processing is unavailable";
        elements.healthMessage.textContent = "Install the project requirements, then restart CutFrame.";
      }
    } catch {
      elements.toolHealth.classList.add("warning");
      elements.toolHealthLabel.textContent = "Service unavailable";
      elements.healthTitle.textContent = "CutFrame is not responding";
      elements.healthMessage.textContent = "Restart the local Python server and reload this page.";
    }
  }

  async function recoverActiveWork() {
    const saved = readActiveWork();
    if (!saved || state.processing) return;

    state.activeJobSummary = saved.summary || null;
    renderJobSummary(state.activeJobSummary);
    state.processing = true;
    state.pollFailures = 0;
    resetExportPanel();
    elements.exportJob.hidden = false;
    setProcessingUi(true);
    setEditorEnabled(false);
    updateExportPanel({
      status: "running",
      phase: "Restoring export",
      progress: null,
      message: "Checking the local server for work from this browser session.",
    });

    const operationRevision = ++state.operationRevision;
    try {
      const response = await fetch(`/api/exports/${saved.id}`, {
        headers: { "Accept": "application/json" },
        cache: "no-store",
      });
      if (response.status === 404 && saved.kind === "upload") {
        state.uploadId = saved.id;
        await cancelCurrentWork({ silent: false });
        elements.exportMessage.textContent = "The interrupted local upload was removed safely.";
        return;
      }
      const job = await responseJson(response, "The saved export could not be restored.");
      if (operationRevision !== state.operationRevision) return;
      state.activeJobId = saved.id;
      state.uploadId = null;
      persistActiveWork("job", saved.id);
      updateExportPanel(job);
      if (TERMINAL_JOB_STATES.has(job.status)) {
        finishProcessing();
        if (job.status === "ready") beginDownload(job);
        else {
          clearActiveWork(saved.id);
          state.activeJobId = null;
          revealYouTubeHelp(job.code);
        }
        return;
      }
      scheduleJobPoll(operationRevision, 250);
    } catch (error) {
      if (operationRevision !== state.operationRevision) return;
      const permanent = error.status >= 400
        && error.status < 500
        && ![408, 429].includes(error.status);
      if (permanent) {
        clearActiveWork(saved.id);
        state.activeJobId = null;
        state.uploadId = null;
        updateExportPanel({
          status: "error",
          phase: "Export unavailable",
          progress: null,
          message: error.message,
          error: error.message,
        });
        finishProcessing();
        return;
      }
      if (saved.kind === "upload") {
        state.processing = false;
        state.pollTimer = setTimeout(recoverActiveWork, 2000);
        updateExportPanel({
          status: "running",
          phase: "Reconnecting to interrupted upload",
          progress: null,
          message: "The local server could not be reached. Trying again in 2 seconds.",
        });
        return;
      }
      state.activeJobId = saved.id;
      persistActiveWork("job", saved.id);
      scheduleJobPoll(operationRevision, 2000);
    }
  }

  function formatTime(seconds, includeFractions = false) {
    let safe = Number(seconds);
    if (!Number.isFinite(safe) || safe < 0) safe = 0;
    if (includeFractions) safe = Math.round(safe * 1000) / 1000;
    const whole = Math.floor(safe);
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    const secs = whole % 60;
    const milliseconds = Math.round((safe - whole) * 1000);
    const fraction = includeFractions && milliseconds
      ? `.${String(milliseconds).padStart(3, "0").replace(/0+$/, "")}`
      : "";
    if (hours) {
      return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}${fraction}`;
    }
    return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}${fraction}`;
  }

  function parseTime(value) {
    const normalized = String(value || "").trim();
    if (!normalized) return null;
    const parts = normalized.split(":");
    if (parts.length > 3 || parts.some((part) => part.trim() === "" || !/^\d+(?:\.\d+)?$/.test(part.trim()))) {
      return null;
    }
    if (parts.length > 1 && parts.slice(0, -1).some((part) => !/^\d+$/.test(part.trim()))) return null;
    const numbers = parts.map(Number);
    if (numbers.length > 1 && numbers[numbers.length - 1] >= 60) return null;
    if (numbers.length === 3 && numbers[1] >= 60) return null;
    let seconds = numbers[0];
    if (numbers.length === 2) seconds = numbers[0] * 60 + numbers[1];
    if (numbers.length === 3) seconds = numbers[0] * 3600 + numbers[1] * 60 + numbers[2];
    return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
  }

  function clamp(value, minimum, maximum) {
    return Math.min(Math.max(Number(value), minimum), maximum);
  }

  function formatBytes(bytes) {
    const safe = Number(bytes);
    if (!Number.isFinite(safe) || safe <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const index = Math.min(Math.floor(Math.log(safe) / Math.log(1024)), units.length - 1);
    return `${(safe / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
  }

  function formatElapsed(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${secs}s`;
    return `${secs}s`;
  }

  function formatFilenameTime(seconds) {
    return formatTime(seconds, true).replaceAll(":", "-").replace(".", "-");
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function delay(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  function abortError() {
    return new DOMException("The operation was cancelled.", "AbortError");
  }

  elements.fileTab.addEventListener("click", () => switchSourceTab("file"));
  elements.brandHome.addEventListener("click", (event) => {
    if (!state.processing) return;
    event.preventDefault();
    setFormStatus("An export is active. Cancel it before resetting CutFrame.", true);
    elements.cancelExport.focus();
  });
  elements.urlTab.addEventListener("click", () => switchSourceTab("url"));
  elements.fileTab.addEventListener("keydown", handleTabKey);
  elements.urlTab.addEventListener("keydown", handleTabKey);
  elements.videoFile.addEventListener("change", () => handleLocalFile(elements.videoFile.files[0]));
  elements.dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (state.processing) {
      if (event.dataTransfer) event.dataTransfer.dropEffect = "none";
      return;
    }
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    elements.dropZone.classList.add("dragging");
  });
  elements.dropZone.addEventListener("dragleave", () => elements.dropZone.classList.remove("dragging"));
  elements.dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    elements.dropZone.classList.remove("dragging");
    if (state.processing) return;
    handleLocalFile(event.dataTransfer?.files?.[0]);
  });
  elements.urlForm.addEventListener("submit", previewUrl);
  elements.cancelPreview.addEventListener("click", () => {
    if (state.previewRequest) state.previewRequest.abort();
    state.previewRequest = null;
    resetSource({ preserveUrl: true });
    switchSourceTab("url");
    elements.videoUrl.focus();
  });
  elements.retryPreview.addEventListener("click", () => {
    if (state.previewErrorKind === "local") {
      state.activeJobId = null;
      clearActiveWork();
      resetSource();
      switchSourceTab("file");
      elements.videoFile.focus();
      elements.videoFile.click();
      return;
    }
    previewUrl();
  });
  elements.video.addEventListener("loadedmetadata", onLocalMetadataLoaded);
  elements.video.addEventListener("timeupdate", stopLocalSelection);
  elements.video.addEventListener("seeked", () => updatePlayhead(getCurrentTime()));
  elements.video.addEventListener("pause", () => {
    if (state.previewingSelection && getCurrentTime() < state.end - 0.025) {
      state.previewingSelection = false;
      elements.previewSelection.textContent = "Play clip";
    }
  });
  elements.replaceButton.addEventListener("click", async () => {
    const previousKind = state.sourceKind;
    const cancelled = await cancelCurrentWork({ silent: true });
    if (!cancelled || state.processing) return;
    state.activeJobId = null;
    clearActiveWork();
    resetSource();
    switchSourceTab(previousKind === "youtube" ? "url" : "file");
    (previousKind === "youtube" ? elements.videoUrl : elements.videoFile).focus();
  });
  elements.startRange.addEventListener("input", () => updateRange("start", false));
  elements.endRange.addEventListener("input", () => updateRange("end", false));
  elements.startRange.addEventListener("change", () => updateRange("start", true));
  elements.endRange.addEventListener("change", () => updateRange("end", true));
  elements.startRange.addEventListener("pointerdown", () => elements.startRange.classList.add("is-active"));
  elements.endRange.addEventListener("pointerdown", () => elements.endRange.classList.add("is-active"));
  window.addEventListener("pointerup", () => {
    elements.startRange.classList.remove("is-active");
    elements.endRange.classList.remove("is-active");
  });
  elements.startTime.addEventListener("change", () => commitTime("start"));
  elements.endTime.addEventListener("change", () => commitTime("end"));
  elements.startTime.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitTime("start");
    }
  });
  elements.endTime.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitTime("end");
    }
  });
  elements.setStartCurrent.addEventListener("click", () => setCurrent("start"));
  elements.setEndCurrent.addEventListener("click", () => setCurrent("end"));
  elements.previewSelection.addEventListener("click", playSelection);
  elements.captureFrame.addEventListener("click", captureFrame);
  elements.outputFormat.addEventListener("change", updateExportOptions);
  elements.fastCopy.addEventListener("change", updateExportOptions);
  elements.clipForm.addEventListener("submit", createClip);
  elements.cancelExport.addEventListener("click", () => cancelCurrentWork());
  window.addEventListener("beforeunload", () => {
    clearTimeout(state.pollTimer);
    if (state.previewRequest) state.previewRequest.abort();
    if (state.uploadRequest) state.uploadRequest.abort();
    if (state.localObjectUrl) URL.revokeObjectURL(state.localObjectUrl);
  });

  updateExportOptions();
  showPreviewState("empty");
  setEditorEnabled(false);
  syncEditor();
  checkHealth();
  recoverActiveWork();
})();
