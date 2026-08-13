from __future__ import annotations

import atexit
import math
import os
import queue
import re
import signal
import shutil
import subprocess
import sys
import sysconfig
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge, SecurityError


__version__ = "0.1.0"

BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = BASE_DIR / "instance" / "jobs"
MAX_CLIP_SECONDS = 12 * 60 * 60
JOB_TTL_SECONDS = 24 * 60 * 60
UPLOAD_TTL_SECONDS = 60 * 60
DEFAULT_MAX_LOCAL_FILE_BYTES = 32 * 1024 * 1024 * 1024
DEFAULT_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_REQUEST_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_ACTIVE_UPLOADS = 4
DEFAULT_MAX_PENDING_EXPORTS = 4
DEFAULT_EXPORT_TIMEOUT_SECONDS = 13 * 60 * 60
DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_MIN_FREE_DISK_BYTES = 512 * 1024 * 1024
REQUEST_MARKER_HEADER = "X-CutFrame-Request"
REQUEST_MARKER_VALUE = "1"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ALLOWED_INPUT_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v",
    ".mpeg",
    ".mpg",
    ".ts",
}
DOWNLOADED_MEDIA_EXTENSIONS = ALLOWED_INPUT_EXTENSIONS | {
    ".mp3",
    ".m4a",
    ".aac",
    ".opus",
    ".ogg",
    ".wav",
}
OUTPUT_EXTENSIONS = {"mp4", "webm", "mov", "gif", "mp3", "m4a"}
VIDEO_HEIGHTS = {"original": None, "1080": 1080, "720": 720, "480": 480, "360": 360}
COOKIE_BROWSERS = {"none", "chrome", "edge", "firefox", "brave"}
MINIMUM_YTDLP_VERSION = (2026, 7, 4)
ACTIVE_PROCESSES: set[subprocess.Popen] = set()
ACTIVE_PROCESSES_LOCK = threading.Lock()


@dataclass
class UploadSession:
    upload_id: str
    directory: Path
    filename: str
    expected_size: int
    received: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    state: str = "uploading"
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


