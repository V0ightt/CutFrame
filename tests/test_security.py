from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import uuid

import pytest

import app as cutter


YOUTUBE_ID = "dQw4w9WgXcQ"
REQUEST_HEADERS = {
    "X-CutFrame-Request": "1",
    "Origin": "http://localhost",
}


@pytest.fixture()
def jobs_root():
    root = Path("instance") / "test-security" / uuid.uuid4().hex
    root.mkdir(parents=True)
    yield root
    cutter.shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def secure_app(jobs_root, monkeypatch):
    monkeypatch.setattr(
        cutter.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=1024 * 1024 * 1024 * 1024),
    )
    application = cutter.create_app(
        {
            "TESTING": False,
            "JOBS_DIR": jobs_root,
            "FFMPEG_BINARY": "missing-ffmpeg",
            "FFPROBE_BINARY": "missing-ffprobe",
            "YTDLP_BINARY": "missing-yt-dlp",
            "DENO_BINARY": None,
            "NODE_BINARY": None,
            "MAX_EXPORT_WORKERS": 1,
            "MAX_PENDING_EXPORTS": 2,
            "MAX_ACTIVE_UPLOADS": 2,
            "MAX_LOCAL_FILE_BYTES": 1024,
            "UPLOAD_CHUNK_BYTES": 8,
            "CLEANUP_INTERVAL_SECONDS": 3600,
        }
    )
    yield application
    application.extensions["cutframe_shutdown"](wait=True)


def test_loopback_host_marker_origin_and_safe_embed_contract(secure_app):
    client = secure_app.test_client()

    hostile_host = client.get("/api/health", headers={"Host": "attacker.example"})
    assert hostile_host.status_code == 400
    assert hostile_host.get_json()["code"] == "host_not_allowed"

    missing_marker = client.post(
        "/api/preview",
        json={"url": f"https://youtu.be/{YOUTUBE_ID}"},
        headers={"Origin": "http://localhost"},
    )
    assert missing_marker.status_code == 403
    assert missing_marker.get_json()["code"] == "request_marker_missing"

    hostile_origin = client.post(
        "/api/preview",
        json={"url": f"https://youtu.be/{YOUTUBE_ID}"},
        headers={
            "X-CutFrame-Request": "1",
            "Origin": "https://attacker.example",
        },
    )
    assert hostile_origin.status_code == 403
    assert hostile_origin.get_json()["code"] == "origin_not_allowed"

    accepted = client.post(
        "/api/preview",
        json={"url": f"https://youtu.be/{YOUTUBE_ID}"},
        headers=REQUEST_HEADERS,
    )
    assert accepted.status_code == 200
    assert "origin=http%3A%2F%2Flocalhost" in accepted.get_json()["embed_url"]
    assert "attacker.example" not in accepted.get_json()["embed_url"]


def test_testing_mode_does_not_bypass_request_marker(jobs_root):
    application = cutter.create_app(
        {
            "TESTING": True,
            "JOBS_DIR": jobs_root,
            "FFMPEG_BINARY": "missing-ffmpeg",
        }
    )
    try:
        response = application.test_client().post(
            "/api/preview",
            json={"url": f"https://youtu.be/{YOUTUBE_ID}"},
        )
        assert response.status_code == 403
        assert response.get_json()["code"] == "request_marker_missing"
    finally:
        application.extensions["cutframe_shutdown"](wait=True)


def test_security_headers_cover_success_and_rejected_requests(secure_app):
    client = secure_app.test_client()
    responses = [
        client.get("/"),
        client.get("/api/health", headers={"Host": "attacker.example"}),
    ]
    for response in responses:
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert "www.youtube-nocookie.com" in response.headers["Content-Security-Policy"]
        assert response.headers["Cache-Control"] == "no-store"


