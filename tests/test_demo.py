from pathlib import Path

from click.testing import CliRunner

from ark_angel.cli import cli


def test_demo_runs_offline_and_surfaces_planted_patterns(tmp_path: Path) -> None:
    out = tmp_path / "demo.html"
    result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path / "cases"), "demo", "--out", str(out)])

    assert result.exit_code == 0, result.output
    top = [line for line in result.output.splitlines() if line.strip()[:2] in {"1.", "2.", "3.", "4.", "5."}]
    assert "Location cluster around Union Station: 7 items" in top[0]
    assert "Handle rt_demo_owl" in top[1]
    assert "Phone 3035550147" in top[2]
    assert "Plate ABC1234" in top[3]
    assert "Email sam.k.demo@example.org" in top[4]
    assert "unresolved place(s): Boulder, Cherry Creek, Union Station" in result.output
    page = out.read_text()
    assert "Synthetic demo" in page and "rt_demo_owl" in page


def test_demo_is_rerunnable(tmp_path: Path) -> None:
    args = ["--data-dir", str(tmp_path / "cases"), "demo", "--out", str(tmp_path / "d.html")]
    CliRunner().invoke(cli, args)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output


def test_demo_links_vague_boulder_tip_to_old_photo(tmp_path: Path) -> None:
    data_dir = str(tmp_path / "cases")
    CliRunner().invoke(cli, ["--data-dir", data_dir, "demo", "--out", str(tmp_path / "d.html")])
    report = CliRunner().invoke(cli, ["--data-dir", data_dir, "report", "DEMO"]).output
    assert "Place Boulder appears in 2 items across 2 sources" in report
    assert "image:trailhead_old.jpg" in report