@dataclass
class ExportJob:
    job_id: str
    directory: Path
    source_kind: str
    start: float
    end: float
    output_format: str
    quality: str
    mute: bool
    strip_metadata: bool
    fast_copy: bool
    source_path: Path | None = None
    source_url: str | None = None
    cookie_browser: str = "none"
    status: str = "queued"
    phase: str = "Queued"
    progress: float | None = 0.0
    message: str = "Waiting for an export worker."
    error: str | None = None
    error_code: str | None = None
    output_path: Path | None = None
    download_name: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    process: subprocess.Popen | None = field(default=None, repr=False)
    active_downloads: int = field(default=0, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def snapshot(self) -> dict:
        with self.lock:
            payload = {
                "job_id": self.job_id,
                "status": self.status,
                "phase": self.phase,
                "progress": round(self.progress, 1) if self.progress is not None else None,
                "message": self.message,
                "error": self.error,
                "code": self.error_code,
                "filename": self.download_name,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
            if self.started_at:
                finished = self.finished_at or time.time()
                payload["elapsed_seconds"] = round(max(0.0, finished - self.started_at), 1)
            if self.status == "ready":
                payload["download_url"] = f"/api/exports/{self.job_id}/download"
            return payload


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    resolved_ffmpeg = resolve_ffmpeg()
    upload_chunk_bytes = environment_int(
        "CUTFRAME_UPLOAD_CHUNK_BYTES",
        DEFAULT_UPLOAD_CHUNK_BYTES,
        minimum=64 * 1024,
        maximum=64 * 1024 * 1024,
    )
    max_request_bytes = environment_int(
        "CUTFRAME_MAX_REQUEST_BYTES",
        DEFAULT_MAX_REQUEST_BYTES,
        minimum=1024 * 1024,
        maximum=128 * 1024 * 1024,
    )
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=max(max_request_bytes, upload_chunk_bytes),
        MAX_LOCAL_FILE_BYTES=environment_int(
            "CUTFRAME_MAX_LOCAL_FILE_BYTES", DEFAULT_MAX_LOCAL_FILE_BYTES, minimum=1
        ),
        UPLOAD_CHUNK_BYTES=upload_chunk_bytes,
        MAX_EXPORT_WORKERS=environment_int(
            "CUTFRAME_EXPORT_WORKERS", 2, minimum=1, maximum=8
        ),
        MAX_ACTIVE_UPLOADS=environment_int(
            "CUTFRAME_MAX_ACTIVE_UPLOADS",
            DEFAULT_MAX_ACTIVE_UPLOADS,
            minimum=1,
            maximum=64,
        ),
        MAX_PENDING_EXPORTS=environment_int(
            "CUTFRAME_MAX_PENDING_EXPORTS",
            DEFAULT_MAX_PENDING_EXPORTS,
            minimum=1,
            maximum=64,
        ),
        UPLOAD_TTL_SECONDS=environment_int(
            "CUTFRAME_UPLOAD_TTL_SECONDS", UPLOAD_TTL_SECONDS, minimum=60
        ),
        JOB_TTL_SECONDS=environment_int(
            "CUTFRAME_JOB_TTL_SECONDS", JOB_TTL_SECONDS, minimum=60
        ),
        CLEANUP_INTERVAL_SECONDS=environment_int(
            "CUTFRAME_CLEANUP_INTERVAL_SECONDS", 60, minimum=0
        ),
        EXPORT_TIMEOUT_SECONDS=environment_int(
            "CUTFRAME_EXPORT_TIMEOUT_SECONDS",
            DEFAULT_EXPORT_TIMEOUT_SECONDS,
            minimum=60,
        ),
        MAX_OUTPUT_BYTES=environment_int(
            "CUTFRAME_MAX_OUTPUT_BYTES",
            DEFAULT_MAX_OUTPUT_BYTES,
            minimum=1024 * 1024,
            maximum=1024 * 1024 * 1024 * 1024,
        ),
        MIN_FREE_DISK_BYTES=environment_int(
            "CUTFRAME_MIN_FREE_DISK_BYTES",
            DEFAULT_MIN_FREE_DISK_BYTES,
            minimum=0,
            maximum=1024 * 1024 * 1024 * 1024,
        ),
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
        RUN_EXPORTS_INLINE=False,
        JOBS_DIR=JOBS_DIR,
        FFMPEG_BINARY=resolved_ffmpeg or "ffmpeg",
        FFPROBE_BINARY=resolve_ffprobe(resolved_ffmpeg) or "ffprobe",
        YTDLP_BINARY=resolve_ytdlp() or "yt-dlp",
        DENO_BINARY=resolve_deno(),
        NODE_BINARY=resolve_node(),
    )
    if test_config:
        app.config.update(test_config)

    jobs_root = Path(app.config["JOBS_DIR"]).resolve()
    jobs_root.mkdir(parents=True, exist_ok=True)
    app.config["JOBS_DIR"] = jobs_root
    cleanup_old_jobs(
        jobs_root,
        ttl_seconds=int(app.config["JOB_TTL_SECONDS"]),
        upload_ttl_seconds=int(app.config["UPLOAD_TTL_SECONDS"]),
    )
    export_jobs: dict[str, ExportJob] = {}
    upload_sessions: dict[str, UploadSession] = {}
    registry_lock = threading.RLock()
    cleanup_lock = threading.Lock()
    shutdown_lock = threading.Lock()
    shutdown_started = threading.Event()
    last_cleanup = [0.0]
    executor = ThreadPoolExecutor(
        max_workers=max(1, int(app.config["MAX_EXPORT_WORKERS"])),
        thread_name_prefix="cutframe-export",
    )
    app.extensions["cutframe_export_jobs"] = export_jobs
    app.extensions["cutframe_upload_sessions"] = upload_sessions
    app.extensions["cutframe_executor"] = executor

    def is_registered_job_directory(candidate: Path) -> bool:
        with registry_lock:
            return candidate.name in export_jobs or candidate.name in upload_sessions

    def active_export_count_locked() -> int:
        return sum(
            job.status in {"queued", "running"}
            for job in export_jobs.values()
        )

    def register_export(job: ExportJob) -> bool:
        with registry_lock:
            if shutdown_started.is_set():
                return False
            if active_export_count_locked() >= int(app.config["MAX_PENDING_EXPORTS"]):
                return False
            export_jobs[job.job_id] = job

        return True

    def dispatch_export(job: ExportJob) -> None:
        if app.config["RUN_EXPORTS_INLINE"]:
            process_export_job(job, dict(app.config))
        else:
            try:
                executor.submit(process_export_job, job, dict(app.config))
            except RuntimeError:
                with job.lock:
                    job.status = "error"
                    job.phase = "Export unavailable"
                    job.progress = None
                    job.message = "CutFrame is shutting down. Start it again before exporting."
                    job.error = job.message
                    job.error_code = "server_stopping"
                    job.finished_at = time.time()
                    job.updated_at = job.finished_at

    def submit_export(job: ExportJob) -> bool:
        if not register_export(job):
            return False
        dispatch_export(job)
        return True

    def registered_job(job_id: str) -> ExportJob | None:
        if not valid_job_id(job_id):
            return None
        with registry_lock:
            return export_jobs.get(job_id)

    def registered_upload(upload_id: str) -> UploadSession | None:
        if not valid_job_id(upload_id):
            return None
        with registry_lock:
            return upload_sessions.get(upload_id)

    def cleanup_stale_state(*, force: bool = False) -> None:
        now_monotonic = time.monotonic()
        interval = max(0, int(app.config["CLEANUP_INTERVAL_SECONDS"]))
        if not force and now_monotonic - last_cleanup[0] < interval:
            return
        if not cleanup_lock.acquire(blocking=False):
            return
        try:
            last_cleanup[0] = now_monotonic
            now = time.time()
            upload_cutoff = now - int(app.config["UPLOAD_TTL_SECONDS"])
            job_cutoff = now - int(app.config["JOB_TTL_SECONDS"])

            with registry_lock:
                stale_uploads = [
                    session
                    for session in upload_sessions.values()
                    if session.updated_at < upload_cutoff
                ]
                terminal_jobs = [
                    job
                    for job in export_jobs.values()
                    if job.status in {"ready", "error", "cancelled"}
                    and job.updated_at < job_cutoff
                ]

            removable_directories: list[Path] = []
            for session in stale_uploads:
                if not session.lock.acquire(blocking=False):
                    continue
                try:
                    with registry_lock:
                        if (
                            upload_sessions.get(session.upload_id) is session
                            and session.updated_at < upload_cutoff
                        ):
                            session.state = "expired"
                            upload_sessions.pop(session.upload_id, None)
                            removable_directories.append(session.directory)
                finally:
                    session.lock.release()

            for job in terminal_jobs:
                if not job.lock.acquire(blocking=False):
                    continue
                try:
                    with registry_lock:
                        if (
                            export_jobs.get(job.job_id) is job
                            and job.status in {"ready", "error", "cancelled"}
                            and job.active_downloads == 0
                            and job.updated_at < job_cutoff
                        ):
                            export_jobs.pop(job.job_id, None)
                            removable_directories.append(job.directory)
                finally:
                    job.lock.release()

            for directory in removable_directories:
                safe_remove_job_dir(jobs_root, directory)
            cleanup_old_jobs(
                jobs_root,
                ttl_seconds=int(app.config["JOB_TTL_SECONDS"]),
                upload_ttl_seconds=int(app.config["UPLOAD_TTL_SECONDS"]),
                should_keep=is_registered_job_directory,
            )
        finally:
            cleanup_lock.release()

    def shutdown_app(*, wait: bool = True) -> None:
        with shutdown_lock:
            if shutdown_started.is_set():
                return
            shutdown_started.set()
            with registry_lock:
                active_jobs = list(export_jobs.values())
            for job in active_jobs:
                with job.lock:
                    job.cancel_event.set()
                    if job.status == "queued":
                        job.status = "cancelled"
                        job.phase = "Cancelled"
                        job.progress = None
                        job.message = "The export was cancelled during shutdown."
                        job.error_code = "cancelled"
                        job.finished_at = time.time()
                        job.updated_at = job.finished_at
            executor.shutdown(wait=wait, cancel_futures=True)
            terminate_active_processes()

    app.extensions["cutframe_cleanup"] = cleanup_stale_state
    app.extensions["cutframe_shutdown"] = shutdown_app

    @app.before_request
    def protect_local_service():
        cleanup_stale_state()
        if shutdown_started.is_set() and request.method in UNSAFE_METHODS:
            return api_error(
                "CutFrame is shutting down. Start it again before making changes.",
                503,
                "server_stopping",
            )
        if request.method not in UNSAFE_METHODS:
            return None

        marker = request.headers.get(REQUEST_MARKER_HEADER)
        origin = request.headers.get("Origin")
        if marker != REQUEST_MARKER_VALUE:
            return api_error(
                f"This request must include {REQUEST_MARKER_HEADER}: {REQUEST_MARKER_VALUE}.",
                403,
                "request_marker_missing",
            )
        if origin and not same_origin(origin, local_request_origin()):
            return api_error(
                "Cross-origin requests are not allowed by this local service.",
                403,
                "origin_not_allowed",
            )
        return None

    @app.after_request
    def add_security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "script-src 'self' https://www.youtube.com; "
            "style-src 'self'; "
            "img-src 'self' data: blob: https://i.ytimg.com; "
            "media-src 'self' blob:; "
            "frame-src https://www.youtube-nocookie.com https://www.youtube.com; "
            "connect-src 'self' https://www.youtube.com https://www.youtube-nocookie.com"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if request.path == "/" or request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(SecurityError)
    def untrusted_host(_error):
        return api_error(
            "This local service accepts requests only through a loopback host.",
            400,
            "host_not_allowed",
        )

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        ytdlp_available = binary_available(app.config["YTDLP_BINARY"])
        ffmpeg_available = binary_available(app.config["FFMPEG_BINARY"])
        ytdlp_version = (
            get_binary_version(app.config["YTDLP_BINARY"]) if ytdlp_available else None
        )
        runtime = choose_js_runtime(app.config)
        runtime_version = get_binary_version(runtime[1]) if runtime else None
        ejs_available = ytdlp_ejs_available(app.config["YTDLP_BINARY"])
        return jsonify(
            {
                "ok": True,
                "version": __version__,
                "ffmpeg": ffmpeg_available,
                "ffprobe": binary_available(app.config["FFPROBE_BINARY"]),
                "yt_dlp": ytdlp_available,
                "yt_dlp_ejs": ejs_available,
                "ffmpeg_path": public_binary_label(app.config["FFMPEG_BINARY"]),
                "ffprobe_path": public_binary_label(app.config["FFPROBE_BINARY"]),
                "yt_dlp_path": public_binary_label(app.config["YTDLP_BINARY"]),
                "js_runtime": runtime[0] if runtime else None,
                "js_runtime_path": public_binary_label(runtime[1]) if runtime else None,
                "js_runtime_version": public_version_label(runtime_version),
                "node_path": public_binary_label(app.config["NODE_BINARY"])
                if app.config["NODE_BINARY"]
                else None,
                "yt_dlp_version": public_version_label(ytdlp_version),
                "yt_dlp_outdated": bool(
                    ytdlp_version and version_tuple(ytdlp_version) < MINIMUM_YTDLP_VERSION
                ),
                "youtube_export_ready": bool(
                    ffmpeg_available
                    and ytdlp_available
                    and ejs_available
                    and runtime
                ),
                "max_local_file_bytes": int(app.config["MAX_LOCAL_FILE_BYTES"]),
                "upload_chunk_bytes": int(app.config["UPLOAD_CHUNK_BYTES"]),
                "max_output_bytes": int(app.config["MAX_OUTPUT_BYTES"]),
                "min_free_disk_bytes": int(app.config["MIN_FREE_DISK_BYTES"]),
            }
        )

    @app.post("/api/preview")
    @app.post("/api/import")
    def preview_url():
        payload = request.get_json(silent=True) or {}
        url = str(payload.get("url", "")).strip()
        if not valid_remote_url(url):
            return api_error("Enter a valid YouTube link.", 400, "invalid_url")
        video_id = extract_youtube_video_id(url)
        if not video_id:
            return api_error(
                "CutFrame can stream-preview YouTube links. Choose a local file for other sources.",
                400,
                "unsupported_preview_url",
            )
        canonical_url = youtube_watch_url(video_id)
        return jsonify(
            {
                "ok": True,
                "kind": "youtube",
                "video_id": video_id,
                "canonical_url": canonical_url,
                "embed_url": youtube_embed_url(video_id, local_request_origin()),
            }
        )

    @app.post("/api/exports")
    def create_remote_export():
        if not binary_available(app.config["FFMPEG_BINARY"]):
            return missing_ffmpeg_error()
        payload = request.get_json(silent=True) or {}
        url = str(payload.get("source_url", "")).strip()
        video_id = extract_youtube_video_id(url)
        if not video_id:
            return api_error("Enter a valid YouTube link.", 400, "invalid_url")
        if not binary_available(app.config["YTDLP_BINARY"]):
            return missing_ytdlp_error()
        runtime = choose_js_runtime(app.config)
        if not runtime:
            return api_error(
                "YouTube export needs Deno 2.3+ or Node.js 22+. Install one, then restart CutFrame.",
                503,
                "js_runtime_missing",
            )
        if not ytdlp_ejs_available(app.config["YTDLP_BINARY"]):
            return api_error(
                (
                    "YouTube export needs yt-dlp's EJS support. Run "
                    "`python -m pip install -U \"yt-dlp[default]\"`, then restart CutFrame."
                ),
                503,
                "ytdlp_ejs_missing",
            )
        try:
            options = parse_export_options(payload)
        except ValueError as exc:
            return api_error(str(exc), 400, "invalid_export")
        cookie_browser = str(payload.get("cookie_browser", "none")).strip().lower()
        if cookie_browser not in COOKIE_BROWSERS:
            return api_error("Unsupported browser session.", 400, "invalid_browser")

        job_dir = make_job_dir(jobs_root)
        job = ExportJob(
            job_id=job_dir.name,
            directory=job_dir,
            source_kind="youtube",
            source_url=youtube_watch_url(video_id),
            cookie_browser=cookie_browser,
            **options,
        )
        if not submit_export(job):
            safe_remove_job_dir(jobs_root, job_dir)
            return api_error(
                "All export slots are busy. Wait for an active export to finish and retry.",
                429,
                "export_capacity_reached",
            )
        return jsonify(job.snapshot()), 202

    @app.post("/api/uploads")
    def create_upload():
        payload = request.get_json(silent=True) or {}
        filename = normalize_upload_filename(payload.get("filename"))
        try:
            expected_size = int(payload.get("size", 0))
        except (TypeError, ValueError):
            expected_size = 0
        extension = Path(filename).suffix.lower()
        if not filename or extension not in ALLOWED_INPUT_EXTENSIONS:
            return api_error("Choose a supported video file.", 400, "unsupported_file")
        if expected_size <= 0:
            return api_error("The selected video is empty.", 400, "empty_file")
        if expected_size > int(app.config["MAX_LOCAL_FILE_BYTES"]):
            limit = format_bytes(int(app.config["MAX_LOCAL_FILE_BYTES"]))
            return api_error(f"That video is larger than the {limit} local-file limit.", 413, "file_too_large")
        with registry_lock:
            if len(upload_sessions) >= int(app.config["MAX_ACTIVE_UPLOADS"]):
                return api_error(
                    "Too many local uploads are active. Cancel one or wait before retrying.",
                    429,
                    "upload_capacity_reached",
                )
            reserved_bytes = sum(
                max(0, session.expected_size - session.received)
                for session in upload_sessions.values()
            )
            try:
                free_bytes = shutil.disk_usage(jobs_root).free
            except OSError:
                free_bytes = expected_size + reserved_bytes
            if free_bytes < expected_size + reserved_bytes + 256 * 1024 * 1024:
                return api_error(
                    "There is not enough free disk space to stage this video.",
                    507,
                    "disk_full",
                )

            job_dir = make_job_dir(jobs_root)
            upload_id = job_dir.name
            part_path = job_dir / "upload.part"
            try:
                part_path.touch(exist_ok=False)
            except OSError as exc:
                safe_remove_job_dir(jobs_root, job_dir)
                error = classify_tool_error("upload", str(exc), include_details=False)
                return api_error(
                    str(error),
                    507 if error.code == "disk_full" else 422,
                    error.code,
                )
            session = UploadSession(upload_id, job_dir, filename, expected_size)
            upload_sessions[upload_id] = session
        return (
            jsonify(
                {
                    "upload_id": upload_id,
                    "chunk_size": int(app.config["UPLOAD_CHUNK_BYTES"]),
                    "received": 0,
                }
            ),
            201,
        )

    @app.put("/api/uploads/<upload_id>")
    def append_upload(upload_id: str):
        session = registered_upload(upload_id)
        if session is None:
            return api_error("Upload not found. Start the upload again.", 404, "upload_missing")
        try:
            offset = int(request.headers.get("X-Upload-Offset", ""))
        except ValueError:
            return api_error("The upload offset is invalid.", 400, "invalid_offset")
        if offset < 0:
            return api_error("The upload offset is invalid.", 400, "invalid_offset")
        content_length = request.content_length
        if content_length is None or content_length <= 0:
            return api_error("This upload chunk is empty.", 400, "empty_chunk")
        if content_length > int(app.config["UPLOAD_CHUNK_BYTES"]):
            return api_error("This upload chunk is too large.", 413, "chunk_too_large")

        part_path = session.directory / "upload.part"
        with session.lock:
            with registry_lock:
                is_current = upload_sessions.get(upload_id) is session
            if not is_current or session.state != "uploading":
                return api_error(
                    "Upload not found. Start the upload again.",
                    404,
                    "upload_missing",
                )
            if not safe_regular_file(session.directory, part_path):
                with registry_lock:
                    if upload_sessions.get(upload_id) is session:
                        upload_sessions.pop(upload_id, None)
                session.state = "error"
                return api_error(
                    "The staged upload is no longer safe to use. Start the upload again.",
                    409,
                    "unsafe_upload_path",
                )
            if offset < session.received and offset + content_length <= session.received:
                if not duplicate_chunk_matches(part_path, offset, content_length, request.stream):
                    return api_error(
                        "This retry does not match the chunk already stored at that offset.",
                        409,
                        "retry_content_mismatch",
                    )
                session.updated_at = time.time()
                return jsonify(
                    {
                        "received": session.received,
                        "complete": session.received == session.expected_size,
                    }
                )
            if offset != session.received:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "Upload offset does not match the server.",
                            "code": "offset_mismatch",
                            "expected_offset": session.received,
                        }
                    ),
                    409,
                )
            if offset + content_length > session.expected_size:
                return api_error("The upload is larger than expected.", 400, "size_mismatch")
            written = 0
            try:
                with part_path.open("r+b") as destination:
                    destination.seek(offset)
                    while True:
                        chunk = request.stream.read(1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
                        written += len(chunk)
                    if written != content_length:
                        destination.truncate(offset)
                        return api_error("The upload chunk was interrupted. Retry it.", 400, "short_chunk")
            except OSError as exc:
                try:
                    with part_path.open("r+b") as destination:
                        destination.truncate(offset)
                except OSError:
                    pass
                error = classify_tool_error("upload", str(exc), include_details=False)
                return api_error(str(error), 507 if error.code == "disk_full" else 422, error.code)
            session.received += written
            session.updated_at = time.time()
            return jsonify(
                {
                    "received": session.received,
                    "complete": session.received == session.expected_size,
                }
            )

    @app.post("/api/uploads/<upload_id>/export")
    def finish_upload(upload_id: str):
        existing_job = registered_job(upload_id)
        if existing_job is not None:
            return jsonify(existing_job.snapshot()), 202
        if not binary_available(app.config["FFMPEG_BINARY"]):
            return missing_ffmpeg_error()
        session = registered_upload(upload_id)
        if session is None:
            return api_error("Upload not found. Start the upload again.", 404, "upload_missing")
        payload = request.get_json(silent=True) or {}
        try:
            options = parse_export_options(payload)
        except ValueError as exc:
            return api_error(str(exc), 400, "invalid_export")

        with session.lock:
            existing_job = registered_job(upload_id)
            if existing_job is not None:
                return jsonify(existing_job.snapshot()), 202
            with registry_lock:
                is_current = upload_sessions.get(upload_id) is session
            if not is_current or session.state != "uploading":
                return api_error(
                    "Upload not found. Start the upload again.",
                    404,
                    "upload_missing",
                )
            if session.received != session.expected_size:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "The video has not finished uploading.",
                            "code": "upload_incomplete",
                            "received": session.received,
                            "expected": session.expected_size,
                        }
                    ),
                    409,
                )
            extension = Path(session.filename).suffix.lower()
            source_path = session.directory / f"source{extension}"
            part_path = session.directory / "upload.part"
            if not safe_regular_file(session.directory, part_path):
                return api_error(
                    "The staged upload is no longer safe to use. Start the upload again.",
                    409,
                    "unsafe_upload_path",
                )

            job = ExportJob(
                job_id=upload_id,
                directory=session.directory,
                source_kind="local",
                source_path=source_path,
                **options,
            )
            with registry_lock:
                if upload_sessions.get(upload_id) is not session:
                    existing_job = export_jobs.get(upload_id)
                    if existing_job is not None:
                        return jsonify(existing_job.snapshot()), 202
                    return api_error(
                        "Upload not found. Start the upload again.",
                        404,
                        "upload_missing",
                    )
                if active_export_count_locked() >= int(app.config["MAX_PENDING_EXPORTS"]):
                    return api_error(
                        "All export slots are busy. Wait for an active export to finish and retry.",
                        429,
                        "export_capacity_reached",
                    )
                try:
                    part_path.replace(source_path)
                except OSError as exc:
                    error = classify_tool_error("upload", str(exc), include_details=False)
                    return api_error(
                        str(error),
                        507 if error.code == "disk_full" else 422,
                        error.code,
                    )
                session.state = "finalized"
                session.updated_at = time.time()
                export_jobs[upload_id] = job
                upload_sessions.pop(upload_id, None)

        dispatch_export(job)
        return jsonify(job.snapshot()), 202

    @app.delete("/api/uploads/<upload_id>")
    def cancel_upload(upload_id: str):
        session = registered_upload(upload_id)
        if session is None:
            return "", 204
        with session.lock:
            with registry_lock:
                if upload_sessions.get(upload_id) is not session:
                    return "", 204
                session.state = "cancelled"
                session.updated_at = time.time()
                upload_sessions.pop(upload_id, None)
            safe_remove_job_dir(jobs_root, session.directory)
        return "", 204

    @app.get("/api/exports/<job_id>")
    def export_status(job_id: str):
        job = registered_job(job_id)
        if job is None:
            return api_error("Export not found. It may have expired.", 404, "export_missing")
        response = jsonify(job.snapshot())
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.delete("/api/exports/<job_id>")
    def cancel_export(job_id: str):
        job = registered_job(job_id)
        if job is None:
            return "", 204
        with job.lock:
            if job.status in {"ready", "error", "cancelled"}:
                return jsonify(job.snapshot())
            job.cancel_event.set()
            job.phase = "Cancelling"
            job.message = "Stopping the active media process."
            job.updated_at = time.time()
        return jsonify(job.snapshot()), 202

    @app.get("/api/exports/<job_id>/download")
    def download_export(job_id: str):
        job = registered_job(job_id)
        if job is None:
            return api_error("Export not found. It may have expired.", 404, "export_missing")
        with job.lock:
            if (
                job.status != "ready"
                or not job.output_path
                or not safe_regular_file(job.directory, job.output_path)
            ):
                return api_error("The export is not ready to download.", 409, "export_not_ready")
            output_path = job.output_path
            download_name = job.download_name or output_path.name
            job.active_downloads += 1
            job.updated_at = time.time()
        try:
            response = send_file(
                output_path,
                as_attachment=True,
                download_name=download_name,
                conditional=True,
            )
        except Exception:
            with job.lock:
                job.active_downloads = max(0, job.active_downloads - 1)
            raise

        def release_download() -> None:
            with job.lock:
                job.active_downloads = max(0, job.active_downloads - 1)
                job.updated_at = time.time()

        response.call_on_close(release_download)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error):
        return api_error(
            "That request is too large. Reload the page and use the chunked local-file uploader.",
            413,
            "request_too_large",
        )

    return app