def test_health_and_setup_errors_do_not_disclose_absolute_paths(jobs_root):
    private_dir = jobs_root / "private-tools"
    private_dir.mkdir()
    ffmpeg = private_dir / "ffmpeg-secret.exe"
    ffmpeg.touch()
    application = cutter.create_app(
        {
            "TESTING": False,
            "JOBS_DIR": jobs_root / "jobs",
            "FFMPEG_BINARY": str(ffmpeg.resolve()),
            "FFPROBE_BINARY": "missing-ffprobe",
            "YTDLP_BINARY": "missing-yt-dlp",
            "DENO_BINARY": None,
            "NODE_BINARY": None,
        }
    )
    try:
        client = application.test_client()
        response = client.get("/api/health")
        rendered = response.get_data(as_text=True)
        assert str(private_dir.resolve()) not in rendered
        assert response.get_json()["ffmpeg_path"] == ffmpeg.name

        missing = cutter.create_app(
            {
                "TESTING": False,
                "JOBS_DIR": jobs_root / "missing-jobs",
                "FFMPEG_BINARY": "definitely-missing-ffmpeg",
            }
        )
        try:
            error = missing.test_client().post(
                "/api/exports",
                json={
                    "source_url": f"https://youtu.be/{YOUTUBE_ID}",
                    "start": 0,
                    "end": 1,
                },
                headers=REQUEST_HEADERS,
            )
            message = error.get_json()["error"]
            assert error.status_code == 503
            assert str(Path(cutter.sys.executable).parent) not in message
            assert "python -m pip" in message
        finally:
            missing.extensions["cutframe_shutdown"](wait=True)
    finally:
        application.extensions["cutframe_shutdown"](wait=True)


def test_streaming_command_does_not_return_raw_stderr_details(monkeypatch):
    secret = "https://signed.example/media?token=do-not-disclose"  # pragma: allowlist secret

    class FailedProcess:
        def __init__(self):
            self.stdout = StringIO("")
            self.stderr = StringIO(f"ERROR: {secret}\nC:\\private\\profile\\cookies.db\n")

        def poll(self):
            return 1

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(cutter.subprocess, "Popen", lambda _command, **_options: FailedProcess())

    with pytest.raises(cutter.ToolError) as raised:
        cutter.run_streaming_command(["ffmpeg", "input"], timeout=1)
    assert secret not in str(raised.value)
    assert "cookies.db" not in str(raised.value)


def test_upload_capacity_and_duplicate_retry_integrity(secure_app):
    secure_app.config["MAX_ACTIVE_UPLOADS"] = 1
    client = secure_app.test_client()
    created = client.post(
        "/api/uploads",
        json={"filename": "movie.mp4", "size": 4},
        headers=REQUEST_HEADERS,
    )
    assert created.status_code == 201
    upload_id = created.get_json()["upload_id"]

    at_capacity = client.post(
        "/api/uploads",
        json={"filename": "other.mp4", "size": 4},
        headers=REQUEST_HEADERS,
    )
    assert at_capacity.status_code == 429
    assert at_capacity.get_json()["code"] == "upload_capacity_reached"

    first = client.put(
        f"/api/uploads/{upload_id}",
        data=b"abcd",
        headers={**REQUEST_HEADERS, "X-Upload-Offset": "0"},
    )
    assert first.status_code == 200
    conflicting_retry = client.put(
        f"/api/uploads/{upload_id}",
        data=b"wxyz",
        headers={**REQUEST_HEADERS, "X-Upload-Offset": "0"},
    )
    assert conflicting_retry.status_code == 409
    assert conflicting_retry.get_json()["code"] == "retry_content_mismatch"


def test_export_admission_is_bounded_and_does_not_leave_directory(
    secure_app,
    monkeypatch,
):
    secure_app.config["MAX_PENDING_EXPORTS"] = 1
    existing_id = uuid.uuid4().hex
    existing_dir = Path(secure_app.config["JOBS_DIR"]) / existing_id
    existing_dir.mkdir()
    secure_app.extensions["cutframe_export_jobs"][existing_id] = cutter.ExportJob(
        job_id=existing_id,
        directory=existing_dir,
        source_kind="youtube",
        source_url=f"https://www.youtube.com/watch?v={YOUTUBE_ID}",
        start=0,
        end=1,
        output_format="mp4",
        quality="original",
        mute=False,
        strip_metadata=True,
        fast_copy=False,
    )
    monkeypatch.setattr(cutter, "binary_available", lambda _binary: True)
    monkeypatch.setattr(cutter, "choose_js_runtime", lambda _config: ("node", "node"))
    monkeypatch.setattr(cutter, "ytdlp_ejs_available", lambda _binary: True)

    response = secure_app.test_client().post(
        "/api/exports",
        json={
            "source_url": f"https://youtu.be/{YOUTUBE_ID}",
            "start": 0,
            "end": 1,
        },
        headers=REQUEST_HEADERS,
    )
    assert response.status_code == 429
    assert response.get_json()["code"] == "export_capacity_reached"
    assert [path.name for path in Path(secure_app.config["JOBS_DIR"]).iterdir()] == [existing_id]


