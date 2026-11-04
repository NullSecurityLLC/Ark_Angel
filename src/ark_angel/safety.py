"""Keep ArkAngel's writes out of the operating system's own folders.

ArkAngel writes in exactly three kinds of place: its data folder (``.ark_angel``
under the folder you run it from, or ``--data-dir``), files you name with
``--out``, and the system temp folder. It never writes the registry, your home
folder or anything else on its own.

Those paths come from you, and the program trusts you, so a typo or a pasted
command run as Administrator could still point one at ``C:\\Windows`` or
``/usr``. This module refuses that, whatever the account's permissions: no
path inside the operating system's folders, and no drive or filesystem root,
is ever used as a place to write.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Mapping, Optional

# Folders that belong to the operating system or installed programs.
_POSIX_PROTECTED = ("/bin", "/sbin", "/usr", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/boot", "/dev",
                    "/proc", "/sys", "/opt", "/snap", "/System", "/Library", "/Applications", "/private/etc",
                    "/private/var/db", "/var/lib", "/var/db")
_WINDOWS_ENV = ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")
_WINDOWS_DEFAULTS = ("C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)", "C:\\ProgramData")


class UnsafeLocation(ValueError):
    """A write was aimed at an operating-system folder. The message says which and what to do instead."""


def _windows_roots(env: Mapping[str, str]) -> list[PureWindowsPath]:
    roots = {PureWindowsPath(value) for name in _WINDOWS_ENV if (value := env.get(name))}
    roots |= {PureWindowsPath(path) for path in _WINDOWS_DEFAULTS}
    return sorted(roots, key=str)


def protected_reason(path: str | os.PathLike, platform: Optional[str] = None,
                     env: Optional[Mapping[str, str]] = None) -> str:
    """Why ``path`` mustn't be written to, or ``""`` if it's fine.

    ``platform`` and ``env`` default to this machine's; tests pass others to check the Windows rules on Linux.
    """
    platform = platform or sys.platform
    env = os.environ if env is None else env
    windows = platform.startswith("win")
    if platform == sys.platform:
        try:  # follow links and "..", so C:\\Users\\..\\Windows or a symlink into /usr is still caught
            resolved: PurePath = Path(path).resolve()
        except (OSError, RuntimeError):
            resolved = Path(os.path.abspath(path))
        if windows:
            resolved = PureWindowsPath(str(resolved))
    else:
        resolved = PureWindowsPath(path) if windows else PurePosixPath(path)

    if windows:
        lowered = PureWindowsPath(str(resolved).lower())
        if str(lowered).rstrip("\\") in (lowered.anchor.rstrip("\\"), "") and lowered.anchor:
            return f"{resolved} is the top of a drive"
        for root in _windows_roots(env):
            low_root = PureWindowsPath(str(root).lower())
            if lowered == low_root or low_root in lowered.parents:
                return f"{resolved} is part of {root}, which belongs to Windows or installed programs"
        return ""
    if str(resolved) == "/":
        return "/ is the top of the filesystem"
    for root in map(PurePosixPath, _POSIX_PROTECTED):
        if resolved == root or root in resolved.parents:
            return f"{resolved} is part of {root}, which belongs to the operating system"
    return ""


def ensure_safe_target(path: str | os.PathLike, what: str = "write") -> None:
    """Raise :class:`UnsafeLocation` if ``path`` (a file or folder about to be written) is an OS folder."""
    reason = protected_reason(path)
    if reason:
        raise UnsafeLocation(f"refusing to {what} there: {reason}. Pick a folder of your own, such as one in "
                             "Documents, and run ArkAngel from it (it never needs Administrator or root).")


def running_elevated() -> bool:
    """True when running as Administrator (Windows). ArkAngel never needs it."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False