class ToolError(RuntimeError):
    def __init__(self, message: str, code: str = "tool_error"):
        super().__init__(message)
        self.code = code


def api_error(message: str, status: int, code: str | None = None):
    payload = {"ok": False, "error": message}
    if code:
        payload["code"] = code
    return jsonify(payload), status


def missing_ffmpeg_error():
    return api_error(
        (
            "FFmpeg was not found. Install the project requirements with "
            "`python -m pip install -U -r requirements.txt`, then restart CutFrame."
        ),
        503,
        "ffmpeg_missing",
    )


def missing_ytdlp_error():
    return api_error(
        (
            "yt-dlp was not found. Install the project requirements with "
            "`python -m pip install -U -r requirements.txt`, then restart CutFrame."
        ),
        503,
        "ytdlp_missing",
    )


def parse_export_options(values) -> dict:
    try:
        start = parse_seconds(values.get("start"), "start time")
        end = parse_seconds(values.get("end"), "end time")
    except (AttributeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if end <= start:
        raise ValueError("End time must be later than start time.")
    if end - start > MAX_CLIP_SECONDS:
        raise ValueError("Selected clips can be at most 12 hours long.")

    output_format = str(values.get("format", "mp4")).strip().lower()
    quality = str(values.get("quality", "original")).strip().lower()
    if output_format not in OUTPUT_EXTENSIONS:
        raise ValueError("Unsupported output format.")
    if quality not in VIDEO_HEIGHTS:
        raise ValueError("Unsupported quality setting.")
    return {
        "start": start,
        "end": end,
        "output_format": output_format,
        "quality": quality,
        "mute": value_bool(values.get("mute")),
        "strip_metadata": value_bool(values.get("strip_metadata")),
        "fast_copy": value_bool(values.get("fast_copy")),
    }


def value_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def valid_job_id(job_id: str) -> bool:
    return len(job_id) == 32 and all(character in "0123456789abcdef" for character in job_id)


def normalize_upload_filename(value) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    filename = raw.rsplit("/", 1)[-1]
    if (
        not filename
        or len(filename) > 255
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
    ):
        return ""
    return filename


def package_available(distribution: str) -> bool:
    try:
        package_version(distribution)
    except PackageNotFoundError:
        return False
    return True


def ytdlp_ejs_available(ytdlp_binary: str) -> bool:
    if package_available("yt-dlp-ejs"):
        return True
    binary_path = Path(ytdlp_binary)
    if not binary_path.is_file():
        return False
    environment_root = binary_path.parent.parent
    package_roots = [
        environment_root / "Lib" / "site-packages",
        *environment_root.glob("lib/python*/site-packages"),
    ]
    found_python_ytdlp = False
    for package_root in package_roots:
        if not package_root.is_dir():
            continue
        found_python_ytdlp = found_python_ytdlp or (package_root / "yt_dlp").is_dir()
        if any(package_root.glob("yt_dlp_ejs-*.dist-info")):
            return True
    # Official standalone builds bundle EJS and have no sibling Python package.
    return not found_python_ytdlp


def format_bytes(value: int | float) -> str:
    size = float(max(0, value))
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    digits = 0 if unit == "B" else 1
    return f"{size:.{digits}f} {unit}"


def environment_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    value = max(minimum, value)
    return min(maximum, value) if maximum is not None else value


def binary_available(binary: str) -> bool:
    path = Path(binary)
    if path.parent != Path("."):
        return path.is_file()
    return shutil.which(binary) is not None


def resolved_binary_path(binary: str) -> str | None:
    path = Path(binary)
    if path.parent != Path(".") and path.is_file():
        return str(path.resolve())
    resolved = shutil.which(binary)
    return str(Path(resolved).resolve()) if resolved else None


def public_binary_label(binary: str | None) -> str | None:
    if not binary:
        return None
    resolved = resolved_binary_path(binary)
    return Path(resolved).name if resolved else None


def public_tool_name(tool: str) -> str:
    name = str(tool).replace("\\", "/").rsplit("/", 1)[-1]
    return name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}", name) else "Media tool"