def test_finalize_is_atomic_idempotent_and_cancel_upload_cannot_delete_export(
    secure_app,
    monkeypatch,
):
    secure_app.config["RUN_EXPORTS_INLINE"] = True
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_process(job, _config):
        calls.append(job.job_id)
        started.set()
        assert release.wait(timeout=5)
        output = job.directory / "clip.mp4"
        output.write_bytes(b"ready")
        with job.lock:
            job.output_path = output
            job.download_name = "cut.mp4"
            job.status = "ready"

    monkeypatch.setattr(cutter, "binary_available", lambda _binary: True)
    monkeypatch.setattr(cutter, "process_export_job", fake_process)
    client = secure_app.test_client()
    created = client.post(
        "/api/uploads",
        json={"filename": "movie.mp4", "size": 4},
        headers=REQUEST_HEADERS,
    )
    upload_id = created.get_json()["upload_id"]
    uploaded = client.put(
        f"/api/uploads/{upload_id}",
        data=b"abcd",
        headers={**REQUEST_HEADERS, "X-Upload-Offset": "0"},
    )
    assert uploaded.status_code == 200
    payload = {"start": 0, "end": 1, "format": "mp4", "quality": "original"}

    with ThreadPoolExecutor(max_workers=1) as requests:
        first = requests.submit(
            lambda: secure_app.test_client().post(
                f"/api/uploads/{upload_id}/export",
                json=payload,
                headers=REQUEST_HEADERS,
            )
        )
        assert started.wait(timeout=5)
        duplicate = secure_app.test_client().post(
            f"/api/uploads/{upload_id}/export",
            json=payload,
            headers=REQUEST_HEADERS,
        )
        assert duplicate.status_code == 202
        assert duplicate.get_json()["job_id"] == upload_id
        release.set()
        assert first.result(timeout=5).status_code == 202

    assert calls == [upload_id]
    job_dir = Path(secure_app.config["JOBS_DIR"]) / upload_id
    cancelled_upload = client.delete(
        f"/api/uploads/{upload_id}",
        headers=REQUEST_HEADERS,
    )
    assert cancelled_upload.status_code == 204
    assert job_dir.is_dir()
    assert upload_id in secure_app.extensions["cutframe_export_jobs"]


