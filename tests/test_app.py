from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import shutil
import threading
import uuid

import pytest

import app as cutter


YOUTUBE_ID = "dQw4w9WgXcQ"


@pytest.fixture()
def workspace_tmp_path():
    directory = Path("instance") / "test-jobs" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture()
def app(workspace_tmp_path, monkeypatch):
    jobs_dir = workspace_tmp_path / "jobs"
    monkeypatch.setattr(
        cutter.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=1024 * 1024 * 1024 * 1024),
    )
    application = cutter.create_app(
        {
            "TESTING": True,
            "JOBS_DIR": jobs_dir,
            "FFMPEG_BINARY": "fake-ffmpeg",
            "FFPROBE_BINARY": "fake-ffprobe",
            "YTDLP_BINARY": "fake-yt-dlp",
            "DENO_BINARY": None,
            "NODE_BINARY": "fake-node",
            "RUN_EXPORTS_INLINE": True,
            "MAX_EXPORT_WORKERS": 1,
            "MAX_LOCAL_FILE_BYTES": 64,
            "UPLOAD_CHUNK_BYTES": 4,
        }
    )
    yield application
    application.extensions["cutframe_executor"].shutdown(
        wait=True,
        cancel_futures=True,
    )
    cutter.terminate_active_processes()


@pytest.fixture()
def client(app):
    test_client = app.test_client()
    test_client.environ_base["HTTP_X_CUTFRAME_REQUEST"] = "1"
    return test_client


def command_value(command: list[str], option: str) -> str:
    index = command.index(option)
    return command[index + 1]


def repeated_command_values(command: list[str], option: str) -> list[str]:
    return [command[index + 1] for index, value in enumerate(command[:-1]) if value == option]


def start_upload(client, filename: str = "movie.mp4", size: int = 8) -> str:
    response = client.post("/api/uploads", json={"filename": filename, "size": size})
    assert response.status_code == 201
    return response.get_json()["upload_id"]


def register_export_job(application, *, status: str = "queued") -> cutter.ExportJob:
    job_id = uuid.uuid4().hex
    directory = Path(application.config["JOBS_DIR"]) / job_id
    directory.mkdir(parents=True)
    job = cutter.ExportJob(
        job_id=job_id,
        directory=directory,
        source_kind="local",
        source_path=directory / "source.mp4",
        start=1.0,
        end=4.0,
        output_format="mp4",
        quality="720",
        mute=False,
        strip_metadata=False,
        fast_copy=False,
        status=status,
    )
    application.extensions["cutframe_export_jobs"][job_id] = job
    return job


def test_index_renders_editor(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Trim a video without opening an editor." in response.data
    assert b"Export clip" in response.data


def test_health_reports_export_runtime_and_upload_limits(client, monkeypatch):
    monkeypatch.setattr(
        cutter,
        "binary_available",
        lambda binary: binary != "fake-ffprobe",
    )
    monkeypatch.setattr(
        cutter,
        "get_binary_version",
        lambda binary: {
            "fake-yt-dlp": "2026.07.04",
            "fake-node": "22.14.0",
        }.get(binary),
    )
    monkeypatch.setattr(cutter, "ytdlp_ejs_available", lambda _binary: True)

    response = client.get("/api/health")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["version"] == "0.1.0"
    assert payload["ffmpeg"] is True
    assert payload["ffprobe"] is False
    assert payload["yt_dlp"] is True
    assert payload["yt_dlp_ejs"] is True
    assert payload["yt_dlp_version"] == "2026.07.04"
    assert payload["yt_dlp_outdated"] is False
    assert payload["js_runtime"] == "node"
    assert payload["js_runtime_version"] == "22.14.0"
    assert payload["js_runtime_path"] is None
    assert payload["node_path"] is None
    assert payload["youtube_export_ready"] is True
    assert payload["max_local_file_bytes"] == 64
    assert payload["upload_chunk_bytes"] == 4


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/video", True),
        ("http://example.com/video", True),
        ("file:///etc/passwd", False),
        ("javascript:alert(1)", False),
        ("not a url", False),
    ],
)
def test_remote_url_validation(url, expected):
    assert cutter.valid_remote_url(url) is expected


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={YOUTUBE_ID}",
        f"https://youtube.com/watch?v={YOUTUBE_ID}&t=7200",
        f"https://m.youtube.com/watch?v={YOUTUBE_ID}",
        f"https://music.youtube.com/watch?v={YOUTUBE_ID}",
        f"https://youtu.be/{YOUTUBE_ID}?si=share-token",
        f"https://www.youtube.com/shorts/{YOUTUBE_ID}",
        f"https://www.youtube.com/live/{YOUTUBE_ID}?feature=share",
        f"https://www.youtube.com/embed/{YOUTUBE_ID}",
        f"https://www.youtube.com/v/{YOUTUBE_ID}",
        f"https://www.youtube-nocookie.com/embed/{YOUTUBE_ID}",
    ],
)
def test_extract_youtube_video_id_supports_all_player_url_shapes(url):
    assert cutter.extract_youtube_video_id(url) == YOUTUBE_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v=short",
        f"https://www.youtube.com/watch?v={YOUTUBE_ID}x",
        "https://www.youtube.com/watch?v=bad!videoid",
        "https://youtu.be/",
        "https://www.youtube.com/playlist?list=PL123",
        f"https://youtube.com.evil.example/watch?v={YOUTUBE_ID}",
        f"https://example.com/watch?v={YOUTUBE_ID}",
        "javascript:alert(1)",
    ],
)
def test_extract_youtube_video_id_rejects_invalid_ids_and_hosts(url):
    assert cutter.extract_youtube_video_id(url) is None