def local_request_origin() -> str:
    return f"{request.scheme}://{request.host}"


def normalized_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme.lower(), parsed.hostname.lower().rstrip("."), port


def same_origin(candidate: str, expected: str) -> bool:
    candidate_origin = normalized_origin(candidate)
    return candidate_origin is not None and candidate_origin == normalized_origin(expected)


def first_existing_file(candidates) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path.resolve())
    return None


@lru_cache(maxsize=1)
def resolve_ytdlp() -> str | None:
    executable_name = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"
    scripts_dir = Path(sysconfig.get_path("scripts"))
    configured = os.environ.get("YTDLP_BINARY")
    candidates = [
        configured,
        BASE_DIR / "venv" / "Scripts" / executable_name,
        BASE_DIR / ".venv" / "Scripts" / executable_name,
        Path(sys.executable).parent / executable_name,
        scripts_dir / executable_name,
        shutil.which("yt-dlp"),
    ]
    return first_existing_file(candidates)


@lru_cache(maxsize=1)
def resolve_ffmpeg() -> str | None:
    configured = os.environ.get("FFMPEG_BINARY")
    direct = first_existing_file(
        [
            configured,
            BASE_DIR / ".tools" / "ffmpeg.exe",
            BASE_DIR / "bin" / "ffmpeg.exe",
            shutil.which("ffmpeg"),
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
            Path(os.environ.get("ProgramData", "C:/ProgramData"))
            / "chocolatey"
            / "bin"
            / "ffmpeg.exe",
            Path(os.environ.get("USERPROFILE", ""))
            / "scoop"
            / "shims"
            / "ffmpeg.exe",
        ]
    )
    if direct:
        return direct

    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(bundled).is_file():
            return str(Path(bundled).resolve())
    except (ImportError, RuntimeError, OSError):
        pass

    for environment in ("venv", ".venv"):
        binaries_dir = (
            BASE_DIR
            / environment
            / "Lib"
            / "site-packages"
            / "imageio_ffmpeg"
            / "binaries"
        )
        if binaries_dir.is_dir():
            bundled = next(
                (
                    candidate
                    for candidate in binaries_dir.glob("ffmpeg*.exe")
                    if candidate.is_file()
                ),
                None,
            )
            if bundled:
                return str(bundled.resolve())

    local_packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if local_packages.is_dir():
        for package_pattern in ("Gyan.FFmpeg*", "BtbN.FFmpeg*"):
            for package_dir in local_packages.glob(package_pattern):
                found = next(package_dir.rglob("ffmpeg.exe"), None)
                if found and found.is_file():
                    return str(found.resolve())
    return None