def test_stale_registries_are_evicted_but_active_and_downloaded_jobs_are_leased(
    secure_app,
):
    secure_app.config.update(
        UPLOAD_TTL_SECONDS=1,
        JOB_TTL_SECONDS=1,
        CLEANUP_INTERVAL_SECONDS=0,
    )
    client = secure_app.test_client()
    created = client.post(
        "/api/uploads",
        json={"filename": "stale.mp4", "size": 4},
        headers=REQUEST_HEADERS,
    )
    upload_id = created.get_json()["upload_id"]
    session = secure_app.extensions["cutframe_upload_sessions"][upload_id]
    session.updated_at = time.time() - 10
    os.utime(session.directory, (time.time() - 10, time.time() - 10))

    ready_id = uuid.uuid4().hex
    ready_dir = Path(secure_app.config["JOBS_DIR"]) / ready_id
    ready_dir.mkdir()
    (ready_dir / "clip.mp4").write_bytes(b"clip")
    ready = cutter.ExportJob(
        job_id=ready_id,
        directory=ready_dir,
        source_kind="local",
        source_path=None,
        start=0,
        end=1,
        output_format="mp4",
        quality="original",
        mute=False,
        strip_metadata=True,
        fast_copy=False,
        status="ready",
        updated_at=time.time() - 10,
        active_downloads=1,
    )
    secure_app.extensions["cutframe_export_jobs"][ready_id] = ready

    active_id = uuid.uuid4().hex
    active_dir = Path(secure_app.config["JOBS_DIR"]) / active_id
    active_dir.mkdir()
    os.utime(active_dir, (time.time() - 10, time.time() - 10))
    active = cutter.ExportJob(
        job_id=active_id,
        directory=active_dir,
        source_kind="local",
        source_path=None,
        start=0,
        end=1,
        output_format="mp4",
        quality="original",
        mute=False,
        strip_metadata=True,
        fast_copy=False,
        status="running",
        updated_at=time.time() - 10,
    )
    secure_app.extensions["cutframe_export_jobs"][active_id] = active

    cleanup = secure_app.extensions["cutframe_cleanup"]
    cleanup(force=True)
    assert upload_id not in secure_app.extensions["cutframe_upload_sessions"]
    assert not session.directory.exists()
    assert ready_id in secure_app.extensions["cutframe_export_jobs"]
    assert ready_dir.exists()
    assert active_id in secure_app.extensions["cutframe_export_jobs"]
    assert active_dir.exists()

    ready.active_downloads = 0
    ready.updated_at = time.time() - 10
    cleanup(force=True)
    assert ready_id not in secure_app.extensions["cutframe_export_jobs"]
    assert not ready_dir.exists()
    assert active_dir.exists()


def test_startup_cleanup_uses_shorter_ttl_for_abandoned_uploads(jobs_root):
    job_id = uuid.uuid4().hex
    staged = jobs_root / job_id
    staged.mkdir()
    (staged / "upload.part").write_bytes(b"partial")
    old = time.time() - 10
    os.utime(staged, (old, old))

    application = cutter.create_app(
        {
            "TESTING": False,
            "JOBS_DIR": jobs_root,
            "UPLOAD_TTL_SECONDS": 1,
            "JOB_TTL_SECONDS": 3600,
        }
    )
    try:
        assert not staged.exists()
    finally:
        application.extensions["cutframe_shutdown"](wait=True)


def test_symlink_job_directory_is_never_followed_or_deleted(jobs_root):
    outside = jobs_root.parent / f"outside-{uuid.uuid4().hex}"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    link = jobs_root / uuid.uuid4().hex
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        cutter.shutil.rmtree(outside, ignore_errors=True)
        pytest.skip("Directory symlinks are unavailable in this environment")
    try:
        assert cutter.safe_job_dir(jobs_root, link.name) is None
        cutter.cleanup_old_jobs(jobs_root, ttl_seconds=0)
        assert sentinel.read_text(encoding="utf-8") == "keep"
        assert outside.is_dir()
    finally:
        try:
            link.unlink()
        except OSError:
            pass
        cutter.shutil.rmtree(outside, ignore_errors=True)


def test_fast_copy_success_with_invalid_output_falls_back_to_reencoding(
    jobs_root,
    monkeypatch,
):
    job_id = uuid.uuid4().hex
    directory = jobs_root / job_id
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"source")
    job = cutter.ExportJob(
        job_id=job_id,
        directory=directory,
        source_kind="local",
        source_path=source,
        start=0,
        end=2,
        output_format="mp4",
        quality="original",
        mute=False,
        strip_metadata=True,
        fast_copy=True,
    )
    commands = []
    validations = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"candidate")

    def fake_validate(_output, _duration, _config):
        validations.append(True)
        if len(validations) == 1:
            raise cutter.ToolError("invalid", "invalid_output")

    monkeypatch.setattr(cutter, "probe_media", lambda *_args, **_kwargs: {"duration": 3})
    monkeypatch.setattr(cutter, "run_streaming_command", fake_run)
    monkeypatch.setattr(cutter, "validate_export_output", fake_validate)
    cutter.process_export_job(job, {"FFMPEG_BINARY": "ffmpeg"})

    assert len(commands) == 2
    assert "copy" in commands[0]
    assert "copy" not in commands[1]
    assert len(validations) == 2
    assert job.status == "ready"


