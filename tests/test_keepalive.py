from pathlib import Path

from usb9_lcd.keepalive import keepalive_worker_command, run_keepalive, stop_existing_keepalive


def test_keepalive_worker_command_uses_module_in_development(monkeypatch):
    monkeypatch.setattr("usb9_lcd.keepalive.sys.executable", "/usr/bin/python")
    monkeypatch.setattr("usb9_lcd.keepalive.sys.frozen", False, raising=False)

    assert keepalive_worker_command(["frame.bin"]) == ["/usr/bin/python", "-m", "usb9_lcd.keepalive", "frame.bin"]


def test_keepalive_worker_command_uses_packaged_exe_when_frozen(monkeypatch):
    monkeypatch.setattr("usb9_lcd.keepalive.sys.executable", "/opt/LumenHub/LumenHub")
    monkeypatch.setattr("usb9_lcd.keepalive.sys.frozen", True, raising=False)

    assert keepalive_worker_command(["frame.bin"]) == [
        "/opt/LumenHub/LumenHub",
        "--lumen-hub-keepalive-worker",
        "frame.bin",
    ]


def test_stop_existing_keepalive_removes_stale_pid(monkeypatch, tmp_path: Path):
    pid_file = tmp_path / "keepalive.pid"
    pid_file.write_text("99999999\n", encoding="utf-8")

    calls = []

    def fake_kill(pid, sig):  # noqa: ANN001
        calls.append((pid, sig))
        raise ProcessLookupError

    monkeypatch.setattr("usb9_lcd.keepalive.os.kill", fake_kill)

    stop_existing_keepalive(pid_file)

    assert calls
    assert not pid_file.exists()


def test_run_keepalive_uploads_frame_until_interrupted(monkeypatch, tmp_path: Path):
    frame_path = tmp_path / "frame.bin"
    pid_file = tmp_path / "keepalive.pid"
    frame_path.write_bytes(b"jpeg-frame")
    uploads = []

    def fake_upload(frame: bytes) -> None:
        uploads.append(frame)

    def fake_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("usb9_lcd.keepalive.stop_existing_keepalive", lambda pid_file: None)
    monkeypatch.setattr("usb9_lcd.keepalive.upload_frame_once", fake_upload)
    monkeypatch.setattr("usb9_lcd.keepalive.time.sleep", fake_sleep)

    try:
        run_keepalive(frame_path, interval=1.0, pid_file=pid_file)
    except KeyboardInterrupt:
        pass

    assert uploads == [b"jpeg-frame"]
    assert not pid_file.exists()