@pytest.mark.parametrize("endpoint", ["/api/preview", "/api/import"])
def test_preview_is_immediate_without_media_tools_or_ytdlp_process(
    app,
    client,
    monkeypatch,
    endpoint,
):
    tool_calls = []
    monkeypatch.setattr(cutter, "binary_available", lambda _binary: False)
    monkeypatch.setattr(
        cutter,
        "run_streaming_command",
        lambda *args, **kwargs: tool_calls.append((args, kwargs)),
    )

    response = client.post(
        endpoint,
        json={"url": f"https://youtu.be/{YOUTUBE_ID}"},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["kind"] == "youtube"
    assert payload["video_id"] == YOUTUBE_ID
    assert payload["canonical_url"] == f"https://www.youtube.com/watch?v={YOUTUBE_ID}"
    parsed_embed = urlparse(payload["embed_url"])
    assert parsed_embed.hostname == "www.youtube-nocookie.com"
    assert parsed_embed.path == f"/embed/{YOUTUBE_ID}"
    embed_query = parse_qs(parsed_embed.query)
    assert embed_query["enablejsapi"] == ["1"]
    assert embed_query["origin"] == ["http://localhost"]
    assert tool_calls == []
    assert app.extensions["cutframe_export_jobs"] == {}
    assert list(Path(app.config["JOBS_DIR"]).iterdir()) == []


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("not a url", "invalid_url"),
        (f"https://example.com/watch?v={YOUTUBE_ID}", "unsupported_preview_url"),
        ("https://youtube.com/watch?v=too-short", "unsupported_preview_url"),
        ("https://youtube.com/watch?v=bad!videoid", "unsupported_preview_url"),
    ],
)
def test_preview_rejects_non_youtube_sources_and_invalid_video_ids(client, url, code):
    response = client.post("/api/preview", json={"url": url})
    assert response.status_code == 400
    assert response.get_json()["code"] == code


def test_ytdlp_section_command_is_exact_bounded_resilient_and_fast():
    command = cutter.build_ytdlp_section_command(
        "yt-dlp",
        Path("instance") / "test-command",
        f"https://www.youtube.com/watch?v={YOUTUBE_ID}",
        3661.125,
        3721.5,
        "720",
        "mp4",
        exact=True,
        cookie_browser="firefox",
        ffmpeg_binary="C:/Tools/ffmpeg.exe",
        js_runtime=("deno", "C:/Tools/deno.exe"),
    )

    assert command[0] == "yt-dlp"
    assert command_value(command, "--download-sections") == "*3661.125-3721.500"
    assert "--force-keyframes-at-cuts" in command
    assert command_value(command, "--concurrent-fragments") == "4"
    assert command_value(command, "--retries") == "10"
    assert command_value(command, "--fragment-retries") == "10"
    assert command_value(command, "--extractor-retries") == "5"
    assert repeated_command_values(command, "--retry-sleep") == [
        "http:exp=1:20",
        "fragment:exp=1:20",
        "extractor:linear=2:10:2",
    ]
    selector = command_value(command, "-f")
    assert "height<=720" in selector
    assert "avc1" in selector
    assert command_value(command, "--cookies-from-browser") == "firefox"
    assert command_value(command, "--ffmpeg-location") == "C:/Tools/ffmpeg.exe"
    assert command_value(command, "--js-runtimes") == "deno:C:/Tools/deno.exe"
    assert "--sleep-requests" not in command
    assert "--sleep-interval" not in command
    assert "--max-sleep-interval" not in command
    assert command[-1] == f"https://www.youtube.com/watch?v={YOUTUBE_ID}"


