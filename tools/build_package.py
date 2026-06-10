from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path


class BuildConfig:
    def __init__(
        self,
        *,
        repo_root: Path,
        system: str | None = None,
        onefile: bool = False,
        clean: bool = False,
        skip_install: bool = False,
        venv_dir: Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.system = system or platform.system()
        self.onefile = bool(onefile)
        self.clean = bool(clean)
        self.skip_install = bool(skip_install)
        self.venv_dir = Path(venv_dir) if venv_dir is not None else self.repo_root / ".build" / "package-venv"

    @property
    def is_windows(self) -> bool:
        return self.system.lower().startswith("win")


def build_pyinstaller_args(config: BuildConfig) -> list[str]:
    repo_root = config.repo_root
    dist_dir = repo_root / "dist"
    build_dir = repo_root / "build" / "pyinstaller"
    spec_dir = build_dir / "spec"
    entry_script = repo_root / "packaging" / "pyinstaller_lumen_hub_entry.py"

    args = [
        "--noconfirm",
        "--onefile" if config.onefile else "--onedir",
        "--windowed",
        "--name",
        "LumenHub",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir / "work"),
        "--specpath",
        str(spec_dir),
        "--collect-all",
        "PySide6",
        "--hidden-import",
        "usb9_lcd.gui.app",
        "--hidden-import",
        "usb9_lcd.gui.gif_preview",
        "--hidden-import",
        "usb.core",
        "--hidden-import",
        "usb.util",
        "--hidden-import",
        "Cryptodome.Cipher.AES",
    ]
    if config.clean:
        args.append("--clean")

    assets_dir = repo_root / "assets"
    args.extend(["--add-data", f"{assets_dir}{_data_separator(config.system)}assets"])

    args.append(str(entry_script))
    return args


def output_executable_path(config: BuildConfig) -> Path:
    if config.onefile:
        return config.repo_root / "dist" / ("LumenHub.exe" if config.is_windows else "lumen-hub")
    return config.repo_root / "dist" / "LumenHub" / ("LumenHub.exe" if config.is_windows else "lumen-hub")


def run_build(config: BuildConfig) -> Path:
    python_exe = sys.executable if config.skip_install else _prepare_build_venv(config)
    _run([python_exe, "-m", "PyInstaller", *build_pyinstaller_args(config)], cwd=config.repo_root)
    _normalize_output_name(config)
    output = output_executable_path(config)
    if not output.exists():
        raise SystemExit(f"Expected packaged executable was not created: {output}")
    print(f"Packaged executable: {output}")
    return output


def _prepare_build_venv(config: BuildConfig) -> str:
    python_exe = _venv_python(config)
    if config.venv_dir.exists() and not Path(python_exe).is_file():
        print(f"Removing incomplete build virtual environment: {config.venv_dir}")
        shutil.rmtree(config.venv_dir)
    if not config.venv_dir.exists():
        print(f"Creating build virtual environment: {config.venv_dir}")
        venv.EnvBuilder(with_pip=True).create(config.venv_dir)

    python_exe = _venv_python(config)
    _run([python_exe, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], cwd=config.repo_root)
    _run([python_exe, "-m", "pip", "install", "--upgrade", "-e", ".[lianli]", "pyinstaller>=6.0"], cwd=config.repo_root)
    return python_exe


def _venv_python(config: BuildConfig) -> str:
    if config.is_windows:
        return str(config.venv_dir / "Scripts" / "python.exe")
    return str(config.venv_dir / "bin" / "python")


def _normalize_output_name(config: BuildConfig) -> None:
    if config.is_windows:
        return
    if config.onefile:
        built = config.repo_root / "dist" / "LumenHub"
    else:
        built = config.repo_root / "dist" / "LumenHub" / "LumenHub"
    target = output_executable_path(config)
    if built == target or not built.exists():
        return
    if target.exists():
        target.unlink()
    built.rename(target)
    target.chmod(target.stat().st_mode | 0o111)


def _data_separator(system: str) -> str:
    return ";" if system.lower().startswith("win") else ":"


def _run(command: list[str], *, cwd: Path) -> None:
    print("+ " + " ".join(_quote(part) for part in command))
    subprocess.run(command, cwd=str(cwd), check=True)


def _quote(value: str) -> str:
    if not value or any(char.isspace() for char in value):
        return repr(value)
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a double-clickable Lumen Hub desktop executable.")
    parser.add_argument("--onefile", action="store_true", help="Build a single-file executable instead of the default directory bundle.")
    parser.add_argument("--clean", action="store_true", help="Ask PyInstaller to remove cached build state before packaging.")
    parser.add_argument("--skip-install", action="store_true", help="Use the current Python environment instead of creating/updating .build/package-venv.")
    parser.add_argument("--venv-dir", type=Path, default=None, help="Custom build virtual environment directory.")
    parser.add_argument("--system", default=None, help="Override platform detection for testing.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    config = BuildConfig(
        repo_root=repo_root,
        system=args.system,
        onefile=args.onefile,
        clean=args.clean,
        skip_install=args.skip_install,
        venv_dir=args.venv_dir,
    )
    run_build(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
