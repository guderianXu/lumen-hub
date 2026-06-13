from __future__ import annotations

import platform
import subprocess
from typing import Any


def hidden_subprocess_kwargs(*, system: str | None = None) -> dict[str, Any]:
    """Return subprocess options that keep helper commands out of the desktop."""
    resolved_system = system or platform.system()
    if not resolved_system.lower().startswith("win"):
        return {}

    kwargs: dict[str, Any] = {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creationflags:
        kwargs["creationflags"] = creationflags

    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    startf_use_show_window = getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    if startupinfo_type is not None and startf_use_show_window:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= startf_use_show_window
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo

    return kwargs
