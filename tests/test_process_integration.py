from __future__ import annotations

import csv
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

import app as cutter


def process_exists(process_id: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {process_id}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        rows = list(csv.reader(completed.stdout.splitlines()))
        return any(len(row) > 1 and row[1] == str(process_id) for row in rows)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def force_stop_process(process_id: int) -> None:
    if not process_exists(process_id):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process_id), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return
    try:
        os.kill(process_id, signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.mark.process
def test_cancellation_terminates_the_spawned_process_tree():
    cancel_event = threading.Event()
    child_seen = threading.Event()
    child_id: list[int] = []
    errors: list[BaseException] = []
    parent_script = (
        "import subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "print(child.pid,flush=True);"
        "time.sleep(60)"
    )

    def capture_line(stream: str, line: str) -> None:
        if stream == "stdout" and line.strip().isdigit():
            child_id.append(int(line.strip()))
            child_seen.set()

    def run_parent() -> None:
        try:
            cutter.run_streaming_command(
                [sys.executable, "-c", parent_script],
                timeout=20,
                cancel_event=cancel_event,
                line_callback=capture_line,
            )
        except BaseException as exc:  # captured for assertion in the test thread
            errors.append(exc)

    worker = threading.Thread(target=run_parent, name="cutframe-process-test")
    worker.start()
    try:
        assert child_seen.wait(10), "The helper process did not report its child PID."
        cancel_event.set()
        worker.join(20)
        assert not worker.is_alive(), "Cancellation did not return promptly."
        assert len(errors) == 1
        assert isinstance(errors[0], cutter.ToolError)
        assert errors[0].code == "cancelled"

        deadline = time.monotonic() + 5
        while process_exists(child_id[0]) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not process_exists(child_id[0]), "A cancelled child process was left running."
        assert not cutter.ACTIVE_PROCESSES
    finally:
        cancel_event.set()
        worker.join(2)
        if child_id:
            force_stop_process(child_id[0])


@pytest.mark.process
def test_process_timeout_is_bounded_and_cleans_the_registry():
    with pytest.raises(cutter.ToolError) as captured:
        cutter.run_streaming_command(
            [sys.executable, "-c", "import time;time.sleep(60)"],
            timeout=0.2,
        )

    assert captured.value.code == "timeout"
    assert not cutter.ACTIVE_PROCESSES