@lru_cache(maxsize=4)
def resolve_ffprobe(ffmpeg_binary: str | None = None) -> str | None:
    configured = os.environ.get("FFPROBE_BINARY")
    candidates = [
        configured,
        BASE_DIR / ".tools" / "ffprobe.exe",
        BASE_DIR / "bin" / "ffprobe.exe",
        shutil.which("ffprobe"),
    ]
    if ffmpeg_binary:
        ffmpeg_path = Path(ffmpeg_binary)
        candidates.insert(1, ffmpeg_path.with_name("ffprobe.exe"))
    direct = first_existing_file(candidates)
    if direct:
        return direct

    local_packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if local_packages.is_dir():
        for package_pattern in ("Gyan.FFmpeg*", "BtbN.FFmpeg*"):
            for package_dir in local_packages.glob(package_pattern):
                found = next(package_dir.rglob("ffprobe.exe"), None)
                if found and found.is_file():
                    return str(found.resolve())
    return None


@lru_cache(maxsize=1)
def resolve_deno() -> str | None:
    executable_name = "deno.exe" if os.name == "nt" else "deno"
    return first_existing_file(
        [
            os.environ.get("DENO_BINARY"),
            BASE_DIR / ".tools" / executable_name,
            Path(sys.executable).parent / executable_name,
            shutil.which("deno"),
        ]
    )


@lru_cache(maxsize=1)
def resolve_node() -> str | None:
    executable_name = "node.exe" if os.name == "nt" else "node"
    return first_existing_file(
        [
            os.environ.get("NODE_BINARY"),
            BASE_DIR / "node_modules" / ".bin" / executable_name,
            Path(sys.executable).parent / executable_name,
            shutil.which("node"),
        ]
    )


def choose_js_runtime(config) -> tuple[str, str] | None:
    candidates = (
        ("deno", config.get("DENO_BINARY"), (2, 3, 0)),
        ("node", config.get("NODE_BINARY"), (22, 0, 0)),
    )
    for name, binary, minimum in candidates:
        if not binary or not binary_available(binary):
            continue
        installed = get_binary_version(binary)
        if installed and version_tuple(installed) >= minimum:
            return name, binary
    return None


@lru_cache(maxsize=8)
def get_binary_version(binary: str) -> str | None:
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return next((line.strip() for line in completed.stdout.splitlines() if line.strip()), None)


def version_tuple(version: str) -> tuple[int, int, int]:
    numbers = []
    for part in version.split(".")[:3]:
        digits = "".join(character for character in part if character.isdigit())
        numbers.append(int(digits) if digits else 0)
    return tuple((numbers + [0, 0, 0])[:3])


def public_version_label(version: str | None) -> str | None:
    if not version:
        return None
    value = version.strip()
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}", value) else None


def valid_remote_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_youtube_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return (
        hostname == "youtu.be"
        or hostname == "youtube.com"
        or hostname.endswith(".youtube.com")
        or hostname == "youtube-nocookie.com"
        or hostname.endswith(".youtube-nocookie.com")
    )


def extract_youtube_video_id(url: str) -> str | None:
    if not valid_remote_url(url) or not is_youtube_url(url):
        return None
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    path_parts = [part for part in parsed.path.split("/") if part]
    candidate = ""
    if hostname == "youtu.be" or hostname.endswith(".youtu.be"):
        candidate = path_parts[0] if path_parts else ""
    elif parsed.path.rstrip("/") == "/watch":
        candidate = parse_qs(parsed.query).get("v", [""])[0]
    elif path_parts and path_parts[0].lower() in {"embed", "shorts", "live", "v"}:
        candidate = path_parts[1] if len(path_parts) > 1 else ""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate or ""):
        return candidate
    return None


def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?{urlencode({'v': video_id})}"


def youtube_embed_url(video_id: str, origin: str) -> str:
    parameters = urlencode(
        {
            "enablejsapi": "1",
            "origin": origin,
            "playsinline": "1",
            "controls": "1",
            "rel": "0",
        }
    )
    return f"https://www.youtube-nocookie.com/embed/{video_id}?{parameters}"


def build_ytdlp_section_command(
    ytdlp_binary: str,
    job_dir: Path,
    url: str,
    start: float,
    end: float,
    quality: str,
    output_format: str,
    *,
    exact: bool,
    cookie_browser: str = "none",
    ffmpeg_binary: str | None = None,
    js_runtime: tuple[str, str] | None = None,
    max_output_bytes: int | None = None,
) -> list[str]:
    height = VIDEO_HEIGHTS[quality]
    audio_only = output_format in {"mp3", "m4a"}
    if audio_only:
        format_selector = "ba[acodec~='^(mp4a|aac)']/ba/b"
    elif height:
        format_selector = (
            f"bv*[vcodec~='^avc1'][height<={height}]+ba[acodec~='^mp4a']/"
            f"b[vcodec~='^avc1'][height<={height}]/"
            f"bv*[height<={height}]+ba/b[height<={height}]/wv*+ba/w"
        )
    else:
        format_selector = (
            "bv*[vcodec~='^avc1']+ba[acodec~='^mp4a']/"
            "b[vcodec~='^avc1']/bv*+ba/b"
        )

    command = [
        ytdlp_binary,
        "--ignore-config",
        "--no-playlist",
        "--restrict-filenames",
        "--no-mtime",
        "--newline",
        "--progress",
        "--progress-delta",
        "0.5",
        "--progress-template",
        "download:cutframe|download|%(progress._percent_str)s|%(progress._eta_str)s|%(progress._speed_str)s",
        "--progress-template",
        "postprocess:cutframe|postprocess|%(progress._percent_str)s|%(progress._eta_str)s|%(progress._speed_str)s",
        "--merge-output-format",
        "mp4",
        "--concurrent-fragments",
        "4",
        "--retries",
        "10",
        "--fragment-retries",
        "10",
        "--extractor-retries",
        "5",
        "--retry-sleep",
        "http:exp=1:20",
        "--retry-sleep",
        "fragment:exp=1:20",
        "--retry-sleep",
        "extractor:linear=2:10:2",
        "--socket-timeout",
        "30",
        "--download-sections",
        f"*{start:.3f}-{end:.3f}",
        "--part",
        "--continue",
        "--downloader-args",
        (
            "ffmpeg_i:-rw_timeout 30000000 -reconnect 1 -reconnect_streamed 1 "
            "-reconnect_on_network_error 1 -reconnect_on_http_error 429,5xx "
            "-reconnect_delay_max 10 -reconnect_max_retries 5 "
            "-reconnect_delay_total_max 90"
        ),
        "-f",
        format_selector,
        "-o",
        str(job_dir / "source.%(ext)s"),
    ]
    if exact:
        command.append("--force-keyframes-at-cuts")
    if cookie_browser != "none":
        command.extend(["--cookies-from-browser", cookie_browser])
    if ffmpeg_binary:
        command.extend(["--ffmpeg-location", ffmpeg_binary])
    if js_runtime:
        command.extend(["--js-runtimes", f"{js_runtime[0]}:{js_runtime[1]}"])
    if max_output_bytes is not None:
        byte_limit = str(max(1, int(max_output_bytes)))
        # Section downloads are handled by yt-dlp's FFmpeg downloader. Apply
        # the quota to both that staged output and any FFmpeg postprocessing.
        command.extend(["--downloader-args", f"ffmpeg_o:-fs {byte_limit}"])
        command.extend(["--postprocessor-args", f"ffmpeg_o:-fs {byte_limit}"])
    command.append(url)
    return command


def make_job_dir(root: Path) -> Path:
    root = root.resolve()
    job_dir = root / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=False)
    try:
        job_dir.chmod(0o700)
    except OSError:
        pass
    return job_dir


def path_is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def path_resolves_within(root: Path, candidate: Path) -> bool:
    try:
        root_resolved = root.resolve(strict=True)
        candidate_resolved = candidate.resolve(strict=True)
        return candidate_resolved == root_resolved or root_resolved in candidate_resolved.parents
    except (OSError, RuntimeError):
        return False


def safe_job_dir(root: Path, job_id: str) -> Path | None:
    if not valid_job_id(job_id):
        return None
    root_resolved = root.resolve()
    candidate = root_resolved / job_id
    if path_is_link_or_junction(candidate) or not candidate.is_dir():
        return None
    try:
        return candidate if candidate.resolve(strict=True).parent == root_resolved else None
    except (OSError, RuntimeError):
        return None


def safe_regular_file(container: Path, candidate: Path) -> bool:
    if path_is_link_or_junction(candidate) or not candidate.is_file():
        return False
    try:
        return candidate.resolve(strict=True).parent == container.resolve(strict=True)
    except (OSError, RuntimeError):
        return False


def safe_remove_job_dir(root: Path, directory: Path) -> bool:
    if not valid_job_id(directory.name):
        return False
    safe_directory = safe_job_dir(root, directory.name)
    if safe_directory is None:
        return False
    shutil.rmtree(safe_directory, ignore_errors=True)
    return not safe_directory.exists()


