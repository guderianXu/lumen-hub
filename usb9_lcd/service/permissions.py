from __future__ import annotations

import os
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

SAFE_PERMISSION_OPERATIONS = (
    "pwm-write",
    "powercap-read",
    "hidraw-write",
    "openrgb-path-check",
)


@dataclass(frozen=True)
class PermissionRequest:
    operation: str
    label: str
    paths: tuple[Path, ...]
    access: str
    shell_command: str = ""
    detail: str = ""


@dataclass(frozen=True)
class PermissionGrantResult:
    ok: bool
    message: str = ""


@dataclass(frozen=True)
class PermissionHelperStatus:
    available: bool
    backend: str
    detail: str
    supported_operations: tuple[str, ...] = SAFE_PERMISSION_OPERATIONS


@dataclass(frozen=True)
class PermissionStatusItem:
    label: str
    state: str
    detail: str


@runtime_checkable
class PermissionHelper(Protocol):
    def status(self) -> PermissionHelperStatus: ...

    def grant(self, request: PermissionRequest, *, interactive: bool, timeout: int) -> PermissionGrantResult | tuple[bool, str]: ...


def build_pwm_write_request(paths: list[Path] | tuple[Path, ...]) -> PermissionRequest:
    target_paths = _normalized_paths(paths)
    return PermissionRequest(
        operation="pwm-write",
        label="授权 PWM 权限",
        paths=target_paths,
        access="read-write",
        shell_command=_chown_chmod_shell(target_paths, chmod="u+rw,g+rw", marker="pwm-permissions"),
        detail=f"{len(target_paths)} 个 pwm* 或 pwm*_enable 文件",
    )


def build_powercap_read_request(paths: list[Path] | tuple[Path, ...], *, shell_command: str | None = None) -> PermissionRequest:
    target_paths = _normalized_paths(paths)
    return PermissionRequest(
        operation="powercap-read",
        label="授权 CPU 功耗权限",
        paths=target_paths,
        access="read",
        shell_command=shell_command if shell_command is not None else _chown_chmod_shell(
            target_paths,
            chmod="u+r,g+r",
            marker="cpu-power-permissions",
        ),
        detail=f"{len(target_paths)} 个 powercap 文件",
    )


def build_hidraw_write_request(paths: list[Path] | tuple[Path, ...]) -> PermissionRequest:
    target_paths = _normalized_paths(paths)
    return PermissionRequest(
        operation="hidraw-write",
        label="授权 HID 写权限",
        paths=target_paths,
        access="read-write",
        shell_command=_chown_chmod_shell(target_paths, chmod="u+rw,g+rw", marker="hidraw-permissions"),
        detail=f"{len(target_paths)} 个 hidraw 文件",
    )


def build_openrgb_path_check_request(path: Path | str) -> PermissionRequest:
    target_path = Path(path)
    return PermissionRequest(
        operation="openrgb-path-check",
        label="检查 OpenRGB 路径",
        paths=(target_path,),
        access="exists-executable",
        shell_command="",
        detail=str(target_path),
    )


def detect_permission_helper_status(helper: object | None = None) -> PermissionHelperStatus:
    if helper is not None and hasattr(helper, "status"):
        try:
            status = helper.status()  # type: ignore[attr-defined]
            if isinstance(status, PermissionHelperStatus):
                return status
        except Exception as exc:  # noqa: BLE001
            return PermissionHelperStatus(
                available=False,
                backend="helper-error",
                detail=f"权限 helper 状态读取失败: {exc}",
            )
    return PermissionHelperStatus(
        available=False,
        backend="direct-shell-fallback",
        detail="权限 helper 服务未连接；GUI 将使用 pkexec/sudo 直接授权。",
    )


def permission_helper_status_items(status: PermissionHelperStatus) -> list[PermissionStatusItem]:
    operations = ", ".join(status.supported_operations) if status.supported_operations else "none"
    state = "ok" if status.available else "info"
    return [
        PermissionStatusItem(
            label="权限 Helper",
            state=state,
            detail=f"{status.backend}: {status.detail}; operations: {operations}",
        )
    ]


def grant_permission_request(
    request: PermissionRequest,
    *,
    helper: object | None,
    fallback_runner: Callable[..., tuple[bool, str]],
    interactive: bool,
    timeout: int,
) -> PermissionGrantResult:
    status = detect_permission_helper_status(helper)
    helper_has_explicit_status = helper is not None and hasattr(helper, "status")
    if helper is not None and hasattr(helper, "grant") and (status.available or not helper_has_explicit_status):
        return _normalize_grant_result(helper.grant(request, interactive=interactive, timeout=timeout))  # type: ignore[attr-defined]
    if not request.shell_command:
        return PermissionGrantResult(False, f"{request.label}没有可执行授权命令。")
    ok, output = fallback_runner(
        request.shell_command,
        timeout=timeout,
        interactive=interactive,
        action_label=request.label,
    )
    return PermissionGrantResult(ok, output)


def _normalize_grant_result(result: PermissionGrantResult | tuple[bool, str]) -> PermissionGrantResult:
    if isinstance(result, PermissionGrantResult):
        return result
    ok, message = result
    return PermissionGrantResult(bool(ok), str(message))


def _normalized_paths(paths: list[Path] | tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[str] = set()
    normalized: list[Path] = []
    for path in paths:
        candidate = Path(path)
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(candidate)
    return tuple(normalized)


def _chown_chmod_shell(paths: tuple[Path, ...], *, chmod: str, marker: str) -> str:
    uid = os.getuid() if hasattr(os, "getuid") else 0
    gid = os.getgid() if hasattr(os, "getgid") else 0
    quoted_paths = " ".join(shlex.quote(str(path)) for path in paths)
    return "\n".join(
        [
            "set -e",
            f"chown {uid}:{gid} -- {quoted_paths}",
            f"chmod {chmod} -- {quoted_paths}",
            f"echo '{marker}=ok files={len(paths)}'",
        ]
    )