def test_shutdown_cancels_queued_jobs_and_rejects_new_mutations(secure_app):
    job_id = uuid.uuid4().hex
    directory = Path(secure_app.config["JOBS_DIR"]) / job_id
    directory.mkdir()
    job = cutter.ExportJob(
        job_id=job_id,
        directory=directory,
        source_kind="local",
        source_path=None,
        start=0,
        end=1,
        output_format="mp4",
        quality="original",
        mute=False,
        strip_metadata=True,
        fast_copy=False,
    )
    secure_app.extensions["cutframe_export_jobs"][job_id] = job

    secure_app.extensions["cutframe_shutdown"](wait=True)
    assert job.cancel_event.is_set()
    assert job.status == "cancelled"
    response = secure_app.test_client().post(
        "/api/preview",
        json={"url": f"https://youtu.be/{YOUTUBE_ID}"},
        headers=REQUEST_HEADERS,
    )
    assert response.status_code == 503
    assert response.get_json()["code"] == "server_stopping"


def test_legacy_synchronous_clip_route_is_absent_from_public_app(secure_app):
    assert all(rule.rule != "/api/clip" for rule in secure_app.url_map.iter_rules())
    response = secure_app.test_client().post(
        "/api/clip",
        data={},
        headers=REQUEST_HEADERS,
    )
    assert response.status_code == 404


def test_media_commands_apply_staging_and_final_output_byte_caps():
    section_command = cutter.build_ytdlp_section_command(
        "yt-dlp",
        Path("instance") / "quota-command",
        f"https://www.youtube.com/watch?v={YOUTUBE_ID}",
        10,
        20,
        "720",
        "mp4",
        exact=True,
        max_output_bytes=123456,
    )
    downloader_arguments = [
        section_command[index + 1]
        for index, value in enumerate(section_command[:-1])
        if value == "--downloader-args"
    ]
    assert "ffmpeg_o:-fs 123456" in downloader_arguments
    postprocessor_index = section_command.index("--postprocessor-args")
    assert section_command[postprocessor_index + 1] == "ffmpeg_o:-fs 123456"
    assert section_command[-1] == f"https://www.youtube.com/watch?v={YOUTUBE_ID}"

    ffmpeg_command = cutter.build_ffmpeg_command(
        "ffmpeg",
        Path("source.mp4"),
        Path("clip.mp4"),
        0,
        10,
        "mp4",
        "720",
        mute=False,
        strip_metadata=True,
        fast_copy=False,
        max_output_bytes=123456,
    )
    limit_index = ffmpeg_command.index("-fs")
    assert ffmpeg_command[limit_index + 1] == "123456"
    assert limit_index == len(ffmpeg_command) - 3


def test_output_limit_uses_only_space_above_the_configured_floor(jobs_root, monkeypatch):
    monkeypatch.setattr(
        cutter.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=600),
    )
    assert cutter.available_export_bytes(
        jobs_root,
        {"MAX_OUTPUT_BYTES": 10_000, "MIN_FREE_DISK_BYTES": 500},
    ) == 100

    monkeypatch.setattr(
        cutter.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=500),
    )
    with pytest.raises(cutter.ToolError) as raised:
        cutter.available_export_bytes(
            jobs_root,
            {"MAX_OUTPUT_BYTES": 10_000, "MIN_FREE_DISK_BYTES": 500},
        )
    assert raised.value.code == "disk_space_low"
    assert "CUTFRAME_MIN_FREE_DISK_BYTES" in str(raised.value)


