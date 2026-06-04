from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from dataclasses import dataclass

NUDENET_PACKAGE_SPEC = "nudenet>=3.4"


@dataclass(frozen=True)
class OptionalDependencyInstallResult:
    available: bool
    attempted: bool
    installed: bool
    returncode: int | None
    message: str
    stdout: str = ""
    stderr: str = ""


def is_nudenet_available() -> bool:
    importlib.invalidate_caches()
    return importlib.util.find_spec("nudenet") is not None


def install_nudenet(timeout_seconds: int = 900) -> OptionalDependencyInstallResult:
    if is_nudenet_available():
        return OptionalDependencyInstallResult(
            available=True,
            attempted=False,
            installed=True,
            returncode=0,
            message="NudeNet is already available.",
        )

    if getattr(sys, "frozen", False):
        return OptionalDependencyInstallResult(
            available=False,
            attempted=False,
            installed=False,
            returncode=None,
            message=(
                "This PyInstaller EXE cannot reliably install NudeNet into its bundled runtime. "
                "Use a release build that already includes NudeNet."
            ),
        )

    command = [sys.executable, "-m", "pip", "install", NUDENET_PACKAGE_SPEC]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as exc:
        return OptionalDependencyInstallResult(
            available=False,
            attempted=True,
            installed=False,
            returncode=None,
            message=f"NudeNet installation failed: {exc.__class__.__name__}",
        )

    available = is_nudenet_available()
    installed = completed.returncode == 0 and available
    message = "NudeNet installed successfully." if installed else "NudeNet installation did not complete successfully."
    return OptionalDependencyInstallResult(
        available=available,
        attempted=True,
        installed=installed,
        returncode=completed.returncode,
        message=message,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
