from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _load_build_module():
    path = Path("tools/build_package.py")
    spec = importlib.util.spec_from_file_location("lumen_hub_build_package", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pyinstaller_args_use_gui_entry_and_bundle_assets():
    module = _load_build_module()
    config = module.BuildConfig(
        repo_root=Path("E:/repo/lumen-hub"),
        system="Windows",
        onefile=False,
        clean=True,
    )

    args = module.build_pyinstaller_args(config)

    assert "--onedir" in args
    assert "--windowed" in args
    assert "--clean" in args
    assert args[args.index("--name") + 1] == "LumenHub"
    assert args[args.index("--collect-all") + 1] == "PySide6"
    assert _arg_pair_exists(args, "--hidden-import", "usb9_lcd.gui.app")
    assert _arg_pair_exists(args, "--hidden-import", "usb9_lcd.gui.gif_preview")
    assert _arg_pair_exists(args, "--hidden-import", "hid")
    assert _arg_pair_exists(args, "--hidden-import", "Cryptodome.Cipher.DES")
    assert not _arg_pair_exists(args, "--hidden-import", "Cryptodome.Cipher.AES")
    assert str(Path("E:/repo/lumen-hub") / "packaging" / "pyinstaller_lumen_hub_entry.py") in args
    assert any(item.endswith("assets;assets") for item in args)


def test_windows_pyinstaller_args_bundle_libusb_backend(monkeypatch, tmp_path):
    module = _load_build_module()
    libusb_dll = tmp_path / "usb1" / "libusb-1.0.dll"
    libusb_dll.parent.mkdir()
    libusb_dll.write_bytes(b"fake dll")
    monkeypatch.setattr(module, "libusb_runtime_binaries", lambda _config: [(libusb_dll, "usb1")])
    config = module.BuildConfig(
        repo_root=Path("E:/repo/lumen-hub"),
        system="Windows",
        onefile=False,
    )

    args = module.build_pyinstaller_args(config)

    assert _arg_pair_exists(args, "--hidden-import", "usb.backend.libusb1")
    assert _arg_pair_exists(args, "--hidden-import", "usb1")
    assert _arg_pair_exists(args, "--add-binary", f"{libusb_dll};usb1")


def test_libusb_runtime_binaries_uses_usb1_package_path(monkeypatch, tmp_path):
    module = _load_build_module()
    libusb_dll = tmp_path / "usb1" / "libusb-1.0.dll"
    libusb_dll.parent.mkdir()
    libusb_dll.write_bytes(b"fake dll")
    fake_usb1 = types.SimpleNamespace(__file__=str(libusb_dll.parent / "__init__.py"))
    monkeypatch.setitem(sys.modules, "usb1", fake_usb1)
    config = module.BuildConfig(repo_root=tmp_path, system="Windows")

    assert module.libusb_runtime_binaries(config) == [(libusb_dll, "usb1")]


def test_libusb_runtime_binaries_uses_build_venv_when_system_package_missing(monkeypatch, tmp_path):
    module = _load_build_module()
    libusb_dll = tmp_path / "build-venv" / "Lib" / "site-packages" / "usb1" / "libusb-1.0.dll"
    libusb_dll.parent.mkdir(parents=True)
    libusb_dll.write_bytes(b"fake dll")
    monkeypatch.setitem(sys.modules, "usb1", None)
    config = module.BuildConfig(repo_root=tmp_path, system="Windows", venv_dir=tmp_path / "build-venv")

    assert module.libusb_runtime_binaries(config) == [(libusb_dll, "usb1")]


def test_output_executable_name_matches_platform():
    module = _load_build_module()

    windows = module.BuildConfig(repo_root=Path("E:/repo/lumen-hub"), system="Windows")
    linux = module.BuildConfig(repo_root=Path("/repo/lumen-hub"), system="Linux")

    assert module.output_executable_path(windows) == Path("E:/repo/lumen-hub/dist/LumenHub/LumenHub.exe")
    assert module.output_executable_path(linux) == Path("/repo/lumen-hub/dist/LumenHub/lumen-hub")

    linux_onefile = module.BuildConfig(repo_root=Path("/repo/lumen-hub"), system="Linux", onefile=True)
    assert module.output_executable_path(linux_onefile) == Path("/repo/lumen-hub/dist/lumen-hub")


def test_platform_one_click_scripts_delegate_to_shared_builder():
    windows_script = Path("packaging/windows/build-exe.ps1").read_text(encoding="utf-8")
    linux_script = Path("packaging/linux/build-executable.sh").read_text(encoding="utf-8")

    assert "tools/build_package.py" in windows_script
    assert "tools/build_package.py" in linux_script
    assert "-OneFile" in windows_script
    assert "--onefile" in linux_script


def test_linux_install_script_builds_and_installs_user_app():
    script = Path("packaging/linux/install-app.sh").read_text(encoding="utf-8")

    assert "build-executable.sh" in script
    assert ".local/opt/lumen-hub" in script
    assert ".local/bin" in script
    assert "lumen-hub-gui" in script
    assert ".local/share/applications" in script
    assert "lumen-hub.desktop" in script
    assert "update-desktop-database" in script


def test_linux_install_script_handles_onefile_output_and_escapes_desktop_exec():
    script = Path("packaging/linux/install-app.sh").read_text(encoding="utf-8")

    assert "ONEFILE=0" in script
    assert 'ONEFILE=1' in script
    assert '"$REPO_ROOT/dist/lumen-hub"' in script
    assert "desktop_escape_exec" in script
    assert "DESKTOP_EXEC=" in script
    assert 'Exec=$DESKTOP_EXEC' in script


def test_prepare_build_venv_recreates_wrong_platform_layout(tmp_path, monkeypatch):
    module = _load_build_module()
    venv_dir = tmp_path / "package-venv"
    (venv_dir / "Scripts").mkdir(parents=True)
    (venv_dir / "Scripts" / "python.exe").write_text("windows placeholder", encoding="utf-8")
    config = module.BuildConfig(repo_root=tmp_path, system="Linux", venv_dir=venv_dir)
    commands: list[list[str]] = []

    class FakeEnvBuilder:
        def __init__(self, *, with_pip: bool) -> None:
            self.with_pip = with_pip

        def create(self, path: Path) -> None:
            python_path = Path(path) / "bin" / "python"
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")

    monkeypatch.setattr(module.venv, "EnvBuilder", FakeEnvBuilder)
    monkeypatch.setattr(module, "_run", lambda command, *, cwd: commands.append(command))

    python_exe = module._prepare_build_venv(config)

    assert python_exe == str(venv_dir / "bin" / "python")
    assert not (venv_dir / "Scripts" / "python.exe").exists()
    assert (venv_dir / "bin" / "python").exists()
    assert commands[0][:3] == [python_exe, "-m", "pip"]


def test_windows_release_script_builds_zip_bundle():
    script = Path("packaging/windows/package-release.ps1").read_text(encoding="utf-8")

    assert "build-exe.ps1" in script
    assert "Compress-Archive" in script
    assert "LumenHub-windows-x64.zip" in script
    assert "-SkipInstall" in script


def test_windows_install_script_creates_user_shortcuts():
    script = Path("packaging/windows/install-app.ps1").read_text(encoding="utf-8")

    assert "$env:LOCALAPPDATA" in script
    assert "Programs" in script
    assert "Start Menu" in script
    assert "WScript.Shell" in script
    assert "LumenHub.exe" in script


def test_windows_install_script_refuses_unmarked_install_directory_deletion():
    script = Path("packaging/windows/install-app.ps1").read_text(encoding="utf-8")

    assert "$InstallMarkerName" in script
    assert "Assert-SafeInstallDir" in script
    assert "Assert-SafeSourceDir" in script
    assert "Assert-InstallRoot" in script
    assert "Refusing to remove unmarked install directory" in script
    assert "GetPathRoot" in script


def test_pyinstaller_entry_dispatches_gif_worker_before_gui(monkeypatch):
    app_calls: list[list[str] | None] = []
    gif_calls: list[list[str] | None] = []
    keepalive_calls: list[list[str] | None] = []
    fake_app = types.ModuleType("usb9_lcd.gui.app")
    fake_gif = types.ModuleType("usb9_lcd.gui.gif_preview")
    fake_keepalive = types.ModuleType("usb9_lcd.keepalive")
    fake_app.main = lambda argv=None: app_calls.append(argv) or 11
    fake_gif.main = lambda argv=None: gif_calls.append(argv) or 22
    fake_keepalive.main = lambda argv=None: keepalive_calls.append(argv) or 33
    monkeypatch.setitem(sys.modules, "usb9_lcd.gui.app", fake_app)
    monkeypatch.setitem(sys.modules, "usb9_lcd.gui.gif_preview", fake_gif)
    monkeypatch.setitem(sys.modules, "usb9_lcd.keepalive", fake_keepalive)
    path = Path("packaging/pyinstaller_lumen_hub_entry.py")
    spec = importlib.util.spec_from_file_location("lumen_hub_pyinstaller_entry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--lumen-hub-gif-preview-worker", "--decode", "a.gif", "out"]) == 22
    assert gif_calls == [["--decode", "a.gif", "out"]]
    assert module.main(["--lumen-hub-keepalive-worker", "frame.bin", "--interval", "1.0"]) == 33
    assert keepalive_calls == [["frame.bin", "--interval", "1.0"]]
    assert app_calls == []


def test_gitignore_excludes_local_package_outputs():
    lines = Path(".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".build/" in lines
    assert "release/" in lines


def _arg_pair_exists(args: list[str], key: str, value: str) -> bool:
    return any(current == key and args[index + 1 : index + 2] == [value] for index, current in enumerate(args))