def test_remote_staging_quota_failure_is_stable_and_cleans_partial_media(
    jobs_root,
    monkeypatch,
):
    job_id = uuid.uuid4().hex
    directory = jobs_root / job_id
    directory.mkdir()
    job = cutter.ExportJob(
        job_id=job_id,
        directory=directory,
        source_kind="youtube",
        source_url=f"https://www.youtube.com/watch?v={YOUTUBE_ID}",
        start=0,
        end=10,
        output_format="mp4",
        quality="720",
        mute=False,
        strip_metadata=True,
        fast_copy=False,
    )
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        (directory / "source.mp4").write_bytes(b"x" * 16)

    monkeypatch.setattr(cutter, "choose_js_runtime", lambda _config: None)
    monkeypatch.setattr(cutter, "run_streaming_command", fake_run)
    monkeypatch.setattr(
        cutter.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=10_000),
    )
    cutter.process_export_job(
        job,
        {
            "YTDLP_BINARY": "yt-dlp",
            "FFMPEG_BINARY": "ffmpeg",
            "FFPROBE_BINARY": "missing-ffprobe",
            "MAX_OUTPUT_BYTES": 16,
            "MIN_FREE_DISK_BYTES": 100,
            "EXPORT_TIMEOUT_SECONDS": 30,
        },
    )

    assert len(commands) == 1
    assert "ffmpeg_o:-fs 16" in commands[0]
    assert job.status == "error"
    assert job.error_code == "output_too_large"
    assert "CUTFRAME_MAX_OUTPUT_BYTES" in job.error
    assert not directory.exists()


def test_final_output_quota_failure_cleans_local_source_and_does_not_retry_copy(
    jobs_root,
    monkeypatch,
):
    job_id = uuid.uuid4().hex
    directory = jobs_root / job_id
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"source")
    job = cutter.ExportJob(
        job_id=job_id,
        directory=directory,
        source_kind="local",
        source_path=source,
        start=0,
        end=2,
        output_format="mp4",
        quality="original",
        mute=False,
        strip_metadata=True,
        fast_copy=True,
    )
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"x" * 16)

    monkeypatch.setattr(cutter, "probe_media", lambda *_args, **_kwargs: {"duration": 2})
    monkeypatch.setattr(cutter, "run_streaming_command", fake_run)
    monkeypatch.setattr(
        cutter.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=10_000),
    )
    cutter.process_export_job(
        job,
        {
            "FFMPEG_BINARY": "ffmpeg",
            "FFPROBE_BINARY": "missing-ffprobe",
            "MAX_OUTPUT_BYTES": 16,
            "MIN_FREE_DISK_BYTES": 100,
            "EXPORT_TIMEOUT_SECONDS": 30,
        },
    )

    assert len(commands) == 1
    assert commands[0][commands[0].index("-fs") + 1] == "16"
    assert job.status == "error"
    assert job.error_code == "output_too_large"
    assert not directory.exists()


def test_free_space_floor_stops_export_before_ffmpeg_and_cleans_job(
    jobs_root,
    monkeypatch,
):
    job_id = uuid.uuid4().hex
    directory = jobs_root / job_id
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"source")
    job = cutter.ExportJob(
        job_id=job_id,
        directory=directory,
        source_kind="local",
        source_path=source,
        start=0,
        end=1,
        output_format="mp4",
        quality="original",
        mute=False,
        strip_metadata=True,
        fast_copy=False,
    )
    monkeypatch.setattr(cutter, "probe_media", lambda *_args, **_kwargs: {"duration": 2})
    monkeypatch.setattr(
        cutter,
        "run_streaming_command",
        lambda *_args, **_kwargs: pytest.fail("FFmpeg must not start below the free-space floor"),
    )
    monkeypatch.setattr(
        cutter.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=500),
    )
    cutter.process_export_job(
        job,
        {
            "FFMPEG_BINARY": "ffmpeg",
            "FFPROBE_BINARY": "missing-ffprobe",
            "MAX_OUTPUT_BYTES": 10_000,
            "MIN_FREE_DISK_BYTES": 500,
        },
    )

    assert job.status == "error"
    assert job.error_code == "disk_space_low"
    assert "CUTFRAME_MIN_FREE_DISK_BYTES" in job.error
    assert not directory.exists()


@pytest.mark.parametrize(
    "stderr",
    [
        "File is larger than max-filesize (20 bytes > 10 bytes). Aborting.",
        "maximum file size exceeded",
        "output file size limit reached",
    ],
)
def test_output_quota_tool_errors_have_a_stable_public_code(stderr):
    error = cutter.classify_tool_error("ffmpeg", stderr, include_details=False)
    assert error.code == "output_too_large"
    assert "CUTFRAME_MAX_OUTPUT_BYTES" in str(error)
    assert stderr not in str(error)