def test_ytdlp_section_command_can_make_fast_inexact_audio_cut():
    command = cutter.build_ytdlp_section_command(
        "yt-dlp",
        Path("instance") / "test-command",
        f"https://www.youtube.com/watch?v={YOUTUBE_ID}",
        0,
        60,
        "original",
        "m4a",
        exact=False,
    )
    assert command_value(command, "--download-sections") == "*0.000-60.000"
    assert command_value(command, "-f").startswith("ba")
    assert "--force-keyframes-at-cuts" not in command


def test_export_options_accept_numeric_zero_start():
    options = cutter.parse_export_options(
        {
            "start": 0,
            "end": 60,
            "format": "mp4",
            "quality": "720",
        }
    )
    assert options["start"] == 0.0
    assert options["end"] == 60.0


def test_export_options_accept_fractional_and_twelve_hour_selections():
    fractional = cutter.parse_export_options(
        {
            "start": 7200.125,
            "end": 7260.875,
            "format": "webm",
            "quality": "original",
        }
    )
    maximum = cutter.parse_export_options(
        {
            "start": 0,
            "end": 12 * 60 * 60,
            "format": "mp4",
            "quality": "1080",
        }
    )

    assert fractional["end"] - fractional["start"] == pytest.approx(60.75)
    assert maximum["end"] == 43_200.0


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (-0.001, 1, "valid start time"),
        (float("nan"), 1, "valid start time"),
        (0, float("inf"), "valid end time"),
        (1, 1, "later than start"),
        (2, 1, "later than start"),
        (0, 12 * 60 * 60 + 0.001, "at most 12 hours"),
    ],
)
def test_export_options_reject_invalid_and_overlong_ranges(start, end, message):
    with pytest.raises(ValueError, match=message):
        cutter.parse_export_options(
            {
                "start": start,
                "end": end,
                "format": "mp4",
                "quality": "original",
            }
        )


def test_remote_export_reports_missing_javascript_runtime(client, monkeypatch):
    monkeypatch.setattr(cutter, "binary_available", lambda _binary: True)
    monkeypatch.setattr(cutter, "choose_js_runtime", lambda _config: None)
    monkeypatch.setattr(
        cutter,
        "ytdlp_ejs_available",
        lambda _binary: pytest.fail("EJS should not be checked without a runtime"),
    )

    response = client.post(
        "/api/exports",
        json={
            "source_url": f"https://youtu.be/{YOUTUBE_ID}",
            "start": 0,
            "end": 60,
        },
    )
    assert response.status_code == 503
    assert response.get_json()["code"] == "js_runtime_missing"


def test_remote_export_reports_missing_ytdlp_ejs(client, monkeypatch):
    monkeypatch.setattr(cutter, "binary_available", lambda _binary: True)
    monkeypatch.setattr(
        cutter,
        "choose_js_runtime",
        lambda _config: ("node", "fake-node"),
    )
    monkeypatch.setattr(cutter, "ytdlp_ejs_available", lambda _binary: False)

    response = client.post(
        "/api/exports",
        json={
            "source_url": f"https://youtu.be/{YOUTUBE_ID}",
            "start": 0,
            "end": 60,
        },
    )
    assert response.status_code == 503
    assert response.get_json()["code"] == "ytdlp_ejs_missing"
    assert "yt-dlp[default]" in response.get_json()["error"]


def test_remote_export_canonicalizes_url_and_dispatches_one_minute_section_at_two_hours(
    app,
    client,
    monkeypatch,
):
    dispatched = []
    monkeypatch.setattr(cutter, "binary_available", lambda _binary: True)
    monkeypatch.setattr(
        cutter,
        "choose_js_runtime",
        lambda _config: ("node", "fake-node"),
    )
    monkeypatch.setattr(cutter, "ytdlp_ejs_available", lambda _binary: True)
    monkeypatch.setattr(
        cutter,
        "process_export_job",
        lambda job, config: dispatched.append((job, config)),
    )

    response = client.post(
        "/api/exports",
        json={
            "source_url": f"https://youtu.be/{YOUTUBE_ID}?si=share",
            "start": 7200,
            "end": 7260,
            "format": "mp4",
            "quality": "720",
            "cookie_browser": "edge",
            "strip_metadata": True,
        },
    )
    payload = response.get_json()

    assert response.status_code == 202
    assert payload["status"] == "queued"
    assert len(dispatched) == 1
    job, config = dispatched[0]
    assert config["RUN_EXPORTS_INLINE"] is True
    assert job.source_kind == "youtube"
    assert job.source_url == f"https://www.youtube.com/watch?v={YOUTUBE_ID}"
    assert job.start == 7200
    assert job.end == 7260
    assert job.quality == "720"
    assert job.cookie_browser == "edge"
    assert job.strip_metadata is True
    assert app.extensions["cutframe_export_jobs"][job.job_id] is job

    status = client.get(f"/api/exports/{job.job_id}")
    assert status.status_code == 200
    assert status.get_json()["status"] == "queued"
    assert status.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"filename": "movie.txt", "size": 1}, 400, "unsupported_file"),
        ({"filename": "movie.mp4", "size": 0}, 400, "empty_file"),
        ({"filename": "movie.mp4", "size": 65}, 413, "file_too_large"),
        ({"filename": "movie.mp4", "size": "not-a-number"}, 400, "empty_file"),
    ],
)
def test_upload_creation_validates_filename_and_size(client, payload, status, code):
    response = client.post("/api/uploads", json=payload)
    assert response.status_code == status
    assert response.get_json()["code"] == code


