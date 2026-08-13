#!/usr/bin/env node

import { spawn } from "node:child_process";
import { access, mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import process from "node:process";


const ROOT_URL = process.env.CUTFRAME_SMOKE_URL || "http://127.0.0.1:5000";
const START_SERVER = !process.env.CUTFRAME_SMOKE_URL;
const PYTHON = process.env.CUTFRAME_PYTHON || (process.platform === "win32" ? "python" : "python3");
const SMOKE_WEBM = "GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAKxEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHWTbuMU6uEElTDZ1OsggEjTbuMU6uEHFO7a1OsggKb7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsCrXsYMPQkBNgIxMYXZmNjEuNy4xMDBXQYxMYXZmNjEuNy4xMDBEiYhAn0AAAAAAABZUrmvIrgEAAAAAAAA/14EBc8WIV8EVMW2DUFycgQAitZyDdW5kiIEAhoVWX1ZQOYOBASPjg4QL68IA4JCwgSC6gSCagQJVsIRVuYEBElTDZ0B/c3OfY8CAZ8iZRaOHRU5DT0RFUkSHjExhdmY2MS43LjEwMHNz2mPAi2PFiFfBFTFtg1BcZ8ilRaOHRU5DT0RFUkSHmExhdmM2MS4xOS4xMDAgbGlidnB4LXZwOWfIoUWjiERVUkFUSU9ORIeTMDA6MDA6MDIuMDAwMDAwMDAwAB9DtnVA7eeBAKOrgQAAgIJJg0IAAfAB9gA4JBwYjAAAMHAAAHBwf/ZcBv//4Ig///V4OOeaQKOTgQDIAIYAQJKcAFQAAAMgAABDQKOTgQGQAIYAQJKcAFLgAAMgAABDQKOTgQJYAIYAQJKcAFQAAAMgAABDQKOTgQMgAIYAQJKcAFGAAAMgAABDQKOTgQPoAIYAQJKcAFQAAAMgAABDQKOTgQSwAIYAQJKcAFLgAAMgAABDQKOTgQV4AIYAQJKcAFQAAAMgAABDQKOTgQZAAIYAQJKcAE8gAAMgAABDQKOTgQcIAIYAQJKcAFQAAAMgAABDQBxTu2uRu4+zgQC3iveBAfGCAajwgQM="; // pragma: allowlist secret


function fail(message) {
  throw new Error(message);
}


async function fileExists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}


async function findExecutableOnPath(name) {
  for (const directory of String(process.env.PATH || "").split(delimiter)) {
    if (!directory) continue;
    const candidate = join(directory, name);
    if (await fileExists(candidate)) return candidate;
  }
  return null;
}


async function findBrowser() {
  if (process.env.CUTFRAME_BROWSER && await fileExists(process.env.CUTFRAME_BROWSER)) {
    return process.env.CUTFRAME_BROWSER;
  }
  const names = process.platform === "win32"
    ? ["chrome.exe", "msedge.exe"]
    : ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"];
  for (const name of names) {
    const candidate = await findExecutableOnPath(name);
    if (candidate) return candidate;
  }
  const fixed = process.platform === "win32"
    ? [
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
      ]
    : [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
      ];
  for (const candidate of fixed) {
    if (await fileExists(candidate)) return candidate;
  }
  fail("Chrome, Edge, or Chromium was not found. Set CUTFRAME_BROWSER to its executable.");
}


async function reservePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise((resolve) => server.close(resolve));
  return port;
}


async function waitFor(check, message, timeout = 20_000, interval = 100) {
  const deadline = Date.now() + timeout;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const value = await check();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  throw lastError || new Error(message);
}


async function waitForHttp(url) {
  return waitFor(async () => {
    const response = await fetch(url, { cache: "no-store" });
    return response.ok;
  }, `CutFrame did not become ready at ${url}.`);
}


class DevToolsClient {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (!message.id || !this.pending.has(message.id)) return;
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result || {});
    });
  }

  async send(method, params = {}) {
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  async evaluate(expression, awaitPromise = true) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise,
      returnByValue: true,
      userGesture: true,
    });
    if (result.exceptionDetails) {
      fail(result.exceptionDetails.exception?.description || "Browser evaluation failed.");
    }
    return result.result?.value;
  }
}