def cleanup_old_jobs(
    root: Path,
    should_keep=None,
    *,
    ttl_seconds: int = JOB_TTL_SECONDS,
    upload_ttl_seconds: int | None = None,
) -> None:
    now = time.time()
    for candidate in root.iterdir():
        try:
            candidate_ttl = ttl_seconds
            part_path = candidate / "upload.part"
            if upload_ttl_seconds is not None and safe_regular_file(candidate, part_path):
                candidate_ttl = upload_ttl_seconds
            if (
                valid_job_id(candidate.name)
                and not path_is_link_or_junction(candidate)
                and safe_job_dir(root, candidate.name) is not None
                and candidate.stat().st_mtime < now - max(0, candidate_ttl)
                and not (should_keep and should_keep(candidate))
            ):
                safe_remove_job_dir(root, candidate)
        except OSError:
            continue


def find_downloaded_media(job_dir: Path) -> Path | None:
    candidates = [
        path
        for path in job_dir.iterdir()
        if safe_regular_file(job_dir, path)
        and path.suffix.lower() in DOWNLOADED_MEDIA_EXTENSIONS
        and not path.name.startswith("clip.")
    ]
    return max(candidates, key=lambda path: path.stat().st_size) if candidates else None


def duplicate_chunk_matches(part_path: Path, offset: int, length: int, stream) -> bool:
    remaining = length
    try:
        with part_path.open("rb") as stored:
            stored.seek(offset)
            while remaining:
                size = min(1024 * 1024, remaining)
                incoming = stream.read(size)
                existing = stored.read(size)
                if not incoming or incoming != existing:
                    return False
                remaining -= len(incoming)
        return remaining == 0
    except OSError:
        return False


def parse_seconds(raw_value: str | None, label: str) -> float:
    try:
        value = float("" if raw_value is None else raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Enter a valid {label}.") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Enter a valid {label}.")
    return value