def test_upload_creation_checks_staging_disk_space(client, monkeypatch):
    monkeypatch.setattr(
        cutter.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=8),
    )
    response = client.post(
        "/api/uploads",
        json={"filename": "movie.mp4", "size": 8},
    )
    assert response.status_code == 507
    assert response.get_json()["code"] == "disk_full"


def test_chunked_upload_is_idempotent_and_dispatches_completed_local_export(
    app,
    client,
    monkeypatch,
):
    dispatched = []

    def fake_process(job, config):
        dispatched.append((job, config, job.source_path.read_bytes()))
        output = job.directory / "clip.mp4"
        output.write_bytes(b"rendered clip")
        with job.lock:
            job.output_path = output
            job.download_name = "cut-local.mp4"
            job.status = "ready"
            job.phase = "Ready"
            job.progress = 100.0
            job.message = "ready"

    monkeypatch.setattr(cutter, "binary_available", lambda _binary: True)
    monkeypatch.setattr(cutter, "process_export_job", fake_process)

    created = client.post(
        "/api/uploads",
        json={"filename": "../movie.MP4", "size": 8},
    )
    assert created.status_code == 201
    created_payload = created.get_json()
    upload_id = created_payload["upload_id"]
    assert cutter.valid_job_id(upload_id)
    assert created_payload == {"upload_id": upload_id, "chunk_size": 4, "received": 0}
    upload_dir = Path(app.config["JOBS_DIR"]) / upload_id
    assert (upload_dir / "upload.part").read_bytes() == b""

    first = client.put(
        f"/api/uploads/{upload_id}",
        data=b"abcd",
        headers={"X-Upload-Offset": "0"},
    )
    assert first.status_code == 200
    assert first.get_json() == {"received": 4, "complete": False}
    assert (upload_dir / "upload.part").read_bytes() == b"abcd"

    duplicate = client.put(
        f"/api/uploads/{upload_id}",
        data=b"abcd",
        headers={"X-Upload-Offset": "0"},
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json() == {"received": 4, "complete": False}
    assert (upload_dir / "upload.part").read_bytes() == b"abcd"

    second = client.put(
        f"/api/uploads/{upload_id}",
        data=b"efgh",
        headers={"X-Upload-Offset": "4"},
    )
    assert second.status_code == 200
    assert second.get_json() == {"received": 8, "complete": True}

    completed = client.post(
        f"/api/uploads/{upload_id}/export",
        json={
            "start": 3600.25,
            "end": 3660.75,
            "format": "mp4",
            "quality": "720",
            "strip_metadata": True,
        },
    )
    completed_payload = completed.get_json()
    assert completed.status_code == 202
    assert completed_payload["status"] == "ready"
    assert completed_payload["download_url"] == f"/api/exports/{upload_id}/download"
    assert len(dispatched) == 1
    job, config, uploaded_bytes = dispatched[0]
    assert config["RUN_EXPORTS_INLINE"] is True
    assert uploaded_bytes == b"abcdefgh"
    assert job.source_kind == "local"
    assert job.source_path == upload_dir / "source.mp4"
    assert job.start == 3600.25
    assert job.end == 3660.75
    assert job.strip_metadata is True
    assert upload_id not in app.extensions["cutframe_upload_sessions"]
    assert app.extensions["cutframe_export_jobs"][upload_id] is job

    download = client.get(completed_payload["download_url"])
    assert download.status_code == 200
    assert download.data == b"rendered clip"
    assert "cut-local.mp4" in download.headers["Content-Disposition"]


def test_upload_rejects_invalid_and_mismatched_offsets(client):
    upload_id = start_upload(client)

    missing = client.put(f"/api/uploads/{upload_id}", data=b"a")
    assert missing.status_code == 400
    assert missing.get_json()["code"] == "invalid_offset"

    malformed = client.put(
        f"/api/uploads/{upload_id}",
        data=b"a",
        headers={"X-Upload-Offset": "1.5"},
    )
    assert malformed.status_code == 400
    assert malformed.get_json()["code"] == "invalid_offset"

    first = client.put(
        f"/api/uploads/{upload_id}",
        data=b"abcd",
        headers={"X-Upload-Offset": "0"},
    )
    assert first.status_code == 200

    mismatch = client.put(
        f"/api/uploads/{upload_id}",
        data=b"x",
        headers={"X-Upload-Offset": "5"},
    )
    assert mismatch.status_code == 409
    assert mismatch.get_json()["code"] == "offset_mismatch"
    assert mismatch.get_json()["expected_offset"] == 4

    partial_overlap = client.put(
        f"/api/uploads/{upload_id}",
        data=b"cdef",
        headers={"X-Upload-Offset": "2"},
    )
    assert partial_overlap.status_code == 409
    assert partial_overlap.get_json()["expected_offset"] == 4


def test_upload_rejects_negative_offset(client):
    upload_id = start_upload(client)
    response = client.put(
        f"/api/uploads/{upload_id}",
        data=b"x",
        headers={"X-Upload-Offset": "-1"},
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_offset"


def test_upload_rejects_empty_oversized_and_excess_chunks(client):
    upload_id = start_upload(client)

    empty = client.put(
        f"/api/uploads/{upload_id}",
        data=b"",
        headers={"X-Upload-Offset": "0"},
    )
    assert empty.status_code == 400
    assert empty.get_json()["code"] == "empty_chunk"

    oversized = client.put(
        f"/api/uploads/{upload_id}",
        data=b"abcde",
        headers={"X-Upload-Offset": "0"},
    )
    assert oversized.status_code == 413
    assert oversized.get_json()["code"] == "chunk_too_large"

    short_upload_id = start_upload(client, size=3)
    excess = client.put(
        f"/api/uploads/{short_upload_id}",
        data=b"abcd",
        headers={"X-Upload-Offset": "0"},
    )
    assert excess.status_code == 400
    assert excess.get_json()["code"] == "size_mismatch"


def test_incomplete_upload_cannot_dispatch_export(client, monkeypatch):
    monkeypatch.setattr(cutter, "binary_available", lambda _binary: True)
    upload_id = start_upload(client)
    client.put(
        f"/api/uploads/{upload_id}",
        data=b"abcd",
        headers={"X-Upload-Offset": "0"},
    )

    response = client.post(
        f"/api/uploads/{upload_id}/export",
        json={"start": "0", "end": 1},
    )
    payload = response.get_json()
    assert response.status_code == 409
    assert payload["code"] == "upload_incomplete"
    assert payload["received"] == 4
    assert payload["expected"] == 8


def test_upload_cancel_is_idempotent_and_removes_staged_file(app, client):
    upload_id = start_upload(client)
    upload_dir = Path(app.config["JOBS_DIR"]) / upload_id
    assert upload_dir.is_dir()

    first = client.delete(f"/api/uploads/{upload_id}")
    second = client.delete(f"/api/uploads/{upload_id}")
    assert first.status_code == 204
    assert second.status_code == 204
    assert not upload_dir.exists()
    assert upload_id not in app.extensions["cutframe_upload_sessions"]


def test_export_status_and_cancel_signal_active_job(app, client):
    job = register_export_job(app, status="running")

    status = client.get(f"/api/exports/{job.job_id}")
    assert status.status_code == 200
    assert status.get_json()["status"] == "running"
    assert status.headers["Cache-Control"] == "no-store"

    cancelled = client.delete(f"/api/exports/{job.job_id}")
    assert cancelled.status_code == 202
    assert cancelled.get_json()["phase"] == "Cancelling"
    assert job.cancel_event.is_set()

    job.status = "cancelled"
    terminal = client.delete(f"/api/exports/{job.job_id}")
    assert terminal.status_code == 200
    assert terminal.get_json()["status"] == "cancelled"

    missing = client.delete(f"/api/exports/{'f' * 32}")
    assert missing.status_code == 204


def test_ready_export_download_streams_attachment_and_honors_range(app, client):
    job = register_export_job(app, status="ready")
    output = job.directory / "clip.mp4"
    output.write_bytes(b"rendered clip")
    job.output_path = output
    job.download_name = "cut-range.mp4"
    job.progress = 100.0

    status = client.get(f"/api/exports/{job.job_id}")
    assert status.get_json()["download_url"] == f"/api/exports/{job.job_id}/download"

    response = client.get(f"/api/exports/{job.job_id}/download")
    assert response.status_code == 200
    assert response.data == b"rendered clip"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "cut-range.mp4" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "no-store"

    partial = client.get(
        f"/api/exports/{job.job_id}/download",
        headers={"Range": "bytes=2-4"},
    )
    assert partial.status_code == 206
    assert partial.data == b"nde"
    assert partial.headers["Content-Range"].startswith("bytes 2-4/")


def test_export_status_and_download_report_missing_or_not_ready(app, client):
    missing_id = "e" * 32
    missing_status = client.get(f"/api/exports/{missing_id}")
    missing_download = client.get(f"/api/exports/{missing_id}/download")
    assert missing_status.status_code == 404
    assert missing_status.get_json()["code"] == "export_missing"
    assert missing_download.status_code == 404
    assert missing_download.get_json()["code"] == "export_missing"

    job = register_export_job(app, status="running")
    not_ready = client.get(f"/api/exports/{job.job_id}/download")
    assert not_ready.status_code == 409
    assert not_ready.get_json()["code"] == "export_not_ready"


def test_process_export_job_local_happy_path_creates_ready_download(
    workspace_tmp_path,
    monkeypatch,
):
    job_id = uuid.uuid4().hex
    directory = workspace_tmp_path / job_id
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"source media")
    job = cutter.ExportJob(
        job_id=job_id,
        directory=directory,
        source_kind="local",
        source_path=source,
        start=1.25,
        end=4.75,
        output_format="mp4",
        quality="720",
        mute=False,
        strip_metadata=True,
        fast_copy=False,
    )
    commands = []

    def fake_streaming_command(
        command,
        *,
        timeout,
        cancel_event=None,
        line_callback=None,
        process_callback=None,
    ):
        commands.append(command)
        assert timeout is None
        assert cancel_event is job.cancel_event
        if process_callback:
            process_callback(None)
        if line_callback:
            line_callback("stdout", "out_time_us=1750000")
        Path(command[-1]).write_bytes(b"finished clip")

    monkeypatch.setattr(cutter, "run_streaming_command", fake_streaming_command)
    cutter.process_export_job(job, {"FFMPEG_BINARY": "fake-ffmpeg"})

    assert len(commands) == 1
    command = commands[0]
    assert command[:5] == [
        "fake-ffmpeg",
        "-progress",
        "pipe:1",
        "-nostats",
        "-hide_banner",
    ]
    assert command_value(command, "-ss") == "1.250"
    assert command_value(command, "-t") == "3.500"
    assert command_value(command, "-preset") == "veryfast"
    assert r"scale=-2:trunc(min(720\,ih)/2)*2" in command
    assert "-map_metadata" in command
    assert job.status == "ready"
    assert job.phase == "Ready"
    assert job.progress == 100.0
    assert job.error is None
    assert job.output_path is not None
    assert job.output_path.read_bytes() == b"finished clip"
    assert job.download_name == "cut-00-00-01-00-00-04.mp4"
    assert not source.exists()


def test_probe_media_falls_back_to_ffmpeg_input_summary_when_ffprobe_is_missing(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        cutter,
        "binary_available",
        lambda binary: binary == "fake-ffmpeg",
    )

    def fake_run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(
            stdout="",
            stderr=(
                "Input #0, matroska,webm, from 'long-video.mkv':\n"
                "  Duration: 02:03:04.56, start: 0.000000, bitrate: 3821 kb/s\n"
                "  Stream #0:0: Video: h264 (High), yuv420p, 1920x1080, 60 fps\n"
            ),
            returncode=1,
        )

    monkeypatch.setattr(cutter.subprocess, "run", fake_run)

    metadata = cutter.probe_media(
        Path("long-video.mkv"),
        "missing-ffprobe",
        "fake-ffmpeg",
    )

    assert metadata == {
        "duration": pytest.approx(7384.56),
        "width": 1920,
        "height": 1080,
    }
    assert len(calls) == 1
    command, options = calls[0]
    assert command == [
        "fake-ffmpeg",
        "-hide_banner",
        "-i",
        "long-video.mkv",
    ]
    assert options == {
        "capture_output": True,
        "text": True,
        "timeout": 30,
        "check": False,
    }


def test_probe_media_uses_ffmpeg_fallback_after_ffprobe_execution_error(monkeypatch):
    calls = []
    monkeypatch.setattr(cutter, "binary_available", lambda _binary: True)

    def fake_run(command, **_options):
        calls.append(command)
        if command[0] == "broken-ffprobe":
            raise cutter.subprocess.CalledProcessError(1, command)
        return SimpleNamespace(
            stdout="",
            stderr=(
                "Duration: 00:01:00.25, start: 0.000000, bitrate: N/A\n"
                "Stream #0:0: Video: vp9, yuv420p, 854x480, 30 fps\n"
            ),
            returncode=1,
        )

    monkeypatch.setattr(cutter.subprocess, "run", fake_run)

    metadata = cutter.probe_media(
        Path("source.webm"),
        "broken-ffprobe",
        "fallback-ffmpeg",
    )

    assert metadata == {"duration": 60.25, "width": 854, "height": 480}
    assert calls[0][0] == "broken-ffprobe"
    assert calls[1] == [
        "fallback-ffmpeg",
        "-hide_banner",
        "-i",
        "source.webm",
    ]


def test_process_export_job_rejects_local_range_past_probed_duration(
    workspace_tmp_path,
    monkeypatch,
):
    job_id = uuid.uuid4().hex
    directory = workspace_tmp_path / job_id
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"source media")
    job = cutter.ExportJob(
        job_id=job_id,
        directory=directory,
        source_kind="local",
        source_path=source,
        start=55.0,
        end=61.0,
        output_format="mp4",
        quality="720",
        mute=False,
        strip_metadata=False,
        fast_copy=False,
    )
    probe_calls = []

    def fake_probe(source_path, ffprobe_binary, ffmpeg_binary):
        probe_calls.append((source_path, ffprobe_binary, ffmpeg_binary))
        return {"duration": 60.0, "width": 1920, "height": 1080}

    monkeypatch.setattr(cutter, "probe_media", fake_probe)
    monkeypatch.setattr(
        cutter,
        "run_streaming_command",
        lambda *args, **kwargs: pytest.fail("FFmpeg must not run for an invalid range"),
    )

    cutter.process_export_job(
        job,
        {
            "FFMPEG_BINARY": "fake-ffmpeg",
            "FFPROBE_BINARY": "fake-ffprobe",
        },
    )

    assert probe_calls == [(source, "fake-ffprobe", "fake-ffmpeg")]
    assert job.status == "error"
    assert job.phase == "Export failed"
    assert job.error_code == "invalid_range"
    assert "past this video's 1:00 duration" in job.error
    assert job.progress is None
    assert not directory.exists()


