from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
JAVASCRIPT = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")


def test_public_brand_and_privacy_contract() -> None:
    assert "<title>CutFrame | Video cutter</title>" in HTML
    assert 'name="description"' in HTML
    assert 'name="application-name" content="CutFrame"' in HTML
    assert "favicon.svg" in HTML
    assert ">CutFrame</span>" in HTML
    assert "YouTube previews contact YouTube or Google" in HTML
    assert "downloads only the selected YouTube range" in HTML
    assert "Local previews stay in your browser until export" in HTML
    assert "Cutframe" not in HTML
    assert "\u2013" not in HTML and "\u2014" not in HTML
    assert "\u2013" not in JAVASCRIPT and "\u2014" not in JAVASCRIPT


def test_youtube_embed_is_privacy_enhanced_and_least_privilege() -> None:
    assert "https://www.youtube-nocookie.com" in JAVASCRIPT
    assert 'iframe.allow = "autoplay; encrypted-media; picture-in-picture"' in JAVASCRIPT
    for unnecessary_permission in ("clipboard-write", "gyroscope", "web-share", "accelerometer"):
        assert unnecessary_permission not in JAVASCRIPT


def test_unsafe_api_fetches_carry_local_request_header() -> None:
    lines = JAVASCRIPT.splitlines()
    mutation_lines = [
        index
        for index, line in enumerate(lines)
        if 'method: "POST"' in line or 'method: "PUT"' in line or 'method: "DELETE"' in line
    ]
    assert len(mutation_lines) == 7
    for index in mutation_lines:
        request_options = "\n".join(lines[index : index + 7])
        assert "mutationHeaders" in request_options or "API_MUTATION_HEADER" in request_options


def test_export_progress_and_range_controls_have_accessible_names() -> None:
    assert 'id="exportProgress"' in HTML
    assert 'aria-labelledby="exportPhase"' in HTML
    assert 'aria-describedby="exportMessage"' in HTML
    assert 'id="exportAnnouncement" role="status" aria-live="polite" aria-atomic="true"' in HTML
    assert 'id="exportJob" aria-labelledby="exportPhase" hidden' in HTML
    assert 'id="startRange"' in HTML and 'aria-valuetext="00:00"' in HTML
    assert HTML.count('step="0.001"') == 2
    assert 'elements.startRange.setAttribute("aria-valuetext"' in JAVASCRIPT
    assert 'elements.endRange.setAttribute("aria-valuetext"' in JAVASCRIPT


def test_frontend_preserves_recoverable_work_and_confirms_cancellation() -> None:
    assert 'const ACTIVE_WORK_KEY = "cutframe.active-work.v1"' in JAVASCRIPT
    assert "sessionStorage.setItem" in JAVASCRIPT
    assert "async function recoverActiveWork()" in JAVASCRIPT
    assert "Cancellation not confirmed" in JAVASCRIPT
    assert "Confirming cancellation" in JAVASCRIPT
    assert "new Set([uploadId, jobId].filter(Boolean))" in JAVASCRIPT
    assert "clearActiveWork(job.job_id)" in JAVASCRIPT
    assert 'error.code === "offset_mismatch"' in JAVASCRIPT
    assert 'error.code === "short_chunk"' in JAVASCRIPT
    assert "error.expectedOffset = Number(payload.expected_offset)" in JAVASCRIPT


def test_keyboard_contrast_and_motion_contracts() -> None:
    assert "input[type=\"url\"]:focus-visible" in CSS
    assert "input[type=\"text\"]:focus-visible" in CSS
    assert "@media (pointer: coarse)" in CSS
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    assert "@media (forced-colors: active)" in CSS
    assert "background: Highlight" in CSS
    assert "aspect-ratio: auto" not in CSS


def test_javascript_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed in this test environment")
    completed = subprocess.run(
        [node, "--check", str(ROOT / "static" / "app.js")],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