def probe_media(
    source: Path,
    ffprobe_binary: str,
    ffmpeg_binary: str | None = None,
) -> dict:
    empty = {"duration": None, "width": None, "height": None}
    if binary_available(ffprobe_binary):
        command = [
            ffprobe_binary,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration:stream=width,height",
            "-of",
            "default=noprint_wrappers=1",
            str(source),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            completed = None
        if completed is not None:
            values: dict[str, str] = {}
            for line in completed.stdout.splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    values[key] = value
            try:
                duration = float(values.get("duration", ""))
            except ValueError:
                duration = None
            return {
                "duration": duration,
                "width": int(values["width"])
                if values.get("width", "").isdigit()
                else None,
                "height": int(values["height"])
                if values.get("height", "").isdigit()
                else None,
            }

    # imageio-ffmpeg intentionally bundles FFmpeg without FFprobe. FFmpeg's
    # input summary still gives us enough metadata to validate clip bounds.
    if not ffmpeg_binary or not binary_available(ffmpeg_binary):
        return empty
    try:
        completed = subprocess.run(
            [ffmpeg_binary, "-hide_banner", "-i", str(source)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return empty
    summary = completed.stderr
    duration_match = re.search(
        r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)",
        summary,
    )
    resolution_match = re.search(
        r"Video:.*?\b(\d{2,5})x(\d{2,5})\b",
        summary,
        flags=re.IGNORECASE,
    )
    duration = None
    if duration_match:
        duration = (
            int(duration_match.group(1)) * 3600
            + int(duration_match.group(2)) * 60
            + float(duration_match.group(3))
        )
    return {
        "duration": duration,
        "width": int(resolution_match.group(1)) if resolution_match else None,
        "height": int(resolution_match.group(2)) if resolution_match else None,
    }


def available_export_bytes(directory: Path, config: dict) -> int:
    output_cap = max(1, int(config.get("MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES)))
    free_space_floor = max(
        0,
        int(config.get("MIN_FREE_DISK_BYTES", DEFAULT_MIN_FREE_DISK_BYTES)),
    )
    try:
        free_bytes = int(shutil.disk_usage(directory).free)
    except OSError as exc:
        raise ToolError(
            "CutFrame could not verify free disk space. Check the jobs directory and retry.",
            "disk_check_failed",
        ) from exc
    usable_bytes = free_bytes - free_space_floor
    if usable_bytes <= 0:
        raise ToolError(
            (
                "Free disk space is below CutFrame's configured reserve. Free some space "
                "or lower CUTFRAME_MIN_FREE_DISK_BYTES before retrying."
            ),
            "disk_space_low",
        )
    # Do not require the entire configured output cap to be free. A small
    # export may use whatever remains above the safety floor.
    return min(output_cap, usable_bytes)


def enforce_output_quota(path: Path, byte_limit: int) -> None:
    try:
        reached_limit = path.stat().st_size >= max(1, int(byte_limit))
    except OSError:
        return
    if reached_limit:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ToolError(
            (
                "The selected export exceeded CutFrame's configured output-size limit. "
                "Choose a shorter range or lower resolution, or raise "
                "CUTFRAME_MAX_OUTPUT_BYTES."
            ),
            "output_too_large",
        )


def validate_export_output(output: Path, expected_duration: float, config: dict) -> None:
    if not safe_regular_file(output.parent, output) or output.stat().st_size == 0:
        raise ToolError(
            "FFmpeg finished without creating a usable clip.",
            "empty_output",
        )
    media = probe_media(
        output,
        config.get("FFPROBE_BINARY", "ffprobe"),
        config.get("FFMPEG_BINARY"),
    )
    duration = media.get("duration")
    if duration is None:
        return
    if not math.isfinite(duration) or duration <= 0:
        raise ToolError(
            "The created file could not be validated as playable media.",
            "invalid_output",
        )
    shortfall_tolerance = max(0.5, expected_duration * 0.03)
    if expected_duration > 1.0 and duration + shortfall_tolerance < expected_duration:
        raise ToolError(
            "The created file ended before the selected range was complete.",
            "invalid_output",
        )


def build_ffmpeg_command(
    ffmpeg_binary: str,
    source: Path,
    output: Path,
    start: float,
    end: float,
    output_format: str,
    quality: str,
    *,
    mute: bool,
    strip_metadata: bool,
    fast_copy: bool,
    max_output_bytes: int | None = None,
) -> list[str]:
    duration = end - start
    command = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
    ]

    can_copy = fast_copy and output_format in {"mp4", "mov"}
    if can_copy:
        command.extend(["-c", "copy"])
        if mute:
            command.append("-an")
    elif output_format == "mp3":
        command.extend(["-vn", "-c:a", "libmp3lame", "-b:a", "192k"])
    elif output_format == "m4a":
        command.extend(["-vn", "-c:a", "aac", "-b:a", "192k"])
    elif output_format == "gif":
        height = VIDEO_HEIGHTS[quality] or 720
        command.extend(
            [
                "-an",
                "-vf",
                f"fps=12,scale=-2:trunc(min({height}\\,ih)/2)*2:flags=lanczos",
                "-loop",
                "0",
            ]
        )
    else:
        video_codec = "libvpx-vp9" if output_format == "webm" else "libx264"
        audio_codec = "libopus" if output_format == "webm" else "aac"
        command.extend(["-c:v", video_codec])
        if output_format == "webm":
            command.extend(["-crf", "31", "-b:v", "0", "-c:a", audio_codec, "-b:a", "128k"])
        else:
            command.extend(
                [
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    audio_codec,
                    "-b:a",
                    "160k",
                ]
            )
            if output_format == "mp4":
                command.extend(["-movflags", "+faststart"])
        height = VIDEO_HEIGHTS[quality]
        if height:
            command.extend(
                [
                    "-vf",
                    f"scale=-2:trunc(min({height}\\,ih)/2)*2",
                ]
            )
        if mute:
            command.append("-an")

    if strip_metadata:
        command.extend(["-map_metadata", "-1"])
    if max_output_bytes is not None:
        command.extend(["-fs", str(max(1, int(max_output_bytes)))])
    command.append(str(output))
    return command


def update_export_job(
    job: ExportJob,
    *,
    status: str | None = None,
    phase: str | None = None,
    progress: float | None | object = ...,
    message: str | None = None,
) -> None:
    with job.lock:
        if status is not None:
            job.status = status
        if phase is not None:
            job.phase = phase
        if progress is not ...:
            job.progress = progress
        if message is not None:
            job.message = message
        job.updated_at = time.time()


def process_export_job(job: ExportJob, config: dict) -> None:
    cancelled_before_start = False
    with job.lock:
        if job.cancel_event.is_set():
            job.status = "cancelled"
            job.phase = "Cancelled"
            job.progress = None
            job.message = "The export was cancelled."
            job.finished_at = time.time()
            job.updated_at = job.finished_at
            cancelled_before_start = True
        else:
            job.status = "running"
            job.phase = "Preparing"
            job.progress = 1.0
            job.message = "Checking the selected range and media tools."
            job.started_at = time.time()
            job.updated_at = job.started_at
    if cancelled_before_start:
        safe_remove_job_dir(job.directory.parent, job.directory)
        return

    source = job.source_path
    remote_exact = False
    try:
        if job.source_kind == "youtube":
            runtime = choose_js_runtime(config)
            remote_exact = not job.fast_copy
            stage_byte_limit = available_export_bytes(job.directory, config)
            command = build_ytdlp_section_command(
                config["YTDLP_BINARY"],
                job.directory,
                job.source_url or "",
                job.start,
                job.end,
                job.quality,
                job.output_format,
                exact=remote_exact,
                cookie_browser=job.cookie_browser,
                ffmpeg_binary=config["FFMPEG_BINARY"],
                js_runtime=runtime,
                max_output_bytes=stage_byte_limit,
            )
            update_export_job(
                job,
                phase="Fetching selected media",
                progress=4.0,
                message="Downloading only the selected time range.",
            )

            def remote_progress(_stream: str, line: str) -> None:
                cleaned = strip_ansi(line).strip()
                if not cleaned.startswith("cutframe|"):
                    return
                parts = cleaned.split("|", 4)
                stage = parts[1] if len(parts) > 1 else "download"
                percent = parse_percent(parts[2] if len(parts) > 2 else "")
                if stage == "postprocess":
                    update_export_job(
                        job,
                        phase="Finalizing selected media",
                        progress=70.0 if percent is None else 66.0 + percent * 0.06,
                        message="Finishing the exact source cut.",
                    )
                    return
                details = [part.strip() for part in parts[3:] if part.strip() and part.strip() != "NA"]
                update_export_job(
                    job,
                    phase="Fetching selected media",
                    progress=None if percent is None else 4.0 + percent * 0.62,
                    message=" | ".join(details) if details else "Downloading the selected time range.",
                )

            attempts = 0
            while True:
                attempts += 1
                try:
                    run_streaming_command(
                        command,
                        timeout=config.get("EXPORT_TIMEOUT_SECONDS"),
                        cancel_event=job.cancel_event,
                        line_callback=remote_progress,
                        process_callback=lambda process: remember_job_process(job, process),
                    )
                    break
                except ToolError as exc:
                    if exc.code not in {"network_error", "server_error"} or attempts >= 2:
                        raise
                    update_export_job(
                        job,
                        phase="Retrying media connection",
                        progress=None,
                        message="The connection was interrupted. Refreshing the media link once.",
                    )
            source = find_downloaded_media(job.directory)
            if source is None:
                raise ToolError(
                    "The selected media was fetched, but no usable file was created.",
                    "missing_media",
                )
            enforce_output_quota(source, stage_byte_limit)

        if source is None or not safe_regular_file(job.directory, source):
            raise ToolError("The source video is no longer available.", "source_missing")
        if job.cancel_event.is_set():
            raise ToolError("The export was cancelled.", "cancelled")

        if job.source_kind == "local":
            media = probe_media(
                source,
                config.get("FFPROBE_BINARY", "ffprobe"),
                config["FFMPEG_BINARY"],
            )
            source_duration = media.get("duration")
            if source_duration is not None and (
                job.start >= source_duration or job.end > source_duration + 0.25
            ):
                raise ToolError(
                    (
                        f"The selected end time is past this video's "
                        f"{format_clock(source_duration)} duration."
                    ),
                    "invalid_range",
                )

        output = job.directory / f"clip.{job.output_format}"
        output_byte_limit = available_export_bytes(job.directory, config)
        selected_duration = job.end - job.start
        source_start = 0.0 if job.source_kind == "youtube" else job.start
        source_end = selected_duration if job.source_kind == "youtube" else job.end
        remote_copy_compatible = (
            job.source_kind == "youtube"
            and source.suffix.lower() in {".mp4", ".mov"}
            and job.output_format in {"mp4", "mov"}
        )
        effective_fast_copy = remote_copy_compatible or (
            job.source_kind == "local" and job.fast_copy
        )
        command = build_ffmpeg_command(
            config["FFMPEG_BINARY"],
            source,
            output,
            source_start,
            source_end,
            job.output_format,
            job.quality,
            mute=job.mute,
            strip_metadata=job.strip_metadata,
            fast_copy=effective_fast_copy,
            max_output_bytes=output_byte_limit,
        )
        command = [command[0], "-progress", "pipe:1", "-nostats", *command[1:]]
        update_export_job(
            job,
            phase="Creating clip",
            progress=73.0,
            message=(
                "Packaging the selected streams without re-encoding."
                if effective_fast_copy
                else "Encoding the selected clip with a fast, compatible preset."
            ),
        )

        def ffmpeg_progress(stream: str, line: str) -> None:
            if stream != "stdout" or not line.startswith("out_time_"):
                return
            key, separator, raw_value = line.strip().partition("=")
            if not separator or key not in {"out_time_us", "out_time_ms"}:
                return
            try:
                processed = int(raw_value) / 1_000_000
            except ValueError:
                return
            ratio = min(1.0, max(0.0, processed / selected_duration))
            update_export_job(
                job,
                phase="Creating clip",
                progress=73.0 + ratio * 25.0,
                message=f"Processed {format_clock(processed)} of {format_clock(selected_duration)}.",
            )

        try:
            run_streaming_command(
                command,
                timeout=config.get("EXPORT_TIMEOUT_SECONDS"),
                cancel_event=job.cancel_event,
                line_callback=ffmpeg_progress,
                process_callback=lambda process: remember_job_process(job, process),
            )
            enforce_output_quota(output, output_byte_limit)
            validate_export_output(output, selected_duration, config)
        except ToolError as exc:
            if not effective_fast_copy or exc.code in {
                "cancelled",
                "disk_full",
                "disk_space_low",
                "disk_check_failed",
                "output_too_large",
            }:
                raise
            output.unlink(missing_ok=True)
            update_export_job(
                job,
                phase="Optimizing compatibility",
                progress=73.0,
                message="Fast copy was not compatible with this source. Encoding it once instead.",
            )
            fallback = build_ffmpeg_command(
                config["FFMPEG_BINARY"],
                source,
                output,
                source_start,
                source_end,
                job.output_format,
                job.quality,
                mute=job.mute,
                strip_metadata=job.strip_metadata,
                fast_copy=False,
                max_output_bytes=output_byte_limit,
            )
            fallback = [fallback[0], "-progress", "pipe:1", "-nostats", *fallback[1:]]
            run_streaming_command(
                fallback,
                timeout=config.get("EXPORT_TIMEOUT_SECONDS"),
                cancel_event=job.cancel_event,
                line_callback=ffmpeg_progress,
                process_callback=lambda process: remember_job_process(job, process),
            )
            enforce_output_quota(output, output_byte_limit)
            validate_export_output(output, selected_duration, config)

        with job.lock:
            job.output_path = output
            job.download_name = (
                f"cut-{format_filename_time(job.start)}-{format_filename_time(job.end)}."
                f"{job.output_format}"
            )
            job.status = "ready"
            job.phase = "Ready"
            job.progress = 100.0
            job.message = f"{format_bytes(output.stat().st_size)} ready to download."
            job.finished_at = time.time()
            job.updated_at = job.finished_at
            job.process = None

        if source != output:
            try:
                source.unlink(missing_ok=True)
            except OSError:
                pass
    except ToolError as exc:
        cancelled = exc.code == "cancelled" or job.cancel_event.is_set()
        with job.lock:
            job.status = "cancelled" if cancelled else "error"
            job.phase = "Cancelled" if cancelled else "Export failed"
            job.progress = None
            job.message = "The export was cancelled." if cancelled else str(exc)
            job.error = None if cancelled else str(exc)
            job.error_code = "cancelled" if cancelled else exc.code
            job.finished_at = time.time()
            job.updated_at = job.finished_at
            job.process = None
        safe_remove_job_dir(job.directory.parent, job.directory)
    except Exception:
        with job.lock:
            job.status = "error"
            job.phase = "Export failed"
            job.progress = None
            job.message = "CutFrame hit an unexpected processing error. Try the export again."
            job.error = job.message
            job.error_code = "unexpected_error"
            job.finished_at = time.time()
            job.updated_at = job.finished_at
            job.process = None
        safe_remove_job_dir(job.directory.parent, job.directory)


def remember_job_process(job: ExportJob, process: subprocess.Popen | None) -> None:
    with job.lock:
        job.process = process
        job.updated_at = time.time()


def strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)


def parse_percent(value: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", value)
    if not match:
        return None
    return min(100.0, max(0.0, float(match.group(1))))


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def run_command(command: list[str], timeout: float | None) -> None:
    run_streaming_command(command, timeout=timeout)


def run_streaming_command(
    command: list[str],
    *,
    timeout: float | None,
    cancel_event: threading.Event | None = None,
    line_callback=None,
    process_callback=None,
) -> None:
    popen_options = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True

    try:
        process = subprocess.Popen(command, **popen_options)
    except FileNotFoundError as exc:
        raise ToolError(
            f"{public_tool_name(command[0])} is not installed or is not on PATH."
        ) from exc

    with ACTIVE_PROCESSES_LOCK:
        ACTIVE_PROCESSES.add(process)
    if process_callback:
        process_callback(process)
    lines: queue.Queue[tuple[str, str | None]] = queue.Queue()
    stderr_tail: list[str] = []

    def read_stream(name: str, stream) -> None:
        try:
            for line in iter(stream.readline, ""):
                lines.put((name, line.rstrip("\r\n")))
        except (OSError, ValueError):
            pass
        finally:
            lines.put((name, None))

    readers = [
        threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    pending_error: ToolError | None = None
    try:
        completed_streams = 0
        while completed_streams < len(readers):
            if pending_error is None and cancel_event and cancel_event.is_set():
                pending_error = ToolError("The export was cancelled.", "cancelled")
                terminate_process_tree(process)
            if pending_error is None and timeout is not None and time.monotonic() - started > timeout:
                pending_error = ToolError("Processing took too long and was stopped.", "timeout")
                terminate_process_tree(process)
            if pending_error is not None:
                # A surviving descendant can keep inherited stdout/stderr
                # handles open after the direct process exits. Do not let that
                # delay cancellation or timeout completion.
                break
            try:
                stream_name, line = lines.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None and not any(reader.is_alive() for reader in readers):
                    break
                continue
            if line is None:
                completed_streams += 1
                continue
            if stream_name == "stderr":
                stderr_tail.append(line)
                if len(stderr_tail) > 160:
                    del stderr_tail[:40]
            if line_callback:
                try:
                    line_callback(stream_name, line)
                except Exception:
                    pass
        try:
            return_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            terminate_process_tree(process)
            try:
                return_code = process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait(timeout=5)
                raise ToolError("The media process would not stop cleanly.", "process_stuck") from exc
        if pending_error:
            raise pending_error
        if return_code != 0:
            raise classify_tool_error(
                command[0],
                "\n".join(stderr_tail),
                include_details=False,
            )
    except KeyboardInterrupt:
        terminate_process_tree(process)
        raise
    finally:
        if process_callback:
            process_callback(None)
        with ACTIVE_PROCESSES_LOCK:
            ACTIVE_PROCESSES.discard(process)


def classify_tool_error(
    tool: str,
    stderr: str,
    *,
    include_details: bool = True,
) -> ToolError:
    cleaned = strip_ansi(stderr)
    lowered = cleaned.lower()
    if any(
        marker in lowered
        for marker in (
            "file is larger than max-filesize",
            "maximum file size exceeded",
            "output file size limit",
            "filesize limit",
        )
    ):
        return ToolError(
            (
                "The selected export exceeded CutFrame's configured output-size limit. "
                "Choose a shorter range or lower resolution, or raise "
                "CUTFRAME_MAX_OUTPUT_BYTES."
            ),
            "output_too_large",
        )
    if any(
        marker in lowered
        for marker in (
            "no space left on device",
            "not enough space on the disk",
            "disk full",
            "errno 28",
            "winerror 112",
        )
    ):
        return ToolError(
            "The export ran out of disk space. Free some space and try again.",
            "disk_full",
        )
    if any(
        marker in lowered
        for marker in (
            "rate-limited",
            "rate limit",
            "http error 429",
            "too many requests",
            "this content isn't available, try again later",
        )
    ):
        return ToolError(
            (
                "YouTube temporarily rate-limited this session. Video length is not the "
                "cause. Open YouTube in your browser, confirm the video plays, then retry "
                "with that browser selected under Browser session. If it still fails, "
                "wait up to an hour before trying again."
            ),
            "rate_limited",
        )
    if any(
        marker in lowered
        for marker in (
            "sign in to confirm",
            "login required",
            "cookies are no longer valid",
            "confirm you're not a bot",
            "confirm you’re not a bot",
        )
    ):
        return ToolError(
            (
                "YouTube requires a signed-in browser session. Open the video in your "
                "browser, then retry with that browser selected under Browser session."
            ),
            "authentication_required",
        )
    if "members-only content" in lowered:
        return ToolError(
            "YouTube requires a signed-in browser session. Open the video in your browser, then retry with that browser selected under Browser session.",
            "authentication_required",
        )
    if any(
        marker in lowered
        for marker in (
            "could not copy chrome cookie database",
            "could not copy edge cookie database",
            "database is locked",
            "failed to decrypt with dpapi",
        )
    ):
        return ToolError(
            "The selected browser's cookie database is locked. Close the browser completely and retry, or export without a browser session.",
            "cookies_locked",
        )
    if any(
        marker in lowered
        for marker in (
            "video unavailable",
            "private video",
            "this video has been removed",
            "requested format is not available",
            "premieres in",
        )
    ):
        return ToolError(
            "This video is unavailable, private, unfinished, or no longer has an exportable format.",
            "video_unavailable",
        )
    if "not available in your country" in lowered or "geo restricted" in lowered:
        return ToolError(
            "This video is not available from your current region.",
            "geo_restricted",
        )
    if "drm" in lowered and any(marker in lowered for marker in ("protected", "encrypted", "detected")):
        return ToolError(
            "This video is DRM-protected and cannot be exported by CutFrame.",
            "drm_protected",
        )
    if any(
        marker in lowered
        for marker in (
            "external javascript runtime",
            "no supported javascript runtime",
            "yt-dlp-ejs",
            "challenge solver",
            "javascript challenge",
        )
    ):
        return ToolError(
            "YouTube's JavaScript support is incomplete. Update with `python -m pip install -U \"yt-dlp[default]\"` and use Deno 2.3+ or Node.js 22+.",
            "javascript_support_missing",
        )
    if any(
        marker in lowered
        for marker in (
            "http error 500",
            "http error 502",
            "http error 503",
            "http error 504",
            "server returned 5",
        )
    ):
        return ToolError(
            "The video service returned a temporary server error. Try again in a moment.",
            "server_error",
        )
    if any(
        marker in lowered
        for marker in (
            "timed out",
            "connection reset",
            "connection aborted",
            "temporary failure in name resolution",
            "name or service not known",
            "unable to download webpage",
            "network is unreachable",
            "winerror 10054",
            "winerror 10060",
        )
    ):
        return ToolError(
            "The media connection was interrupted. Check the connection and try again.",
            "network_error",
        )
    if any(
        marker in lowered
        for marker in (
            "unsupported url",
            "no suitable extractor",
            "is not a valid url",
        )
    ):
        return ToolError("This video link is not supported.", "unsupported_url")
    if "unknown encoder" in lowered or "encoder not found" in lowered:
        return ToolError(
            "This FFmpeg build is missing an encoder needed for the selected format.",
            "encoder_missing",
        )
    message = f"{public_tool_name(tool)} could not process this video."
    if include_details:
        details = last_nonempty_line(cleaned)
        if details.lower().startswith("error:"):
            details = details[6:].strip()
        if details:
            message = f"{message} {details[:400]}"
    return ToolError(message)


def terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            # Descendants inherit the new console process group. CTRL_BREAK is
            # the least-privilege way to ask the entire group to stop and also
            # works where invoking taskkill is restricted.
            os.kill(process.pid, signal.CTRL_BREAK_EVENT)
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is not None:
            return
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if completed.returncode != 0 and process.poll() is None:
                process.kill()
        except (OSError, subprocess.SubprocessError):
            process.kill()
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        # The direct process may exit while a downloader/encoder descendant in
        # the same group remains alive. Always make a final group-wide pass.
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        if process.poll() is None:
            process.kill()


def terminate_active_processes() -> None:
    with ACTIVE_PROCESSES_LOCK:
        processes = list(ACTIVE_PROCESSES)
    for process in processes:
        terminate_process_tree(process)


atexit.register(terminate_active_processes)


def last_nonempty_line(text: str) -> str:
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def format_filename_time(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}-{minutes:02d}-{secs:02d}"


app = create_app()


def run_development_server(application: Flask) -> None:
    previous_sigterm = None

    def stop_for_signal(_signum, _frame):
        raise KeyboardInterrupt

    if threading.current_thread() is threading.main_thread():
        try:
            previous_sigterm = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, stop_for_signal)
        except (AttributeError, OSError, ValueError):
            previous_sigterm = None
    try:
        application.run(
            host="127.0.0.1",
            port=5000,
            debug=os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"},
            use_reloader=False,
            threaded=True,
        )
    except KeyboardInterrupt:
        pass
    finally:
        shutdown = getattr(application, "extensions", {}).get("cutframe_shutdown")
        if shutdown:
            shutdown(wait=True)
        else:
            terminate_active_processes()
        if previous_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, previous_sigterm)
            except (AttributeError, OSError, ValueError):
                pass


if __name__ == "__main__":
    run_development_server(app)