def test_process_export_job_reencodes_after_fast_copy_failure(
    workspace_tmp_path,
    monkeypatch,
):
    job_id = uuid.uuid4().hex
    directory = workspace_tmp_path / job_id
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"source media")
    job = cutter.ExportJob(
        job_id=job_id,
        directory=directory,
        source_kind="local",
        source_path=source,
        start=5.0,
        end=10.0,
        output_format="mp4",
        quality="720",
        mute=True,
        strip_metadata=True,
        fast_copy=True,
    )
    commands = []

    monkeypatch.setattr(
        cutter,
        "probe_media",
        lambda *_args: {"duration": 30.0, "width": 1280, "height": 720},
    )

    def fake_streaming_command(
        command,
        *,
        timeout,
        cancel_event=None,
        line_callback=None,
        process_callback=None,
    ):
        commands.append(command)
        assert timeout is None
        assert cancel_event is job.cancel_event
        if process_callback:
            process_callback(None)
        output = Path(command[-1])
        if len(commands) == 1:
            assert command_value(command, "-c") == "copy"
            output.write_bytes(b"partial incompatible output")
            raise cutter.ToolError("The streams cannot be muxed into MP4.", "tool_error")
        assert not output.exists(), "failed fast-copy output must be removed before retry"
        assert "-c" not in command
        assert command_value(command, "-c:v") == "libx264"
        assert command_value(command, "-preset") == "veryfast"
        assert "-an" in command
        assert "-map_metadata" in command
        if line_callback:
            line_callback("stdout", "out_time_us=5000000")
        output.write_bytes(b"compatible re-encoded clip")

    monkeypatch.setattr(cutter, "run_streaming_command", fake_streaming_command)

    cutter.process_export_job(
        job,
        {
            "FFMPEG_BINARY": "fake-ffmpeg",
            "FFPROBE_BINARY": "fake-ffprobe",
        },
    )

    assert len(commands) == 2
    assert command_value(commands[0], "-c") == "copy"
    assert "-c:v" not in commands[0]
    assert "-c" not in commands[1]
    assert command_value(commands[1], "-c:v") == "libx264"
    assert job.status == "ready"
    assert job.phase == "Ready"
    assert job.progress == 100.0
    assert job.error is None
    assert job.error_code is None
    assert job.output_path is not None
    assert job.output_path.read_bytes() == b"compatible re-encoded clip"
    assert not source.exists()


