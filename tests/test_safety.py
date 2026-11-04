"""ArkAngel never writes into the operating system's folders, whoever runs it and whatever path it's given."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from ark_angel.cases.store import FileCaseStore
from ark_angel.cli import cli
from ark_angel.models import Case
from ark_angel.safety import UnsafeLocation, ensure_safe_target, protected_reason

WIN_ENV = {"SystemRoot": "C:\\Windows", "ProgramFiles": "C:\\Program Files",
           "ProgramFiles(x86)": "C:\\Program Files (x86)", "ProgramData": "C:\\ProgramData"}


@pytest.mark.parametrize("path", [
    "C:\\Windows\\System32\\drivers\\etc\\hosts", "c:\\windows\\x.html", "C:\\Program Files\\ArkAngel\\cases",
    "C:\\Program Files (x86)\\x", "C:\\ProgramData\\x", "C:\\", "D:\\", "E:\\Windows\\x",
])
def test_windows_system_folders_are_refused(path: str) -> None:
    env = {**WIN_ENV, "windir": "E:\\Windows"}  # a second install, named by the environment
    assert protected_reason(path, "win32", env)


@pytest.mark.parametrize("path", [
    "C:\\Users\\riley\\Documents\\cases", "C:\\Users\\riley\\Downloads\\ark_angel_demo.html",
    "C:\\Users\\riley\\AppData\\Local\\Temp\\x", "D:\\work\\cases", "C:\\Windowsill\\x", "C:\\report.html",
])
def test_windows_user_folders_are_fine(path: str) -> None:
    assert protected_reason(path, "win32", WIN_ENV) == ""


@pytest.mark.parametrize("path, refused", [
    ("/usr/share/x", True), ("/usr", True), ("/etc/hosts", True), ("/bin/sh", True), ("/", True), ("/boot/x", True),
    ("/System/Library/x", True), ("/Library/LaunchDaemons/x", True), ("/var/lib/dpkg", True),
    ("/home/riley/cases", False), ("/Users/riley/Library/x", False), ("/tmp/x", False), ("/usrdata/x", False),
    ("/data/cases", False), ("/var/tmp/x", False),
])
def test_unix_rules(path: str, refused: bool) -> None:
    assert bool(protected_reason(path, "linux", {})) is refused


@pytest.mark.skipif(sys.platform == "win32", reason="Unix paths")
def test_links_and_dotdot_cant_sneak_in(tmp_path: Path) -> None:
    assert protected_reason("/home/../usr/lib/x")
    link = tmp_path / "innocent"
    link.symlink_to("/etc")
    with pytest.raises(UnsafeLocation, match="part of /etc"):
        ensure_safe_target(link / "x.html")


def _run(*args: str, cwd: Path):
    old = os.getcwd()
    os.chdir(cwd)
    try:
        return CliRunner().invoke(cli, list(args))
    finally:
        os.chdir(old)


def _system_folder() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "arkangel-test")
    return "/usr/arkangel-test"


def test_cli_refuses_a_data_folder_or_output_in_a_system_folder(tmp_path: Path) -> None:
    """Even run as Administrator/root (as CI's Windows runners are), nothing gets written there."""
    target = _system_folder()
    result = _run("--data-dir", target, "case", "create", "X", "x", cwd=tmp_path)
    assert result.exit_code == 1 and "refusing to save cases there" in result.output
    assert not os.path.exists(target)

    _run("--data-dir", "cases", "case", "create", "X", "x", cwd=tmp_path)
    out = os.path.join(target, "report.html")
    result = _run("--data-dir", "cases", "report", "X", "--out", out, cwd=tmp_path)
    assert result.exit_code == 1 and "refusing to write a file there" in result.output
    assert not os.path.exists(out)

    result = _run("--data-dir", "cases", "report", "X", "--out", "mine.html", cwd=tmp_path)
    assert result.exit_code == 0 and (tmp_path / "mine.html").exists()


def test_the_store_checks_before_it_writes_or_deletes(tmp_path: Path) -> None:
    store = FileCaseStore(_system_folder())
    with pytest.raises(UnsafeLocation):
        store.create_case(Case("X", "x"))
    with pytest.raises(UnsafeLocation):
        store.delete_case("X")
    assert not os.path.exists(_system_folder())


def test_demo_writes_only_where_it_was_run(tmp_path: Path) -> None:
    before = set(Path(tmp_path).iterdir())
    result = _run("--data-dir", "cases", "demo", "--out", "demo.html", cwd=tmp_path)
    assert result.exit_code == 0, result.output
    created = {p.name for p in set(Path(tmp_path).iterdir()) - before}
    assert created == {"cases", "demo_inputs", "demo.html"}
