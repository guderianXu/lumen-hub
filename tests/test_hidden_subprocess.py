from __future__ import annotations

import usb9_lcd.platforms.process as process_module
from usb9_lcd.platforms.process import hidden_subprocess_kwargs


def test_hidden_subprocess_kwargs_hides_windows_console_windows(monkeypatch):
    class FakeStartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(process_module.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(process_module.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(process_module.subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(process_module.subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)

    kwargs = hidden_subprocess_kwargs(system="Windows")

    assert kwargs["creationflags"] & 0x08000000
    startupinfo = kwargs["startupinfo"]
    assert startupinfo.dwFlags & 1
    assert startupinfo.wShowWindow == 0


def test_hidden_subprocess_kwargs_is_empty_on_non_windows():
    assert hidden_subprocess_kwargs(system="Linux") == {}