@pytest.mark.parametrize(
    ("stderr", "code"),
    [
        ("No space left on device", "disk_full"),
        ("HTTP Error 429: Too Many Requests", "rate_limited"),
        ("Sign in to confirm you're not a bot", "authentication_required"),
        ("Could not copy Chrome cookie database: database is locked", "cookies_locked"),
        ("ERROR: Private video", "video_unavailable"),
        ("This video is not available in your country", "geo_restricted"),
        ("DRM protected streams detected", "drm_protected"),
        ("No supported external JavaScript runtime", "javascript_support_missing"),
        ("HTTP Error 503: Service Unavailable", "server_error"),
        ("Connection reset by peer", "network_error"),
        ("Unsupported URL: https://example.invalid", "unsupported_url"),
        ("Unknown encoder 'libmissing'", "encoder_missing"),
    ],
)
def test_error_classifier_returns_actionable_codes(stderr, code):
    error = cutter.classify_tool_error("yt-dlp", stderr)
    assert error.code == code
    assert str(error)


def test_generic_error_classifier_strips_ansi_and_error_prefix():
    error = cutter.classify_tool_error(
        "ffmpeg",
        "\x1b[31mERROR: a precise final failure\x1b[0m",
    )
    assert error.code == "tool_error"
    assert str(error) == "ffmpeg could not process this video. a precise final failure"


