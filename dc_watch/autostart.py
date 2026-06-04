from __future__ import annotations

import shlex
import subprocess
import sys

TASK_NAME = "DCWatch"


def install_autostart() -> subprocess.CompletedProcess[str]:
    target = _launch_command()
    return subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON", "/TR", target, "/F"],
        text=True,
        capture_output=True,
        check=False,
    )


def uninstall_autostart() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        text=True,
        capture_output=True,
        check=False,
    )


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return shlex.quote(sys.executable)
    return f"{shlex.quote(sys.executable)} -m dc_watch gui"