async function connectDevTools(port) {
  const target = await waitFor(async () => {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`);
    const targets = await response.json();
    return targets.find((item) => item.type === "page" && item.url.startsWith(ROOT_URL))
      || targets.find((item) => item.type === "page");
  }, "The headless browser did not expose a page target.");
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const client = new DevToolsClient(socket);
  await client.send("Runtime.enable");
  await client.send("Page.enable");
  await client.send("Page.navigate", { url: ROOT_URL });
  return client;
}


async function browserChecks(client) {
  await waitFor(
    () => client.evaluate(`document.readyState === "complete" && document.title.includes("CutFrame")`),
    "The application page did not finish loading.",
  );
  const title = await client.evaluate("document.title");
  if (!title.includes("CutFrame")) fail(`Unexpected page title: ${title}`);
  const initial = await client.evaluate(`(() => ({
    heading: document.querySelector("h1")?.textContent.trim(),
    description: document.querySelector('meta[name="description"]')?.content,
    applicationName: document.querySelector('meta[name="application-name"]')?.content,
    exportDisabled: document.querySelector("#createClip")?.disabled,
    localTabSelected: document.querySelector("#fileTab")?.getAttribute("aria-selected"),
    progressLabel: document.querySelector("#exportProgress")?.getAttribute("aria-label")
      || document.querySelector("#exportProgress")?.getAttribute("aria-labelledby"),
    hasNoscript: Boolean(document.querySelector("noscript")),
    horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }))()`);
  if (!initial.heading) fail("The primary heading is missing.");
  if (!initial.description) fail("The application description metadata is missing.");
  if (initial.applicationName !== "CutFrame") fail("The application-name metadata is incorrect.");
  if (!initial.exportDisabled) fail("Export must be disabled before choosing a source.");
  if (initial.localTabSelected !== "true") fail("The local-file tab should be selected initially.");
  if (!initial.progressLabel) fail("Export progress needs an accessible name.");
  if (!initial.hasNoscript) fail("A no-JavaScript explanation is required.");
  if (initial.horizontalOverflow) fail("The initial page has horizontal overflow.");

  const focus = await client.evaluate(`(() => {
    const input = document.querySelector("#videoUrl");
    document.querySelector("#urlTab").click();
    input.focus();
    const style = getComputedStyle(input);
    return { outlineStyle: style.outlineStyle, outlineWidth: parseFloat(style.outlineWidth) || 0 };
  })()`);
  if (focus.outlineStyle === "none" || focus.outlineWidth < 2) {
    fail("The URL field lacks a visible keyboard focus indicator.");
  }
  await client.evaluate(`document.querySelector("#fileTab").click()`);

  const localPreview = await client.evaluate(`(async () => {
    const bytes = Uint8Array.from(atob(${JSON.stringify(SMOKE_WEBM)}), (character) => character.charCodeAt(0));
    const file = new File([bytes], "smoke.webm", { type: "video/webm" });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    const input = document.querySelector("#videoFile");
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    const video = document.querySelector("#videoPreview");
    const deadline = Date.now() + 10000;
    while ((!Number.isFinite(video.duration) || video.duration <= 0) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return {
      source: video.currentSrc,
      duration: video.duration,
      error: video.error ? { code: video.error.code, message: video.error.message } : null,
      readyState: video.readyState,
      networkState: video.networkState,
      support: video.canPlayType("video/webm; codecs=vp9"),
      exportDisabled: document.querySelector("#createClip").disabled,
      captureDisabled: document.querySelector("#captureFrame").disabled,
      sourceName: document.querySelector("#sourceName").textContent.trim(),
    };
  })()`);
  if (!localPreview.source.startsWith("blob:")) fail("Local preview did not use a browser blob URL.");
  if (!(localPreview.duration > 0)) {
    fail(`Local preview metadata did not load: ${JSON.stringify(localPreview)}`);
  }
  if (localPreview.exportDisabled) fail("Export did not unlock after local metadata loaded.");
  if (localPreview.captureDisabled) fail("Frame capture did not unlock for a local preview.");
  if (localPreview.sourceName !== "smoke.webm") fail("The selected local filename was not shown.");

  const trimmedFrame = await client.evaluate(`(async () => {
    const start = document.querySelector("#startTime");
    const end = document.querySelector("#endTime");
    start.value = "0.25";
    start.dispatchEvent(new Event("change", { bubbles: true }));
    end.value = "1.5";
    end.dispatchEvent(new Event("change", { bubbles: true }));

    const video = document.querySelector("#videoPreview");
    video.currentTime = 1;
    const deadline = Date.now() + 5000;
    while ((video.readyState < 2 || Math.abs(video.currentTime - 1) > 0.05) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }

    window.__cutframeSavedFrame = null;
    const originalClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function captureDownload() {
      window.__cutframeSavedFrame = { href: this.href, download: this.download };
    };
    try {
      document.querySelector("#captureFrame").click();
      const frameDeadline = Date.now() + 5000;
      while (!window.__cutframeSavedFrame && Date.now() < frameDeadline) {
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
    } finally {
      HTMLAnchorElement.prototype.click = originalClick;
    }
    return {
      start: start.value,
      end: end.value,
      startRange: Number(document.querySelector("#startRange").value),
      endRange: Number(document.querySelector("#endRange").value),
      summary: document.querySelector("#selectionSummary").textContent.trim(),
      frame: window.__cutframeSavedFrame,
      notice: document.querySelector("#previewNotice").textContent.trim(),
    };
  })()`);
  if (trimmedFrame.startRange !== 0.25 || trimmedFrame.endRange !== 1.5) {
    fail(`Fractional trim inputs did not update the range: ${JSON.stringify(trimmedFrame)}`);
  }
  if (!trimmedFrame.summary.includes("01.25 selected")) fail("The trim summary is incorrect.");
  if (!trimmedFrame.frame?.href.startsWith("blob:") || trimmedFrame.frame.download !== "frame-00-01.jpg") {
    fail(`Frame capture did not produce a direct JPEG download: ${JSON.stringify(trimmedFrame)}`);
  }

  const selectionPlayback = await client.evaluate(`(async () => {
    const button = document.querySelector("#previewSelection");
    const video = document.querySelector("#videoPreview");
    button.click();
    const deadline = Date.now() + 5000;
    while (button.textContent.trim() !== "Play clip" && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return {
      label: button.textContent.trim(),
      currentTime: video.currentTime,
      paused: video.paused,
      notice: document.querySelector("#previewNotice").textContent.trim(),
    };
  })()`);
  if (selectionPlayback.label !== "Play clip" || !selectionPlayback.paused) {
    fail(`Selection preview did not stop at the selected end: ${JSON.stringify(selectionPlayback)}`);
  }
  if (Math.abs(selectionPlayback.currentTime - 0.25) > 0.08) {
    fail(`Selection preview did not return to its fractional start: ${JSON.stringify(selectionPlayback)}`);
  }

  await client.evaluate(`document.querySelector("#clipForm").requestSubmit()`);
  const completedExport = await waitFor(
    () => client.evaluate(`(() => {
      const link = document.querySelector("#downloadResult");
      const saved = JSON.parse(sessionStorage.getItem("cutframe.active-work.v1") || "null");
      if (!link || link.hidden || !link.getAttribute("href") || saved?.kind !== "job") return null;
      return {
        href: link.getAttribute("href"),
        jobId: saved.id,
        phase: document.querySelector("#exportPhase")?.textContent.trim(),
        status: document.querySelector("#formStatus")?.textContent.trim(),
      };
    })()`),
    "The primary local upload and export workflow did not finish.",
    90_000,
    250,
  );
  if (!/^[0-9a-f]{32}$/.test(completedExport.jobId)) fail("The completed export ID is invalid.");
  if (!completedExport.href.includes(`/api/exports/${completedExport.jobId}/download`)) {
    fail("The completed export did not expose its direct-download URL.");
  }
  if (!completedExport.status.includes("Export ready")) {
    fail(`The ready export message was not actionable: ${completedExport.status}`);
  }

  const rangedDownload = await client.evaluate(`(async () => {
    const response = await fetch(${JSON.stringify(completedExport.href)}, {
      headers: { Range: "bytes=0-15" },
      cache: "no-store",
    });
    const body = await response.arrayBuffer();
    return {
      status: response.status,
      contentRange: response.headers.get("Content-Range"),
      bytes: body.byteLength,
    };
  })()`);
  if (rangedDownload.status !== 206 || !rangedDownload.contentRange?.startsWith("bytes 0-15/")) {
    fail(`Direct download did not honor HTTP Range: ${JSON.stringify(rangedDownload)}`);
  }
  if (rangedDownload.bytes !== 16) fail("The Range smoke check read more than the requested bytes.");

  await client.send("Page.reload", { ignoreCache: true });
  const restoredExport = await waitFor(
    () => client.evaluate(`(() => {
      if (document.readyState !== "complete") return null;
      const link = document.querySelector("#downloadResult");
      if (!link || link.hidden || !link.getAttribute("href")) return null;
      return {
        href: link.getAttribute("href"),
        source: document.querySelector("#jobSource")?.textContent.trim(),
        phase: document.querySelector("#exportPhase")?.textContent.trim(),
      };
    })()`),
    "The completed export was not recovered after a page refresh.",
    30_000,
    200,
  );
  if (!restoredExport.href.includes(completedExport.jobId)) fail("Refresh recovered the wrong export.");
  if (restoredExport.source !== "smoke.webm") fail("Refresh lost the submitted job summary.");

  const preview = await client.evaluate(`(async () => {
    const response = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CutFrame-Request": "1" },
      body: JSON.stringify({ url: "https://youtu.be/dQw4w9WgXcQ?list=PLtest" }),
    });
    const payload = await response.json();
    return { status: response.status, payload };
  })()`);
  if (preview.status !== 200) fail(`Preview API returned ${preview.status}.`);
  if (preview.payload.video_id !== "dQw4w9WgXcQ") fail("Preview did not isolate one video ID.");
  if (!preview.payload.embed_url.startsWith("https://www.youtube-nocookie.com/embed/")) {
    fail("Preview did not use the privacy-enhanced YouTube host.");
  }

  await client.send("Network.enable");
  await client.send("Network.setBlockedURLs", {
    urls: [
      "*://youtube.com/*",
      "*://*.youtube.com/*",
      "*://youtube-nocookie.com/*",
      "*://*.youtube-nocookie.com/*",
    ],
  });
  const iframePreview = await client.evaluate(`(async () => {
    document.querySelector("#urlTab").click();
    const input = document.querySelector("#videoUrl");
    input.value = "https://youtu.be/dQw4w9WgXcQ?list=PLtest";
    document.querySelector("#urlForm").requestSubmit();
    const deadline = Date.now() + 10000;
    let frame;
    while (!(frame = document.querySelector("#youtubePreview iframe")) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return frame ? {
      src: frame.src,
      title: frame.title,
      allow: frame.allow,
      status: document.querySelector("#sourceStatus").textContent.trim(),
      exportDisabled: document.querySelector("#createClip").disabled,
    } : null;
  })()`);
  if (!iframePreview?.src.startsWith("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?")) {
    fail(`The UI did not mount the privacy-enhanced iframe immediately: ${JSON.stringify(iframePreview)}`);
  }
  if (!iframePreview.status.includes("has not downloaded the video")) {
    fail("The immediate YouTube preview status did not explain its streaming behavior.");
  }
  if (!iframePreview.exportDisabled) {
    fail("YouTube export unlocked before the embedded player supplied real duration metadata.");
  }

  const rejected = await client.evaluate(`(async () => {
    const response = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: "https://youtu.be/dQw4w9WgXcQ" }),
    });
    return response.status;
  })()`);
  if (rejected !== 403) fail(`Mutation without the local request header returned ${rejected}, expected 403.`);

  const widths = [1440, 820, 375, 320];
  for (const width of widths) {
    await client.send("Emulation.setDeviceMetricsOverride", {
      width,
      height: width <= 375 ? 812 : 900,
      deviceScaleFactor: 1,
      mobile: width <= 820,
    });
    await new Promise((resolve) => setTimeout(resolve, 80));
    const layout = await client.evaluate(`(() => {
      const preview = document.querySelector(".preview-stage")?.getBoundingClientRect();
      const exportButton = document.querySelector("#createClip")?.getBoundingClientRect();
      return {
        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        previewWidth: preview?.width || 0,
        exportWidth: exportButton?.width || 0,
      };
    })()`);
    if (layout.overflow) fail(`Horizontal overflow detected at ${width}px.`);
    if (layout.previewWidth <= 0 || layout.previewWidth > width + 1) {
      fail(`Preview is not usable at ${width}px.`);
    }
    if (layout.exportWidth <= 0 || layout.exportWidth > width + 1) {
      fail(`Export action is not usable at ${width}px.`);
    }
  }

  await client.send("Emulation.setEmulatedMedia", {
    features: [
      { name: "prefers-color-scheme", value: "dark" },
      { name: "prefers-reduced-motion", value: "reduce" },
    ],
  });
  const preferences = await client.evaluate(`({
    dark: matchMedia("(prefers-color-scheme: dark)").matches,
    reduced: matchMedia("(prefers-reduced-motion: reduce)").matches,
    animationDuration: getComputedStyle(document.querySelector(".preview-skeleton")).animationDuration,
    canvas: getComputedStyle(document.documentElement).getPropertyValue("--canvas").trim(),
  })`);
  if (!preferences.dark || !preferences.reduced) fail("User preference emulation did not apply.");
  if (preferences.canvas !== "#0d1117") fail("Dark-mode design tokens did not apply.");
  if (parseFloat(preferences.animationDuration) > 0.01) fail("Reduced motion did not suppress the loading animation.");

  return completedExport.jobId;
}


async function terminate(processHandle) {
  if (!processHandle || processHandle.exitCode !== null) return;
  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/PID", String(processHandle.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
    await new Promise((resolve) => killer.once("exit", resolve));
  } else {
    try {
      process.kill(-processHandle.pid, "SIGTERM");
    } catch {
      processHandle.kill("SIGTERM");
    }
  }
  if (processHandle.exitCode === null) {
    await Promise.race([
      new Promise((resolve) => processHandle.once("exit", resolve)),
      new Promise((resolve) => setTimeout(resolve, 5_000)),
    ]);
  }
}


async function removeProfile(profile) {
  let lastError;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
      return;
    } catch (error) {
      lastError = error;
      if (!error || !["EBUSY", "EPERM", "ENOTEMPTY"].includes(error.code)) throw error;
      await new Promise((resolve) => setTimeout(resolve, 150 * (attempt + 1)));
    }
  }
  process.stderr.write(`Browser smoke warning: temporary profile cleanup was deferred (${lastError?.code || "unknown"}).\n`);
}


async function main() {
  const browser = await findBrowser();
  const profile = await mkdtemp(join(tmpdir(), "cutframe-browser-"));
  const browserPort = await reservePort();
  let application;
  let chrome;
  let generatedJobId;
  try {
    if (START_SERVER) {
      application = spawn(PYTHON, ["app.py"], {
        cwd: process.cwd(),
        env: { ...process.env, FLASK_DEBUG: "0" },
        detached: process.platform !== "win32",
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      });
      application.stdout?.pipe(process.stderr);
      application.stderr?.pipe(process.stderr);
      await waitForHttp(ROOT_URL);
    }

    chrome = spawn(browser, [
      "--headless=new",
      "--disable-gpu",
      "--disable-features=Crashpad,CrashpadForTesting",
      "--disable-breakpad",
      "--disable-crash-reporter",
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-default-apps",
      "--disable-extensions",
      "--disable-sync",
      "--hide-scrollbars",
      "--no-first-run",
      "--no-default-browser-check",
      `--remote-debugging-port=${browserPort}`,
      `--user-data-dir=${profile}`,
      "--window-size=1440,900",
      ROOT_URL,
    ], {
      stdio: ["ignore", "ignore", "pipe"],
      windowsHide: true,
    });
    let browserError = "";
    chrome.stderr?.on("data", (chunk) => { browserError += String(chunk); });
    chrome.once("exit", (code) => {
      if (code && code !== 0) process.stderr.write(browserError);
    });

    const client = await connectDevTools(browserPort);
    try {
      generatedJobId = await browserChecks(client);
    } finally {
      client.socket.close();
    }
    process.stdout.write(
      "Browser smoke passed: local upload/export, Range download, refresh recovery, API trust, privacy, responsive layout, and preferences.\n",
    );
  } finally {
    await terminate(chrome);
    await terminate(application);
    if (START_SERVER && /^[0-9a-f]{32}$/.test(generatedJobId || "")) {
      await rm(join(process.cwd(), "instance", "jobs", generatedJobId), {
        recursive: true,
        force: true,
      });
    }
    await removeProfile(profile);
  }
}


main().catch((error) => {
  process.stderr.write(`Browser smoke failed: ${error.stack || error.message}\n`);
  process.exitCode = 1;
});