def test_run_streaming_command_honors_preexisting_cancellation(monkeypatch):
    class FakeProcess:
        def __init__(self):
            self.stdout = StringIO("")
            self.stderr = StringIO("")

        def poll(self):
            return None

        def wait(self, timeout=None):
            return -9

    process = FakeProcess()
    terminated = []
    callbacks = []
    cancel_event = threading.Event()
    cancel_event.set()
    monkeypatch.setattr(cutter.subprocess, "Popen", lambda _command, **_options: process)
    monkeypatch.setattr(cutter, "terminate_process_tree", lambda active: terminated.append(active))

    with pytest.raises(cutter.ToolError) as raised:
        cutter.run_streaming_command(
            ["fake-tool", "argument"],
            timeout=None,
            cancel_event=cancel_event,
            process_callback=callbacks.append,
        )

    assert raised.value.code == "cancelled"
    assert terminated == [process]
    assert callbacks == [process, None]
    assert process not in cutter.ACTIVE_PROCESSES


def test_version_parser_handles_release_dates():
    assert cutter.version_tuple("2026.06.09") == (2026, 6, 9)
    assert cutter.version_tuple("2026.6") == (2026, 6, 0)
    assert cutter.version_tuple("v22.14.0") == (22, 14, 0)


def test_legacy_clip_endpoint_is_not_registered(app, client):
    assert all(rule.rule != "/api/clip" for rule in app.url_map.iter_rules())
    response = client.post("/api/clip")
    assert response.status_code == 404


