from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

import app as cutter


def run_ffmpeg(binary: str, *arguments: str, timeout: float = 90) -> None:
    completed = subprocess.run(
        [binary, "-hide_banner", "-loglevel", "error", "-y", *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(completed.stderr.strip() or "FFmpeg fixture generation failed.")


@pytest.fixture(scope="session")
def media_fixtures() -> dict[str, Path | str]:
    ffmpeg = cutter.resolve_ffmpeg()
    if not ffmpeg or not cutter.binary_available(ffmpeg):
        pytest.skip("FFmpeg is required for generated-media integration tests.")

    root = Path(".pytest-tmp") / "generated-media" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    minute_source = root / "minute-source.mp4"
    long_metadata_source = root / "long-metadata-source.mp4"
    incompatible_copy_source = root / "incompatible-copy-source.avi"

    run_ffmpeg(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=5:duration=60",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=16000:duration=60",
        "-t",
        "60",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "10",
        "-c:a",
        "aac",
        "-b:a",
        "32k",
        str(minute_source),
    )
    # Two frames with a large presentation-time gap create a tiny fixture whose
    # container metadata reports just over two hours.
    run_ffmpeg(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=16x16:r=1:d=2",
        "-vf",
        "setpts=7201*PTS",
        "-fps_mode",
        "passthrough",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(long_metadata_source),
    )
    # Raw video cannot be stream-copied into MP4 by FFmpeg, which exercises the
    # production copy-then-reencode recovery path without mocking the process.
    run_ffmpeg(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=64x64:rate=5:duration=3",
        "-c:v",
        "rawvideo",
        "-pix_fmt",
        "yuv420p",
        str(incompatible_copy_source),
    )

    fixtures: dict[str, Path | str] = {
        "ffmpeg": ffmpeg,
        "root": root,
        "minute": minute_source,
        "long": long_metadata_source,
        "copy_incompatible": incompatible_copy_source,
    }
    yield fixtures
    shutil.rmtree(root, ignore_errors=True)


@pytest.mark.media
def test_generated_sources_cover_one_minute_and_more_than_two_hours(media_fixtures):
    ffmpeg = str(media_fixtures["ffmpeg"])
    minute = cutter.probe_media(
        Path(media_fixtures["minute"]),
        "cutframe-missing-ffprobe",
        ffmpeg,
    )
    long_source = cutter.probe_media(
        Path(media_fixtures["long"]),
        "cutframe-missing-ffprobe",
        ffmpeg,
    )

    assert minute["duration"] == pytest.approx(60.0, abs=0.15)
    assert minute["width"] == 160
    assert minute["height"] == 90
    assert long_source["duration"] is not None
    assert long_source["duration"] > 2 * 60 * 60
    assert Path(media_fixtures["long"]).stat().st_size < 10_000


@pytest.mark.media
@pytest.mark.parametrize(
    ("label", "start", "end"),
    [
        ("beginning", 0.0, 1.25),
        ("middle", 29.375, 31.625),
        ("near-end", 58.125, 59.75),
    ],
)
def test_exact_generated_cuts_support_fractional_boundaries(
    media_fixtures,
    label,
    start,
    end,
):
    ffmpeg = str(media_fixtures["ffmpeg"])
    root = Path(media_fixtures["root"])
    output = root / f"{label}.mp4"
    command = cutter.build_ffmpeg_command(
        ffmpeg,
        Path(media_fixtures["minute"]),
        output,
        start,
        end,
        "mp4",
        "original",
        mute=False,
        strip_metadata=True,
        fast_copy=False,
    )

    cutter.run_command(command, timeout=60)

    metadata = cutter.probe_media(output, "cutframe-missing-ffprobe", ffmpeg)
    assert output.stat().st_size > 0
    assert metadata["duration"] == pytest.approx(end - start, abs=0.2)
    assert metadata["width"] == 160
    assert metadata["height"] == 90


@pytest.mark.media
def test_real_fast_copy_failure_falls_back_to_reencoding(media_fixtures, monkeypatch):
    ffmpeg = str(media_fixtures["ffmpeg"])
    root = Path(media_fixtures["root"]) / f"fallback-{uuid.uuid4().hex}"
    root.mkdir()
    source = root / "source.avi"
    shutil.copyfile(Path(media_fixtures["copy_incompatible"]), source)
    job = cutter.ExportJob(
        job_id=root.name.removeprefix("fallback-"),
        directory=root,
        source_kind="local",
        source_path=source,
        start=0.25,
        end=1.75,
        output_format="mp4",
        quality="original",
        mute=False,
        strip_metadata=True,
        fast_copy=True,
    )
    commands: list[list[str]] = []
    original_runner = cutter.run_streaming_command

    def recording_runner(command, **kwargs):
        if str(root / "clip.mp4") in command:
            commands.append(command)
        return original_runner(command, **kwargs)

    monkeypatch.setattr(cutter, "run_streaming_command", recording_runner)
    application = cutter.create_app(
        {
            "TESTING": True,
            "JOBS_DIR": root.parent / f"registry-{uuid.uuid4().hex}",
            "FFMPEG_BINARY": ffmpeg,
            "FFPROBE_BINARY": "cutframe-missing-ffprobe",
            "RUN_EXPORTS_INLINE": True,
            "MAX_EXPORT_WORKERS": 1,
        }
    )
    try:
        cutter.process_export_job(job, dict(application.config))
    finally:
        application.extensions["cutframe_executor"].shutdown(
            wait=True,
            cancel_futures=True,
        )

    assert job.status == "ready"
    assert job.output_path is not None and job.output_path.stat().st_size > 0
    assert len(commands) >= 2
    assert any("copy" in command for command in commands[:1])
    assert any("libx264" in command for command in commands[1:])
    output_metadata = cutter.probe_media(
        job.output_path,
        "cutframe-missing-ffprobe",
        ffmpeg,
    )
    assert output_metadata["duration"] == pytest.approx(1.5, abs=0.2)