def test_fast_copy_uses_stream_copy():
    command = cutter.build_ffmpeg_command(
        "ffmpeg",
        Path("source.mp4"),
        Path("clip.mp4"),
        5,
        10,
        "mp4",
        "1080",
        mute=True,
        strip_metadata=False,
        fast_copy=True,
    )
    assert ["-c", "copy"] == command[command.index("-c") : command.index("-c") + 2]
    assert "-an" in command
    assert "-vf" not in command


def test_audio_export_has_no_video():
    command = cutter.build_ffmpeg_command(
        "ffmpeg",
        Path("source.webm"),
        Path("clip.mp3"),
        0,
        8,
        "mp3",
        "original",
        mute=False,
        strip_metadata=True,
        fast_copy=False,
    )
    assert "-vn" in command
    assert "libmp3lame" in command
    assert "-map_metadata" in command


def test_server_disables_reloader_and_cleans_children(monkeypatch):
    calls = {}

    class FakeApplication:
        def run(self, **kwargs):
            calls.update(kwargs)
            raise KeyboardInterrupt

    cleaned = []
    monkeypatch.setattr(cutter, "terminate_active_processes", lambda: cleaned.append(True))
    cutter.run_development_server(FakeApplication())
    assert calls["use_reloader"] is False
    assert calls["threaded"] is True
    assert cleaned == [True]
